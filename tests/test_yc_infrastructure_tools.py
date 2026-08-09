"""Compute, Kubernetes, managed databases, serverless, and load balancers.

What these tools are for is narrowing: turning "the service is broken" into a
named host, cluster, or target that is actually unhealthy. So the assertions
here care less about field plumbing than about whether the unhealthy thing is
picked out of the healthy ones, and whether the response says what to do next.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from yc_plugin.yc_compute.tools import get_yc_instance_diagnostics, list_yc_instances
from yc_plugin.yc_mdb.engines import ENGINE_KEYS, resolve_engine
from yc_plugin.yc_mdb.tools import get_yc_db_cluster, list_yc_db_clusters
from yc_plugin.yc_mk8s.tools import get_yc_k8s_cluster, list_yc_k8s_clusters
from yc_plugin.yc_network.tools import get_yc_lb_health
from yc_plugin.yc_serverless.tools import get_yc_function, list_yc_serverless
from tools.registry import get_registered_tool_map

FOLDER = "b1gexamplefolder"
_CREDENTIALS: dict[str, Any] = {"folder_id": FOLDER, "iam_token": "t1.token"}


@pytest.fixture(autouse=True)
def _no_endpoint_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yc_plugin.yandex_cloud.endpoints._fetch_endpoints", dict)
    from yc_plugin.yandex_cloud.endpoints import reset_endpoint_cache

    reset_endpoint_cache()


def _responder(routes: dict[str, dict[str, Any]]) -> Any:
    """Return an httpx.request stand-in that matches on a path fragment."""

    def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
        for fragment, payload in routes.items():
            if fragment in url:
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"message": f"no stub for {url}"})

    return _request


class TestRegistration:
    @pytest.mark.parametrize(
        "name",
        [
            "list_yc_instances",
            "get_yc_instance_diagnostics",
            "list_yc_k8s_clusters",
            "get_yc_k8s_cluster",
            "list_yc_db_clusters",
            "get_yc_db_cluster",
            "list_yc_serverless",
            "get_yc_function",
            "get_yc_lb_health",
        ],
    )
    def test_the_tool_is_discoverable(self, name: str) -> None:
        assert name in get_registered_tool_map("investigation")

    def test_the_family_stays_inside_the_schema_budget(self) -> None:
        """32 schemas go to the model per turn, shared with every other integration."""
        from tools.investigation.stages.gather_evidence.tools import MAX_AGENT_TOOL_SCHEMAS

        yc_tools = [
            name for name in get_registered_tool_map("investigation") if "yc" in name.split("_")
        ]

        assert len(yc_tools) <= MAX_AGENT_TOOL_SCHEMAS // 2


class TestCompute:
    def test_stopped_instances_are_picked_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/compute/v1/instances": {
                        "instances": [
                            {"id": "a", "name": "web-1", "status": "RUNNING"},
                            {"id": "b", "name": "web-2", "status": "STOPPED"},
                        ]
                    }
                }
            ),
        )
        result = list_yc_instances(**_CREDENTIALS)

        assert result["count"] == 2
        assert [item["name"] for item in result["unhealthy"]] == ["web-2"]

    def test_addresses_are_surfaced_for_matching_against_an_alert(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/compute/v1/instances": {
                        "instances": [
                            {
                                "id": "a",
                                "name": "web-1",
                                "status": "RUNNING",
                                "networkInterfaces": [
                                    {
                                        "primaryV4Address": {
                                            "address": "10.0.0.5",
                                            "oneToOneNat": {"address": "51.2.3.4"},
                                        }
                                    }
                                ],
                            }
                        ]
                    }
                }
            ),
        )
        instance = list_yc_instances(**_CREDENTIALS)["instances"][0]

        assert instance["private_addresses"] == ["10.0.0.5"]
        assert instance["public_addresses"] == ["51.2.3.4"]

    def test_name_filter_narrows_the_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/compute/v1/instances": {
                        "instances": [
                            {"id": "a", "name": "web-1", "status": "RUNNING"},
                            {"id": "b", "name": "db-1", "status": "RUNNING"},
                        ]
                    }
                }
            ),
        )
        result = list_yc_instances(name_filter="web", **_CREDENTIALS)

        assert result["count"] == 1

    def test_serial_console_is_read_for_diagnosis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The console is where an unreachable VM still reports what is wrong."""
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    ":serialPortOutput": {"contents": "kernel: Out of memory"},
                    "/compute/v1/instances/fhm1": {"id": "fhm1", "status": "RUNNING"},
                }
            ),
        )
        result = get_yc_instance_diagnostics(instance_id="fhm1", **_CREDENTIALS)

        assert "Out of memory" in result["serial_port_output"]

    def test_an_unreadable_console_does_not_fail_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if ":serialPortOutput" in url:
                return httpx.Response(400, json={"message": "instance is stopped"})
            return httpx.Response(200, json={"id": "fhm1", "status": "STOPPED"})

        monkeypatch.setattr("yc_plugin.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_instance_diagnostics(instance_id="fhm1", **_CREDENTIALS)

        assert result["available"] is True
        assert "stopped" in result["serial_port_error"]

    def test_a_missing_instance_id_asks_for_one(self) -> None:
        result = get_yc_instance_diagnostics(instance_id="", **_CREDENTIALS)

        assert result["available"] is False
        assert "list_yc_instances" in result["error"]


class TestManagedKubernetes:
    def test_a_degraded_cluster_is_picked_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/managed-kubernetes/v1/clusters": {
                        "clusters": [
                            {"id": "a", "name": "prod", "status": "RUNNING", "health": "HEALTHY"},
                            {
                                "id": "b",
                                "name": "staging",
                                "status": "RUNNING",
                                "health": "UNHEALTHY",
                            },
                        ]
                    }
                }
            ),
        )
        result = list_yc_k8s_clusters(**_CREDENTIALS)

        assert [cluster["name"] for cluster in result["unhealthy"]] == ["staging"]

    def test_node_groups_are_scoped_to_the_cluster(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The node-group list is folder-wide, so it has to be filtered."""

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "/nodeGroups" in url:
                return httpx.Response(
                    200,
                    json={
                        "nodeGroups": [
                            {
                                "id": "ng-1",
                                "name": "workers",
                                "clusterId": "cat1",
                                "status": "RUNNING",
                            },
                            {
                                "id": "ng-2",
                                "name": "other",
                                "clusterId": "other",
                                "status": "RUNNING",
                            },
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "cat1",
                    "name": "prod",
                    "status": "RUNNING",
                    "health": "HEALTHY",
                    "master": {
                        "versionInfo": {"currentVersion": "1.30"},
                        "endpoints": {"externalV4Endpoint": "https://1.2.3.4"},
                        "masterAuth": {"clusterCaCertificate": "-----BEGIN CERTIFICATE-----"},
                    },
                },
            )

        monkeypatch.setattr("yc_plugin.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_k8s_cluster(cluster_id="cat1", **_CREDENTIALS)

        assert [group["name"] for group in result["node_groups"]] == ["workers"]
        assert result["cluster"]["version"] == "1.30"

    def test_master_access_is_returned_for_workload_inspection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/nodeGroups": {"nodeGroups": []},
                    "/managed-kubernetes/v1/clusters/cat1": {
                        "id": "cat1",
                        "master": {
                            "endpoints": {"externalV4Endpoint": "https://1.2.3.4"},
                            "masterAuth": {"clusterCaCertificate": "CERT"},
                        },
                    },
                }
            ),
        )
        access = get_yc_k8s_cluster(cluster_id="cat1", **_CREDENTIALS)["master_access"]

        assert access["external_endpoint"] == "https://1.2.3.4"
        assert access["cluster_ca_certificate"] == "CERT"


class TestManagedDatabases:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("postgresql", "postgresql"),
            ("postgres", "postgresql"),
            ("redis", "valkey"),
            ("mongodb", "storedoc"),
            ("greenplum", "greenplum"),
            ("mpp", "greenplum"),
        ],
    )
    def test_former_product_names_still_resolve(self, name: str, expected: str) -> None:
        """Redis became Valkey and MongoDB became StoreDoc; people still say both."""
        engine = resolve_engine(name)

        assert engine is not None
        assert engine.key == expected

    def test_postgresql_uses_the_pooler_port(self) -> None:
        engine = resolve_engine("postgresql")

        assert engine is not None
        assert engine.port == 6432

    def test_every_engine_resolves_to_a_known_endpoint(self) -> None:
        """A key the registry does not carry is refused before the read is sent."""
        from yc_plugin.yandex_cloud.endpoints import STATIC_ENDPOINTS
        from yc_plugin.yc_mdb.engines import ENGINES

        for engine in ENGINES:
            assert engine.service in STATIC_ENDPOINTS, engine.key

    def test_engines_answering_with_nothing_is_still_an_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Most folders run one or two engines, so empty is the usual truth."""
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request", lambda *_a, **_k: httpx.Response(200, json={"clusters": []})
        )
        result = list_yc_db_clusters(**_CREDENTIALS)

        assert result["available"] is True
        assert result["count"] == 0

    def test_no_engine_reachable_is_reported_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            lambda *_a, **_k: httpx.Response(403, json={"message": "permission denied"}),
        )
        result = list_yc_db_clusters(**_CREDENTIALS)

        assert result["available"] is False

    def test_an_unknown_engine_lists_the_valid_ones(self) -> None:
        result = list_yc_db_clusters(engine="oracle", **_CREDENTIALS)

        assert result["available"] is False
        for key in ENGINE_KEYS:
            assert key in result["error"]

    def test_one_engine_failing_does_not_hide_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A folder rarely uses every engine, and permissions are often per-service."""

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "managed-postgresql" in url:
                return httpx.Response(
                    200,
                    json={
                        "clusters": [
                            {"id": "c1", "name": "main", "status": "RUNNING", "health": "ALIVE"}
                        ]
                    },
                )
            return httpx.Response(403, json={"message": "permission denied"})

        monkeypatch.setattr("yc_plugin.yandex_cloud.rest_client.send_request", _request)
        result = list_yc_db_clusters(**_CREDENTIALS)

        assert result["available"] is True
        assert result["count"] == 1
        assert "could not be listed" in result["note"]

    def test_recent_operations_expose_a_failover(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/hosts": {
                        "hosts": [
                            {"name": "rc1a.mdb", "role": "MASTER", "health": "ALIVE"},
                            {"name": "rc1b.mdb", "role": "REPLICA", "health": "DEAD"},
                        ]
                    },
                    "/operations": {
                        "operations": [
                            {"id": "op-1", "description": "Failover cluster", "done": True}
                        ]
                    },
                    "/managed-postgresql/v1/clusters/c1": {
                        "id": "c1",
                        "name": "main",
                        "status": "RUNNING",
                        "health": "DEGRADED",
                    },
                }
            ),
        )
        result = get_yc_db_cluster(cluster_id="c1", engine="postgresql", **_CREDENTIALS)

        assert result["recent_operations"][0]["description"] == "Failover cluster"
        assert [host["name"] for host in result["unhealthy_hosts"]] == ["rc1b.mdb"]

    def test_the_response_says_how_to_query_the_data_plane(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/hosts": {
                        "hosts": [{"name": "rc1a.mdb", "role": "MASTER", "health": "ALIVE"}]
                    },
                    "/operations": {"operations": []},
                    "/managed-postgresql/v1/clusters/c1": {"id": "c1", "status": "RUNNING"},
                }
            ),
        )
        connect = get_yc_db_cluster(cluster_id="c1", engine="postgresql", **_CREDENTIALS)["connect"]

        assert connect["integration"] == "postgresql"
        assert connect["host"] == "rc1a.mdb"
        assert connect["port"] == 6432
        assert "CA.pem" in connect["tls"]


class TestServerless:
    def test_functions_and_containers_are_listed_together(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/functions/v1/functions": {"functions": [{"id": "f1", "name": "api"}]},
                    "/containers/v1/containers": {"containers": [{"id": "c1", "name": "worker"}]},
                }
            ),
        )
        result = list_yc_serverless(**_CREDENTIALS)

        assert result["count"] == 2
        assert {item["kind"] for item in result["workloads"]} == {"function", "container"}

    def test_the_log_group_is_returned_for_pairing_with_log_reads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "versions:byFunction": {
                        "id": "v1",
                        "runtime": "python312",
                        "resources": {"memory": "134217728"},
                        "logOptions": {"logGroupId": "e23abc"},
                    },
                    "/functions/v1/functions/f1": {"id": "f1", "name": "api", "status": "ACTIVE"},
                }
            ),
        )
        result = get_yc_function(function_id="f1", **_CREDENTIALS)

        assert result["version"]["log_group_id"] == "e23abc"
        assert result["version"]["runtime"] == "python312"

    def test_disabled_logging_is_called_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Otherwise the agent chases logs that were never written."""
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "versions:byFunction": {"id": "v1", "logOptions": {"disabled": True}},
                    "/functions/v1/functions/f1": {"id": "f1", "name": "api"},
                }
            ),
        )
        result = get_yc_function(function_id="f1", **_CREDENTIALS)

        assert "Logging is disabled" in result["note"]


