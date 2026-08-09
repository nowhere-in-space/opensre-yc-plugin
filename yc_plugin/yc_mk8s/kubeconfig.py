"""Point OpenSRE's ``kubernetes`` integration at a Managed Kubernetes cluster.

OpenSRE already reads workloads — pods, events, pod logs, deployments, nodes —
through its own ``kubernetes`` integration, and that integration wants one
thing: a kubeconfig. Managed Kubernetes hands out everything needed to build
one (master endpoint, cluster CA), and its API server accepts a Yandex Cloud
IAM token as a bearer credential, so no ``yc`` CLI and no exec plugin are
involved.

So this adds no tools. It sets ``KUBECONFIG_CONTENT`` the same way the language
model is pointed at AI Studio: configuration, not code. The alternative —
writing pod and event readers of our own — would duplicate twelve existing
tools and spend twelve of the thirty-two tool schemas an investigation is
allowed.

What the agent can do inside the cluster is decided by the service account's
role, not by anything here. ``k8s.cluster-api.viewer`` maps to the built-in
``view`` role, which covers namespace resources but excludes nodes and secrets.
Reading nodes needs a narrow ClusterRole bound to the ``yc:viewer`` group;
``cluster-admin`` would work too and should not be used, because it puts a
credential that can delete the cluster into the agent's environment.
"""

from __future__ import annotations

import json
import logging
import os
from base64 import b64encode
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

SERVICE: Final = "managed-kubernetes"
CLUSTERS_PATH: Final = "/managed-kubernetes/v1/clusters"

#: Read by OpenSRE's kubernetes integration. Set here, never by the operator.
KUBECONFIG_CONTENT_ENV: Final = "KUBECONFIG_CONTENT"

_PEM_HEADER: Final = "-----BEGIN"


@dataclass(frozen=True)
class ClusterAccess:
    """Everything needed to reach one cluster's API server."""

    cluster_id: str
    name: str
    internal_endpoint: str
    external_endpoint: str
    ca_certificate: str
    status: str = ""

    @property
    def is_reachable(self) -> bool:
        return bool(self.internal_endpoint or self.external_endpoint)

    def as_config(self) -> dict[str, Any]:
        """Return the fields worth saving. The token is deliberately absent.

        An IAM token expires within hours, so storing one would leave a config
        file that works on the day it is written and fails afterwards.
        """
        return {
            "cluster_id": self.cluster_id,
            "cluster_name": self.name,
            "internal_endpoint": self.internal_endpoint,
            "external_endpoint": self.external_endpoint,
            "ca_certificate": self.ca_certificate,
        }


def from_payload(cluster: dict[str, Any]) -> ClusterAccess:
    """Read one cluster out of a ``managed-kubernetes`` list response."""
    master = cluster.get("master") or {}
    endpoints = master.get("endpoints") or {}
    return ClusterAccess(
        cluster_id=str(cluster.get("id", "")),
        name=str(cluster.get("name", "")),
        internal_endpoint=str(endpoints.get("internalV4Endpoint", "")),
        external_endpoint=str(endpoints.get("externalV4Endpoint", "")),
        ca_certificate=str((master.get("masterAuth") or {}).get("clusterCaCertificate", "")),
        status=str(cluster.get("status", "")),
    )


def from_config(saved: dict[str, Any]) -> ClusterAccess:
    """Rebuild the access details saved by the setup wizard."""
    return ClusterAccess(
        cluster_id=str(saved.get("cluster_id", "")),
        name=str(saved.get("cluster_name", "")),
        internal_endpoint=str(saved.get("internal_endpoint", "")),
        external_endpoint=str(saved.get("external_endpoint", "")),
        ca_certificate=str(saved.get("ca_certificate", "")),
    )


def list_clusters(client: Any, folder_id: str = "") -> list[ClusterAccess]:
    """Return the clusters in the folder, or an empty list when none are readable."""
    result = client.get(SERVICE, CLUSTERS_PATH, {"folderId": folder_id or client.folder_id})
    clusters = (result.get("data") or {}).get("clusters") or []
    return [from_payload(cluster) for cluster in clusters]


