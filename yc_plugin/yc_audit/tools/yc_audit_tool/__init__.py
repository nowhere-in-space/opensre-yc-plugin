"""Reading Yandex Cloud audit events during an investigation."""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from yc_plugin.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    config_from_params,
    yc_available_or_backend,
    yc_credentials,
)
from yc_plugin.yc_audit.client import (
    DESTINATION_LOGGING,
    YandexAuditTrailsClient,
    deduplicate,
    describe_trail,
)
from yc_plugin.yc_logging.client import (
    DEFAULT_PAGE_SIZE,
    LogReadingUnavailableError,
    resolve_window,
)

SOURCE = "yc_audit"

DEFAULT_WINDOW_MINUTES = 180


def _audit_client(params: dict[str, Any]) -> YandexAuditTrailsClient | None:
    """Build the client from injected credentials, or None when unusable."""
    config = config_from_params(params)
    return None if config is None else YandexAuditTrailsClient(config)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


def _unreadable_destination_note(trails: list[dict[str, Any]]) -> str:
    kinds = {trail["destination"] for trail in trails if not trail["readable"]}
    if not kinds:
        return ""
    return (
        f"Trails in this folder deliver to {', '.join(sorted(kinds))}, which this tool "
        "cannot read directly. For an Object Storage destination, configure the s3 "
        "integration against the bucket and read the event files from there."
    )


@tool(
    name="read_yc_audit_events",
    surfaces=("investigation", "action"),
    display_name="Audit Trails",
    source=SOURCE,
    description=(
        "Read Yandex Cloud audit events — who changed what, and when. Lists the "
        "folder's trails and reads events from any that deliver into Cloud "
        "Logging. Use to tie an incident to a configuration change, a role "
        "grant, or a resource being stopped or deleted. Note that failed "
        "authentication is not audited; failed authorization is."
    ),
    use_cases=[
        "Finding what changed shortly before an incident began",
        "Identifying who stopped, deleted, or resized a resource",
        "Reviewing recent role grants and service-account key activity",
        "Checking whether audit delivery is healthy at all",
    ],
    requires=[],
    outputs={
        "trails": "the folder's trails, where each delivers, and whether it is healthy",
        "events": "audit events from readable trails, deduplicated",
        "event_count": "how many distinct events were returned",
        "note": "caveats, such as destinations this tool cannot read",
    },
    input_schema={
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": (
                    "Cloud Logging filter over the events, e.g. "
                    "'json_payload.event_type: \"DeleteInstance\"'."
                ),
                "default": "",
            },
            "since": {
                "type": "string",
                "description": "Start of the window, RFC3339. Defaults to window_minutes ago.",
                "default": "",
            },
            "until": {
                "type": "string",
                "description": "End of the window, RFC3339. Defaults to now.",
                "default": "",
            },
            "window_minutes": {
                "type": "integer",
                "description": "Window size when since is omitted.",
                "default": DEFAULT_WINDOW_MINUTES,
            },
            "trails_only": {
                "type": "boolean",
                "description": "List the trails without reading any events.",
                "default": False,
            },
            "page_size": {
                "type": "integer",
                "description": "Events per trail.",
                "default": DEFAULT_PAGE_SIZE,
            },
        },
        "required": [],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def read_yc_audit_events(
    filter: str = "",  # noqa: A002 - schema-facing name, matches how the model reads it
    since: str = "",
    until: str = "",
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    trails_only: bool = False,
    page_size: int = DEFAULT_PAGE_SIZE,
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Read audit events from the folder's trails."""
    if yc_backend is not None:
        return dict(yc_backend.read_yc_audit_events(filter, since, until, trails_only))

    client = _audit_client(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

    listed = client.list_trails()
    if not listed.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": listed.get("error", "Could not list audit trails."),
        }

    raw_trails = (listed.get("data") or {}).get("trails") or []
    trails = [describe_trail(trail) for trail in raw_trails]
    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "trails": trails,
        "trail_count": len(trails),
    }

    if not trails:
        result["note"] = (
            "No audit trail is configured in this folder, so no audit events are "
            "being captured. Create one with: yc audit-trails create"
        )
        return result

    if trails_only:
        note = _unreadable_destination_note(trails)
        if note:
            result["note"] = note
        return result

    start, end = resolve_window(since, until, window_minutes)
    events: list[dict[str, Any]] = []
    errors: list[str] = []

    for trail in trails:
        if trail["destination"] != DESTINATION_LOGGING or not trail["destination_target"]:
            continue
        try:
            response = client.read_events(
                trail["destination_target"],
                since=start,
                until=end,
                filter_expression=filter,
                page_size=page_size,
            )
        except LogReadingUnavailableError as exc:
            return tool_unavailable(SOURCE, str(exc))
        if response.get("success"):
            events.extend(response.get("entries", []))
        else:
            errors.append(f"{trail['name'] or trail['id']}: {response.get('error', '')}")

    # Cloud Logging delivery can repeat an event; collapse those.
    unique = deduplicate(events)
    result |= {
        "window": {"since": start.isoformat(), "until": end.isoformat()},
        "events": unique,
        "event_count": len(unique),
    }
    notes = [note for note in (_unreadable_destination_note(trails),) if note]
    if errors:
        notes.append("Some trails could not be read: " + "; ".join(errors))
    if notes:
        result["note"] = " ".join(notes)
    return result


__all__ = ["read_yc_audit_events"]
