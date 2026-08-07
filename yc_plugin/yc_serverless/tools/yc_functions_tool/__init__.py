"""Cloud Functions and Serverless Containers during an investigation."""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from yc_plugin.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    client_from_params,
    yc_available_or_backend,
    yc_credentials,
)

SOURCE = "yc_serverless"

_FUNCTIONS_SERVICE = "serverless-functions"
_FUNCTIONS_PATH = "/functions/v1/functions"
_CONTAINERS_SERVICE = "serverless-containers"
_CONTAINERS_PATH = "/containers/v1/containers"

KIND_FUNCTION = "function"
KIND_CONTAINER = "container"


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


def _summarize(item: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "kind": kind,
        "status": item.get("status", ""),
        "created_at": item.get("createdAt", ""),
        "labels": item.get("labels", {}),
    }


def _summarize_version(version: dict[str, Any]) -> dict[str, Any]:
    resources = version.get("resources") or {}
    log_options = version.get("logOptions") or {}
    return {
        "id": version.get("id", ""),
        "runtime": version.get("runtime", ""),
        "entrypoint": version.get("entrypoint", ""),
        "memory": resources.get("memory", ""),
        "execution_timeout": version.get("executionTimeout", ""),
        "service_account_id": version.get("serviceAccountId", ""),
        "concurrency": version.get("concurrency", ""),
        "created_at": version.get("createdAt", ""),
        "environment": sorted((version.get("environment") or {}).keys()),
        "log_group_id": log_options.get("logGroupId", ""),
        "logs_disabled": bool(log_options.get("disabled", False)),
    }


@tool(
    name="list_yc_serverless",
    surfaces=("investigation", "action"),
    display_name="Serverless",
    source=SOURCE,
    description=(
        "List Cloud Functions and Serverless Containers in the folder. Use to "
        "find what serverless workloads exist and their ids before looking at "
        "one in detail."
    ),
    use_cases=[
        "Finding a function or container id from its name",
        "Establishing which serverless workloads run in a folder",
        "Checking whether a function exists at all before chasing its logs",
    ],
    requires=[],
    outputs={
        "workloads": "functions and containers with id, kind, and status",
        "count": "how many were returned",
    },
    input_schema={
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "description": "Restrict to one kind. Omit for both.",
                "enum": ["function", "container", ""],
                "default": "",
            }
        },
        "required": [],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def list_yc_serverless(
    kind: str = "",
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """List serverless functions and containers."""
    if yc_backend is not None:
        return dict(yc_backend.list_yc_serverless(kind))

    client = client_from_params(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

    wanted = kind.strip().lower()
    workloads: list[dict[str, Any]] = []
    errors: list[str] = []
    folder = {"folderId": client.folder_id}

    if wanted in ("", KIND_FUNCTION):
        response = client.get(_FUNCTIONS_SERVICE, _FUNCTIONS_PATH, folder)
        if response.get("success"):
            raw = (response.get("data") or {}).get("functions") or []
            workloads.extend(_summarize(item, KIND_FUNCTION) for item in raw)
        else:
            errors.append(f"functions: {response.get('error', '')}")

    if wanted in ("", KIND_CONTAINER):
        response = client.get(_CONTAINERS_SERVICE, _CONTAINERS_PATH, folder)
        if response.get("success"):
            raw = (response.get("data") or {}).get("containers") or []
            workloads.extend(_summarize(item, KIND_CONTAINER) for item in raw)
        else:
            errors.append(f"containers: {response.get('error', '')}")

    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "workloads": workloads,
        "count": len(workloads),
    }
    if errors and not workloads:
        result["available"] = False
        result["error"] = "; ".join(errors)
    elif errors:
        result["note"] = "Partial results: " + "; ".join(errors)
    return result


@tool(
    name="get_yc_function",
    display_name="Serverless",
    source=SOURCE,
    description=(
        "Read a Cloud Function's current version: runtime, memory, timeout, "
        "concurrency, service account, and which log group it writes to. Pair "
        "the log group id with read_yc_logs to see what the function actually "
        "logged. Memory and timeout are the usual culprits behind a function "
        "that fails only under load."
    ),
    use_cases=[
        "Finding the log group a function writes to, before reading its logs",
        "Checking a function's memory and timeout against the errors it produces",
        "Confirming when a function was last deployed relative to an incident",
        "Checking which service account a function runs as when it hits permission errors",
    ],
    requires=["function_id"],
    outputs={
        "function": "the function's identity and status",
        "version": "the active version's configuration, including its log group",
        "note": "flagged when the function writes no logs at all",
    },
    input_schema={
        "type": "object",
        "properties": {
            "function_id": {
                "type": "string",
                "description": "Function id, as returned by list_yc_serverless.",
            }
        },
        "required": ["function_id"],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def get_yc_function(
    function_id: str,
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Read a function and its active version."""
    if not function_id.strip():
        return tool_unavailable(
            SOURCE, "function_id is required. Call list_yc_serverless to find one."
        )

    if yc_backend is not None:
        return dict(yc_backend.get_yc_function(function_id))

    client = client_from_params(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

    detail = client.get(_FUNCTIONS_SERVICE, f"{_FUNCTIONS_PATH}/{function_id}", page_size=None)
    if not detail.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": detail.get("error", "Could not read the function."),
            "function_id": function_id,
        }

    version_response = client.get(
        _FUNCTIONS_SERVICE,
        "/functions/v1/versions:byFunction",
        {"functionId": function_id},
        page_size=None,
    )
    version = _summarize_version(version_response.get("data") or {})

    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "function_id": function_id,
        "function": _summarize(detail.get("data") or {}, KIND_FUNCTION),
        "version": version,
    }
    if version["logs_disabled"]:
        result["note"] = (
            "Logging is disabled for this function, so read_yc_logs will find nothing "
            "for it however the incident unfolded."
        )
    elif not version["log_group_id"]:
        result["note"] = (
            "This function writes to the folder's default log group. Find it with "
            "list_yc_log_groups."
        )
    return result


__all__ = ["get_yc_function", "list_yc_serverless"]
