"""Walking a real incident through the Yandex Cloud tools.

This is the deterministic half of a scenario test: the same evidence an
investigation would gather, fetched through the same tools, asserted on
without an LLM in the loop. It answers whether the tools actually lead
somewhere — whether the metric, the operations log, the host list, and the
application logs combine into the root cause rather than each being
individually correct and jointly useless.

The LLM-scored half, where an agent has to reach that conclusion on its own,
belongs with the other synthetic suites and needs live model credentials.
"""

from __future__ import annotations

from typing import Any

import pytest

from yc_plugin.yandex_cloud.availability import yc_available_or_backend, yc_credentials
from yc_plugin.yc_audit.tools import read_yc_audit_events
from yc_plugin.yc_logging.tools import list_yc_log_groups, read_yc_logs
from yc_plugin.yc_mdb.tools import get_yc_db_cluster, list_yc_db_clusters
from yc_plugin.yc_monitoring.tools import list_yc_metrics, query_yc_metrics
from tests.mock_yc_backend import FixtureYandexCloudBackend
from tests.scenarios_yc.scenarios import HIGH_CPU_MANAGED_POSTGRESQL
from tools.investigation.stages.gather_evidence.tools import availability_view

FOLDER = "b1gexamplefolder"


@pytest.fixture
def backend() -> FixtureYandexCloudBackend:
    return FixtureYandexCloudBackend(HIGH_CPU_MANAGED_POSTGRESQL)


@pytest.fixture
def sources(backend: FixtureYandexCloudBackend) -> dict[str, dict]:
    """The resolved-source view an investigation sees for this scenario."""
    return availability_view(
        {
            "yandex_cloud": {
                "source": "yandex_cloud",
                "folder_id": FOLDER,
                "_backend": backend,
            }
        }
    )


def _params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


class TestScenarioWiring:
    def test_a_backend_alone_makes_the_tools_available(self, sources: dict[str, dict]) -> None:
        """No credentials are configured; the fixture stands in for the cloud."""
        assert yc_available_or_backend(sources) is True

    def test_the_backend_reaches_the_tools(self, sources: dict[str, dict]) -> None:
        assert _params(sources)["yc_backend"] is not None


class TestEvidenceChain:
    """The path from the alert to the cause, one tool at a time."""

    def test_the_metric_shows_the_database_saturating(self, sources: dict[str, dict]) -> None:
        result = query_yc_metrics(
            query='cpu_usage{service="managed-postgresql"}', **_params(sources)
        )

        assert result["available"] is True
        series = result["series"][0]
        assert series["max"] >= 95.0
        # Rising, not a momentary spike — first well below last.
        assert series["first"] < series["last"]

    def test_metric_discovery_names_what_can_be_queried(self, sources: dict[str, dict]) -> None:
        result = list_yc_metrics(**_params(sources))

        assert "cpu_usage" in result["names"]
        assert "host" in result["labels"]

    def test_the_cluster_is_reported_degraded(self, sources: dict[str, dict]) -> None:
        result = list_yc_db_clusters(**_params(sources))

        assert result["count"] == 2
        assert [cluster["name"] for cluster in result["unhealthy"]] == ["orders-db"]

    def test_the_failover_is_visible_in_recent_operations(self, sources: dict[str, dict]) -> None:
        """This is the cause; everything else in the scenario is its consequence."""
        result = get_yc_db_cluster(cluster_id="c1prod", engine="postgresql", **_params(sources))

        descriptions = [op["description"] for op in result["recent_operations"]]
        assert any("Failover" in description for description in descriptions)

    def test_the_dead_replica_explains_the_load_on_the_master(
        self, sources: dict[str, dict]
    ) -> None:
        result = get_yc_db_cluster(cluster_id="c1prod", engine="postgresql", **_params(sources))

        assert [host["name"] for host in result["unhealthy_hosts"]] == ["rc1a-replica.mdb"]
        assert result["hosts"][0]["role"] == "MASTER"

    def test_application_logs_show_the_downstream_symptom(self, sources: dict[str, dict]) -> None:
        groups = list_yc_log_groups(**_params(sources))
        group_id = next(
            group["id"] for group in groups["log_groups"] if group["name"] == "orders-api"
        )

        result = read_yc_logs(log_group_id=group_id, levels=["ERROR"], **_params(sources))

        assert result["entry_count"] == 2
        assert all(entry["level"] == "ERROR" for entry in result["entries"])
        assert any("connection from pool" in entry["message"] for entry in result["entries"])

    def test_a_log_filter_narrows_to_the_symptom(self, sources: dict[str, dict]) -> None:
        result = read_yc_logs(
            log_group_id="e23orders", filter="statement timeout", **_params(sources)
        )

        assert result["entry_count"] == 1

    def test_the_audit_trail_records_who_triggered_the_failover(
        self, sources: dict[str, dict]
    ) -> None:
        result = read_yc_audit_events(**_params(sources))

        assert result["event_count"] == 1
        event_type = result["events"][0]["json_payload"]["event_type"]
        assert "StartClusterFailover" in event_type

    def test_the_response_says_how_to_query_the_database_itself(
        self, sources: dict[str, dict]
    ) -> None:
        """Where the investigation goes next: the data plane, via another integration."""
        result = get_yc_db_cluster(cluster_id="c1prod", engine="postgresql", **_params(sources))

        assert result["connect"]["integration"] == "postgresql"
        assert result["connect"]["port"] == 6432


class TestEvidenceIsReachedThroughTheTools:
    def test_every_step_went_through_the_backend(
        self, backend: FixtureYandexCloudBackend, sources: dict[str, dict]
    ) -> None:
        """Guards against a tool quietly answering from somewhere other than the scenario."""
        params = _params(sources)
        query_yc_metrics(query="cpu_usage", **params)
        list_yc_db_clusters(**params)
        get_yc_db_cluster(cluster_id="c1prod", engine="postgresql", **params)
        read_yc_logs(log_group_id="e23orders", **params)
        read_yc_audit_events(**params)

        assert {"metrics", "db_clusters", "db_cluster_detail", "logs", "audit"} <= set(
            backend.calls
        )

    def test_an_unknown_resource_is_reported_not_invented(self, sources: dict[str, dict]) -> None:
        result = get_yc_db_cluster(
            cluster_id="does-not-exist", engine="postgresql", **_params(sources)
        )

        assert result["available"] is False
        assert "does-not-exist" in result["error"]
