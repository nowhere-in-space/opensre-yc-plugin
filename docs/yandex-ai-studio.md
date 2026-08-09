# Running OpenSRE on Yandex AI Studio

OpenSRE can use [Yandex AI Studio](https://yandex.cloud/en/services/ai-studio)
as its language model without any change to the OpenSRE code. Yandex AI Studio
exposes an OpenAI-compatible API, and OpenSRE already ships an OpenAI-compatible
provider (`ollama`) whose endpoint is configurable. Pointing that endpoint at
Yandex is enough to run the agent on YandexGPT, Alice AI, DeepSeek, Qwen, or the
GPT-OSS models hosted in your folder.

This is useful when data-residency rules keep production logs inside Russia and
sending them to Anthropic or OpenAI is not an option.

## Requirements

- A Yandex Cloud folder with AI Studio enabled.
- A service account with the `ai.languageModels.user` role.
- An API key for that service account, or an IAM token if you run OpenSRE on a
  Yandex Cloud VM.
- OpenSRE installed and working with any provider.

## Get an API key

1. In the console, open **Identity and Access Management → Service accounts**
   and create one (or reuse an existing account).
2. Assign it the `ai.languageModels.user` role on the folder that holds the
   models.
3. Open the service account, select **Create new key → API key**, and copy the
   secret. It is shown once.

The folder ID is in the console URL, or from the CLI:

```
yc config get folder-id
```

## Configure OpenSRE

Set four environment variables. Put them in your OpenSRE `.env` file or export
them in the shell that starts the agent.

```
LLM_PROVIDER=ollama
OLLAMA_HOST=https://ai.api.cloud.yandex.net
OLLAMA_MODEL=gpt://<folder-id>/gpt-oss-120b
OLLAMA_API_KEY=<your AI Studio API key>
```

`OLLAMA_HOST` becomes the base URL `https://ai.api.cloud.yandex.net/v1`, which is
the AI Studio OpenAI-compatible endpoint. `OLLAMA_API_KEY` is sent as the bearer
token. `OLLAMA_MODEL` is passed through unchanged, so it must be a full model
URI, described next.

## Choosing a model

AI Studio addresses models by URI, not by short name:

```
gpt://<folder-id>/<model>
```

The models available in a standard folder, with their context windows:

| Model | URI suffix | Context |
| --- | --- | --- |
| GPT-OSS 120B | `gpt-oss-120b` | 128k |
| GPT-OSS 20B | `gpt-oss-20b` | 128k |
| Alice AI LLM | `aliceai-llm` | 128k |
| Alice AI LLM Flash | `aliceai-llm-flash` | 64k |
| DeepSeek V4 Flash | `deepseek-v4-flash` | 1M |
| Qwen3 235B | `qwen3-235b-a22b-fp8` | 256k |
| Qwen3.6 35B | `qwen3.6-35b-a3b` | 256k |
| YandexGPT Pro 5.1 | `yandexgpt-5.1` | 32k |
| YandexGPT Lite 5 | `yandexgpt-5-lite` | 32k |

Prefer a model with a context window of 128k or larger. Through the `ollama`
provider OpenSRE assumes a 128k window for any model it does not recognise, and
an investigation that accumulates tool output can exceed a 32k model mid-run.
`gpt-oss-120b`, `aliceai-llm`, and `deepseek-v4-flash` are all safe. Avoid
`yandexgpt-5.1` and `yandexgpt-5-lite` for long investigations.

To pin a model version instead of the default, append it to the URI:

```
OLLAMA_MODEL=gpt://<folder-id>/gpt-oss-120b/latest
```

## Verify

Check that the provider is configured:

```
opensre doctor
```

The `llm_provider` line should read `provider=ollama`. Then run an investigation
against a sample alert:

```
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
```

A successful run ends with a diagnosis rather than an authentication error.

## Authentication on a Yandex Cloud VM

If OpenSRE runs on a Yandex Cloud VM, you can authenticate with the instance's
service account instead of a stored API key. Fetch an IAM token from the
metadata service and use it in place of the API key:

```
IAM_TOKEN=$(curl -s -H "Metadata-Flavor: Google" \
  "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token" \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

export OLLAMA_API_KEY="$IAM_TOKEN"
```

The VM's attached service account still needs the `ai.languageModels.user` role.
An IAM token expires within twelve hours, so for a long-running deployment an API
key is simpler. A token works well for a one-off run or a wrapper that refreshes
it.

## Limitations

Running through the `ollama` provider is a configuration-only path, so a few
provider-specific features do not apply:

- **One model for every role.** OpenSRE normally uses a smaller model for
  classification and a larger one for reasoning. The `ollama` provider uses a
  single model for all roles, so classification runs on the same model you set
  in `OLLAMA_MODEL`.
- **Context window is assumed, not exact.** OpenSRE treats the model as having a
  128k window. This is why a model with a smaller real window can overflow. It
  does not affect models at 128k or above.
- **Cost reporting is unavailable.** The `/cost` command shows no price for a
  Yandex model, because the model is not in the built-in pricing table.
- **Prompt-logging opt-out is not set.** A native provider can send
  `x-data-logging-enabled: false` so Yandex does not retain prompt content. This
  path cannot set that header. If prompt retention matters, review the AI Studio
  data-processing terms for your account.
- **A metadata token is minted once per process.** When `opensre-yc configure`
  is set to authenticate from the instance metadata service, the token is
  fetched while the plugin installs and stays in the environment for the life of
  the process. That is fine for a run that finishes in minutes, but a process
  that outlives the token — a gateway left running for days — starts failing
  with `401` and has to be restarted. Use an API key for anything long-lived.

These are the trade-offs of a zero-code setup. A native Yandex provider would
remove them, at the cost of changes to OpenSRE itself.

## Troubleshooting

**`401` or `unauthorized`.** The API key is wrong, or the service account lacks
the `ai.languageModels.user` role on the folder. Confirm the role binding and
that the key belongs to that account.

**`400` mentioning the model.** `OLLAMA_MODEL` is not a full URI. It must start
with `gpt://` and include the folder ID, for example
`gpt://b1gxxxxxxxxxxxxxxxxx/gpt-oss-120b`.

**The agent answers in the wrong language or format.** The model choice drives
this. Alice AI and YandexGPT models follow Russian instructions closely;
GPT-OSS and Qwen are stronger at structured output. Try a different model from
the table above.

**Requests time out on a VM.** Check that the VM can reach
`ai.api.cloud.yandex.net` on port 443. AI Studio is a public endpoint and needs
outbound HTTPS.
