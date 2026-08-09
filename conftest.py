"""Put an OpenSRE checkout ahead of the standard library on ``sys.path``.

OpenSRE ships a top-level ``platform`` package that shadows the standard
library module of the same name. Importing OpenSRE therefore only works when
its checkout precedes the stdlib on ``sys.path`` — having it merely installed
is not enough, because the stdlib is searched before ``site-packages``.

Point ``OPENSRE_ROOT`` at the checkout, or keep it beside this repository.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent

# A checkout is recognised by the four top-level packages the tests import.
_OPENSRE_MARKERS = ("platform", "tools", "integrations", "core")


def _looks_like_opensre(candidate: Path) -> bool:
    return all((candidate / name).is_dir() for name in _OPENSRE_MARKERS)


def _find_opensre_root() -> Path | None:
    configured = os.environ.get("OPENSRE_ROOT")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        return candidate if _looks_like_opensre(candidate) else None
    for sibling in sorted(_PLUGIN_ROOT.parent.iterdir()):
        if sibling.is_dir() and _looks_like_opensre(sibling):
            return sibling
    return None


_OPENSRE_ROOT = _find_opensre_root()

# Prepend at import time: pytest collects test modules after conftest is
# loaded, so the path is already in place when they import OpenSRE.
for _path in (_PLUGIN_ROOT, _OPENSRE_ROOT):
    if _path is not None and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

if _OPENSRE_ROOT is not None:
    # pytest imports the stdlib ``platform`` during startup, before this file
    # runs, so the name is already bound and the path change above would not
    # be consulted again. Drop the binding to let OpenSRE's package win. Its
    # ``__init__`` re-exports the stdlib API, so callers holding the old module
    # keep working either way.
    _bound_platform = sys.modules.get("platform")
    if _bound_platform is not None and not hasattr(_bound_platform, "__path__"):
        del sys.modules["platform"]
        import platform  # noqa: F401  (re-imported from the OpenSRE checkout)


# Keep the suite away from a real ``~/.opensre-yc/config.json``: the plugin
# resolves this directory at import time, so it has to be set before anything
# imports ``yc_plugin.config``. An empty directory also keeps ``install()``
# from configuring a language model, which would otherwise reach the metadata
# service during collection.
_CONFIG_SANDBOX = tempfile.mkdtemp(prefix="opensre-yc-tests-")
os.environ["OPENSRE_YC_CONFIG_DIR"] = _CONFIG_SANDBOX


def pytest_configure(config: pytest.Config) -> None:
    """Stop with an actionable message when no OpenSRE checkout was found."""
    if _OPENSRE_ROOT is None:
        raise pytest.UsageError(
            "No OpenSRE checkout found. Set OPENSRE_ROOT to one, or clone "
            "Tracer-Cloud/opensre beside this repository."
        )


@pytest.fixture(autouse=True)
def _cold_client_cache() -> None:
    """Start every test with no cached client, hence a cold IAM token cache."""
    from yc_plugin.yandex_cloud.availability import reset_client_cache

    reset_client_cache()


@pytest.fixture(autouse=True)
def _off_an_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer "not running in Yandex Cloud" without asking the network.

    The metadata service lives on a link-local address that does not answer
    anywhere else, so the honest answer costs a full timeout to obtain. A test
    that wants the other answer overrides this.
    """
    from yc_plugin import metadata

    metadata.forget_availability()
    monkeypatch.setattr(metadata, "is_available", lambda: False)


@pytest.fixture(scope="session", autouse=True)
def _installed_plugin() -> None:
    """Register the plugin once, the way a host application would at startup.

    In-tree integrations are picked up by OpenSRE's tool discovery. A plugin is
    not: nothing is registered until ``install()`` runs, so the registry has to
    be primed before any test asks it for a Yandex Cloud tool.
    """
    import yc_plugin
    from tools.registry import clear_tool_registry_cache

    yc_plugin.install()
    clear_tool_registry_cache()
