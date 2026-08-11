"""Join OpenSRE's integration catalog, when the installed OpenSRE allows it.

Until Tracer-Cloud/opensre#4866 lands there is no way for a package outside that
repository to say "this integration exists", so the plugin keeps its own config
file and its own ``opensre-yc configure`` command, and the tools read credentials
themselves. Everything here is what replaces that once the hooks are available:
Yandex Cloud becomes a service like any other, with ``opensre integrations setup
yandex_cloud``, ``verify yandex_cloud``, credentials in ``~/.opensre``, and a row
in the welcome banner.

Registration is attempted and never required. An OpenSRE without the hooks raises
``ImportError`` here, the plugin logs one line and falls back to the environment
path it has always used, so the same wheel works against both.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from yc_plugin.constants import (
    YC_CLOUD_ID_ENV,
    YC_FOLDER_ID_ENV,
    YC_IAM_TOKEN_ENV,
    YC_SA_KEY_ENV,
    YC_SA_KEY_FILE_ENV,
    YC_TOKEN_ENV,
    YC_USE_METADATA_ENV,
)
from yc_plugin.yandex_cloud import SERVICE, classify

logger = logging.getLogger(__name__)

#: Ordered after the built-ins. The number only decides where the service sits
#: in `integrations setup` and `integrations verify` listings.
SETUP_ORDER = 90
VERIFY_ORDER = 90

#: Any one of these is enough to hold a Yandex Cloud credential.
_CREDENTIAL_ENVS = (
    YC_SA_KEY_FILE_ENV,
    YC_SA_KEY_ENV,
    YC_TOKEN_ENV,
    YC_IAM_TOKEN_ENV,
)


def _env_value(name: str) -> str:
    return os.getenv(name, "").strip()


def is_configured_in_env() -> bool:
    """Cheap "is it configured?" for the pre-prompt startup check.

    Reads environment variables only. This runs before the first prompt, where
    resolving a secret from the keyring would block, so it deliberately does not
    build or validate a config.
    """
    if not _env_value(YC_FOLDER_ID_ENV):
        return False
    if _env_value(YC_USE_METADATA_ENV).lower() in {"1", "true", "yes"}:
        return True
    return any(_env_value(name) for name in _CREDENTIAL_ENVS)


def load_from_env() -> dict[str, Any] | None:
    """Return the integration record described by ``YC_*``, or None.

    Same shape the built-in loaders produce. Called during the full environment
    load, which is allowed to resolve secrets.
    """
    if not is_configured_in_env():
        return None

    from config.llm_credentials import resolve_env_credential

    credentials = {
        "folder_id": _env_value(YC_FOLDER_ID_ENV),
        "cloud_id": _env_value(YC_CLOUD_ID_ENV),
        "sa_key_file": _env_value(YC_SA_KEY_FILE_ENV),
        "sa_key": resolve_env_credential(YC_SA_KEY_ENV),
        "oauth_token": resolve_env_credential(YC_TOKEN_ENV),
        "iam_token": resolve_env_credential(YC_IAM_TOKEN_ENV),
        "use_metadata": _env_value(YC_USE_METADATA_ENV).lower() in {"1", "true", "yes"},
    }
    return {
        "id": "env-yandex-cloud",
        "service": SERVICE,
        "status": "active",
        "source": "local env",
        "credentials": credentials,
    }


def _register_setup_handler() -> None:
    """Make ``opensre integrations setup yandex_cloud`` run the plugin's flow.

    ``_HANDLERS`` has no public wrapper yet, but entries are appended to it after
    the literal for built-ins too (``_HANDLERS["temporal"] = ...``), so adding one
    is the documented shape rather than a reach into private state.
    """
    from integrations.cli import _HANDLERS, _run_spec_setup
    from yc_plugin.yandex_cloud.setup import YANDEX_CLOUD_SETUP

    def _setup_yandex_cloud() -> None:
        _run_spec_setup(YANDEX_CLOUD_SETUP)

    _HANDLERS[SERVICE] = _setup_yandex_cloud


def register_with_core() -> bool:
    """Register the integration with OpenSRE. Returns whether it took.

    False means the installed OpenSRE predates the registration hooks; the
    caller keeps using the plugin's own configuration path.
    """
    try:
        from integrations.catalog import (
            register_classifier,
            register_env_loader,
            register_env_presence,
        )
        from integrations.registry import IntegrationSpec, register_integration_spec
    except ImportError:
        logger.info(
            "yandex_cloud: this OpenSRE has no integration registration hooks; "
            "using the plugin's own configuration instead"
        )
        return False

    try:
        register_integration_spec(
            IntegrationSpec(
                service=SERVICE,
                has_verifier=True,
                direct_effective=True,
                setup_order=SETUP_ORDER,
                verify_order=VERIFY_ORDER,
            )
        )
        register_classifier(SERVICE, classify)
        register_env_loader(SERVICE, load_from_env)
        register_env_presence(SERVICE, is_configured_in_env)

        # Imported for its side effect: the module calls register_probe_verifier,
        # which is what `integrations verify yandex_cloud` dispatches to.
        import yc_plugin.yandex_cloud.verifier  # noqa: F401

        _register_setup_handler()
    except ValueError:
        # A key collision, i.e. a genuine bug in this plugin rather than an old
        # OpenSRE. Loud, because setup and verify will be missing.
        logger.exception("yandex_cloud could not be registered with the integration catalog")
        return False

    logger.debug("yandex_cloud registered with the integration catalog")
    return True


__all__ = [
    "is_configured_in_env",
    "load_from_env",
    "register_with_core",
]
