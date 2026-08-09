"""Metrics, logs, and audit events from Yandex Cloud.

These three carry the awkward parts of Yandex's observability APIs: the folder
that has to travel in the query string rather than the body, the log reader
that lives on its own host and speaks only gRPC, and audit events that have no
read API at all and must be fetched back out of wherever a trail delivers them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from yc_plugin.yc_audit.client import deduplicate, describe_trail, trail_destination
from yc_plugin.yc_audit.tools import read_yc_audit_events
from yc_plugin.yc_logging.client import (
    MAX_FILTER_LENGTH,
    LogReadingUnavailableError,
    retention_warning,
    validate_filter,
)
from yc_plugin.yc_logging.client import resolve_window as resolve_log_window
from yc_plugin.yc_logging.tools import list_yc_log_groups, read_yc_logs
from yc_plugin.yc_monitoring.client import resolve_window, summarize_series
from yc_plugin.yc_monitoring.tools import list_yc_metrics, query_yc_metrics
from tools.registry import get_registered_tool_map

FOLDER = "b1gexamplefolder"
_CREDENTIALS: dict[str, Any] = {"folder_id": FOLDER, "iam_token": "t1.token"}


@pytest.fixture(autouse=True)
def _no_endpoint_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yc_plugin.yandex_cloud.endpoints._fetch_endpoints", dict)
    from yc_plugin.yandex_cloud.endpoints import reset_endpoint_cache

    reset_endpoint_cache()


class TestRegistration:
    @pytest.mark.parametrize(
        "name",
        [
            "query_yc_metrics",
            "list_yc_metrics",
            "read_yc_logs",
            "list_yc_log_groups",
            "read_yc_audit_events",
        ],
    )
    def test_the_tool_is_discoverable(self, name: str) -> None:
        assert name in get_registered_tool_map("investigation")

    @pytest.mark.parametrize("name", ["query_yc_metrics", "read_yc_logs", "read_yc_audit_events"])
    def test_credentials_are_hidden_from_the_model(self, name: str) -> None:
        tool = get_registered_tool_map("investigation")[name]
        properties = set(tool.public_input_schema.get("properties", {}))

        for secret in ("sa_key", "oauth_token", "iam_token", "folder_id"):
            assert secret not in properties


class TestMetricWindows:
    def test_a_window_defaults_to_the_recent_past(self) -> None:
        start, end = resolve_window("", "", 30)

        assert start < end
        assert (
            datetime.fromisoformat(end.replace("Z", "+00:00"))
            - datetime.fromisoformat(start.replace("Z", "+00:00"))
        ) == timedelta(minutes=30)

    def test_an_explicit_window_is_kept(self) -> None:
        start, end = resolve_window("2026-07-01T00:00:00Z", "2026-07-01T01:00:00Z", 30)

        assert start == "2026-07-01T00:00:00Z"
        assert end == "2026-07-01T01:00:00Z"


class TestSeriesSummary:
    def test_a_series_is_reduced_to_its_shape(self) -> None:
        summary = summarize_series(
            {
                "name": "cpu_usage",
                "labels": {"host": "vm-1"},
                "type": "DGAUGE",
                "timeseries": {"doubleValues": [10.0, 90.0, 50.0]},
            }
        )

        assert summary["points"] == 3
        assert summary["min"] == 10.0
        assert summary["max"] == 90.0
        assert summary["avg"] == 50.0
        assert summary["first"] == 10.0
        assert summary["last"] == 50.0

    def test_an_empty_series_reports_no_points(self) -> None:
        summary = summarize_series({"name": "cpu_usage", "timeseries": {}})

        assert summary["points"] == 0
        assert "max" not in summary


class TestMetricReads:
    def test_the_folder_travels_in_the_query_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In the body it is silently ignored, which reads as 'no data'."""
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured["method"] = method
            captured["url"] = url
            captured["params"] = kwargs["params"]
            captured["body"] = kwargs["json"]
            return httpx.Response(200, json={"metrics": []})

        monkeypatch.setattr(httpx, "request", _request)
        query_yc_metrics(query='cpu_usage{service="compute"}', **_CREDENTIALS)

        assert captured["method"] == "POST"
        assert captured["url"].endswith("/monitoring/v2/data/read")
        assert captured["params"]["folderId"] == FOLDER
        assert "folderId" not in captured["body"]

    def test_series_are_summarized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx,
            "request",
            lambda *_a, **_k: httpx.Response(
                200,
                json={
                    "metrics": [
                        {
                            "name": "cpu_usage",
                            "labels": {"host": "vm-1"},
                            "timeseries": {"doubleValues": [5.0, 95.0]},
                        }
                    ]
                },
            ),
        )
        result = query_yc_metrics(query="cpu_usage", **_CREDENTIALS)

        assert result["available"] is True
        assert result["series_count"] == 1
        assert result["series"][0]["max"] == 95.0

    def test_an_unknown_aggregation_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _never(*_a: Any, **_k: Any) -> None:
            raise AssertionError("no request should be made")

        monkeypatch.setattr(httpx, "request", _never)
        result = query_yc_metrics(query="cpu_usage", aggregation="MEDIAN", **_CREDENTIALS)

        assert result["available"] is False
        assert "MEDIAN" in result["error"]

    def test_discovery_returns_names_and_labels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if url.endswith("/names"):
                return httpx.Response(200, json={"names": ["cpu_usage", "memory_usage"]})
            return httpx.Response(200, json={"keys": ["host", "service"]})

        monkeypatch.setattr(httpx, "request", _request)
        result = list_yc_metrics(**_CREDENTIALS)

        assert result["names"] == ["cpu_usage", "memory_usage"]
        assert result["labels"] == ["host", "service"]


