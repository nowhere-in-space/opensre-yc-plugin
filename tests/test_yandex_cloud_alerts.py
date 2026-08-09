"""A Yandex Monitoring alert must reach the tools that can explain it.

Yandex Monitoring has no plain webhook: alerts leave it by Email, SMS, push,
Telegram or a Cloud Function, so the payload OpenSRE receives is whatever the
operator's function forwards. There is no published schema to match, which is
why detection keys off markers only a Yandex Cloud alert carries rather than an
exact shape — and why a payload that merely mentions a folder must not be
mistaken for a firing alert.
"""

from __future__ import annotations

import pytest

from core.domain.alerts.alert_source import (
    resolve_alert_source,
    routing_for_alert_source,
    seed_tool_sources_for_alert,
)
from core.domain.alerts.extraction import alert_detail_field_names
from yc_plugin.yandex_cloud.alert_source_detect import detect_yandex_cloud_alert_source

FOLDER = "b1gapqc3kb2vii7cs9i3"


@pytest.fixture(autouse=True)
def _adapters() -> None:
    """Rebuild OpenSRE's adapters the way its startup does, on every test.

    Looked up on the module rather than through the name imported above,
    because that is what ``install_runtime()`` does — it imports inside the
    function, so it sees whatever the attribute points at when it runs.
    """
    import integrations.harness_adapters as harness_adapters

    harness_adapters.register_harness_adapters()


class TestDetection:
    def test_a_cloud_function_forwarding_an_alert(self) -> None:
        """The shape an operator's notification function most naturally sends."""
        payload = {
            "alert_id": "aoe1abc",
            "alert_name": "PostgreSQL connections saturated",
            "status": "ALARM",
            "folder_id": FOLDER,
        }

        assert detect_yandex_cloud_alert_source(payload) == "yandex_monitoring"

    def test_the_rest_api_spelling(self) -> None:
        """The API answers camelCase; a function that passes it through keeps that."""
        payload = {"alertId": "aoe1abc", "evaluationStatus": "ALARM", "folderId": FOLDER}

        assert detect_yandex_cloud_alert_source(payload) == "yandex_monitoring"

    def test_a_console_link_is_enough_on_its_own(self) -> None:
        """Nothing but Yandex Cloud produces one of these."""
        payload = {"title": "disk full", "url": f"https://console.yandex.cloud/folders/{FOLDER}"}

        assert detect_yandex_cloud_alert_source(payload) == "yandex_monitoring"

    def test_markers_nested_under_labels(self) -> None:
        payload = {"status": "ALARM", "labels": {"folder_id": FOLDER, "cluster_id": "c9q7k"}}

        assert detect_yandex_cloud_alert_source(payload) == "yandex_monitoring"


class TestItDoesNotOverreach:
    def test_a_folder_id_alone_is_not_an_alert(self) -> None:
        """A forwarded tool result mentions folders too; that is not an incident."""
        assert detect_yandex_cloud_alert_source({"folder_id": FOLDER}) is None

    def test_another_vendors_alert_is_left_alone(self) -> None:
        payload = {
            "status": "firing",
            "commonLabels": {"grafana_folder": "prod", "alertname": "HighCPU"},
        }

        assert detect_yandex_cloud_alert_source(payload) is None

    def test_an_empty_payload_matches_nothing(self) -> None:
        assert detect_yandex_cloud_alert_source({}) is None


class TestRoutingReachesTheRightTools:
    def test_the_source_resolves_from_a_raw_payload(self) -> None:
        state = {
            "raw_alert": {
                "alert_id": "aoe1abc",
                "alert_name": "CPU saturated",
                "status": "ALARM",
                "folder_id": FOLDER,
            }
        }

        assert resolve_alert_source(state) == "yandex_monitoring"

    def test_metrics_and_logs_are_seeded_first(self) -> None:
        """They are what establish the shape of an incident before anything else."""
        state = {"raw_alert": {"alert_id": "a", "status": "ALARM", "folder_id": FOLDER}}

        assert seed_tool_sources_for_alert(state) == ("yc_monitoring", "yc_logging")

    def test_every_yandex_tool_family_is_reachable(self) -> None:
        entry = routing_for_alert_source("yandex_monitoring")

        assert entry is not None
        assert {"yc_compute", "yc_mdb", "yc_mk8s", "yc_serverless", "yc_network"} <= set(
            entry.relevance_tool_sources
        )


class TestTheIdentifiersSurvive:
    def test_the_fields_an_investigation_needs_are_registered(self) -> None:
        """Without the folder no follow-up read is even possible: reads are folder-scoped."""
        from core.domain.alerts.extraction import alert_detail_field_names

        registered = set(alert_detail_field_names())

        assert {"yc_folder_id", "yc_cluster_id", "yc_instance_id"} <= registered

    def test_another_vendors_fields_are_not_displaced(self) -> None:
        from core.domain.alerts.extraction import alert_detail_field_names

        assert "eks_cluster" in set(alert_detail_field_names())


class TestTheRegistrationSurvivesStartup:
    """OpenSRE rebuilds its adapters after the plugin is installed.

    ``register_harness_adapters()`` clears the alert registries before filling
    them from the built-in integrations, so a plugin that registered earlier is
    dropped unless it re-applies itself afterwards. ``opensre-yc run`` installs
    the plugin and only then hands over to the OpenSRE CLI, which makes this the
    normal order rather than an edge case.
    """

    def test_a_rebuild_does_not_drop_the_detector(self) -> None:
        import integrations.harness_adapters as harness_adapters

        harness_adapters.register_harness_adapters()

        state = {"raw_alert": {"alert_id": "a", "status": "ALARM", "folder_id": FOLDER}}
        assert resolve_alert_source(state) == "yandex_monitoring"

    def test_a_rebuild_does_not_drop_the_routing(self) -> None:
        import integrations.harness_adapters as harness_adapters

        harness_adapters.register_harness_adapters()

        assert routing_for_alert_source("yandex_monitoring") is not None

    def test_repeated_rebuilds_stay_stable(self) -> None:
        """``install_runtime()`` is documented as safe to call more than once."""
        import integrations.harness_adapters as harness_adapters

        for _ in range(3):
            harness_adapters.register_harness_adapters()

        state = {"raw_alert": {"alert_id": "a", "status": "ALARM", "folder_id": FOLDER}}
        assert resolve_alert_source(state) == "yandex_monitoring"
        assert "yc_folder_id" in set(alert_detail_field_names())
