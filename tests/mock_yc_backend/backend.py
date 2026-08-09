"""The Yandex Cloud surface a scenario has to provide, and a fixture that does.

The Protocol is the contract every Yandex Cloud tool honours when a backend is
attached: each tool calls exactly one method on it and returns what it gets. So
a scenario describes an incident once, as data, and the whole tool family reads
that description instead of the API.

``FixtureYandexCloudBackend`` serves a plain dict in the shape the tools expect.
Absent sections behave the way the real cloud does when nothing is there —
empty lists, not errors — so a scenario only has to describe the parts of the
incident it cares about.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class YandexCloudBackend(Protocol):
    """What a scenario must answer for the Yandex Cloud tools."""

    def execute_yc_operation(
        self, service: str, path: str, params: dict[str, Any], page_token: str
    ) -> dict[str, Any]:
        """Return the generic read envelope for *service* and *path*."""

    def known_endpoints(self) -> dict[str, str]:
        """Return the service-to-host map the scenario pretends to have."""

    def query_yc_metrics(
        self, query: str, from_time: str, to_time: str, window_minutes: int
    ) -> dict[str, Any]:
        """Return a metric-read envelope for *query*."""

    def list_yc_metrics(self, name_filter: str, _selectors: str) -> dict[str, Any]:
        """Return the metric names and label keys the scenario exposes."""

    def read_yc_logs(
        self,
        log_group_id: str,
        filter_expression: str,
        levels: tuple[str, ...],
        since: datetime,
        until: datetime,
    ) -> dict[str, Any]:
        """Return log entries for *log_group_id*."""

    def list_yc_log_groups(self, page_token: str) -> dict[str, Any]:
        """Return the folder's log groups."""

    def read_yc_audit_events(
        self, filter_expression: str, since: str, until: str, trails_only: bool
    ) -> dict[str, Any]:
        """Return audit trails and their events."""

    def list_yc_instances(self, name_filter: str, page_token: str) -> dict[str, Any]:
        """Return the folder's compute instances."""

    def get_yc_instance_diagnostics(
        self, instance_id: str, include_serial_output: bool
    ) -> dict[str, Any]:
        """Return one instance's configuration and serial console."""

    def list_yc_k8s_clusters(self, page_token: str) -> dict[str, Any]:
        """Return the folder's Kubernetes clusters."""

    def get_yc_k8s_cluster(self, cluster_id: str) -> dict[str, Any]:
        """Return one Kubernetes cluster and its node groups."""

    def list_yc_db_clusters(self, engine: str) -> dict[str, Any]:
        """Return the folder's managed database clusters."""

    def get_yc_db_cluster(self, cluster_id: str, engine: str) -> dict[str, Any]:
        """Return one database cluster with hosts and recent operations."""

    def list_yc_serverless(self, kind: str) -> dict[str, Any]:
        """Return the folder's functions and containers."""

    def get_yc_function(self, function_id: str) -> dict[str, Any]:
        """Return one function and its active version."""

    def get_yc_lb_health(self, balancer_type: str) -> dict[str, Any]:
        """Return load balancer and target health."""


