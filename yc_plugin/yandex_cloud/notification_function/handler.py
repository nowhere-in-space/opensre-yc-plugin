"""Yandex Cloud Function that turns a Monitoring alert into an OpenSRE investigation.

Yandex Monitoring has no plain webhook channel — notifications go to Email, SMS,
push, Telegram or a Cloud Function — so this is the supported way to reach an
HTTP endpoint. The function forwards the alert to the OpenSRE gateway and
returns the root cause it gets back.

Deploy with ``deploy.sh`` beside this file, then attach the function as a
notification channel on the alerts that should trigger an investigation.

Configuration comes from the function's environment:

``OPENSRE_URL``      required, e.g. ``https://opensre.internal:8000``
``OPENSRE_TOKEN``    the gateway's ``OPENSRE_ALERT_LISTENER_TOKEN``; only
                     omittable when the function and gateway share a loopback,
                     which they do not in a normal deployment
``OPENSRE_TIMEOUT``  seconds to wait for the report, default 540

An investigation takes roughly a minute, so the function's own execution
timeout must be at least as generous — see ``deploy.sh``, which sets it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_TIMEOUT_SECONDS = 540

#: Where a name might sit, in the order a human would read them. The payload is
#: whatever the operator's channel template produces, so nothing is guaranteed.
_NAME_KEYS = ("alert_name", "alertName", "name", "title", "summary")
_SEVERITY_KEYS = ("severity", "status", "evaluation_status", "evaluationStatus", "state")


def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """First non-empty value among *keys*, looking one level into nested blocks."""
    blocks = [payload]
    for nested_key in ("alert", "labels", "annotations", "data"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            blocks.append(nested)
    for block in blocks:
        for key in keys:
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _as_payload(event: Any) -> dict[str, Any]:
    """Return the alert object from whatever the trigger handed us.

    A function attached as a notification channel is called with the alert
    directly; the same function invoked over HTTP receives an API-gateway
    envelope with the alert as a JSON string in ``body``. Accepting both means
    the deployment can be tested with curl before an alert ever fires.
    """
    if isinstance(event, dict) and isinstance(event.get("body"), str):
        try:
            decoded = json.loads(event["body"])
        except ValueError:
            return {"text": event["body"]}
        if isinstance(decoded, dict):
            return decoded
        return {"payload": decoded}
    if isinstance(event, dict):
        return event
    return {"payload": event}


def handler(event: Any, context: Any = None) -> dict[str, Any]:  # noqa: ARG001 - runtime contract
    """Forward the alert to OpenSRE and return the resulting diagnosis."""
    base_url = os.environ.get("OPENSRE_URL", "").strip().rstrip("/")
    if not base_url:
        return {"statusCode": 500, "body": "OPENSRE_URL is not set on this function"}

    alert = _as_payload(event)
    request_body = json.dumps(
        {
            "raw_alert": alert,
            "alert_name": _first_text(alert, _NAME_KEYS),
            "severity": _first_text(alert, _SEVERITY_KEYS),
        }
    ).encode()

    headers = {"Content-Type": "application/json"}
    token = os.environ.get("OPENSRE_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = float(os.environ.get("OPENSRE_TIMEOUT", _DEFAULT_TIMEOUT_SECONDS))
    request = urllib.request.Request(  # noqa: S310 - scheme comes from our own config
        f"{base_url}/investigate", data=request_body, headers=headers, method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            report = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # The gateway already keeps internals out of its error bodies.
        return {"statusCode": exc.code, "body": exc.read().decode(errors="replace")[:2000]}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"statusCode": 502, "body": f"could not reach OpenSRE: {type(exc).__name__}"}

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "root_cause": report.get("root_cause", ""),
                "validity_score": report.get("validity_score", 0.0),
                "is_noise": report.get("is_noise", False),
                "report": report.get("report", ""),
            },
            ensure_ascii=False,
        ),
    }
