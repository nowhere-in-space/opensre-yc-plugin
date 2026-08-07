"""opensre-yandex-cloud — external plugin adding Yandex Cloud to OpenSRE.

Everything a plugin can do today without a change to the core package: the 16
read-only tools, alert-source detection and routing, and the model skill — all
wired through public ``register_*`` hooks. Credentials come from the environment
(YC_* / the instance metadata service), because registering the integration in
the core catalog needs a hook core does not yet expose.

Call :func:`install` once at startup.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The tool subpackages, one per Yandex Cloud service family.
_TOOL_PACKAGES = (
    "yc_plugin.yandex_cloud.tools",
    "yc_plugin.yc_monitoring.tools",
    "yc_plugin.yc_logging.tools",
    "yc_plugin.yc_audit.tools",
    "yc_plugin.yc_compute.tools",
    "yc_plugin.yc_mk8s.tools",
    "yc_plugin.yc_mdb.tools",
    "yc_plugin.yc_serverless.tools",
    "yc_plugin.yc_network.tools",
)

_ALERT_SOURCE = "yandex_monitoring"
_RELEVANCE_SOURCES = (
    "yc_monitoring",
    "yc_logging",
    "yc_compute",
    "yc_mdb",
    "yc_mk8s",
    "yc_serverless",
    "yc_network",
    "yc_audit",
    "kubernetes",
)
_SEED_SOURCES = ("yc_monitoring", "yc_logging")


def install() -> None:
    """Register the plugin's tools, alert routing and skill via public hooks."""
    import importlib

    from core.domain.alerts.alert_source import (
        AlertSourceRouting,
        register_alert_source_detector,
        register_alert_source_routing,
    )
    from core.domain.alerts.extraction import register_alert_detail_fields
    from tools.registry import register_external_tool_package

    from yc_plugin.yandex_cloud.alert_detail_fields import ALERT_DETAIL_FIELDS
    from yc_plugin.yandex_cloud.alert_source_detect import detect_yandex_cloud_alert_source

    for dotted in _TOOL_PACKAGES:
        register_external_tool_package(importlib.import_module(dotted))

    register_alert_source_detector(detect_yandex_cloud_alert_source)
    register_alert_source_routing(
        _ALERT_SOURCE,
        AlertSourceRouting(
            relevance_tool_sources=_RELEVANCE_SOURCES,
            seed_tool_sources=_SEED_SOURCES,
        ),
    )
    register_alert_detail_fields(*ALERT_DETAIL_FIELDS)
    logger.info("yandex_cloud plugin installed: %d tool packages", len(_TOOL_PACKAGES))


__all__ = ["install"]
