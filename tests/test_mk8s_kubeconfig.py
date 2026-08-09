"""Reaching workloads in a Managed Kubernetes cluster.

Nothing here reads a pod. OpenSRE's own ``kubernetes`` integration does that,
with twelve tools it already ships, and all it wants is a kubeconfig. So the
plugin's job is to assemble one from what Managed Kubernetes publishes and hand
it over — which makes the interesting failures configuration failures, not API
ones: a cluster with no route from where the agent runs, a CA in the wrong
encoding, a kubeconfig the consumer cannot parse.
"""

from __future__ import annotations

import json
import os
from base64 import b64decode, b64encode
from typing import Any

import pytest
import yaml

from yc_plugin.yc_mk8s import kubeconfig
from yc_plugin.yc_mk8s.kubeconfig import ClusterAccess

PEM = "-----BEGIN CERTIFICATE-----\nMIIBmock\n-----END CERTIFICATE-----\n"
INTERNAL = "https://10.128.0.7"
EXTERNAL = "https://158.160.187.208"


def _access(**overrides: Any) -> ClusterAccess:
    fields: dict[str, Any] = {
        "cluster_id": "cat1csmonpub0p9jvrv1",
        "name": "test-zone-shift",
        "internal_endpoint": INTERNAL,
        "external_endpoint": EXTERNAL,
        "ca_certificate": PEM,
        "status": "RUNNING",
    }
    fields.update(overrides)
    return ClusterAccess(**fields)


class TestReadingTheClusterOutOfTheApi:
    def test_the_endpoints_and_ca_are_picked_up(self) -> None:
        access = kubeconfig.from_payload(
            {
                "id": "cat1abc",
                "name": "prod",
                "status": "RUNNING",
                "master": {
                    "endpoints": {
                        "internalV4Endpoint": INTERNAL,
                        "externalV4Endpoint": EXTERNAL,
                    },
                    "masterAuth": {"clusterCaCertificate": PEM},
                },
            }
        )

        assert access.cluster_id == "cat1abc"
        assert access.internal_endpoint == INTERNAL
        assert access.external_endpoint == EXTERNAL
        assert access.ca_certificate == PEM

    def test_a_cluster_still_starting_up_reports_no_route(self) -> None:
        """A master with no endpoints yet must not look like a usable cluster."""
        access = kubeconfig.from_payload({"id": "cat1abc", "master": {}})

        assert access.is_reachable is False

    def test_what_gets_saved_leaves_the_token_out(self) -> None:
        """An IAM token expires in hours; saving one writes a config that rots."""
        saved = _access().as_config()

        assert "token" not in json.dumps(saved)
        assert saved["cluster_id"] == "cat1csmonpub0p9jvrv1"
        assert saved["ca_certificate"] == PEM

    def test_a_saved_cluster_comes_back_unchanged(self) -> None:
        access = _access()

        assert kubeconfig.from_config(access.as_config()).as_config() == access.as_config()


class TestWhichAddressToDial:
    def test_inside_the_cloud_the_internal_address_wins(self) -> None:
        """Keeps API-server traffic off the public internet."""
        assert kubeconfig.choose_endpoint(_access(), on_instance=True) == INTERNAL

    def test_outside_the_cloud_only_the_public_one_is_routable(self) -> None:
        assert kubeconfig.choose_endpoint(_access(), on_instance=False) == EXTERNAL

    def test_inside_the_cloud_a_public_only_cluster_still_works(self) -> None:
        access = _access(internal_endpoint="")

        assert kubeconfig.choose_endpoint(access, on_instance=True) == EXTERNAL

    def test_outside_the_cloud_a_private_cluster_is_out_of_reach(self) -> None:
        """Common in production, and worth saying rather than timing out on."""
        access = _access(external_endpoint="")

        assert kubeconfig.choose_endpoint(access, on_instance=False) == ""

    def test_the_reason_says_what_to_change(self) -> None:
        access = _access(external_endpoint="")

        reason = kubeconfig.unreachable_reason(access, on_instance=False)

        assert "test-zone-shift" in reason
        assert "same network" in reason or "public endpoint" in reason

    def test_a_cluster_without_any_endpoint_is_described_differently(self) -> None:
        access = _access(internal_endpoint="", external_endpoint="")

        assert "no API-server endpoint" in kubeconfig.unreachable_reason(
            access, on_instance=True
        )


