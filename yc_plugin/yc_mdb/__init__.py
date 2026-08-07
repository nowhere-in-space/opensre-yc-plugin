"""Managed Databases tools.

Scope is the control plane: cluster health, host roles, and recent operations
such as failovers and backups. Querying the data inside a cluster is the job of
the matching data-plane integration — ``postgresql``, ``clickhouse``,
``mysql``, and so on — pointed at the cluster's host.

Credentials come from the ``yandex_cloud`` integration record rather than one
of this package's own — see ``integrations/yandex_cloud/availability.py``.
"""

from __future__ import annotations

SOURCE = "yc_mdb"

__all__ = ["SOURCE"]
