# opensre-yc

Yandex Cloud support for [OpenSRE](https://github.com/Tracer-Cloud/opensre):
a read-only integration that lets the agent investigate Yandex Cloud
infrastructure, and a setup guide for running OpenSRE on Yandex AI Studio models.

Two independent parts, useful together or on their own:

- **Integration** — 16 tools plus alert routing, installed as a plugin. No
  change to OpenSRE itself.
- **LLM setup** — run the agent on YandexGPT, Alice AI, DeepSeek, Qwen, or
  GPT-OSS through Yandex AI Studio. Configuration only, no code. See
  [docs/yandex-ai-studio.md](docs/yandex-ai-studio.md).

## What the integration reads

Everything the agent needs to explain an incident on Yandex Cloud, over the REST
API, read-only:

- Metrics and metric discovery (Yandex Monitoring)
- Logs and log groups (Cloud Logging)
- Audit events (Audit Trails)
- Compute instances and serial-port diagnostics
- Managed Kubernetes clusters
- Managed databases (PostgreSQL, MySQL, ClickHouse, Redis, MongoDB, Kafka,
  OpenSearch, Greenplum)
- Serverless functions and containers
- Network load balancer health

Any other service is reachable through a generic REST reader backed by an index
of about 900 read endpoints, generated from Yandex's own protobuf definitions.
The agent never writes to Yandex Cloud: the client issues GET only, and every
mutating operation in the API uses a different verb.

## Install

Install alongside an existing OpenSRE checkout:

```
pip install -e /path/to/opensre-yc
```

Register the plugin once at startup, before the first investigation:

```python
import yc_plugin
yc_plugin.install()
```

The optional `logs` extra adds gRPC support for reading Cloud Logging entries:

```
pip install -e "/path/to/opensre-yc[logs]"
```

## Credentials

The integration reads credentials from the environment. Set a folder and one of
the authentication methods:

```
YC_FOLDER_ID=<folder-id>
```

Then one of:

- `YC_SA_KEY_FILE=/path/to/authorized_key.json` — service-account key file
- `YC_SA_KEY='{...}'` — the same key inline
- `YC_TOKEN=<oauth-token>` — OAuth token
- `YC_IAM_TOKEN=<iam-token>` — a ready IAM token
- `YC_USE_METADATA=true` — on a Yandex Cloud VM, use the attached service
  account. The folder is read from the metadata service, so `YC_FOLDER_ID` is
  optional in this mode.

The service account needs read access to the folder. For a full investigation,
the `viewer` role on the folder is enough.

## Alerts

Yandex Monitoring has no plain webhook. Notifications go to a Cloud Function,
which forwards the alert to OpenSRE. A ready-to-deploy bridge function is in
`yc_plugin/yandex_cloud/notification_function/`, with a deploy script and setup
notes.

## Compatibility

Built against OpenSRE's public extension points
(`register_external_tool_package`, `register_alert_source_detector`,
`register_alert_source_routing`, `register_alert_detail_fields`). It adds tools
and alert handling without modifying the OpenSRE core package.

Registering the integration in OpenSRE's own catalog — so that
`opensre integrations setup` and `opensre integrations verify` list it — needs a
small addition to OpenSRE that is not yet available. Until then, credentials are
supplied through the environment as described above.

## License

Apache-2.0. This project is derived from and depends on OpenSRE, which is also
Apache-2.0.