class TestTheKubeconfigItBuilds:
    def test_the_consumer_can_parse_it(self) -> None:
        """OpenSRE loads it with yaml.safe_load, and JSON is valid YAML."""
        parsed = yaml.safe_load(kubeconfig.build(_access(), INTERNAL, "t1.token"))

        assert parsed["apiVersion"] == "v1"
        assert parsed["kind"] == "Config"

    def test_it_points_at_the_endpoint_it_was_given(self) -> None:
        parsed = yaml.safe_load(kubeconfig.build(_access(), EXTERNAL, "t1.token"))

        assert parsed["clusters"][0]["cluster"]["server"] == EXTERNAL

    def test_the_token_is_the_bearer_credential(self) -> None:
        """Managed Kubernetes accepts an IAM token directly — no exec plugin."""
        parsed = yaml.safe_load(kubeconfig.build(_access(), INTERNAL, "t1.token"))

        assert parsed["users"][0]["user"]["token"] == "t1.token"
        assert "exec" not in parsed["users"][0]["user"]

    def test_a_pem_certificate_is_encoded_for_the_field_that_wants_base64(self) -> None:
        parsed = yaml.safe_load(kubeconfig.build(_access(), INTERNAL, "t1.token"))
        data = parsed["clusters"][0]["cluster"]["certificate-authority-data"]

        assert b64decode(data).decode() == PEM

    def test_an_already_encoded_certificate_is_not_encoded_twice(self) -> None:
        encoded = b64encode(PEM.encode()).decode()
        access = _access(ca_certificate=encoded)

        parsed = yaml.safe_load(kubeconfig.build(access, INTERNAL, "t1.token"))

        assert parsed["clusters"][0]["cluster"]["certificate-authority-data"] == encoded

    def test_the_context_is_selected_so_no_kubectl_switch_is_needed(self) -> None:
        parsed = yaml.safe_load(kubeconfig.build(_access(), INTERNAL, "t1.token"))

        assert parsed["current-context"] == "test-zone-shift"
        assert parsed["contexts"][0]["context"]["cluster"] == "test-zone-shift"

    def test_a_nameless_cluster_still_produces_a_valid_context(self) -> None:
        parsed = yaml.safe_load(kubeconfig.build(_access(name=""), INTERNAL, "t1.token"))

        assert parsed["current-context"] == "cat1csmonpub0p9jvrv1"


