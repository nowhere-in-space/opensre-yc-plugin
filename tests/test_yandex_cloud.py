"""Yandex Cloud integration: config, auth, endpoint resolution, and the client.

The auth tests stub the network but keep the real JWT signing, because the
exchange is picky in ways that are easy to get wrong and impossible to notice
without a live account: Yandex accepts only PS256, refuses a JWT that claims
more than an hour, and expects the key id in the header rather than the body.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from integrations.probes import ProbeResult
from yc_plugin.yandex_cloud import classify
from yc_plugin.yandex_cloud.auth import (
    MintedToken,
    YandexCloudAuth,
    YandexCloudAuthError,
    mint_iam_token,
)
from yc_plugin.yandex_cloud.config_model import YandexCloudIntegrationConfig
from yc_plugin.yandex_cloud.endpoints import (
    STATIC_ENDPOINTS,
    known_endpoints,
    reset_endpoint_cache,
    resolve_endpoint,
)
from yc_plugin.yandex_cloud.rest_client import (
    MAX_LIST_ITEMS,
    MAX_STRING_LENGTH,
    YandexCloudClient,
    sanitize,
)

FOLDER = "b1gexamplefolder"


@pytest.fixture(autouse=True)
def _no_endpoint_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test on the checked-in snapshot instead of the live registry."""
    monkeypatch.setattr("yc_plugin.yandex_cloud.endpoints._fetch_endpoints", dict)
    reset_endpoint_cache()


def _authorized_key() -> dict[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return {
        "id": "aje-key-id",
        "service_account_id": "aje-sa-id",
        "private_key": pem,
    }


def _config(**overrides: Any) -> YandexCloudIntegrationConfig:
    payload: dict[str, Any] = {"folder_id": FOLDER, "iam_token": "t1.token"}
    payload.update(overrides)
    return YandexCloudIntegrationConfig.model_validate(payload)


class TestConfigModel:
    def test_folder_is_required(self) -> None:
        with pytest.raises(ValueError, match="folder_id"):
            YandexCloudIntegrationConfig.model_validate({"iam_token": "t1.token"})

    def test_at_least_one_credential_is_required(self) -> None:
        with pytest.raises(ValueError, match="credential"):
            YandexCloudIntegrationConfig.model_validate({"folder_id": FOLDER})

    @pytest.mark.parametrize(
        ("field", "expected_mode"),
        [
            ("sa_key_file", "sa_key_file"),
            ("sa_key", "sa_key"),
            ("oauth_token", "oauth"),
            ("iam_token", "iam_token"),
        ],
    )
    def test_each_credential_selects_its_mode(self, field: str, expected_mode: str) -> None:
        config = YandexCloudIntegrationConfig.model_validate({"folder_id": FOLDER, field: "value"})

        assert config.auth_mode == expected_mode
        assert config.is_configured is True

    def test_metadata_mode_needs_no_secret(self) -> None:
        config = YandexCloudIntegrationConfig.model_validate(
            {"folder_id": FOLDER, "use_metadata": "true"}
        )

        assert config.auth_mode == "metadata"

    def test_a_key_file_wins_over_a_short_lived_token(self) -> None:
        """Order matters: a renewable credential should outrank one that expires."""
        config = _config(sa_key_file="/keys/sa.json", iam_token="t1.token")

        assert config.auth_mode == "sa_key_file"

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unexpected field"):
            YandexCloudIntegrationConfig.model_validate(
                {"folder_id": FOLDER, "iam_token": "t1.x", "regoin": "ru-central1"}
            )


class TestClassify:
    def test_returns_typed_config(self) -> None:
        config, key = classify(
            {"folder_id": FOLDER, "cloud_id": "b1cloud", "iam_token": "t1.token"},
            record_id="rec-1",
        )

        assert key == "yandex_cloud"
        assert config is not None
        assert config.folder_id == FOLDER
        assert config.cloud_id == "b1cloud"
        assert config.integration_id == "rec-1"

    def test_skips_a_record_with_no_credential(self) -> None:
        config, key = classify({"folder_id": FOLDER}, record_id="rec-1")

        assert config is None
        assert key is None


class TestEndpoints:
    def test_snapshot_covers_the_services_the_family_needs(self) -> None:
        for service in (
            "iam",
            "compute",
            "monitoring",
            "logging",
            "log-reading",
            "audittrails",
            "managed-kubernetes",
            "mdb-postgresql",
            "resource-manager",
            "serverless-functions",
            "vpc",
            "alb",
            "load-balancer",
        ):
            assert service in STATIC_ENDPOINTS, service

    def test_log_reads_go_to_the_reader_host(self) -> None:
        """Reads and management live on different hosts; mixing them 404s."""
        assert resolve_endpoint("log-reading") == "reader.logging.yandexcloud.net"
        assert resolve_endpoint("logging") == "logging.api.cloud.yandex.net"

    def test_unknown_service_resolves_to_nothing(self) -> None:
        assert resolve_endpoint("not-a-service") is None

    def test_live_registry_wins_over_the_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.endpoints._fetch_endpoints",
            lambda: {"compute": "compute.api.yandexcloud.kz"},
        )
        reset_endpoint_cache()

        assert resolve_endpoint("compute") == "compute.api.yandexcloud.kz"
        # Services the region omits still resolve from the snapshot.
        assert resolve_endpoint("iam") == "iam.api.cloud.yandex.net"

    def test_env_overrides_win_over_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("YC_ENDPOINT_OVERRIDES", json.dumps({"compute": "compute.internal"}))

        assert resolve_endpoint("compute") == "compute.internal"

    def test_malformed_overrides_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("YC_ENDPOINT_OVERRIDES", "{not json")

        assert resolve_endpoint("compute") == "compute.api.cloud.yandex.net"

    def test_an_unreachable_registry_falls_back_to_the_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> dict[str, str]:
            raise httpx.ConnectError("no network")

        monkeypatch.setattr("yc_plugin.yandex_cloud.endpoints._fetch_endpoints", _boom)
        reset_endpoint_cache()

        with pytest.raises(httpx.ConnectError):
            known_endpoints()
        # The snapshot path is what production uses; _fetch_endpoints swallows
        # its own errors, so only a bug in the caller could surface one.
        assert resolve_endpoint("compute", refresh=False) == "compute.api.cloud.yandex.net"