class TestLoadBalancers:
    def test_unhealthy_targets_are_collected_across_balancers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Partial target health is what explains intermittent errors."""
        monkeypatch.setattr(
            "yc_plugin.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    ":targetStates": {
                        "targetStates": [
                            {"address": "10.0.0.1", "status": "HEALTHY"},
                            {"address": "10.0.0.2", "status": "UNHEALTHY"},
                        ]
                    },
                    "/load-balancer/v1/networkLoadBalancers": {
                        "loadBalancers": [
                            {
                                "id": "nlb-1",
                                "name": "edge",
                                "status": "ACTIVE",
                                "attachedTargetGroups": [{"targetGroupId": "tg-1"}],
                            }
                        ]
                    },
                    "/apploadbalancer/v1/loadBalancers": {"loadBalancers": []},
                }
            ),
        )
        result = get_yc_lb_health(**_CREDENTIALS)

        assert result["count"] == 1
        assert len(result["unhealthy_targets"]) == 1
        assert result["unhealthy_targets"][0]["address"] == "10.0.0.2"
        assert result["unhealthy_targets"][0]["balancer"] == "edge"

    def test_one_balancer_type_can_be_requested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            seen.append(url)
            return httpx.Response(200, json={"loadBalancers": []})

        monkeypatch.setattr("yc_plugin.yandex_cloud.rest_client.send_request", _request)
        get_yc_lb_health(type="application", **_CREDENTIALS)

        assert all("apploadbalancer" in url for url in seen)