def choose_endpoint(access: ClusterAccess, *, on_instance: bool) -> str:
    """Return the endpoint to dial, or "" when the cluster cannot be reached.

    Running inside Yandex Cloud, the internal address keeps API-server traffic
    on the cloud network. From anywhere else it is unroutable, so the public
    one is the only option — and a cluster without public access simply cannot
    be reached from outside, which is a configuration answer rather than a
    connection to retry.
    """
    if on_instance and access.internal_endpoint:
        return access.internal_endpoint
    if access.external_endpoint:
        return access.external_endpoint
    # Off-instance with no public endpoint, or a cluster still being created.
    return access.internal_endpoint if on_instance else ""


def unreachable_reason(access: ClusterAccess, *, on_instance: bool) -> str:
    """Explain why no endpoint was usable, in terms of what to change."""
    if not access.is_reachable:
        return (
            f"Cluster {access.name or access.cluster_id} publishes no API-server endpoint. "
            "It may still be starting up."
        )
    return (
        f"Cluster {access.name or access.cluster_id} is reachable only from inside its "
        "cloud network. Run the agent on a VM in the same network, or give the cluster's "
        "master a public endpoint."
    )


def _certificate_authority_data(certificate: str) -> str:
    """Return the CA as base64, which is the only form a kubeconfig accepts."""
    if certificate.startswith(_PEM_HEADER):
        return b64encode(certificate.encode("utf-8")).decode("ascii")
    return certificate


def build(access: ClusterAccess, endpoint: str, token: str) -> str:
    """Return a self-contained kubeconfig for *endpoint*, authenticating with *token*.

    Emitted as JSON, which every YAML parser accepts, so the plugin does not
    need a YAML library of its own to produce it.
    """
    name = access.name or access.cluster_id or "yandex-managed-kubernetes"
    return json.dumps(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": name,
            "clusters": [
                {
                    "name": name,
                    "cluster": {
                        "server": endpoint,
                        "certificate-authority-data": _certificate_authority_data(
                            access.ca_certificate
                        ),
                    },
                }
            ],
            "users": [{"name": name, "user": {"token": token}}],
            "contexts": [{"name": name, "context": {"cluster": name, "user": name}}],
        }
    )


def configure(settings: dict[str, Any], token: str, *, on_instance: bool) -> bool:
    """Publish a kubeconfig for the configured cluster; return whether one was set.

    Called during ``install()``. A cluster that cannot be reached, or a
    credential that could not be minted, leaves the environment untouched and
    says why — an empty ``KUBECONFIG_CONTENT`` would make OpenSRE report the
    kubernetes integration as configured and then fail on every call.
    """
    if not settings.get("enabled"):
        return False

    access = from_config(settings)
    if not access.ca_certificate:
        logger.warning("yandex_cloud kubernetes not configured: no cluster CA saved")
        return False

    endpoint = choose_endpoint(access, on_instance=on_instance)
    if not endpoint:
        logger.warning(
            "yandex_cloud kubernetes not configured: %s",
            unreachable_reason(access, on_instance=on_instance),
        )
        return False

    if not token:
        logger.warning("yandex_cloud kubernetes not configured: no IAM token available")
        return False

    os.environ[KUBECONFIG_CONTENT_ENV] = build(access, endpoint, token)
    logger.info(
        "yandex_cloud kubernetes configured: cluster %s via %s",
        access.name or access.cluster_id,
        endpoint,
    )
    return True


__all__ = [
    "CLUSTERS_PATH",
    "KUBECONFIG_CONTENT_ENV",
    "SERVICE",
    "ClusterAccess",
    "build",
    "choose_endpoint",
    "configure",
    "from_config",
    "from_payload",
    "list_clusters",
    "unreachable_reason",
]
