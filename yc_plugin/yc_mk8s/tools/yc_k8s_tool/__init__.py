"""Managed Kubernetes cluster and node-group health."""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from yc_plugin.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    client_from_params,
    yc_available_or_backend,
    yc_credentials,
)

SOURCE = "yc_mk8s"
SERVICE = "managed-kubernetes"

#: Named here rather than inside the result literal: the point of the hint is
#: that it arrives exactly when the agent is looking at a cluster and about to
#: go looking for its pods in the wrong API.
_WORKLOAD_TOOLS = (
    "kubernetes_list_pods",
    "kubernetes_get_events",
    "kubernetes_get_pod_logs",
    "kubernetes_describe_pod",
    "kubernetes_list_nodes",
)
_WORKLOAD_HINT = (
    "Pods, events, container logs and nodes are read with these tools, not "
    "through the Yandex Cloud API — it has no endpoint for them. A pod that is "
    "not starting needs kubernetes_get_events and kubernetes_describe_pod: the "
    "scheduler's own reason is there, and a pod listing only shows that it is "
    "stuck. A Pending pod has no container logs at all."
)
_WORKLOAD_UNAVAILABLE = (
    "No cluster is connected for workload reads, so pods and events cannot be "
    "read. `opensre-yc configure` connects one. Do not try to read them through "
    "the Yandex Cloud API instead: it has no endpoint for them."
)


def _workload_access() -> dict[str, Any]:
    """Say how to read what runs inside the cluster, and whether that is set up."""
    from tools.registry import get_registered_tool_map

    if "kubernetes_list_pods" in get_registered_tool_map():
        return {"workload_tools": list(_WORKLOAD_TOOLS), "workload_hint": _WORKLOAD_HINT}
    return {"workload_tools": [], "workload_hint": _WORKLOAD_UNAVAILABLE}

_CLUSTERS_PATH = "/managed-kubernetes/v1/clusters"
_NODE_GROUPS_PATH = "/managed-kubernetes/v1/nodeGroups"

_HEALTHY_CLUSTER_STATUS = "RUNNING"
_HEALTHY_CLUSTER_HEALTH = "HEALTHY"


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


def _summarize_cluster(cluster: dict[str, Any]) -> dict[str, Any]:
    master = cluster.get("master") or {}
    version = (master.get("versionInfo") or {}).get("currentVersion", "")
    return {
        "id": cluster.get("id", ""),
        "name": cluster.get("name", ""),
        "status": cluster.get("status", ""),
        "health": cluster.get("health", ""),
        "version": version,
        "release_channel": cluster.get("releaseChannel", ""),
        "created_at": cluster.get("createdAt", ""),
        "healthy": (
            cluster.get("status") == _HEALTHY_CLUSTER_STATUS
            and cluster.get("health") == _HEALTHY_CLUSTER_HEALTH
        ),
    }


def _summarize_node_group(group: dict[str, Any]) -> dict[str, Any]:
    scale = (group.get("scalePolicy") or {}).get("fixedScale") or {}
    auto = (group.get("scalePolicy") or {}).get("autoScale") or {}
    return {
        "id": group.get("id", ""),
        "name": group.get("name", ""),
        "status": group.get("status", ""),
        "version": (group.get("versionInfo") or {}).get("currentVersion", ""),
        "fixed_size": scale.get("size", ""),
        "auto_scale": {"min": auto.get("minSize", ""), "max": auto.get("maxSize", "")}
        if auto
        else {},
        "allocation_zones": [
            location.get("zoneId", "")
            for location in (group.get("allocationPolicy") or {}).get("locations", [])
        ],
    }


def _master_access(cluster: dict[str, Any]) -> dict[str, Any]:
    """Return what is needed to reach the cluster's API server.

    Handed back so the kubernetes integration can be pointed at the cluster
    without anyone having to run the CLI to find these.
    """
    master = cluster.get("master") or {}
    endpoints = master.get("endpoints") or {}
    return {
        "external_endpoint": endpoints.get("externalV4Endpoint", ""),
        "internal_endpoint": endpoints.get("internalV4Endpoint", ""),
        "cluster_ca_certificate": (master.get("masterAuth") or {}).get("clusterCaCertificate", ""),
    }


