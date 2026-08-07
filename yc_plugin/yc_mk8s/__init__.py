"""Managed Service for Kubernetes tools.

Scope is the control plane: cluster and node-group health, versions, and
maintenance state. Workloads — pods, logs, events — are the ``kubernetes``
integration's job, and pointing it at a Managed Kubernetes cluster works
exactly as it does for any other cluster. ``get_yc_k8s_cluster`` returns the
master endpoint and CA certificate needed to do that.

Credentials come from the ``yandex_cloud`` integration record rather than one
of this package's own — see ``integrations/yandex_cloud/availability.py``.
"""

from __future__ import annotations

SOURCE = "yc_mk8s"

__all__ = ["SOURCE"]
