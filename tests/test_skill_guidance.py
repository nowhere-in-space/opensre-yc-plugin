"""Telling the agent where Kubernetes workloads actually live.

The plugin shipped a SKILL.md that no code ever loaded, and it pointed
Kubernetes at ``execute_yc_operation``. Watching a real investigation showed the
cost: asked why pods were stuck, the agent called the generic Yandex Cloud
reader seven times looking for a pod endpoint that does not exist, and never
touched the twelve kubernetes tools that were offered to it.

Guidance only helps if it reaches the model, so these assert it is attached and
— the part that silently failed first time — that the sentence that matters
survives the length cap.
"""

from __future__ import annotations

from typing import Any

import pytest

from yc_plugin import skills


class TestTheSkillIsActuallyLoaded:
    def test_the_file_parses(self) -> None:
        declared, text = skills._loaded()

        assert text
        assert declared

    def test_it_is_attached_to_the_tools_that_route_a_read(self) -> None:
        """These are where the wrong turn was taken, so this is where it belongs."""
        for name in ("execute_yc_operation", "find_yc_api"):
            assert skills.guidance_for(name), name

    def test_a_tool_that_knows_its_own_job_is_left_alone(self) -> None:
        """Every copy is paid for in the context budget."""
        assert skills.guidance_for("query_yc_metrics") == ""
        assert skills.guidance_for("kubernetes_list_pods") == ""

    def test_describing_a_tool_appends_the_guidance(self) -> None:
        described = skills.describe("execute_yc_operation", "Base description.")

        assert described.startswith("Base description.")
        assert "Workflow guidance:" in described

    def test_describing_an_undeclared_tool_changes_nothing(self) -> None:
        assert skills.describe("list_yc_instances", "Base.") == "Base."


#: The sentence the whole fix rests on.
THE_RULE = "A pod is not a Yandex Cloud resource"

#: Everything the agent needs before it can act on that rule. The names matter
#: as much as the rule: "use the kubernetes tools" is not actionable without
#: them, and the control-plane pair is what gives the split a landing point.
ESSENTIALS = (
    THE_RULE,
    "kubernetes_list_pods",
    "kubernetes_get_events",
    "kubernetes_get_pod_logs",
    "kubernetes_list_nodes",
    "list_yc_k8s_clusters",
    "get_yc_k8s_cluster",
)

#: Slack the rule must keep above the cut, in characters. The formatted guidance
#: embeds the absolute path of SKILL.md, so how much of the document survives
#: depends on where the plugin happens to be installed. CI's path is some forty
#: characters longer than a typical checkout, and that difference alone was once
#: enough to push the rule past the cut — passing locally, failing there.
PATH_HEADROOM = 600


def _formatted_untruncated() -> str:
    """Return the guidance as the registry formats it, before any truncation."""
    from core.tool_framework.skill_guidance import (
        format_tool_skill_guidance,
        load_tool_skill_guidance,
    )

    result = load_tool_skill_guidance(skills.SKILL_FILE)
    assert result.skill is not None
    return format_tool_skill_guidance(result.skill)


class TestTheKubernetesRoutingSurvivesTheLengthCap:
    """It did not, the first time — the section was written past the cut.

    The registry truncates skill guidance, so guidance is only as useful as its
    first couple of thousand characters. Losing this particular sentence puts
    the agent straight back to searching the Yandex Cloud API for pods.
    """

    def test_the_guidance_is_within_the_cap(self) -> None:
        _, text = skills._loaded()

        assert len(text) <= skills.MAX_GUIDANCE_CHARS

    def test_the_rule_itself_is_still_there(self) -> None:
        _, text = skills._loaded()

        assert THE_RULE in text

    @pytest.mark.parametrize("essential", ESSENTIALS)
    def test_each_essential_is_present(self, essential: str) -> None:
        _, text = skills._loaded()

        assert essential in text

    @pytest.mark.parametrize("essential", ESSENTIALS)
    def test_each_essential_sits_far_enough_above_the_cut(self, essential: str) -> None:
        """Surviving on this machine is not the same as surviving anywhere.

        Checking only presence lets the document grow until something drifts past
        the cut on a machine with a longer install path — which is how this failed
        twice, once for the rule and once for the control-plane tool names.
        """
        text = _formatted_untruncated()

        end = text.index(essential) + len(essential)

        assert end < skills.MAX_GUIDANCE_CHARS - PATH_HEADROOM


class TestWhatTheToolsThemselvesSay:
    def test_the_generic_reader_disclaims_workloads(self) -> None:
        from tools.registry import get_registered_tool_map

        tool = get_registered_tool_map()["execute_yc_operation"]

        assert "kubernetes_" in tool.description
        assert any("kubernetes_list_pods" in example for example in tool.anti_examples)

    def test_the_endpoint_index_disclaims_them_as_well(self) -> None:
        """It indexes the Yandex Cloud API, and pods are not in that API."""
        from tools.registry import get_registered_tool_map

        assert "Kubernetes pods" in get_registered_tool_map()["find_yc_api"].description

    def test_the_cluster_reader_points_at_the_workload_tools(self) -> None:
        from tools.registry import get_registered_tool_map

        assert "kubernetes_" in get_registered_tool_map()["list_yc_k8s_clusters"].description


class TestTheClusterResultCarriesThePointer:
    """A hint in the result lands when the agent is already looking at a cluster."""

    def test_it_names_the_workload_tools_when_they_are_registered(self) -> None:
        from yc_plugin.yc_mk8s.tools import yc_k8s_tool

        access = yc_k8s_tool._workload_access()

        assert "kubernetes_list_pods" in access["workload_tools"]
        assert "not through the Yandex Cloud API" in access["workload_hint"]

    def test_it_explains_the_absence_when_no_cluster_is_connected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise "no tool for that" reads as "Yandex Cloud cannot do it"."""
        from yc_plugin.yc_mk8s.tools import yc_k8s_tool

        def _without_kubernetes() -> dict[str, Any]:
            return {}

        monkeypatch.setattr("tools.registry.get_registered_tool_map", _without_kubernetes)
        access = yc_k8s_tool._workload_access()

        assert access["workload_tools"] == []
        assert "opensre-yc configure" in access["workload_hint"]
        assert "Do not try to read them through" in access["workload_hint"]