class _Client:
    """A REST client stub that answers the cluster listing."""

    folder_id = "b1gfolder"

    def __init__(self, clusters: list[dict[str, Any]]) -> None:
        self._clusters = clusters
        self.asked_for: dict[str, Any] = {}

    def get(self, service: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.asked_for = {"service": service, "path": path, "params": params}
        return {"data": {"clusters": self._clusters}}


class TestListingClusters:
    def test_it_reads_the_folder_the_client_is_scoped_to(self) -> None:
        client = _Client([])

        kubeconfig.list_clusters(client)

        assert client.asked_for["service"] == "managed-kubernetes"
        assert client.asked_for["params"] == {"folderId": "b1gfolder"}

    def test_an_explicit_folder_overrides_the_clients_own(self) -> None:
        client = _Client([])

        kubeconfig.list_clusters(client, folder_id="b1gother")

        assert client.asked_for["params"] == {"folderId": "b1gother"}

    def test_each_cluster_comes_back_ready_to_use(self) -> None:
        client = _Client(
            [
                {
                    "id": "cat1a",
                    "name": "prod",
                    "master": {
                        "endpoints": {"externalV4Endpoint": EXTERNAL},
                        "masterAuth": {"clusterCaCertificate": PEM},
                    },
                }
            ]
        )

        clusters = kubeconfig.list_clusters(client)

        assert [access.name for access in clusters] == ["prod"]
        assert clusters[0].external_endpoint == EXTERNAL

    def test_a_folder_with_nothing_in_it_is_not_an_error(self) -> None:
        assert kubeconfig.list_clusters(_Client([])) == []


class _Auth:
    def __init__(self, token: str = "", fails: bool = False) -> None:
        self._token = token
        self._fails = fails

    def token(self) -> str:
        if self._fails:
            raise RuntimeError("the service account key was rejected")
        return self._token


class _AuthedClient:
    def __init__(self, auth: _Auth) -> None:
        self.auth = auth


class TestTheInstallTimeWiring:
    """``install()`` publishes the kubeconfig; these cover that path."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(kubeconfig.KUBECONFIG_CONTENT_ENV, "sentinel")
        monkeypatch.delenv(kubeconfig.KUBECONFIG_CONTENT_ENV)

    def test_nothing_is_published_when_no_cluster_was_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yc_plugin
        from yc_plugin import config

        def _none() -> dict[str, Any]:
            return {}

        monkeypatch.setattr(config, "kubernetes_settings", _none)
        yc_plugin._configure_kubernetes()

        assert kubeconfig.KUBECONFIG_CONTENT_ENV not in os.environ

    def test_a_configured_cluster_is_published_with_a_fresh_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The token is minted at startup, never read from the config file."""
        import yc_plugin
        from yc_plugin import config

        def _settings() -> dict[str, Any]:
            return {"enabled": True, **_access().as_config()}

        def _client(_params: Any) -> Any:
            return _AuthedClient(_Auth("t1.fresh"))

        monkeypatch.setattr(config, "kubernetes_settings", _settings)
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.availability.client_from_params", _client
        )
        yc_plugin._configure_kubernetes()

        parsed = yaml.safe_load(os.environ[kubeconfig.KUBECONFIG_CONTENT_ENV])
        assert parsed["users"][0]["user"]["token"] == "t1.fresh"
        # Off an instance, so the public address is the one that can be dialled.
        assert parsed["clusters"][0]["cluster"]["server"] == EXTERNAL

    def test_a_credential_that_cannot_be_exchanged_publishes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import yc_plugin
        from yc_plugin import config

        def _settings() -> dict[str, Any]:
            return {"enabled": True, **_access().as_config()}

        def _client(_params: Any) -> Any:
            return _AuthedClient(_Auth(fails=True))

        monkeypatch.setattr(config, "kubernetes_settings", _settings)
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.availability.client_from_params", _client
        )
        yc_plugin._configure_kubernetes()

        assert kubeconfig.KUBECONFIG_CONTENT_ENV not in os.environ
        assert "could not mint an IAM token" in caplog.text

    def test_unusable_credentials_publish_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yc_plugin
        from yc_plugin import config

        def _settings() -> dict[str, Any]:
            return {"enabled": True, **_access().as_config()}

        def _no_client(_params: Any) -> None:
            return None

        monkeypatch.setattr(config, "kubernetes_settings", _settings)
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.availability.client_from_params", _no_client
        )
        yc_plugin._configure_kubernetes()

        assert kubeconfig.KUBECONFIG_CONTENT_ENV not in os.environ


class TestPublishingIt:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(kubeconfig.KUBECONFIG_CONTENT_ENV, "sentinel")
        monkeypatch.delenv(kubeconfig.KUBECONFIG_CONTENT_ENV)

    def test_it_sets_the_variable_opensre_reads(self) -> None:
        settings = {"enabled": True, **_access().as_config()}

        assert kubeconfig.configure(settings, "t1.token", on_instance=True) is True

        parsed = yaml.safe_load(os.environ[kubeconfig.KUBECONFIG_CONTENT_ENV])
        assert parsed["clusters"][0]["cluster"]["server"] == INTERNAL

    def test_declining_it_during_setup_leaves_the_environment_alone(self) -> None:
        assert kubeconfig.configure({}, "t1.token", on_instance=True) is False
        assert kubeconfig.KUBECONFIG_CONTENT_ENV not in os.environ

    def test_an_unreachable_cluster_configures_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Setting it anyway would make OpenSRE report kubernetes as ready, then fail."""
        settings = {"enabled": True, **_access(external_endpoint="").as_config()}

        assert kubeconfig.configure(settings, "t1.token", on_instance=False) is False
        assert kubeconfig.KUBECONFIG_CONTENT_ENV not in os.environ
        assert "same network" in caplog.text

    def test_a_missing_certificate_configures_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        settings = {"enabled": True, **_access(ca_certificate="").as_config()}

        assert kubeconfig.configure(settings, "t1.token", on_instance=True) is False
        assert "no cluster CA" in caplog.text

    def test_a_credential_that_could_not_be_minted_configures_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        settings = {"enabled": True, **_access().as_config()}

        assert kubeconfig.configure(settings, "", on_instance=True) is False
        assert kubeconfig.KUBECONFIG_CONTENT_ENV not in os.environ
        assert "no IAM token" in caplog.text
