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


#: Authentication methods, in the order they are offered.
_AUTH_CHOICES: list[tuple[str, str]] = [
    ("metadata", "Instance service account (running on a Yandex Cloud VM)"),
    ("sa_key_file", "Service-account key file"),
    ("sa_key", "Service-account key, pasted"),
    ("oauth", "OAuth token"),
    ("iam", "IAM token (short-lived)"),
]

_YES_NO: list[tuple[str, str]] = [("yes", "Yes"), ("no", "No")]

DEFAULT_MODEL = "gpt-oss-120b"


def _index_of(options: list[tuple[str, str]], value: Any, fallback: int = 0) -> int:
    """Return where *value* sits among *options*, so a re-run starts where it left off."""
    keys = [key for key, _ in options]
    return keys.index(value) if value in keys else fallback


def _yes_no_default(previous: dict[str, Any] | None) -> int:
    """Default a yes/no question to what was chosen last time."""
    return 0 if previous else 1


def configure(_args: list[str]) -> int:
    """Interactive setup. Writes the config file and reports where it went."""
    print("Yandex Cloud plugin setup\n")

    # Re-running is a fresh configuration, but every question starts from the
    # previous answer — otherwise changing one setting means retyping a
    # service-account key.
    previous = config.load()

    auth = _choose(
        "How should the plugin authenticate?",
        _AUTH_CHOICES,
        default_index=_index_of(_AUTH_CHOICES, previous.get("auth")),
    )

    saved: dict[str, Any] = {"auth": auth}

    if auth == "metadata":
        saved["folder_id"] = _prompt(
            "Folder ID (blank to read it from the instance metadata)",
            str(previous.get("folder_id", "")),
        )
    else:
        saved["folder_id"] = _prompt("Folder ID", str(previous.get("folder_id", "")))
        saved["cloud_id"] = _prompt("Cloud ID (optional)", str(previous.get("cloud_id", "")))

    if auth == "sa_key_file":
        saved["sa_key_file"] = _prompt(
            "Path to the authorized key JSON", str(previous.get("sa_key_file", ""))
        )
    elif auth == "sa_key":
        saved["sa_key"] = _secret("Authorized key JSON", previous.get("sa_key", ""))
    elif auth == "oauth":
        saved["oauth_token"] = _secret("OAuth token", previous.get("oauth_token", ""))
    elif auth == "iam":
        saved["iam_token"] = _secret("IAM token", previous.get("iam_token", ""))

    previous_llm = previous.get("llm") if isinstance(previous.get("llm"), dict) else None
    if (
        _choose(
            "Also run OpenSRE's language model on Yandex AI Studio?",
            _YES_NO,
            default_index=_yes_no_default(previous_llm),
        )
        == "yes"
    ):
        model = _prompt("Model", str((previous_llm or {}).get("model") or DEFAULT_MODEL))
        saved["llm"] = {"enabled": True, "model": model}

    kubernetes = _kubernetes_step(saved, previous)
    if kubernetes:
        saved["kubernetes"] = kubernetes

    path = config.save(saved)
    print(f"\nSaved to {path}")
    print("Run investigations with:  opensre-yc run investigate -i <alert.json>")
    return 0


def _secret(label: str, previous: Any) -> str:
    """Prompt for a secret, keeping the stored one when the answer is blank."""
    stored = str(previous or "")
    suffix = " (blank to keep the saved one)" if stored else ""
    return _prompt(f"{label}{suffix}", stored, secret=True)


def _kubernetes_step(answers: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any] | None:
    """Offer to connect a Managed Kubernetes cluster, returning what to save.

    Nothing is registered here: OpenSRE's own ``kubernetes`` integration reads
    workloads, and it only needs a kubeconfig, which the plugin assembles at
    startup from what is saved now.
    """
    previous_k8s = (
        previous.get("kubernetes") if isinstance(previous.get("kubernetes"), dict) else None
    )
    if (
        _choose(
            "Read workloads from a Managed Kubernetes cluster?",
            _YES_NO,
            default_index=_yes_no_default(previous_k8s),
        )
        != "yes"
    ):
        return None

    clusters = _list_clusters(answers)
    if clusters is None:
        return None

    options = [
        (access.cluster_id, f"{access.name} ({access.status or 'unknown'})")
        for access in clusters
    ]
    chosen_id = _choose(
        "Which cluster?",
        options,
        default_index=_index_of(options, (previous_k8s or {}).get("cluster_id")),
    )
    chosen = next(access for access in clusters if access.cluster_id == chosen_id)

    _report_reachability(chosen)
    return {"enabled": True, **chosen.as_config()}


def _list_clusters(answers: dict[str, Any]) -> list[Any] | None:
    """Return the folder's clusters, or None with an explanation printed."""
    from yc_plugin.yandex_cloud.availability import client_from_params
    from yc_plugin.yc_mk8s import kubeconfig

    client = client_from_params(_credentials_from(answers))
    if client is None:
        print("  Cannot list clusters: the credentials entered are not usable yet.")
        return None

    try:
        clusters = kubeconfig.list_clusters(client)
    except Exception as exc:  # noqa: BLE001 - the reason is shown, not raised
        print(f"  Cannot list clusters: {exc}")
        return None

    if not clusters:
        print("  No Managed Kubernetes clusters in this folder.")
        return None
    return clusters


def _credentials_from(answers: dict[str, Any]) -> dict[str, Any]:
    """Turn the answers given so far into the shape the REST client expects."""
    return {
        "folder_id": answers.get("folder_id", ""),
        "cloud_id": answers.get("cloud_id", ""),
        "sa_key_file": answers.get("sa_key_file", ""),
        "sa_key": answers.get("sa_key", ""),
        "oauth_token": answers.get("oauth_token", ""),
        "iam_token": answers.get("iam_token", ""),
        "use_metadata": answers.get("auth") == "metadata",
    }


def _report_reachability(access: Any) -> None:
    """Say up front when the cluster will not be reachable from where this runs."""
    from yc_plugin import metadata
    from yc_plugin.yc_mk8s import kubeconfig

    on_instance = metadata.is_available()
    endpoint = kubeconfig.choose_endpoint(access, on_instance=on_instance)
    if endpoint:
        print(f"  Will connect to {endpoint}")
        return
    print(f"  {kubeconfig.unreachable_reason(access, on_instance=on_instance)}")


def run(args: list[str]) -> int:
    """Install the plugin, then delegate to the OpenSRE CLI.

    OpenSRE is imported first, and the order is not incidental. It ships a
    top-level ``platform`` package that shadows the standard library module of
    the same name, and an editable install only puts its directory on the path
    once one of its own modules is imported. Installing the plugin first means
    the plugin's imports reach OpenSRE before that has happened, ``platform``
    binds to the standard library, and every import beneath it fails — which
    made ``opensre-yc run`` work only from inside the OpenSRE checkout.
    """
    try:
        from surfaces.cli.app import main as opensre_main
    except ImportError:
        print("OpenSRE is not importable from here. Install it in the same environment.")
        return 1

    import yc_plugin

    yc_plugin.install()

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
