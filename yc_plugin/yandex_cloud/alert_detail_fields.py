"""Yandex Cloud alert-detail field registration.

These are the identifiers that turn "something is wrong" into a query. Every
read is folder-scoped, so ``yc_folder_id`` is what makes any follow-up possible
at all; the rest name the resource an alert is about, which is what decides
whether the agent goes to Compute, a managed database, Kubernetes or a function.

Registered from ``integrations/harness_adapters.py`` via
:func:`core.domain.alerts.extraction.register_alert_detail_fields`, so the
extraction schema knows the names without core hardcoding a vendor.
"""

from __future__ import annotations

ALERT_DETAIL_FIELDS: tuple[str, ...] = (
    "yc_folder_id",
    "yc_cloud_id",
    "yc_alert_id",
    "yc_cluster_id",
    "yc_instance_id",
    "yc_log_group_id",
    "yc_function_name",
)

__all__ = ["ALERT_DETAIL_FIELDS"]
