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

    _configure_llm()
    logger.info("yandex_cloud plugin installed: %d tool packages", len(_TOOL_PACKAGES))


_AI_STUDIO_HOST = "https://ai.api.cloud.yandex.net"


def _configure_llm() -> None:
    """Point the OpenAI-compatible ollama provider at Yandex AI Studio.

    Yandex AI Studio speaks the OpenAI API, and OpenSRE's ollama provider takes
    its endpoint from OLLAMA_HOST, so a language model is a matter of setting a
    few variables rather than adding a provider. When the credential is the
    instance service account, the bearer token is minted from the metadata
    service here, so the operator never handles a token.
    """
    import os

    from yc_plugin import config

    settings = config.llm_settings()
    if not settings:
        return

    creds = config.resolved_credentials()
    folder_id = str(creds.get("folder_id") or "")
    if not folder_id and creds.get("use_metadata"):
        from yc_plugin import metadata

        folder_id = metadata.fetch_folder_id() or ""
    model = str(settings.get("model") or "gpt-oss-120b")

    key = _llm_bearer(creds)
    if not (folder_id and key):
        logger.warning("yandex_cloud LLM not configured: missing folder or credential")
        return

    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_HOST"] = _AI_STUDIO_HOST
    os.environ["OLLAMA_MODEL"] = f"gpt://{folder_id}/{model}"
    os.environ["OLLAMA_API_KEY"] = key
    logger.info("yandex_cloud LLM configured via ollama provider: %s", model)


def _llm_bearer(creds: dict) -> str:
    """Return the bearer token AI Studio accepts, minting from metadata if needed.

    An IAM token expires within hours, so it is minted per process here rather
    than stored. A static API key or OAuth token from the config is used as-is.
    """
    if creds.get("iam_token"):
        return str(creds["iam_token"])
    if creds.get("oauth_token"):
        return str(creds["oauth_token"])
    if creds.get("use_metadata"):
        from yc_plugin import metadata

        token = metadata.fetch_token()
        return token.token if token else ""
    return ""


__all__ = ["install"]
