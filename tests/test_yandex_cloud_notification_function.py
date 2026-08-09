"""The bridge that gets a Yandex Monitoring alert to OpenSRE at all.

Monitoring cannot POST to an arbitrary URL — its channels are Email, SMS, push,
Telegram and Cloud Functions — so this function is the whole path from a firing
alert to an investigation. It runs in Yandex's runtime, not here, which is why
it uses only the standard library and is tested through its handler contract.
"""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from yc_plugin.yandex_cloud.notification_function import handler as bridge

FOLDER = "b1gapqc3kb2vii7cs9i3"
ALERT = {
    "alert_id": "aoe1abc",
    "alert_name": "PostgreSQL connections saturated",
    "status": "ALARM",
    "folder_id": FOLDER,
}


@pytest.fixture(autouse=True)
def _gateway_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_URL", "https://opensre.internal:8000/")
    monkeypatch.setenv("OPENSRE_TOKEN", "secret-token")


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture the request the handler builds instead of making it."""
    captured: dict[str, Any] = {}

    def _urlopen(request: Any, timeout: float | None = None) -> _Response:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response({"root_cause": "primary restarted", "validity_score": 0.8})

    monkeypatch.setattr(bridge.urllib.request, "urlopen", _urlopen)
    return captured


class TestWhatItSends:
    def test_the_alert_goes_through_whole(self, sent: dict[str, Any]) -> None:
        """The detector runs on raw_alert, so nothing may be dropped on the way."""
        bridge.handler(ALERT)

        assert sent["body"]["raw_alert"] == ALERT

    def test_it_targets_the_investigation_endpoint(self, sent: dict[str, Any]) -> None:
        """/alerts only queues for the shell to display; /investigate actually runs one."""
        bridge.handler(ALERT)

        assert sent["url"] == "https://opensre.internal:8000/investigate"

    def test_the_token_is_carried(self, sent: dict[str, Any]) -> None:
        bridge.handler(ALERT)

        assert sent["headers"]["Authorization"] == "Bearer secret-token"

    def test_name_and_severity_are_lifted_for_the_report_header(self, sent: dict[str, Any]) -> None:
        bridge.handler(ALERT)

        assert sent["body"]["alert_name"] == "PostgreSQL connections saturated"
        assert sent["body"]["severity"] == "ALARM"

    def test_it_waits_longer_than_an_investigation_takes(self, sent: dict[str, Any]) -> None:
        """A real run took 89s; a short timeout throws the report away."""
        bridge.handler(ALERT)

        assert sent["timeout"] >= 120


class TestPayloadShapes:
    def test_a_nested_name_is_found(self, sent: dict[str, Any]) -> None:
        """The channel template is written by the operator; fields may be nested."""
        bridge.handler({"folder_id": FOLDER, "alert": {"name": "disk full"}, "status": "ALARM"})

        assert sent["body"]["alert_name"] == "disk full"

    def test_an_api_gateway_envelope_is_unwrapped(self, sent: dict[str, Any]) -> None:
        """Same function invoked over HTTP, so it can be curl-tested before going live."""
        bridge.handler({"body": json.dumps(ALERT)})

        assert sent["body"]["raw_alert"] == ALERT

    def test_an_unparseable_body_still_reaches_the_agent(self, sent: dict[str, Any]) -> None:
        """A human-readable notification is worth investigating even without fields."""
        bridge.handler({"body": "disk full on rc1b-abc"})

        assert sent["body"]["raw_alert"] == {"text": "disk full on rc1b-abc"}


class TestFailuresAreLegible:
    def test_a_missing_url_says_which_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENSRE_URL", raising=False)

        result = bridge.handler(ALERT)

        assert result["statusCode"] == 500
        assert "OPENSRE_URL" in result["body"]

    def test_an_unreachable_gateway_is_reported_as_such(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _refuse(*_args: object, **_kwargs: object) -> None:
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(bridge.urllib.request, "urlopen", _refuse)

        result = bridge.handler(ALERT)

        assert result["statusCode"] == 502
        assert "could not reach OpenSRE" in result["body"]

    def test_a_gateway_error_keeps_its_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _unauthorized(*_args: object, **_kwargs: object) -> None:
            raise urllib.error.HTTPError(
                "url", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"unauthorized"}')
            )

        monkeypatch.setattr(bridge.urllib.request, "urlopen", _unauthorized)

        result = bridge.handler(ALERT)

        assert result["statusCode"] == 401


class TestWhatItReturns:
    @pytest.mark.usefixtures("sent")
    def test_the_diagnosis_comes_back_to_the_caller(self) -> None:
        result = bridge.handler(ALERT)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["root_cause"] == "primary restarted"
        assert body["validity_score"] == 0.8
