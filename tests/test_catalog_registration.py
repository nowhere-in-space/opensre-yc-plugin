"""Joining OpenSRE's integration catalog, and coping when it has no room.

The four hooks this exercises land in OpenSRE with Tracer-Cloud/opensre#4866.
Until that is released the plugin has to run against both: an OpenSRE with the
hooks, where Yandex Cloud becomes a service like any other, and one without,
where the plugin keeps its own configuration file. Both paths are covered here,
the second by hiding the hooks from the import.
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from yc_plugin.constants import (
    YC_FOLDER_ID_ENV,
    YC_IAM_TOKEN_ENV,
    YC_SA_KEY_FILE_ENV,
    YC_USE_METADATA_ENV,
)
from yc_plugin.yandex_cloud import SERVICE
from yc_plugin.yandex_cloud.catalog_registration import (
    is_configured_in_env,
    load_from_env,
    register_with_core,
)

FOLDER = "b1gtestfolder"


def _hooks_available() -> bool:
    """Whether the installed OpenSRE carries the registration hooks."""
    try:
        from integrations.registry import register_integration_spec  # noqa: F401
    except ImportError:
        return False
    return True


#: CI runs this suite against OpenSRE ``main``, which does not have the hooks
#: yet. The tests that need them skip there; the fallback and the pure
#: environment tests run everywhere.
HOOKS_AVAILABLE = _hooks_available()
NO_HOOKS_REASON = "OpenSRE without the registration hooks (Tracer-Cloud/opensre#4866)"


@pytest.fixture
def registered() -> Any:
    """Register with the catalog, then take the entry out again.

    Registration mutates module-level tables in OpenSRE, so a leak would change
    the service lists every other test in this suite and OpenSRE's own see.
    """
    if not HOOKS_AVAILABLE:
        pytest.skip(NO_HOOKS_REASON)

    from integrations import _catalog_impl, registry

    assert register_with_core(), "the hooks are present but registration did not take"

    yield SERVICE

    registry._EXTERNAL_SPECS[:] = [
        spec for spec in registry._EXTERNAL_SPECS if spec.service != SERVICE
    ]
    registry._rebuild_registry()
    _catalog_impl._EXTERNAL_CLASSIFIERS.pop(SERVICE, None)
    _catalog_impl._EXTERNAL_ENV_LOADERS.pop(SERVICE, None)
    _catalog_impl._EXTERNAL_ENV_PRESENCE.pop(SERVICE, None)
    for hook, key in list(getattr(_catalog_impl, "_EXTERNAL_HOOK_OWNERS", {})):
        if key == SERVICE:
            del _catalog_impl._EXTERNAL_HOOK_OWNERS[hook, key]

    from integrations.cli import _HANDLERS

    _HANDLERS.pop(SERVICE, None)


@pytest.fixture
def env_configured(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv(YC_FOLDER_ID_ENV, FOLDER)
    monkeypatch.setenv(YC_IAM_TOKEN_ENV, "t1.test-token")
    return FOLDER


def test_setup_lists_the_integration(registered: str) -> None:
    """``opensre integrations setup yandex_cloud`` has to be dispatchable."""
    import integrations.cli as cli

    assert SERVICE in cli.setup_services()


def test_the_cli_argument_accepts_the_integration(registered: str) -> None:
    """Click validates the service name before the command body runs."""
    import surfaces.cli.commands.integrations as integrations_group

    setup_command = integrations_group.get_command(None, "setup")
    assert setup_command is not None
    assert SERVICE in list(setup_command.params[0].type.choices)


def test_verify_lists_the_integration(registered: str) -> None:
    from integrations.registry import SUPPORTED_VERIFY_SERVICES

    assert SERVICE in SUPPORTED_VERIFY_SERVICES


def test_a_verifier_is_registered(registered: str) -> None:
    """``integrations verify yandex_cloud`` needs something to dispatch to."""
    from integrations.verification import get_verifier

    assert get_verifier(SERVICE) is not None


def test_the_startup_banner_sees_a_configured_integration(
    registered: str, env_configured: str
) -> None:
    from integrations.catalog import load_env_integration_services

    assert SERVICE in load_env_integration_services()


def test_the_startup_check_stays_quiet_without_a_credential(
    registered: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder alone is not a configured integration."""
    monkeypatch.setenv(YC_FOLDER_ID_ENV, FOLDER)
    monkeypatch.delenv(YC_IAM_TOKEN_ENV, raising=False)

    from integrations.catalog import load_env_integration_services

    assert SERVICE not in load_env_integration_services()


def test_metadata_auth_counts_as_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a Yandex Cloud VM there is no credential to hold - the metadata service issues one."""
    monkeypatch.setenv(YC_FOLDER_ID_ENV, FOLDER)
    monkeypatch.delenv(YC_IAM_TOKEN_ENV, raising=False)
    monkeypatch.setenv(YC_USE_METADATA_ENV, "true")

    assert is_configured_in_env()


def test_the_environment_record_survives_effective_resolution(
    registered: str, env_configured: str
) -> None:
    """The resolver filters unknown services out before validating."""
    from integrations.catalog import resolve_effective_integrations

    record = load_from_env()
    assert record is not None

    effective = resolve_effective_integrations(store_integrations=[], env_integrations=[record])

    assert SERVICE in effective


def test_a_missing_folder_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(YC_FOLDER_ID_ENV, raising=False)
    monkeypatch.setenv(YC_SA_KEY_FILE_ENV, "/tmp/key.json")

    assert not is_configured_in_env()
    assert load_from_env() is None


def test_registration_is_skipped_on_an_opensre_without_the_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same wheel has to run against a released OpenSRE.

    Simulated by making the hook import fail, which is exactly what happens
    there, and asserting the plugin reports it rather than raising.
    """
    real_import = builtins.__import__

    def _import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "integrations.registry" and "register_integration_spec" in (args[2] or ()):
            raise ImportError("no registration hooks in this OpenSRE")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)

    assert register_with_core() is False