class TestAuth:
    def test_service_account_key_is_exchanged_for_a_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key = _authorized_key()
        captured: dict[str, Any] = {}

        def _post(url: str, json: dict[str, str], timeout: float) -> httpx.Response:
            captured["url"] = url
            captured["jwt"] = json["jwt"]
            return httpx.Response(200, json={"iamToken": "t1.minted"})

        monkeypatch.setattr(httpx, "post", _post)
        config = _config(sa_key=json_dumps(key), iam_token="")

        assert mint_iam_token(config) == "t1.minted"
        assert captured["url"] == "https://iam.api.cloud.yandex.net/iam/v1/tokens"

        import jwt as pyjwt

        header = pyjwt.get_unverified_header(captured["jwt"])
        claims = pyjwt.decode(captured["jwt"], options={"verify_signature": False})
        assert header["alg"] == "PS256"
        assert header["kid"] == key["id"]
        assert claims["iss"] == key["service_account_id"]
        assert claims["aud"] == "https://iam.api.cloud.yandex.net/iam/v1/tokens"
        assert claims["exp"] - claims["iat"] <= 3600

    def test_oauth_token_is_exchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _post(url: str, json: dict[str, str], timeout: float) -> httpx.Response:
            captured.update(json)
            return httpx.Response(200, json={"iamToken": "t1.from-oauth"})

        monkeypatch.setattr(httpx, "post", _post)

        assert mint_iam_token(_config(oauth_token="y0_oauth", iam_token="")) == "t1.from-oauth"
        assert captured["yandexPassportOauthToken"] == "y0_oauth"

    def test_a_supplied_iam_token_is_used_as_is(self) -> None:
        assert mint_iam_token(_config(iam_token="t1.supplied")) == "t1.supplied"

    def test_metadata_mode_reads_the_instance_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _get(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
            assert "169.254.169.254" in url
            assert headers["Metadata-Flavor"] == "Google"
            return httpx.Response(
                200,
                json={"access_token": "t1.from-metadata"},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx, "get", _get)
        config = YandexCloudIntegrationConfig.model_validate(
            {"folder_id": FOLDER, "use_metadata": True}
        )

        assert mint_iam_token(config) == "t1.from-metadata"

    def test_a_malformed_key_says_what_to_do_about_it(self) -> None:
        with pytest.raises(YandexCloudAuthError, match="yc iam key create"):
            mint_iam_token(_config(sa_key="not json", iam_token=""))

    def test_an_api_key_pasted_as_a_service_account_key_is_named(self) -> None:
        with pytest.raises(YandexCloudAuthError, match="private_key"):
            mint_iam_token(_config(sa_key=json_dumps({"id": "x"}), iam_token=""))

    def test_a_rejected_exchange_surfaces_the_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx,
            "post",
            lambda *_a, **_k: httpx.Response(401, text="key not found"),
        )

        with pytest.raises(YandexCloudAuthError, match="key not found"):
            mint_iam_token(_config(oauth_token="stale", iam_token=""))

    def test_a_token_is_reused_until_it_ages_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def _mint(_config: YandexCloudIntegrationConfig) -> MintedToken:
            calls["n"] += 1
            return MintedToken(f"t1.token-{calls['n']}", 50 * 60)

        monkeypatch.setattr("yc_plugin.yandex_cloud.auth.mint_iam_token_with_ttl", _mint)
        auth = YandexCloudAuth(_config(oauth_token="y0", iam_token=""))

        assert auth.token() == "t1.token-1"
        assert auth.token() == "t1.token-1"
        assert calls["n"] == 1

        # Well inside the 12h validity, but past the renewal point.
        an_hour_on = time.monotonic() + 60 * 60
        monkeypatch.setattr(time, "monotonic", lambda: an_hour_on)
        assert auth.token() == "t1.token-2"

    def test_invalidate_forces_a_new_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def _mint(_config: YandexCloudIntegrationConfig) -> MintedToken:
            calls["n"] += 1
            return MintedToken(f"t1.token-{calls['n']}", 50 * 60)

        monkeypatch.setattr("yc_plugin.yandex_cloud.auth.mint_iam_token_with_ttl", _mint)
        auth = YandexCloudAuth(_config(oauth_token="y0", iam_token=""))

        auth.token()
        auth.invalidate()
        auth.token()

        assert calls["n"] == 2


