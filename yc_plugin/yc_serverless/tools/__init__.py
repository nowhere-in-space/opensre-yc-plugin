"""Agent-callable tools for serverless workloads."""

from __future__ import annotations

from yc_plugin.yc_serverless.tools.yc_functions_tool import (
    get_yc_function,
    list_yc_serverless,
)

__all__ = ["get_yc_function", "list_yc_serverless"]
