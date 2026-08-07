"""Persistent configuration for the Yandex Cloud plugin.

Configuration is written once by ``opensre-yc configure`` and read on every run,
so credentials do not have to live in the environment. The file is created with
owner-only permissions because it may hold a service-account key.

Environment variables still work and take precedence over the file, which keeps
CI and container overrides simple.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("OPENSRE_YC_CONFIG_DIR", "~/.opensre-yc")).expanduser()
CONFIG_PATH = CONFIG_DIR / "config.json"

#: Recognised authentication methods, in the order the wizard offers them.
AUTH_METHODS = ("metadata", "sa_key_file", "sa_key", "oauth", "iam")

#: Credential fields, mapped to the environment variable that overrides each.
_ENV_OVERRIDES = {
    "folder_id": "YC_FOLDER_ID",
    "cloud_id": "YC_CLOUD_ID",
    "sa_key_file": "YC_SA_KEY_FILE",
    "sa_key": "YC_SA_KEY",
    "oauth_token": "YC_TOKEN",
    "iam_token": "YC_IAM_TOKEN",
}


def load() -> dict[str, Any]:
    """Return the saved configuration, or an empty mapping when none exists."""
    try:
        return dict(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}


def save(config: dict[str, Any]) -> Path:
    """Write *config* with owner-only permissions and return the path."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    CONFIG_PATH.chmod(0o600)
    return CONFIG_PATH


def resolved_credentials() -> dict[str, Any]:
    """Return the credential fields tools consume, from the file then the env.

    The environment wins field by field, so a stored config can be overridden
    one value at a time without rewriting the file.
    """
    config = load()
    use_metadata = bool(config.get("auth") == "metadata" or config.get("use_metadata"))
    if os.environ.get("YC_USE_METADATA", "").strip().lower() in {"1", "true", "yes", "on"}:
        use_metadata = True

    credentials: dict[str, Any] = {
        field: str(config.get(field, "") or "") for field in _ENV_OVERRIDES
    }
    for field, env_name in _ENV_OVERRIDES.items():
        override = os.environ.get(env_name, "").strip()
        if override:
            credentials[field] = override

    credentials["use_metadata"] = use_metadata
    credentials["yc_backend"] = None
    return credentials


def llm_settings() -> dict[str, Any]:
    """Return the language-model settings, or an empty mapping when disabled."""
    llm = load().get("llm")
    return dict(llm) if isinstance(llm, dict) and llm.get("enabled") else {}
