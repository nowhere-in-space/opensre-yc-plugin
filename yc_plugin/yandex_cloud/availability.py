"""Availability and parameter extraction shared by the Yandex Cloud family.

Only one integration record is registered for Yandex Cloud, and every sibling
package — logging, monitoring, compute and the rest — reads its credentials
from that single ``yandex_cloud`` source entry. The helpers here are how they
do it, mirroring how the AWS sub-service tools share the ``aws`` record.

Synthetic tests inject a fixture through the ``_backend`` slot on the source
dict, so availability accepts either real credentials or a backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from yc_plugin.yandex_cloud.config_model import YandexCloudIntegrationConfig
from yc_plugin.yandex_cloud.rest_client import YandexCloudClient

SOURCE = "yandex_cloud"


def yc_source(sources: dict[str, dict]) -> dict[str, Any]:
    """Return the Yandex Cloud source entry, or an empty dict when absent."""
    return sources.get(SOURCE, {}) or {}


def _env_credentials() -> dict[str, Any]:
    """Resolve credentials straight from the environment.

    The plugin ships without an integration registered in the core catalog — that
    needs a hook core does not yet expose — so tools read YC_* / the metadata flag
    themselves rather than from sources[SOURCE]. Same shape client_from_params
    expects, so nothing downstream changes.
    """
    import os

    from yc_plugin.constants import (
        YC_CLOUD_ID_ENV,
        YC_FOLDER_ID_ENV,
        YC_IAM_TOKEN_ENV,
        YC_SA_KEY_ENV,
        YC_SA_KEY_FILE_ENV,
        YC_TOKEN_ENV,
        YC_USE_METADATA_ENV,
    )

    use_metadata = os.getenv(YC_USE_METADATA_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "folder_id": os.getenv(YC_FOLDER_ID_ENV, "").strip(),
        "cloud_id": os.getenv(YC_CLOUD_ID_ENV, "").strip(),
        "sa_key_file": os.getenv(YC_SA_KEY_FILE_ENV, "").strip(),
        "sa_key": os.getenv(YC_SA_KEY_ENV, "").strip(),
        "oauth_token": os.getenv(YC_TOKEN_ENV, "").strip(),
        "iam_token": os.getenv(YC_IAM_TOKEN_ENV, "").strip(),
        "use_metadata": use_metadata,
        "yc_backend": None,
    }


def yc_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when a backend is attached (tests) or env credentials resolve.

    The sources dict still carries a synthetic backend for tests; real
    availability is decided by whether the environment yields a usable config.
    """
    backend = yc_source(sources).get("_backend")
    if backend is not None:
        return True
    return config_from_params(_env_credentials()) is not None


def yc_credentials(sources: dict[str, dict]) -> dict[str, Any]:
    """Return the credential fields tools pass to the client, resolved from env.

    A test may still attach a synthetic backend through sources; otherwise the
    credentials come from the environment.
    """
    creds = _env_credentials()
    backend = yc_source(sources).get("_backend")
    if backend is not None:
        creds["yc_backend"] = backend
    return creds


#: Credential keys tools declare as injected, so they never appear in the
#: schema the model sees.
YC_INJECTED_PARAMS: tuple[str, ...] = (
    "folder_id",
    "cloud_id",
    "sa_key_file",
    "sa_key",
    "oauth_token",
    "iam_token",
    "use_metadata",
    "yc_backend",
)


def config_from_params(params: Mapping[str, Any]) -> YandexCloudIntegrationConfig | None:
    """Build a config from the credentials injected into a tool call.

    Returns None when they do not validate — which for a tool means reporting
    itself unavailable rather than raising, since a missing credential is a
    setup problem the agent cannot solve mid-investigation.
    """
    try:
        return YandexCloudIntegrationConfig.model_validate(
            {
                "folder_id": params.get("folder_id", ""),
                "cloud_id": params.get("cloud_id", ""),
                "sa_key_file": params.get("sa_key_file", ""),
                "sa_key": params.get("sa_key", ""),
                "oauth_token": params.get("oauth_token", ""),
                "iam_token": params.get("iam_token", ""),
                "use_metadata": params.get("use_metadata", False),
            }
        )
    except Exception:
        return None


def client_from_params(params: Mapping[str, Any]) -> YandexCloudClient | None:
    """Build a REST client from injected credentials, or None when unusable."""
    config = config_from_params(params)
    return None if config is None else YandexCloudClient(config)


__all__ = [
    "SOURCE",
    "YC_INJECTED_PARAMS",
    "client_from_params",
    "config_from_params",
    "yc_available_or_backend",
    "yc_credentials",
    "yc_source",
]
