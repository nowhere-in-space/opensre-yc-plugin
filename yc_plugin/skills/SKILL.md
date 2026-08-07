---
name: yandex-cloud
description: >
  Read Yandex Cloud through its REST API. Applies to any question about VMs,
  metrics, logs, audit events, Kubernetes, managed databases, serverless,
  networking or any other Yandex Cloud resource. Never shell out to the `yc`
  CLI — it is not how this agent reaches Yandex Cloud and is usually not
  installed.
tools:
  - find_yc_api
  - execute_yc_operation
  - query_yc_metrics
  - list_yc_metrics
  - read_yc_logs
  - list_yc_log_groups
  - read_yc_audit_events
---

# yandex-cloud

Everything reaches Yandex Cloud over its REST API with the configured
credential. There is no CLI step and no shell step.

**Never run `yc` via a shell tool.** It is normally not installed, it needs its
own separate authentication, and it can mutate. If you catch yourself writing
`yc ...` to answer a question, use `execute_yc_operation` instead.

## What a connected `yandex_cloud` means

`yandex_cloud` appears once in the connected-integrations list, but it is an
umbrella. When it is connected you can already read **all** of this — do not
tell the user a piece of it "is not configured":

| Ask about | Reach it with |
| --- | --- |
| Metrics, CPU, memory, disk, saturation | `query_yc_metrics`, `list_yc_metrics` |
| Logs (Cloud Logging) | `read_yc_logs`, `list_yc_log_groups` |
| Logs of a managed database | `find_yc_api`, then `execute_yc_operation` — see below |
| Audit events, who changed what | `read_yc_audit_events` |
| VMs, disks, images, instance groups | `execute_yc_operation` |
| Kubernetes clusters and node groups | `execute_yc_operation` |
| Managed PostgreSQL/MySQL/ClickHouse/Redis/MongoDB/Kafka/OpenSearch | `execute_yc_operation` |
| Functions, containers, triggers, API gateways | `execute_yc_operation` |
| Networks, subnets, security groups, load balancers | `execute_yc_operation` |
| Anything else in the API | `find_yc_api`, then `execute_yc_operation` |
| Which services exist at all | `find_yc_api` with no query |

Monitoring and logging are configured whenever Yandex Cloud is configured. They
share one credential; there is no separate setup to ask the user for.

## Reading anything

`find_yc_api` indexes ~900 read endpoints across 62 services, generated from
Yandex's own protobuf definitions. It is the answer to "what is the path for
X", so use it rather than guessing:

1. `find_yc_api` with the resource in plain words — `security groups`,
   `postgresql clusters`, `node group`, `certificates`.
2. `execute_yc_operation` with the `service` and `path` it returned.
3. A path with a `{placeholder}` needs an id. Call the list endpoint on the
   same service first and take the id from there.

Reads only. Every mutating operation uses a different HTTP verb and the client
refuses those, so there is no way to change anything here. When the answer is
that something *should* change, give the user the exact `yc ...` command to run
themselves — writing that command out is correct, running it is not.

## Cloud Logging is not the only place logs live

A managed service keeps its own log stream, and it is not in Cloud Logging. An
empty `read_yc_logs` therefore means "not in Cloud Logging", never "there are no
logs" — the owning service has its own endpoint:

    /managed-postgresql/v1/clusters/{cluster_id}:logs
    /managed-clickhouse/v1/clusters/{cluster_id}:logs
    /managed-mysql/v1/clusters/{cluster_id}:logs

and the same shape for the other engines, with a `:stream_logs` variant beside
each. `find_yc_api` with the engine and `logs` returns them. The rule
generalises: **before saying something cannot be read, ask `find_yc_api`.**

Never answer by telling the user to install or run the `yc` CLI. If the CLI can
read it, so can this agent — the CLI is a client of the same REST API, and the
path is in the index.

## A past incident needs an explicit window

Every reader defaults to a window ending now. Asked about an incident on a given
date, **pass `from_time` and `to_time` around that date** — `window_minutes`
counts back from the present and will silently return nothing for anything
older, which reads as "no evidence" rather than "wrong window".

Retention differs by source, and an empty result means different things:

| Source | Kept | An empty result means |
| --- | --- | --- |
| Cloud Logging (`read_yc_logs`) | ~31 days | Beyond retention if the date is older — say so, do not call it "no evidence" |
| Managed-database logs (`:logs`) | per cluster | Try it: it is a separate store from Cloud Logging |
| Monitoring (`query_yc_metrics`) | months | Genuinely no data for that window, if the window was right |

So for an incident weeks back, metrics are usually the only surviving evidence,
and they are worth querying with the exact window before concluding anything.
Cluster operation history (`/managed-*/v1/clusters/{id}/operations`) outlives
logs too and shows maintenance and failovers.

## Answering well

- **Query first, then answer.** Infrastructure questions are answered from live
  reads, never from what you remember about Yandex Cloud in general.
- **Say what you actually read.** Name the folder, the resource ids, the time
  window. "CPU on `build-vm` averaged 140% over the last hour (2 cores)" beats
  "CPU looks high".
- **An empty list is an answer.** "No managed database clusters in this folder"
  is a finding; do not present it as a failure or a missing integration.
- **One folder at a time.** Every read is scoped to the configured folder.
  Reading a different one means passing `folderId` explicitly to
  `execute_yc_operation`; the folder list is at
  `/resource-manager/v1/folders` with `cloudId`.
- **A bare 404 usually means a wrong parameter**, not a missing resource — a
  single-resource read rejects `folderId` and `pageSize`. Re-read the path shape
  before telling the user the resource does not exist.

## Metrics

`query_yc_metrics` takes a Yandex Monitoring query: `"cpu_usage"{service="compute"}`,
narrowed by labels such as `resource_id`, `device`, `subcluster_name`.

**It is not PromQL.** `sum by (...)`, `rate(x[5m])` and `topk(...)` come back as
parse errors, not empty results. Aggregate with Yandex's own functions:

| Want | Write |
| --- | --- |
| Total across series | `series_sum("metric"{...})` |
| Average across series | `series_avg("metric"{...})` |
| Highest n series | `top_max(5, "metric"{...})` |
| Drop gaps | `drop_nan("metric"{...})` |
| Name the result | `alias(..., "label")` |

**The folder label is `folder_id`.** Writing `folderId` is accepted and matches
nothing, so it looks like the metric has no data rather than like a mistake.

**Metrics the user pushes themselves live under `service="custom"`, and the
default metric listing does not include them.** So `list_yc_metrics` returning
nothing is not evidence a metric is missing — search again with
`selectors='service="custom"'` before saying it does not exist. The tool says so
in its own output when a search comes back empty; believe it rather than
repeating the same call with a different name filter.
