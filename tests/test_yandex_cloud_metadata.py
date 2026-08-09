"""Running inside Yandex Cloud, on a VM with a service account attached.

This is the deployment where nothing has to be stored: the instance metadata
service issues IAM tokens for the attached account and also knows which folder
and cloud the instance lives in. So the whole configuration is saying that this
is where the agent runs, and everything else is discovered.

The tests below pin that down — that no folder is required, that the token's
stated expiry is honoured rather than a fixed guess, and that off an instance
the failure explains itself instead of hanging or reporting something vague.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from integrations.catalog import load_env_integration_services
from yc_plugin import config as plugin_config
from yc_plugin import metadata
from yc_plugin.yandex_cloud.auth import (
    YandexCloudAuth,
    YandexCloudAuthError,
    mint_iam_token,
    mint_iam_token_with_ttl,
)
from yc_plugin.yandex_cloud.config_model import YandexCloudIntegrationConfig
from yc_plugin.yandex_cloud.rest_client import YandexCloudClient

FOLDER = "b1ginstancefolder"
CLOUD = "b1cinstancecloud"

_YC_ENV_VARS = (
    "YC_FOLDER_ID",
    "YC_CLOUD_ID",
    "YC_SA_KEY_FILE",
    "YC_SA_KEY",
    "YC_TOKEN",
    "YC_IAM_TOKEN",
    "YC_USE_METADATA",
    "YC_INSTANCES",
)


#: Captured while importing, before the shared fixture stubs it out. This module
#: is the one place that tests availability detection itself, so it needs the
#: real implementation rather than the "not in the cloud" answer used elsewhere.
_REAL_IS_AVAILABLE = metadata.is_available


@pytest.fixture(autouse=True)
def _real_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "is_available", _REAL_IS_AVAILABLE)
    _REAL_IS_AVAILABLE.cache_clear()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _YC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("yc_plugin.yandex_cloud.endpoints._fetch_endpoints", dict)


def _on_an_instance(monkeypatch: pytest.MonkeyPatch, *, expires_in: int = 42617) -> None:
    """Make the metadata service answer the way a real instance does."""

    def _get(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        assert headers["Metadata-Flavor"] == "Google"
        request = httpx.Request("GET", url)
        if url.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "t1.from-metadata",
                    "expires_in": expires_in,
                    "token_type": "Bearer",
                },
                request=request,
            )
        if url.endswith("/folder-id"):
            return httpx.Response(200, text=FOLDER, request=request)
        if url.endswith("/cloud-id"):
            return httpx.Response(200, text=CLOUD, request=request)
        if url.endswith("/instance/id"):
            return httpx.Response(200, text="fhm1instance", request=request)
        return httpx.Response(404, text="", request=request)

    monkeypatch.setattr(httpx, "get", _get)


def _off_an_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the link-local address behave as it does anywhere else: unreachable."""

    def _get(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", _get)


class TestMetadataService:
    def test_it_reports_the_instance_folder_and_cloud(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _on_an_instance(monkeypatch)

        assert metadata.fetch_folder_id() == FOLDER
        assert metadata.fetch_cloud_id() == CLOUD
        assert metadata.fetch_instance_id() == "fhm1instance"
        assert metadata.is_available() is True

    def test_the_token_carries_its_own_expiry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _on_an_instance(monkeypatch, expires_in=3600)
        minted = metadata.fetch_token()

        assert minted is not None
        assert minted.token == "t1.from-metadata"
        # Renewed early, so a long investigation never carries a stale token.
        assert minted.ttl_seconds == pytest.approx(3600 - 300)

    def test_it_stays_quiet_off_an_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Everywhere else the address is unroutable; asking must not raise."""
        _off_an_instance(monkeypatch)

        assert metadata.fetch_token() is None
        assert metadata.fetch_folder_id() is None
        assert metadata.is_available() is False


class TestConfiguration:
    def test_metadata_mode_needs_no_folder(self) -> None:
        """The instance knows which folder it is in, so asking would be busywork."""
        config = YandexCloudIntegrationConfig.model_validate({"use_metadata": True})

        assert config.auth_mode == "metadata"
        assert config.is_configured is True
        assert config.folder_id_is_discoverable is True

    def test_every_other_mode_still_needs_a_folder(self) -> None:
        with pytest.raises(ValueError, match="folder_id"):
            YandexCloudIntegrationConfig.model_validate({"iam_token": "t1.token"})

    def test_the_folder_error_names_the_instance_route(self) -> None:
        with pytest.raises(ValueError, match="use_metadata"):
            YandexCloudIntegrationConfig.model_validate({"oauth_token": "y0_x"})

    def test_an_explicit_folder_still_wins(self) -> None:
        config = YandexCloudIntegrationConfig.model_validate(
            {"use_metadata": True, "folder_id": "b1goverride"}
        )

        assert config.folder_id == "b1goverride"


class TestClientOnAnInstance:
    def _client(self) -> YandexCloudClient:
        return YandexCloudClient(
            YandexCloudIntegrationConfig.model_validate({"use_metadata": True})
        )

    def test_the_folder_is_discovered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _on_an_instance(monkeypatch)

        assert self._client().folder_id == FOLDER

    def test_the_cloud_is_discovered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _on_an_instance(monkeypatch)

        assert self._client().cloud_id == CLOUD

    def test_the_folder_is_only_looked_up_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def _fetch_folder_id() -> str:
            calls["n"] += 1
            return FOLDER

        monkeypatch.setattr(metadata, "fetch_folder_id", _fetch_folder_id)
        client = self._client()

        assert client.folder_id == FOLDER
        assert client.folder_id == FOLDER
        assert calls["n"] == 1

    def test_a_configured_folder_skips_the_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _never() -> str:
            raise AssertionError("metadata should not be consulted")

        monkeypatch.setattr(metadata, "fetch_folder_id", _never)
        client = YandexCloudClient(
            YandexCloudIntegrationConfig.model_validate(
                {"use_metadata": True, "folder_id": "b1gconfigured"}
            )
        )

        assert client.folder_id == "b1gconfigured"

    def test_reads_are_scoped_to_the_discovered_folder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _on_an_instance(monkeypatch)
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured["headers"] = kwargs["headers"]
            return httpx.Response(200, json={"id": FOLDER, "name": "production"})

        monkeypatch.setattr("yc_plugin.yandex_cloud.rest_client.send_request", _request)
        probe = self._client().probe_access()

        assert probe.ok is True
        assert "production" in probe.detail
        assert captured["headers"]["Authorization"] == "Bearer t1.from-metadata"


class TestFailingOffAnInstance:
    def test_minting_says_where_this_mode_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _off_an_instance(monkeypatch)
        config = YandexCloudIntegrationConfig.model_validate({"use_metadata": True})

        with pytest.raises(YandexCloudAuthError, match="service account attached"):
            mint_iam_token(config)

    def test_the_probe_explains_the_missing_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Off an instance the token fails first, so this is about the folder path."""
        monkeypatch.setattr(
            metadata,
            "fetch_token",
            lambda: metadata.MetadataToken(token="t1.x", ttl_seconds=600),
        )
        monkeypatch.setattr(metadata, "fetch_folder_id", lambda: None)
        config = YandexCloudIntegrationConfig.model_validate({"use_metadata": True})

        probe = YandexCloudClient(config).probe_access()

        assert probe.ok is False
        assert "YC_FOLDER_ID" in probe.detail


class TestTokenLifetime:
    def test_the_stated_expiry_drives_renewal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fixed renewal period would be wrong for a token that says its own."""
        _on_an_instance(monkeypatch, expires_in=900)
        config = YandexCloudIntegrationConfig.model_validate({"use_metadata": True})

        assert mint_iam_token_with_ttl(config).ttl_seconds == pytest.approx(600)

    def test_a_token_is_reused_until_its_expiry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def _mint(_config: YandexCloudIntegrationConfig) -> Any:
            from yc_plugin.yandex_cloud.auth import MintedToken

            calls["n"] += 1
            return MintedToken(f"t1.token-{calls['n']}", 600.0)

        monkeypatch.setattr("yc_plugin.yandex_cloud.auth.mint_iam_token_with_ttl", _mint)
        auth = YandexCloudAuth(YandexCloudIntegrationConfig.model_validate({"use_metadata": True}))

        assert auth.token() == "t1.token-1"
        assert auth.token() == "t1.token-1"
        assert calls["n"] == 1

        past_expiry = time.monotonic() + 601
        monkeypatch.setattr(time, "monotonic", lambda: past_expiry)
        assert auth.token() == "t1.token-2"


class TestEnvironmentConfiguration:
    """Asserted at the plugin's own boundary rather than OpenSRE's catalog.

    In-tree integrations are picked up by ``load_env_integrations()``, but a
    plugin has no way to register an environment loader there, so the catalog
    never lists ``yandex_cloud`` however the environment is set. What the tools
    actually consume is ``config.resolved_credentials()``, which is what these
    assertions cover.
    """

    def test_one_variable_configures_an_instance_deployment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On a VM this is the whole configuration."""
        monkeypatch.setenv("YC_USE_METADATA", "true")

        credentials = plugin_config.resolved_credentials()

        assert credentials["use_metadata"] is True
        assert credentials["folder_id"] == ""

    def test_the_catalog_still_does_not_list_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pins the known gap, so a core seam landing shows up here as a failure."""
        monkeypatch.setenv("YC_USE_METADATA", "true")

        assert "yandex_cloud" not in load_env_integration_services()

    def test_nothing_configured_stays_unconfigured(self) -> None:
        credentials = plugin_config.resolved_credentials()

        assert credentials["use_metadata"] is False
        assert credentials["folder_id"] == ""

    def test_the_environment_overrides_the_saved_file_field_by_field(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """The README promises this: one value replaced without rewriting the file."""
        config_dir = tmp_path / "cfg"
        monkeypatch.setattr(plugin_config, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(plugin_config, "CONFIG_PATH", config_dir / "config.json")
        plugin_config.save({"auth": "iam", "folder_id": "b1gsaved", "iam_token": "t1.saved"})
        monkeypatch.setenv("YC_FOLDER_ID", "b1gfromenv")

        credentials = plugin_config.resolved_credentials()

        assert credentials["folder_id"] == "b1gfromenv"
        assert credentials["iam_token"] == "t1.saved"
