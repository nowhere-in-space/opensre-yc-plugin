"""Agent-callable tools for Managed Service for Kubernetes."""

from __future__ import annotations

from yc_plugin.yc_mk8s.tools.yc_k8s_tool import get_yc_k8s_cluster, list_yc_k8s_clusters

__all__ = ["get_yc_k8s_cluster", "list_yc_k8s_clusters"]
