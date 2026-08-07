"""Reading Yandex Cloud Audit Trails.

Audit Trails has no API for reading events. A trail is a delivery rule: it
writes events into a destination — a Cloud Logging group, an Object Storage
bucket, Data Streams, or an event bus — and reading them means reading that
destination. So this module does two things: it lists the trails so the agent
can see what is being captured and where it lands, and for trails delivering
into Cloud Logging it reads the events back out of that group.

Delivery into Cloud Logging can duplicate an event, so entries are deduplicated
on the event id Yandex stamps into the payload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from yc_plugin.yandex_cloud.config_model import YandexCloudIntegrationConfig
from yc_plugin.yandex_cloud.rest_client import YandexCloudClient
from yc_plugin.yc_logging.client import YandexLoggingClient

SERVICE: Final = "audittrails"
_TRAILS_PATH: Final = "/audit-trails/v1/trails"

#: Where a trail can deliver, and whether events can be read back from there.
DESTINATION_LOGGING: Final = "cloudLogging"
DESTINATION_STORAGE: Final = "objectStorage"
DESTINATION_DATA_STREAM: Final = "dataStream"

_READABLE_DESTINATIONS: Final = (DESTINATION_LOGGING,)


def trail_destination(trail: dict[str, Any]) -> tuple[str, str]:
    """Return where a trail delivers, as (kind, target id).

    The target is a log group id, a bucket name, or a stream name depending on
    the kind — which is exactly what a reader needs to go fetch the events.
    """
    destination = trail.get("destination") or {}
    if DESTINATION_LOGGING in destination:
        return DESTINATION_LOGGING, str(destination[DESTINATION_LOGGING].get("logGroupId", ""))
    if DESTINATION_STORAGE in destination:
        return DESTINATION_STORAGE, str(destination[DESTINATION_STORAGE].get("bucketName", ""))
    if DESTINATION_DATA_STREAM in destination:
        return DESTINATION_DATA_STREAM, str(
            destination[DESTINATION_DATA_STREAM].get("streamName", "")
        )
    return "", ""


def describe_trail(trail: dict[str, Any]) -> dict[str, Any]:
    """Return the trail fields worth showing, including whether it is healthy."""
    kind, target = trail_destination(trail)
    status = str(trail.get("status", ""))
    described: dict[str, Any] = {
        "id": trail.get("id", ""),
        "name": trail.get("name", ""),
        "status": status,
        "destination": kind,
        "destination_target": target,
        "readable": kind in _READABLE_DESTINATIONS,
    }
    if status.upper() == "ERROR":
        described["warning"] = (
            "This trail is in ERROR, so events are not being delivered. Events older "
            "than 28 days may never arrive even once it is fixed."
        )
    return described


def deduplicate(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop repeated events, which Cloud Logging delivery can produce."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("json_payload") or {}
        event_id = str(payload.get("event_id") or payload.get("eventId") or "")
        key = event_id or event.get("uid", "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(event)
    return unique


class YandexAuditTrailsClient:
    """Lists trails, and reads events back from the ones delivering to Cloud Logging."""

    def __init__(self, config: YandexCloudIntegrationConfig) -> None:
        self._config = config
        self._rest = YandexCloudClient(config)
        self._logging = YandexLoggingClient(config)

    @property
    def folder_id(self) -> str:
        return self._rest.folder_id

    def list_trails(self, page_token: str = "") -> dict[str, Any]:
        return self._rest.get(
            SERVICE,
            _TRAILS_PATH,
            {"folderId": self.folder_id},
            page_token=page_token,
        )

    def read_events(
        self,
        log_group_id: str,
        *,
        since: datetime,
        until: datetime,
        filter_expression: str = "",
        page_size: int = 100,
        page_token: str = "",
    ) -> dict[str, Any]:
        """Read audit events from the Cloud Logging group a trail delivers to."""
        return self._logging.read_entries(
            log_group_id,
            since=since,
            until=until,
            filter_expression=filter_expression,
            page_size=page_size,
            page_token=page_token,
        )


__all__ = [
    "DESTINATION_DATA_STREAM",
    "DESTINATION_LOGGING",
    "DESTINATION_STORAGE",
    "SERVICE",
    "YandexAuditTrailsClient",
    "deduplicate",
    "describe_trail",
    "trail_destination",
]
