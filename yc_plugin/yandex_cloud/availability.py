"""Availability and parameter extraction shared by the Yandex Cloud family.

Only one integration record is registered for Yandex Cloud, and every sibling
package — logging, monitoring, compute and the rest — reads its credentials
from that single ``yandex_cloud`` source entry. The helpers here are how they
do it, mirroring how the AWS sub-service tools share the ``aws`` record.

Synthetic tests inject a fixture through the ``_backend`` slot on the source
dict, so availability accepts either real credentials or a backend.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Final

from yc_plugin.yandex_cloud.config_model import YandexCloudIntegrationConfig
from yc_plugin.yandex_cloud.rest_client import YandexCloudClient

SOURCE = "yandex_cloud"


def yc_source(sources: dict[str, dict]) -> dict[str, Any]:
    """Return the Yandex Cloud source entry, or an empty dict when absent."""
    return sources.get(SOURCE, {}) or {}


def _env_credentials() -> dict[str, Any]:
    """Resolve credentials from the saved config file, with env overrides.

    A blocker-free plugin has no entry in the core integration catalog, so tools
    resolve credentials themselves. They come from ``~/.opensre-yc/config.json``
    (written by ``opensre-yc configure``) and any YC_* environment variable takes
    precedence, which keeps the file the source of truth without losing the
    env-override escape hatch.
    """
    from yc_plugin import config

    return config.resolved_credentials()


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


#: Clients kept per credential set. Small on purpose: an investigation uses one
#: credential set, and the extra slots only cover a token rotating mid-run.
_CLIENT_CACHE_SIZE: Final = 8

_client_cache: OrderedDict[str, YandexCloudClient] = OrderedDict()
_client_cache_lock = threading.Lock()


def _credential_fingerprint(config: YandexCloudIntegrationConfig) -> str:
    """Identify the credential set a client is bound to.

    Hashed rather than kept verbatim, so a service-account key never becomes a
    dictionary key that a debugger, a crash dump or a stray repr could surface.
    """
    material = "\x00".join(
        str(value)
        for value in (
            config.folder_id,
            config.cloud_id,
            config.sa_key_file,
            config.sa_key,
            config.oauth_token,
            config.iam_token,
            config.use_metadata,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def client_from_params(params: Mapping[str, Any]) -> YandexCloudClient | None:
    """Return a REST client for the injected credentials, or None when unusable.

    Clients are reused across tool calls. A client owns the IAM token cache, and
    every tool builds its client from the credentials injected into that one
    call, so a fresh instance per call meant the cache never lived long enough
    to be read: an investigation minted a token — and on a VM, made a metadata
    round-trip for it — once per tool call rather than once per run.
    """
    config = config_from_params(params)
    if config is None:
        return None

    key = _credential_fingerprint(config)
    with _client_cache_lock:
        cached = _client_cache.get(key)
        if cached is not None:
            _client_cache.move_to_end(key)
            return cached

        client = YandexCloudClient(config)
        _client_cache[key] = client
        if len(_client_cache) > _CLIENT_CACHE_SIZE:
            _client_cache.popitem(last=False)
        return client


def reset_client_cache() -> None:
    """Drop every cached client, so a test starts from a cold token cache."""
    with _client_cache_lock:
        _client_cache.clear()


__all__ = [
    "SOURCE",
    "YC_INJECTED_PARAMS",
    "client_from_params",
    "config_from_params",
    "reset_client_cache",
    "yc_available_or_backend",
    "yc_credentials",
    "yc_source",
]
