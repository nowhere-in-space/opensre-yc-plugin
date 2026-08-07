"""Command line for the Yandex Cloud plugin: configure once, then run.

``opensre-yc configure`` walks through authentication and writes the config
file. ``opensre-yc run ...`` installs the plugin and hands the rest of the
command to OpenSRE, so the plugin is active without touching OpenSRE's entry
point. Any credential set here is stored in ``~/.opensre-yc/config.json`` with
owner-only permissions.
"""

from __future__ import annotations

import getpass
import sys
from typing import Any

from yc_plugin import config


def _prompt(label: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default and not secret else ""
    raw = getpass.getpass(f"{label}: ") if secret else input(f"{label}{suffix}: ")
    return raw.strip() or default


def _choose(label: str, options: list[tuple[str, str]], default_index: int = 0) -> str:
    print(f"\n{label}")
    for i, (_, text) in enumerate(options, 1):
        marker = " (default)" if i - 1 == default_index else ""
        print(f"  {i}. {text}{marker}")
    while True:
        raw = input(f"Choice [1-{len(options)}]: ").strip()
        if not raw:
            return options[default_index][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("Enter a number from the list.")


def configure(_args: list[str]) -> int:
    """Interactive setup. Writes the config file and reports where it went."""
    print("Yandex Cloud plugin setup\n")

    auth = _choose(
        "How should the plugin authenticate?",
        [
            ("metadata", "Instance service account (running on a Yandex Cloud VM)"),
            ("sa_key_file", "Service-account key file"),
            ("sa_key", "Service-account key, pasted"),
            ("oauth", "OAuth token"),
            ("iam", "IAM token (short-lived)"),
        ],
    )

    saved: dict[str, Any] = {"auth": auth}

    if auth == "metadata":
        saved["folder_id"] = _prompt(
            "Folder ID (blank to read it from the instance metadata)", ""
        )
    else:
        saved["folder_id"] = _prompt("Folder ID")
        saved["cloud_id"] = _prompt("Cloud ID (optional)")

    if auth == "sa_key_file":
        saved["sa_key_file"] = _prompt("Path to the authorized key JSON")
    elif auth == "sa_key":
        saved["sa_key"] = _prompt("Authorized key JSON", secret=True)
    elif auth == "oauth":
        saved["oauth_token"] = _prompt("OAuth token", secret=True)
    elif auth == "iam":
        saved["iam_token"] = _prompt("IAM token", secret=True)

    if _choose(
        "Also run OpenSRE's language model on Yandex AI Studio?",
        [("yes", "Yes"), ("no", "No")],
    ) == "yes":
        model = _prompt("Model", "gpt-oss-120b")
        saved["llm"] = {"enabled": True, "model": model}

    path = config.save(saved)
    print(f"\nSaved to {path}")
    print("Run investigations with:  opensre-yc run investigate -i <alert.json>")
    return 0


def run(args: list[str]) -> int:
    """Install the plugin, then delegate to the OpenSRE CLI."""
    import yc_plugin

    yc_plugin.install()

    try:
        from surfaces.cli.app import main as opensre_main
    except ImportError:
        print("OpenSRE is not importable from here. Install it in the same environment.")
        return 1

    sys.argv = ["opensre", *args]
    return int(opensre_main() or 0)


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        print("usage: opensre-yc {configure | run <opensre args...>}")
        return 0
    command, rest = argv[0], argv[1:]
    if command == "configure":
        return configure(rest)
    if command == "run":
        return run(rest)
    print(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
