"""Incident fixtures for the Yandex Cloud tools.

Each fixture describes one incident the way the cloud would report it, so a
test can walk the same evidence an investigation would and check that the
chain actually leads somewhere.
"""

from __future__ import annotations

from typing import Any, Final

#: A managed PostgreSQL cluster saturating after a failover left the new master
#: undersized for the load. The evidence is deliberately spread the way it
#: really is: the metric shows saturation, the operations log says a failover
#: happened, the host list shows a replica is dead, and the application logs
#: show the downstream symptom.
HIGH_CPU_MANAGED_POSTGRESQL: Final[dict[str, Any]] = {
    "metrics": {
        "cpu_usage": [
            {
                "name": "cpu_usage",
                "labels": {"service": "managed-postgresql", "host": "rc1b-primary.mdb"},
                "type": "DGAUGE",
                "timeseries": {"doubleValues": [31.0, 44.0, 78.0, 96.0, 98.0, 99.0]},
            }
        ],
        "disk.read_bytes": [
            {
                "name": "disk.read_bytes",
                "labels": {"service": "managed-postgresql", "host": "rc1b-primary.mdb"},
                "type": "RATE",
                "timeseries": {"doubleValues": [1200.0, 1400.0, 88000.0, 91000.0]},
            }
        ],
    },
    "metric_labels": ["host", "service", "resource_id"],
    "log_groups": [
        {"id": "e23orders", "name": "orders-api", "status": "ACTIVE", "retentionPeriod": "3d"},
        {"id": "e23billing", "name": "billing", "status": "ACTIVE", "retentionPeriod": "3d"},
    ],
    "logs": {
        "e23orders": [
            {
                "uid": "1",
                "level": "ERROR",
                "message": "could not obtain connection from pool within 5s",
                "timestamp": "2026-07-30T09:12:04Z",
            },
            {
                "uid": "2",
                "level": "ERROR",
                "message": "statement timeout on SELECT orders WHERE status = 'pending'",
                "timestamp": "2026-07-30T09:12:41Z",
            },
            {
                "uid": "3",
                "level": "INFO",
                "message": "health check ok",
                "timestamp": "2026-07-30T09:10:00Z",
            },
        ]
    },
    "db_clusters": [
        {
            "id": "c1prod",
            "name": "orders-db",
            "engine": "postgresql",
            "status": "RUNNING",
            "health": "DEGRADED",
            "healthy": False,
        },
        {
            "id": "c2analytics",
            "name": "analytics",
            "engine": "clickhouse",
            "status": "RUNNING",
            "health": "ALIVE",
            "healthy": True,
        },
    ],
    "db_cluster_detail": {
        "c1prod": {
            "source": "yc_mdb",
            "available": True,
            "cluster_id": "c1prod",
            "engine": "postgresql",
            "cluster": {
                "id": "c1prod",
                "name": "orders-db",
                "engine": "postgresql",
                "status": "RUNNING",
                "health": "DEGRADED",
                "healthy": False,
            },
            "hosts": [
                {
                    "name": "rc1b-primary.mdb",
                    "zone": "ru-central1-b",
                    "role": "MASTER",
                    "health": "ALIVE",
                },
                {
                    "name": "rc1a-replica.mdb",
                    "zone": "ru-central1-a",
                    "role": "REPLICA",
                    "health": "DEAD",
                },
            ],
            "unhealthy_hosts": [
                {
                    "name": "rc1a-replica.mdb",
                    "zone": "ru-central1-a",
                    "role": "REPLICA",
                    "health": "DEAD",
                }
            ],
            "recent_operations": [
                {
                    "id": "op-9",
                    "description": "Failover PostgreSQL cluster",
                    "created_at": "2026-07-30T09:08:12Z",
                    "done": True,
                    "error": "",
                }
            ],
            "connect": {
                "integration": "postgresql",
                "host": "rc1b-primary.mdb",
                "port": 6432,
                "tls": "Public hosts require TLS against Yandex's private CA.",
            },
        }
    },
    "audit": {
        "trails": [
            {
                "id": "trail-1",
                "name": "folder-audit",
                "status": "ACTIVE",
                "destination": "cloudLogging",
                "destination_target": "e23audit",
                "readable": True,
            }
        ],
        "events": [
            {
                "uid": "a1",
                "json_payload": {
                    "event_id": "evt-77",
                    "event_type": "yandex.cloud.audit.mdb.postgresql.StartClusterFailover",
                    "authentication": {"subject_id": "ajel..."},
                },
                "timestamp": "2026-07-30T09:08:10Z",
            }
        ],
    },
    "instances": [],
    "load_balancers": [],
}


__all__ = ["HIGH_CPU_MANAGED_POSTGRESQL"]
