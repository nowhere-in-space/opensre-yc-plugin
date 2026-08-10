"""Which words in an alert make a Yandex Cloud tool worth planning.

The investigation planner scores tools before the loop and keeps the top ten.
Naming the source in the alert text is worth +70, and the plugin registered no
keywords at all — so its tools scored 104 against the Kubernetes integration's
170 and were cut before the model saw them. Its own source name was the only
keyword it had, and no alert says ``yc_mk8s``.

The opposite failure is just as real: claim ``pod`` and every Yandex tool looks
relevant to every cluster alert, displacing the tools that would have answered
it. OpenSRE guards against exactly that for ``eks``, and these tests hold the
plugin to the same line.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.domain.alerts.alert_source import relevant_sources_for_alert, source_aliases
from yc_plugin.yandex_cloud.alert_source_aliases import (
    MIN_ALIAS_LENGTH,
    SOURCE_ALIASES,
    register_aliases,
)

OUR_SOURCES = frozenset(SOURCE_ALIASES)

#: Terms OpenSRE assigns to the ``kubernetes`` source. Claiming any of them
#: would put the plugin in front of the tools that actually read a cluster.
KUBERNETES_TERMS = ("kubernetes", "k8s", "kubectl", "pod", "crashloopbackoff", "oomkilled")


def _matched(alert_name: str, candidates: Any = None) -> list[str]:
    """Return the sources an alert's text makes relevant.

    Core's own aliases are registered too: the interesting question is not what
    the plugin matches in isolation but what it matches *beside* the sources it
    could displace.
    """
    from integrations.harness_adapters import register_harness_adapters

    register_harness_adapters()
    register_aliases()
    alert = {"alert_name": alert_name}
    state = {"raw_alert": alert, "alert": alert, "alert_name": alert_name}
    return relevant_sources_for_alert(state, candidates or [*OUR_SOURCES, "kubernetes"])


class TestTheAliasesAreRegistered:
    def test_installing_registers_them(self) -> None:
        register_aliases()

        registered = source_aliases()
        for source, aliases in SOURCE_ALIASES.items():
            assert registered.get(source) == aliases, source

    def test_every_source_that_has_aliases_is_one_of_ours(self) -> None:
        assert all(source.startswith(("yc_", "yandex_")) for source in SOURCE_ALIASES)


class TestNoAliasIsASubstringAccident:
    """Matching is ``keyword in text``, so a short alias matches by accident."""

    @pytest.mark.parametrize(
        ("source", "alias"),
        [(source, alias) for source, aliases in SOURCE_ALIASES.items() for alias in aliases],
    )
    def test_it_is_long_enough_to_be_distinctive(self, source: str, alias: str) -> None:
        assert len(alias) >= MIN_ALIAS_LENGTH, f"{source}: {alias!r} is short enough to misfire"

    def test_yc_is_not_an_alias(self) -> None:
        """It would match "policy", "cycle" and "recycle"."""
        every = {alias for aliases in SOURCE_ALIASES.values() for alias in aliases}

        assert "yc" not in every

    @pytest.mark.parametrize("word", ["policy", "recycle", "lifecycle", "encyclopedia"])
    def test_an_ordinary_word_matches_nothing(self, word: str) -> None:
        assert _matched(f"Something about {word} failed") == []


class TestGenericClusterTermsStayWithKubernetes:
    """The lesson OpenSRE wrote down for eks, applied to this plugin."""

    @pytest.mark.parametrize("term", KUBERNETES_TERMS)
    def test_the_plugin_does_not_claim_it(self, term: str) -> None:
        every = {alias for aliases in SOURCE_ALIASES.values() for alias in aliases}

        assert term not in every

    def test_a_plain_kubernetes_alert_stays_with_kubernetes(self) -> None:
        """A pod that will not start is answered by the cluster's own tools."""
        matched = _matched("CrashLoopBackOff on pod api-7f9")

        assert matched == ["kubernetes"]

    def test_a_managed_kubernetes_alert_brings_in_both(self) -> None:
        """The control plane is ours; the workload is still theirs."""
        matched = _matched("Managed Kubernetes node group degraded")

        assert "yc_mk8s" in matched
        assert "kubernetes" in matched


class TestAYandexAlertReachesTheRightSource:
    @pytest.mark.parametrize(
        ("alert_name", "expected"),
        [
            ("High CPU on Managed PostgreSQL cluster", "yc_mdb"),
            ("Connection refused on rc1a-abc.mdb.yandexcloud.net", "yc_mdb"),
            ("Managed Database cluster degraded", "yc_mdb"),
            ("Valkey cluster is not responding", "yc_mdb"),
            ("StoreDoc replica lag growing", "yc_mdb"),
            ("Yandex Monitoring alert did not clear", "yc_monitoring"),
            ("Nothing arriving in Cloud Logging", "yc_logging"),
            ("Yandex Function timing out", "yc_serverless"),
            ("Audit Trails delivery stopped", "yc_audit"),
            ("Yandex Compute instance unreachable", "yc_compute"),
        ],
    )
    def test_the_owning_source_is_relevant(self, alert_name: str, expected: str) -> None:
        assert expected in _matched(alert_name)

    def test_naming_the_cloud_reaches_the_generic_reader(self) -> None:
        """It is the one that can reach any service, so it belongs on a vague alert."""
        assert "yandex_cloud" in _matched("Something broke in Yandex Cloud")


class TestWhatTheMatchIsWorth:
    """Being relevant is worth +70, and that is the whole margin.

    Scored directly rather than through the planner, which only considers tools
    whose credentials resolve — a condition about how the machine is configured,
    not about whether the aliases work.
    """

    def _score(self, tool_name: str, alert_name: str, *, relevant: set[str]) -> int:
        from core.domain.alerts.tool_planning import score_tool
        from tools.registry import get_registered_tool_map

        return score_tool(
            get_registered_tool_map()[tool_name],
            alert_text=alert_name.lower(),
            primary_sources=set(),
            relevant_sources=relevant,
            evidence_keys=set(),
        ).score

    def test_the_match_is_the_margin(self) -> None:
        alert = "High CPU on Managed PostgreSQL cluster"

        unmatched = self._score("list_yc_db_clusters", alert, relevant=set())
        matched = self._score("list_yc_db_clusters", alert, relevant={"yc_mdb"})

        assert matched - unmatched == 70

    def test_the_alert_that_should_match_does(self) -> None:
        """Chaining the two halves: the words match, and matching is worth the margin."""
        assert "yc_mdb" in _matched("High CPU on Managed PostgreSQL cluster")

    def test_a_kubernetes_alert_gives_the_margin_to_kubernetes(self) -> None:
        assert _matched("CrashLoopBackOff on pod api-7f9") == ["kubernetes"]
