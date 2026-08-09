"""Workflow guidance for the plugin's tools, kept in ``SKILL.md``.

OpenSRE attaches ``SKILL.md`` guidance to tools when it builds the registry, but
only from a fixed list of paths inside its own tree, so a plugin's file is never
found there. The loader and formatter it uses are public, so the same guidance
is attached from here instead.

It has to be folded into the description **where the tool is declared**. The
registry rebuilds its snapshot by re-reading tool metadata out of the module, so
anything attached to the registry afterwards is dropped the next time the cache
clears — which is exactly what happens during startup.

Only the tools that decide *how to read something* carry the whole document:
``find_yc_api`` and ``execute_yc_operation``. A tool that already knows its own
job does not need five kilobytes explaining the rest of the API, and every copy
is paid for in the context budget.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from core.tool_framework.skill_guidance import (
    format_tool_skill_guidance,
    load_tool_skill_guidance,
)

SKILL_FILE = Path(__file__).with_name("SKILL.md")

#: Mirrors the ceiling the OpenSRE registry applies to its own skill guidance.
MAX_GUIDANCE_CHARS = 2400


@lru_cache(maxsize=1)
def _loaded() -> tuple[frozenset[str], str]:
    """Return the tools the skill declares and the text to attach to them."""
    result = load_tool_skill_guidance(SKILL_FILE)
    if result.skill is None:
        return frozenset(), ""

    text = format_tool_skill_guidance(result.skill)
    if len(text) > MAX_GUIDANCE_CHARS:
        text = text[: MAX_GUIDANCE_CHARS - 3].rstrip() + "..."
    return frozenset(result.skill.tool_names), text


def guidance_for(tool_name: str) -> str:
    """Return the guidance ``SKILL.md`` declares for *tool_name*, or "" when none."""
    tool_names, text = _loaded()
    return text if tool_name in tool_names else ""


def describe(tool_name: str, description: str) -> str:
    """Return *description* with the tool's workflow guidance appended."""
    guidance = guidance_for(tool_name)
    if not guidance:
        return description
    return f"{description}\n\nWorkflow guidance:\n{guidance}"


__all__ = ["MAX_GUIDANCE_CHARS", "SKILL_FILE", "describe", "guidance_for"]