class TestSanitize:
    def test_long_lists_are_truncated_with_a_note(self) -> None:
        result = sanitize(list(range(MAX_LIST_ITEMS + 25)))

        assert len(result) == MAX_LIST_ITEMS + 1
        assert "25 more items truncated" in result[-1]

    def test_long_strings_are_truncated_with_a_note(self) -> None:
        result = sanitize("x" * (MAX_STRING_LENGTH + 40))

        assert result.startswith("x" * 100)
        assert "40 more chars" in result

    def test_nesting_is_bounded(self) -> None:
        deep: Any = "leaf"
        for _ in range(20):
            deep = {"next": deep}

        assert "max depth reached" in json_dumps(sanitize(deep))

    def test_ordinary_values_pass_through(self) -> None:
        payload = {"id": "abc", "count": 3, "ready": True, "tags": ["a", "b"], "none": None}

        assert sanitize(payload) == payload


class TestClient:
    def _client(self, **overrides: Any) -> YandexCloudClient:
        return YandexCloudClient(_config(**overrides))

    def test_unknown_service_is_refused_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _never(*_a: Any, **_k: Any) -> None:
            raise AssertionError("no request should be made")

        monkeypatch.setattr(httpx, "request", _never)
        response = self._client().get("not-a-service", "/v1/things")

        assert response["success"] is False
        assert "Unknown Yandex Cloud service" in response["error"]
        assert response["metadata"]["unknown_service"] is True

    @pytest.mark.parametrize(
        "path",
        ["compute/v1/instances", "/compute/../../etc/passwd", "https://evil.example/v1"],
    )
    def test_a_suspicious_path_is_refused(self, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
        def _never(*_a: Any, **_k: Any) -> None:
            raise AssertionError("no request should be made")

        monkeypatch.setattr(httpx, "request", _never)
        response = self._client().get("compute", path)

        assert response["success"] is False
        assert "Invalid path" in response["error"]

    def test_a_successful_read_returns_the_standard_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured["method"] = method
            captured["url"] = url
            captured["params"] = kwargs["params"]
            captured["headers"] = kwargs["headers"]
            return httpx.Response(
                200,
                json={"instances": [{"id": "fhm1"}], "nextPageToken": "page-2"},
                headers={"x-request-id": "req-7"},
            )

        monkeypatch.setattr(httpx, "request", _request)
        response = self._client().get("compute", "/compute/v1/instances")

        assert captured["method"] == "GET"
        assert captured["url"] == "https://compute.api.cloud.yandex.net/compute/v1/instances"
        assert captured["params"]["pageSize"] == 100
        assert captured["headers"]["Authorization"] == "Bearer t1.token"
        assert response["success"] is True
        assert response["data"]["instances"] == [{"id": "fhm1"}]
        assert response["metadata"]["next_page_token"] == "page-2"
        assert response["metadata"]["request_id"] == "req-7"

    def test_a_page_token_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured.update(kwargs["params"])
            return httpx.Response(200, json={})

        monkeypatch.setattr(httpx, "request", _request)
        self._client().get("compute", "/compute/v1/instances", page_token="page-2")

        assert captured["pageToken"] == "page-2"

    def test_a_forbidden_read_explains_how_to_grant_access(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            httpx,
            "request",
            lambda *_a, **_k: httpx.Response(403, json={"message": "permission denied"}),
        )
        response = self._client().get("compute", "/compute/v1/instances")

        assert response["success"] is False
        assert "permission denied" in response["error"]
        assert "add-access-binding" in response["error"]
        assert FOLDER in response["error"]

    def test_rate_limiting_is_retried_then_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        attempts = {"n": 0}

        def _request(*_a: Any, **_k: Any) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(429, json={"message": "too many requests"})

        monkeypatch.setattr(httpx, "request", _request)
        monkeypatch.setattr("yc_plugin.yandex_cloud.rest_client.time.sleep", lambda _s: None)
        response = self._client().get("compute", "/compute/v1/instances")

        assert attempts["n"] == 3
        assert response["success"] is False
        assert response["metadata"]["status_code"] == 429

    def test_an_expired_token_is_reminted_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        attempts = {"n": 0}

        def _request(*_a: Any, **_k: Any) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(401, json={"message": "token expired"})
            return httpx.Response(200, json={"ok": True})

        monkeypatch.setattr(httpx, "request", _request)
        client = self._client(oauth_token="y0", iam_token="")
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.auth.mint_iam_token_with_ttl",
            lambda _c: MintedToken("t1.fresh", 50 * 60),
        )
        response = client.get("compute", "/compute/v1/instances")

        assert attempts["n"] == 2
        assert response["success"] is True

    def test_probe_reports_the_folder_it_reached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx,
            "request",
            lambda *_a, **_k: httpx.Response(200, json={"id": FOLDER, "name": "production"}),
        )
        probe = self._client().probe_access()

        assert isinstance(probe, ProbeResult)
        assert probe.ok is True
        assert "production" in probe.detail

    def test_probe_reports_a_failure_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            httpx,
            "request",
            lambda *_a, **_k: httpx.Response(403, json={"message": "denied"}),
        )
        probe = self._client().probe_access()

        assert probe.ok is False
        assert probe.status == "failed"


def json_dumps(value: Any) -> str:
    return json.dumps(value)