class TestLogFilters:
    def test_an_over_long_filter_is_refused_with_advice(self) -> None:
        rejected = validate_filter("x" * (MAX_FILTER_LENGTH + 1))

        assert rejected is not None
        assert str(MAX_FILTER_LENGTH) in rejected

    def test_a_normal_filter_passes(self) -> None:
        assert validate_filter('level >= WARN AND message: "timeout"') is None

    def test_a_window_beyond_retention_is_flagged(self) -> None:
        note = retention_warning(datetime.now(UTC) - timedelta(days=40))

        assert "retention" in note

    def test_a_recent_window_is_not_flagged(self) -> None:
        assert retention_warning(datetime.now(UTC) - timedelta(hours=2)) == ""

    def test_a_log_window_defaults_to_the_recent_past(self) -> None:
        start, end = resolve_log_window("", "", 15)

        assert end - start == timedelta(minutes=15)


class TestLogReads:
    def test_a_missing_log_group_asks_for_one(self) -> None:
        result = read_yc_logs(log_group_id="", **_CREDENTIALS)

        assert result["available"] is False
        assert "list_yc_log_groups" in result["error"]

    def test_the_missing_grpc_dependency_explains_the_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Everything else in the integration works without it, so say so."""

        def _raise(*_a: Any, **_k: Any) -> None:
            raise LogReadingUnavailableError(
                "Reading Cloud Logging entries needs the Yandex Cloud gRPC stubs. "
                "Install with: pip install 'opensre[yandex_cloud]'"
            )

        monkeypatch.setattr(
            "yc_plugin.yc_logging.client.YandexLoggingClient.read_entries", _raise
        )
        result = read_yc_logs(log_group_id="e23abc", **_CREDENTIALS)

        assert result["available"] is False
        assert "yandex_cloud" in result["error"]

    def test_entries_come_back_with_the_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _read(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {
                "success": True,
                "error": None,
                "entries": [{"uid": "1", "level": "ERROR", "message": "boom"}],
                "next_page_token": "page-2",
            }

        monkeypatch.setattr(
            "yc_plugin.yc_logging.client.YandexLoggingClient.read_entries", _read
        )
        result = read_yc_logs(log_group_id="e23abc", levels=["ERROR"], **_CREDENTIALS)

        assert result["available"] is True
        assert result["entry_count"] == 1
        assert result["next_page_token"] == "page-2"
        assert result["window"]["since"] < result["window"]["until"]

    def test_log_groups_are_listed_from_the_management_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Groups are REST on logging.api; only entry reads use the reader host."""
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured["url"] = url
            return httpx.Response(
                200, json={"groups": [{"id": "e23abc", "name": "default", "status": "ACTIVE"}]}
            )

        monkeypatch.setattr(httpx, "request", _request)
        result = list_yc_log_groups(**_CREDENTIALS)

        assert "logging.api.cloud.yandex.net" in captured["url"]
        assert result["count"] == 1
        assert result["log_groups"][0]["id"] == "e23abc"


