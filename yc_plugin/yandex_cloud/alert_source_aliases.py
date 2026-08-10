"""Words in an alert that mean "this is Yandex Cloud".

The investigation planner scores every tool before the loop starts and keeps
only the top ten. A source named in the alert's own text earns +70, and without
it a tool is effectively out of the running: the plugin's tools scored 104
against the Kubernetes integration's 170 and were cut before the model ever saw
them.

That +70 comes from matching the alert text against a source's registered
keywords, and the plugin registered none. The only keyword a source has by
default is its own name, and no alert has ever said ``yc_mk8s``.

Two things shape what is listed here.

**Matching is substring, not word.** ``keyword in text``. So ``yc`` would match
"policy", "cycle" and "recycle", and every Yandex tool would look relevant to
every alert. Nothing shorter than a distinctive word belongs here.

**Generic terms belong to the source that owns them.** OpenSRE deliberately
gives ``eks`` only ``eks`` and leaves ``pod``, ``k8s`` and ``kubectl`` to the
``kubernetes`` source, so that EKS does not look relevant to every cluster
alert. The same restraint applies here: ``yc_mk8s`` gets ``managed kubernetes``,
not ``kubernetes``. An alert about a pod that will not start is answered by the
Kubernetes tools, and it would be wrong to displace them.
"""

from __future__ import annotations

from typing import Final

#: Keyword sets per tool source. A source absent from this table keeps only its
#: own name as a keyword, which is the right answer when nothing in an alert
#: distinguishes it — ``yc_network`` is one: "load balancer" belongs to whoever
#: the alert is actually about.
SOURCE_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    # The umbrella. Anything naming the cloud itself belongs to the generic
    # reader and the endpoint index, which can reach every service.
    "yandex_cloud": ("yandex", "yandexcloud"),
    "yc_monitoring": ("yandex monitoring",),
    "yc_logging": ("yandex logging", "cloud logging"),
    "yc_compute": ("yandex compute",),
    # "managed kubernetes" and not "kubernetes": see the module docstring.
    "yc_mk8s": ("managed kubernetes", "managed-kubernetes", "mk8s"),
    # `mdb` is how Yandex spells managed databases everywhere, including in the
    # hostnames an alert quotes: rc1a-abc.mdb.yandexcloud.net. The engine names
    # are the current product ones — Valkey was Redis, StoreDoc was MongoDB —
    # and the plain engine words stay with the data-plane integrations that own
    # them.
    "yc_mdb": (
        # Not a bare "mdb": three characters match by accident. These are the
        # forms that actually appear — the hostname a connection error quotes,
        # and the product name a Monitoring alert uses.
        "mdb.yandexcloud",
        "managed database",
        "managed postgresql",
        "managed-postgresql",
        "managed mysql",
        "managed-mysql",
        "managed clickhouse",
        "managed-clickhouse",
        "managed mongodb",
        "managed kafka",
        "managed opensearch",
        "managed greenplum",
        "storedoc",
        "valkey",
    ),
    "yc_serverless": ("yandex function", "serverless container"),
    "yc_audit": ("audit trails",),
}

#: Anything shorter than this is a substring accident waiting to happen.
MIN_ALIAS_LENGTH: Final = 4


def register_aliases() -> None:
    """Tell the planner which words point at each Yandex Cloud tool source."""
    from core.domain.alerts.alert_source import register_source_aliases

    for source, aliases in SOURCE_ALIASES.items():
        register_source_aliases(source, aliases)


__all__ = ["MIN_ALIAS_LENGTH", "SOURCE_ALIASES", "register_aliases"]