@tool(
    name="list_yc_k8s_clusters",
    display_name="Managed Kubernetes",
    source=SOURCE,
    description=(
        "List Managed Kubernetes clusters in the folder with their status, "
        "health, and version. Use to find a cluster id or to check whether the "
        "control plane itself is degraded before investigating workloads. "
        "Reads the control plane only — pods, events and container logs come "
        "from the kubernetes_* tools, and the result names them."
    ),
    use_cases=[
        "Finding a cluster id from its name",
        "Checking whether the control plane is healthy",
        "Reviewing cluster versions and release channels",
    ],
    requires=[],
    outputs={
        "clusters": "clusters with status, health, and version",
        "unhealthy": "the subset that is not running and healthy",
        "count": "how many clusters were returned",
    },
    input_schema={
        "type": "object",
        "properties": {
            "page_token": {
                "type": "string",
                "description": "Token from a previous call's next_page_token.",
                "default": "",
            }
        },
        "required": [],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def list_yc_k8s_clusters(
    page_token: str = "",
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """List Managed Kubernetes clusters."""
    if yc_backend is not None:
        response = dict(yc_backend.list_yc_k8s_clusters(page_token))
    else:
        client = client_from_params(credentials)
        if client is None:
            return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")
        response = client.get(
            SERVICE,
            _CLUSTERS_PATH,
            {"folderId": client.folder_id},
            page_token=page_token,
        )

    if not response.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": response.get("error", "Could not list clusters."),
        }

    raw = (response.get("data") or {}).get("clusters") or []
    clusters = [_summarize_cluster(cluster) for cluster in raw]
    return {
        "source": SOURCE,
        "available": True,
        "clusters": clusters,
        "unhealthy": [cluster for cluster in clusters if not cluster["healthy"]],
        "count": len(clusters),
        "next_page_token": response.get("metadata", {}).get("next_page_token", ""),
        **_workload_access(),
    }


@tool(
    name="get_yc_k8s_cluster",
    display_name="Managed Kubernetes",
    source=SOURCE,
    description=(
        "Read one Managed Kubernetes cluster: control-plane health, version, "
        "maintenance window, and the state of every node group. Use to tell a "
        "cluster-level problem from a workload one — a node group part-way "
        "through an update or failing to scale explains a lot of pod symptoms. "
        "Also returns the master endpoint and CA certificate, which is what the "
        "kubernetes integration needs to inspect workloads in this cluster."
    ),
    use_cases=[
        "Checking whether node groups are healthy, updating, or failing to scale",
        "Confirming a cluster is mid-upgrade when pods start failing",
        "Finding the master endpoint and CA to connect for workload inspection",
        "Reviewing the maintenance window against the time an incident started",
    ],
    requires=["cluster_id"],
    outputs={
        "cluster": "control-plane status, health, version, and maintenance policy",
        "node_groups": "each node group's status, size, and version",
        "master_access": "endpoint and CA certificate for reaching the API server",
    },
    input_schema={
        "type": "object",
        "properties": {
            "cluster_id": {
                "type": "string",
                "description": "Cluster id, as returned by list_yc_k8s_clusters.",
            }
        },
        "required": ["cluster_id"],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def get_yc_k8s_cluster(
    cluster_id: str,
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Read one cluster and its node groups."""
    if not cluster_id.strip():
        return tool_unavailable(
            SOURCE, "cluster_id is required. Call list_yc_k8s_clusters to find one."
        )

    if yc_backend is not None:
        return dict(yc_backend.get_yc_k8s_cluster(cluster_id))

    client = client_from_params(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

    detail = client.get(SERVICE, f"{_CLUSTERS_PATH}/{cluster_id}", page_size=None)
    if not detail.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": detail.get("error", "Could not read the cluster."),
            "cluster_id": cluster_id,
        }

    cluster = detail.get("data") or {}
    groups_response = client.get(
        SERVICE,
        _NODE_GROUPS_PATH,
        {"folderId": client.folder_id},
    )
    raw_groups = (groups_response.get("data") or {}).get("nodeGroups") or []
    node_groups = [
        _summarize_node_group(group) for group in raw_groups if group.get("clusterId") == cluster_id
    ]

    summary = _summarize_cluster(cluster)
    return {
        "source": SOURCE,
        "available": True,
        "cluster_id": cluster_id,
        "cluster": {
            **summary,
            "maintenance_policy": cluster.get("masterAutoUpgrade", "")
            or (cluster.get("master") or {}).get("maintenancePolicy", {}),
            "network_id": cluster.get("networkId", ""),
        },
        "node_groups": node_groups,
        "unhealthy_node_groups": [group for group in node_groups if group["status"] != "RUNNING"],
        "master_access": _master_access(cluster),
        **_workload_access(),
    }


__all__ = ["get_yc_k8s_cluster", "list_yc_k8s_clusters"]