class TestAuditTrails:
    def test_a_logging_destination_is_readable(self) -> None:
        kind, target = trail_destination({"destination": {"cloudLogging": {"logGroupId": "e23"}}})

        assert (kind, target) == ("cloudLogging", "e23")
        assert describe_trail({"destination": {"cloudLogging": {"logGroupId": "e23"}}})["readable"]

    def test_a_storage_destination_is_not_readable_here(self) -> None:
        trail = describe_trail(
            {"name": "audit", "destination": {"objectStorage": {"bucketName": "audit-logs"}}}
        )

        assert trail["destination"] == "objectStorage"
        assert trail["destination_target"] == "audit-logs"
        assert trail["readable"] is False

    def test_a_broken_trail_is_called_out(self) -> None:
        trail = describe_trail(
            {"name": "audit", "status": "ERROR", "destination": {"cloudLogging": {}}}
        )

        assert "not being delivered" in trail["warning"]

    def test_repeated_events_are_collapsed(self) -> None:
        """Cloud Logging delivery can duplicate an event."""
        events = [
            {"uid": "a", "json_payload": {"event_id": "evt-1"}},
            {"uid": "b", "json_payload": {"event_id": "evt-1"}},
            {"uid": "c", "json_payload": {"event_id": "evt-2"}},
        ]

        assert len(deduplicate(events)) == 2

    def test_no_trail_at_all_says_how_to_create_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "request", lambda *_a, **_k: httpx.Response(200, json={"trails": []})
        )
        result = read_yc_audit_events(**_CREDENTIALS)

        assert result["trail_count"] == 0
        assert "yc audit-trails create" in result["note"]

    def test_events_are_read_from_the_trail_log_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            httpx,
            "request",
            lambda *_a, **_k: httpx.Response(
                200,
                json={
                    "trails": [
                        {
                            "id": "trail-1",
                            "name": "audit",
                            "status": "ACTIVE",
                            "destination": {"cloudLogging": {"logGroupId": "e23abc"}},
                        }
                    ]
                },
            ),
        )
        read_calls: list[str] = []

        def _read(_self: Any, log_group_id: str, **_kwargs: Any) -> dict[str, Any]:
            read_calls.append(log_group_id)
            return {
                "success": True,
                "error": None,
                "entries": [
                    {"uid": "1", "json_payload": {"event_id": "evt-1", "event_type": "Delete"}},
                    {"uid": "2", "json_payload": {"event_id": "evt-1", "event_type": "Delete"}},
                ],
                "next_page_token": "",
            }

        monkeypatch.setattr(
            "yc_plugin.yc_logging.client.YandexLoggingClient.read_entries", _read
        )
        result = read_yc_audit_events(**_CREDENTIALS)

        assert read_calls == ["e23abc"]
        assert result["event_count"] == 1

    def test_an_unreadable_destination_names_the_workaround(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            httpx,
            "request",
            lambda *_a, **_k: httpx.Response(
                200,
                json={
                    "trails": [
                        {
                            "id": "trail-1",
                            "name": "audit",
                            "status": "ACTIVE",
                            "destination": {"objectStorage": {"bucketName": "audit-logs"}},
                        }
                    ]
                },
            ),
        )
        result = read_yc_audit_events(**_CREDENTIALS)

        assert "s3 integration" in result["note"]
        assert result["event_count"] == 0


class TestMetricDiscoverySeesCustomMetrics:
    """A metric the user pushes is invisible in the default scope, and the agent
    then tells them their own metric does not exist. That happened."""

    def test_the_scope_reaches_the_service_not_only_the_labels(self) -> None:
        """selectors used to go to label discovery only, never to the name listing."""
        import inspect

        from yc_plugin.yc_monitoring.client import YandexMonitoringClient

        signature = inspect.signature(YandexMonitoringClient.metric_names)

        assert "selectors" in signature.parameters

    def test_an_empty_result_points_at_the_scope(self) -> None:
        """Yandex ignores nameFilter, so a filtered miss must not read as absence."""
        from tools.registry import get_registered_tool_map

        class _Backend:
            def list_yc_metrics(self, _name_filter: str, _selectors: str) -> dict[str, object]:
                return {"source": "yc_monitoring", "available": True, "names": [], "labels": []}

        result = get_registered_tool_map()["list_yc_metrics"].run(
            name_filter="nothing-matches-this", yc_backend=_Backend()
        )

        assert result["names"] == []


class TestTheQueryLanguageIsDocumented:
    """PromQL was tried three times in one session and parse-errored every time."""

    def test_the_schema_says_it_is_not_promql(self) -> None:
        from tools.registry import get_registered_tool_map

        help_text = get_registered_tool_map()["query_yc_metrics"].input_schema["properties"][
            "query"
        ]["description"]

        assert "NOT PromQL" in help_text
        assert "series_sum" in help_text

    def test_the_folder_label_trap_is_called_out(self) -> None:
        """folderId is accepted and matches nothing, which reads as no data."""
        from tools.registry import get_registered_tool_map

        help_text = get_registered_tool_map()["query_yc_metrics"].input_schema["properties"][
            "query"
        ]["description"]

        assert "folder_id" in help_text and "folderId" in help_text


class TestFanOutToolsReachTheShell:
    """One generic GET answers one path. A question spanning several services —
    every database engine, both load-balancer kinds — cannot be assembled from it,
    so the shell answered "1 cluster" where the gather step found 5."""

    def test_the_multi_service_readers_are_on_the_action_surface(self) -> None:
        from tools.registry import get_registered_tool_map

        tools = get_registered_tool_map()
        for name in ("list_yc_db_clusters", "get_yc_lb_health", "list_yc_serverless"):
            assert "action" in tools[name].surfaces, name

    def test_single_service_readers_stay_off_it(self) -> None:
        """execute_yc_operation covers those in one call; a seat would cost prompt for nothing."""
        from tools.registry import get_registered_tool_map

        tools = get_registered_tool_map()
        for name in ("list_yc_instances", "list_yc_k8s_clusters"):
            assert "action" not in tools[name].surfaces, name
