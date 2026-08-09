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

## Setup

Configure once and the settings are saved to `~/.opensre-yc/config.json` (owner
readable only). No environment variables to keep:

```
opensre-yc configure
```

The wizard asks how to authenticate and, optionally, whether to run OpenSRE's
language model on Yandex AI Studio. Then run investigations through the plugin:

```
opensre-yc run investigate -i <alert.json>
```

`opensre-yc run` installs the plugin and hands the rest of the command to
OpenSRE, so the plugin is active without changing OpenSRE's entry point.

### Authentication methods

- **Instance service account** — on a Yandex Cloud VM. Nothing to store: the
  folder and the token both come from the metadata service.
- **Service-account key** — a key file, or the key pasted during setup.
- **OAuth token** or **IAM token**.

Environment variables (`YC_FOLDER_ID`, `YC_SA_KEY_FILE`, `YC_TOKEN`,
`YC_IAM_TOKEN`, `YC_USE_METADATA`) still work and override the saved config field
by field, which is convenient in CI or a container.

The service account needs read access to the folder. The `viewer` role is
enough for a full investigation.

## Language model on Yandex AI Studio

Yandex AI Studio speaks the OpenAI API, so OpenSRE can run on YandexGPT, Alice
AI, DeepSeek, Qwen, or GPT-OSS with no code change. Enable it during
`opensre-yc configure`, and the plugin points OpenSRE's OpenAI-compatible
provider at Yandex on every run.

On a Yandex Cloud VM the bearer token is minted from the instance metadata
service automatically, so there is nothing to store or rotate. The full setup,
model list, and trade-offs are in [docs/yandex-ai-studio.md](docs/yandex-ai-studio.md).

## Alerts

Yandex Monitoring has no plain webhook. Notifications go to a Cloud Function,
which forwards the alert to OpenSRE. A ready-to-deploy bridge function is in
`yc_plugin/yandex_cloud/notification_function/`, with a deploy script and setup
notes.

## Tests

The suite runs against a source checkout of OpenSRE, because it exercises the
plugin through OpenSRE's own tool registry and alert routing:

```
pip install -e ".[dev]"
OPENSRE_ROOT=/path/to/opensre pytest
```

`OPENSRE_ROOT` can be omitted if the checkout sits beside this repository. It
has to be a checkout rather than an installed package: OpenSRE ships a
top-level `platform` package that shadows the standard library module of the
same name, so its directory must precede the standard library on `sys.path`,
which `conftest.py` arranges.

Configuration is redirected to a temporary directory for the run, so the tests
cannot read or write a real `~/.opensre-yc/config.json`.

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