class FixtureYandexCloudBackend:
    """Serves one scenario's evidence to every Yandex Cloud tool."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self._fixture = fixture
        #: Which tools the scenario actually exercised, so a test can assert on
        #: the path an investigation took rather than only its conclusion.
        self.calls: list[str] = []

    def _section(self, name: str) -> Any:
        self.calls.append(name)
        return self._fixture.get(name)

    # -- generic ----------------------------------------------------------

    def execute_yc_operation(
        self, service: str, path: str, _params: dict[str, Any], _page_token: str
    ) -> dict[str, Any]:
        operations = self._section("operations") or {}
        data = operations.get(f"{service}{path}")
        if data is None:
            return {
                "success": False,
                "service": service,
                "path": path,
                "data": None,
                "error": f"This scenario describes no {service} resource at {path}.",
                "metadata": {},
            }
        return {
            "success": True,
            "service": service,
            "path": path,
            "data": data,
            "error": None,
            "metadata": {"status_code": 200, "request_id": "fixture", "next_page_token": ""},
        }

    def known_endpoints(self) -> dict[str, str]:
        endpoints = self._section("endpoints")
        return dict(endpoints or {"compute": "compute.api.cloud.yandex.net"})

    # -- observability ----------------------------------------------------

    def query_yc_metrics(
        self, query: str, from_time: str, to_time: str, _window_minutes: int
    ) -> dict[str, Any]:
        metrics = self._section("metrics") or {}
        # Match on the metric name, which is what precedes the label selector.
        name = query.split("{", maxsplit=1)[0].strip()
        series = metrics.get(name, [])
        return {
            "success": True,
            "data": {"metrics": series},
            "error": None,
            "metadata": {"from": from_time, "to": to_time},
        }

    def list_yc_metrics(self, name_filter: str, _selectors: str) -> dict[str, Any]:
        metrics = self._fixture.get("metrics") or {}
        self.calls.append("list_metrics")
        names = [name for name in sorted(metrics) if not name_filter or name_filter in name]
        return {
            "source": "yc_monitoring",
            "available": True,
            "names": names,
            "labels": self._fixture.get("metric_labels", []),
            "name_count": len(names),
        }

    def read_yc_logs(
        self,
        log_group_id: str,
        filter_expression: str,
        levels: tuple[str, ...],
        _since: datetime,
        _until: datetime,
    ) -> dict[str, Any]:
        logs = self._section("logs") or {}
        entries = list(logs.get(log_group_id, []))
        if levels:
            entries = [entry for entry in entries if entry.get("level") in levels]
        if filter_expression:
            # Enough of the filter language to be useful in a fixture: a bare
            # term matches the message.
            needle = filter_expression.strip().strip('"').lower()
            entries = [
                entry for entry in entries if needle in str(entry.get("message", "")).lower()
            ]
        return {"success": True, "error": None, "entries": entries, "next_page_token": ""}

    def list_yc_log_groups(self, _page_token: str) -> dict[str, Any]:
        groups = self._section("log_groups") or []
        return {
            "success": True,
            "data": {"groups": groups},
            "error": None,
            "metadata": {"next_page_token": ""},
        }

    def read_yc_audit_events(
        self, _filter_expression: str, _since: str, _until: str, trails_only: bool
    ) -> dict[str, Any]:
        audit = self._section("audit") or {}
        events = [] if trails_only else list(audit.get("events", []))
        return {
            "source": "yc_audit",
            "available": True,
            "trails": audit.get("trails", []),
            "trail_count": len(audit.get("trails", [])),
            "events": events,
            "event_count": len(events),
        }

    # -- infrastructure ---------------------------------------------------

    def list_yc_instances(self, _name_filter: str, _page_token: str) -> dict[str, Any]:
        instances = self._section("instances") or []
        return {
            "success": True,
            "data": {"instances": instances},
            "error": None,
            "metadata": {"next_page_token": ""},
        }

    def get_yc_instance_diagnostics(
        self, instance_id: str, _include_serial_output: bool
    ) -> dict[str, Any]:
        diagnostics = self._section("instance_diagnostics") or {}
        return dict(
            diagnostics.get(
                instance_id,
                {
                    "source": "yc_compute",
                    "available": False,
                    "error": f"This scenario describes no instance {instance_id}.",
                },
            )
        )

    def list_yc_k8s_clusters(self, _page_token: str) -> dict[str, Any]:
        clusters = self._section("k8s_clusters") or []
        return {
            "success": True,
            "data": {"clusters": clusters},
            "error": None,
            "metadata": {"next_page_token": ""},
        }

    def get_yc_k8s_cluster(self, cluster_id: str) -> dict[str, Any]:
        clusters = self._section("k8s_cluster_detail") or {}
        return dict(
            clusters.get(
                cluster_id,
                {
                    "source": "yc_mk8s",
                    "available": False,
                    "error": f"This scenario describes no cluster {cluster_id}.",
                },
            )
        )

    def list_yc_db_clusters(self, engine: str) -> dict[str, Any]:
        clusters = self._section("db_clusters") or []
        if engine:
            clusters = [cluster for cluster in clusters if cluster.get("engine") == engine]
        return {
            "source": "yc_mdb",
            "available": True,
            "clusters": clusters,
            "unhealthy": [cluster for cluster in clusters if not cluster.get("healthy", True)],
            "count": len(clusters),
        }

    def get_yc_db_cluster(self, cluster_id: str, _engine: str) -> dict[str, Any]:
        detail = self._section("db_cluster_detail") or {}
        return dict(
            detail.get(
                cluster_id,
                {
                    "source": "yc_mdb",
                    "available": False,
                    "error": f"This scenario describes no cluster {cluster_id}.",
                },
            )
        )

    def list_yc_serverless(self, kind: str) -> dict[str, Any]:
        workloads = self._section("serverless") or []
        if kind:
            workloads = [item for item in workloads if item.get("kind") == kind]
        return {
            "source": "yc_serverless",
            "available": True,
            "workloads": workloads,
            "count": len(workloads),
        }

    def get_yc_function(self, function_id: str) -> dict[str, Any]:
        functions = self._section("function_detail") or {}
        return dict(
            functions.get(
                function_id,
                {
                    "source": "yc_serverless",
                    "available": False,
                    "error": f"This scenario describes no function {function_id}.",
                },
            )
        )

    def get_yc_lb_health(self, balancer_type: str) -> dict[str, Any]:
        balancers = self._section("load_balancers") or []
        if balancer_type:
            balancers = [item for item in balancers if item.get("type") == balancer_type]
        unhealthy = [
            {"balancer": balancer.get("name", ""), **target}
            for balancer in balancers
            for target in balancer.get("targets", [])
            if not target.get("healthy", True)
        ]
        return {
            "source": "yc_network",
            "available": True,
            "balancers": balancers,
            "unhealthy_targets": unhealthy,
            "count": len(balancers),
        }


__all__ = ["FixtureYandexCloudBackend", "YandexCloudBackend"]
