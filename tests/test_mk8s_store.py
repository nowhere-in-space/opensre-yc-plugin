"""Getting the kubeconfig somewhere an investigation will actually look.

Setting ``KUBECONFIG_CONTENT`` is not enough. OpenSRE reads environment
integrations only when its store is empty
(``platform/harness_ports.py::_resolve_from_local_sources``), so on any machine
with a single configured integration the variable is ignored — and the
Kubernetes tools report themselves unavailable partway through an
investigation, after the agent has already decided to use them.

That is how it failed in practice: every run called the tools, every call came
back "not available", and the agent invented a cause from what was left. The
record therefore goes into the store, rewritten each startup because the token
inside it expires within hours.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from integrations import store
from yc_plugin.yc_mk8s import kubeconfig
from yc_plugin.yc_mk8s.kubeconfig import ClusterAccess

PEM = "-----BEGIN CERTIFICATE-----\nMIIBmock\n-----END CERTIFICATE-----\n"
INTERNAL = "https://10.128.0.7"


def _access(**overrides: Any) -> ClusterAccess:
    fields: dict[str, Any] = {
        "cluster_id": "cat1csmonpub0p9jvrv1",
        "name": "test-zone-shift",
        "internal_endpoint": INTERNAL,
        "external_endpoint": "https://158.160.187.208",
        "ca_certificate": PEM,
        "status": "RUNNING",
    }
    fields.update(overrides)
    return ClusterAccess(**fields)


def _settings(**overrides: Any) -> dict[str, Any]:
    return {"enabled": True, **_access(**overrides).as_config()}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch a real ~/.opensre/integrations.json."""
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "integrations.json")
    monkeypatch.setenv(kubeconfig.KUBECONFIG_CONTENT_ENV, "sentinel")
    monkeypatch.delenv(kubeconfig.KUBECONFIG_CONTENT_ENV)


def _kubernetes_records() -> list[dict[str, Any]]:
    return [r for r in store.load_integrations() if r.get("service") == "kubernetes"]


class TestTheRecordReachesTheStore:
    def test_configuring_writes_one(self) -> None:
        assert kubeconfig.configure(_settings(), "t1.token", on_instance=True) is True

        records = _kubernetes_records()
        assert len(records) == 1

    def test_it_carries_the_kubeconfig_the_tools_need(self) -> None:
        kubeconfig.configure(_settings(), "t1.token", on_instance=True)

        credentials = _kubernetes_records()[0]["instances"][0]["credentials"]
        parsed = yaml.safe_load(credentials["kubeconfig"])

        assert parsed["clusters"][0]["cluster"]["server"] == INTERNAL
        assert parsed["users"][0]["user"]["token"] == "t1.token"

    def test_it_is_marked_as_ours(self) -> None:
        """So a later run can tell its own record from one the operator wrote."""
        kubeconfig.configure(_settings(), "t1.token", on_instance=True)

        tags = _kubernetes_records()[0]["instances"][0]["tags"]

        assert tags["managed_by"] == kubeconfig.MANAGED_BY

    def test_the_environment_variable_is_still_set(self) -> None:
        """It covers the case the store path exists for: an empty store."""
        kubeconfig.configure(_settings(), "t1.token", on_instance=True)

        assert kubeconfig.KUBECONFIG_CONTENT_ENV in os.environ


class TestEveryStartupRefreshesIt:
    def test_a_second_run_replaces_the_token(self) -> None:
        """The token expires within hours; a stale record fails with 401."""
        kubeconfig.configure(_settings(), "t1.first", on_instance=True)
        kubeconfig.configure(_settings(), "t1.second", on_instance=True)

        records = _kubernetes_records()
        assert len(records) == 1
        parsed = yaml.safe_load(records[0]["instances"][0]["credentials"]["kubeconfig"])
        assert parsed["users"][0]["user"]["token"] == "t1.second"

    def test_changing_cluster_replaces_the_record(self) -> None:
        kubeconfig.configure(_settings(), "t1.token", on_instance=True)
        kubeconfig.configure(
            _settings(cluster_id="cat1other", name="other"), "t1.token", on_instance=True
        )

        parsed = yaml.safe_load(
            _kubernetes_records()[0]["instances"][0]["credentials"]["kubeconfig"]
        )
        assert parsed["current-context"] == "other"


class TestSomebodyElsesClusterIsLeftAlone:
    """Redirecting a kubeconfig the operator configured would be worse than nothing."""

    def _foreign_record(self) -> None:
        store.upsert_integration(
            "kubernetes",
            {
                "instances": [
                    {
                        "name": "default",
                        "tags": {},
                        "credentials": {"kubeconfig_path": "/home/ops/.kube/config"},
                    }
                ]
            },
        )

    def test_an_existing_record_is_not_overwritten(self) -> None:
        self._foreign_record()

        kubeconfig.configure(_settings(), "t1.token", on_instance=True)

        credentials = _kubernetes_records()[0]["instances"][0]["credentials"]
        assert credentials["kubeconfig_path"] == "/home/ops/.kube/config"
        assert "kubeconfig" not in credentials

    def test_the_environment_variable_is_still_published(self) -> None:
        """It is the only path left, and it works when the store is empty."""
        self._foreign_record()

        assert kubeconfig.configure(_settings(), "t1.token", on_instance=True) is True
        assert kubeconfig.KUBECONFIG_CONTENT_ENV in os.environ


class TestNothingIsWrittenWhenThereIsNothingToWrite:
    def test_a_cluster_that_was_never_connected(self) -> None:
        assert kubeconfig.configure({}, "t1.token", on_instance=True) is False
        assert _kubernetes_records() == []

    def test_an_unreachable_cluster(self) -> None:
        settings = _settings(external_endpoint="")

        assert kubeconfig.configure(settings, "t1.token", on_instance=False) is False
        assert _kubernetes_records() == []

    def test_a_credential_that_could_not_be_minted(self) -> None:
        assert kubeconfig.configure(_settings(), "", on_instance=True) is False
        assert _kubernetes_records() == []


class TestAStoreThatCannotBeWritten:
    def test_it_degrades_rather_than_stops(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The variable still covers an empty store, so this is not fatal."""

        def _explode(service: str, entry: dict[str, Any]) -> None:
            raise OSError("read-only file system")

        monkeypatch.setattr("integrations.store.upsert_integration", _explode)

        assert kubeconfig.configure(_settings(), "t1.token", on_instance=True) is True
        assert kubeconfig.KUBECONFIG_CONTENT_ENV in os.environ
        assert "could not write the store" in caplog.text


class TestWhatAnInvestigationThenSees:
    """The check that would have caught the original failure."""

    def test_the_resolver_an_investigation_uses_finds_the_cluster(self) -> None:
        from integrations.harness_adapters import register_harness_adapters

        register_harness_adapters()
        kubeconfig.configure(_settings(), "t1.token", on_instance=True)

        from platform.harness_ports import resolve_integrations

        resolved = resolve_integrations({})

        assert "kubernetes" in resolved, (
            "разрешение по пути расследования не видит кластер — "
            f"видит только {sorted(s for s in resolved if not s.startswith('_'))}"
        )

    def test_the_record_is_json_serialisable(self) -> None:
        """It goes to disk, so anything unserialisable fails at write time."""
        kubeconfig.configure(_settings(), "t1.token", on_instance=True)

        assert json.loads(json.dumps(_kubernetes_records()[0]))
