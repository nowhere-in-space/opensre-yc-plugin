"""``opensre-yc configure`` — the one thing a person actually touches.

If the wizard writes the wrong file, or writes it readable by everyone, or
quietly saves an unusable credential set, the plugin does not work and the only
way to find out is by running an investigation and watching it fail. So these
assert what lands on disk for every authentication mode, not just that the
prompts appear.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest

from yc_plugin import cli, config


@pytest.fixture(autouse=True)
def _config_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the config at a per-test directory.

    The module resolves these at import time, so a test cannot rely on the
    environment variable alone.
    """
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "cfg" / "config.json")


def _answering(*answers: str) -> Any:
    """Return an ``input`` replacement that reads the given answers in order."""
    remaining = list(answers)

    def _input(prompt: str = "") -> str:
        if not remaining:
            raise AssertionError(f"wizard asked more than expected: {prompt!r}")
        return remaining.pop(0)

    return _input


def _secret_answering(*answers: str) -> Any:
    """Return a ``getpass`` replacement that reads the given answers in order."""
    remaining = list(answers)

    def _getpass(prompt: str = "") -> str:
        if not remaining:
            raise AssertionError(f"wizard asked for more secrets: {prompt!r}")
        return remaining.pop(0)

    return _getpass


def _run_wizard(
    monkeypatch: pytest.MonkeyPatch,
    answers: tuple[str, ...],
    secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    monkeypatch.setattr("builtins.input", _answering(*answers))
    monkeypatch.setattr(cli.getpass, "getpass", _secret_answering(*secrets))

    assert cli.configure([]) == 0
    return config.load()


class TestWhatEachAuthModeSaves:
    def test_the_instance_account_needs_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On a VM the folder comes from metadata, so a blank answer is correct."""
        saved = _run_wizard(monkeypatch, answers=("1", "", "2"))

        assert saved["auth"] == "metadata"
        assert saved["folder_id"] == ""
        assert "llm" not in saved

    def test_the_instance_account_still_accepts_an_explicit_folder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = _run_wizard(monkeypatch, answers=("1", "b1gexplicit", "2"))

        assert saved["folder_id"] == "b1gexplicit"

    def test_a_key_file_is_stored_by_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved = _run_wizard(
            monkeypatch, answers=("2", "b1gfolder", "b1ccloud", "/keys/sa.json", "2")
        )

        assert saved["auth"] == "sa_key_file"
        assert saved["sa_key_file"] == "/keys/sa.json"
        assert saved["cloud_id"] == "b1ccloud"

    def test_a_pasted_key_is_read_without_echoing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key pasted into a terminal must not be left on screen."""
        saved = _run_wizard(
            monkeypatch,
            answers=("3", "b1gfolder", "", "2"),
            secrets=('{"id": "aje1"}',),
        )

        assert saved["auth"] == "sa_key"
        assert saved["sa_key"] == '{"id": "aje1"}'

    def test_an_oauth_token_is_read_without_echoing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = _run_wizard(
            monkeypatch, answers=("4", "b1gfolder", "", "2"), secrets=("y0_secret",)
        )

        assert saved["auth"] == "oauth"
        assert saved["oauth_token"] == "y0_secret"

    def test_an_iam_token_is_read_without_echoing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = _run_wizard(
            monkeypatch, answers=("5", "b1gfolder", "", "2"), secrets=("t1.short",)
        )

        assert saved["auth"] == "iam"
        assert saved["iam_token"] == "t1.short"


class TestTheFileItWrites:
    def test_only_its_owner_can_read_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """It may hold a service-account key, so the mode is part of the contract."""
        _run_wizard(monkeypatch, answers=("1", "", "2"))

        mode = stat.S_IMODE(config.CONFIG_PATH.stat().st_mode)

        assert mode == 0o600

    def test_the_directory_is_created_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert not config.CONFIG_DIR.exists()

        _run_wizard(monkeypatch, answers=("1", "", "2"))

        assert config.CONFIG_PATH.is_file()

    def test_running_it_again_replaces_the_previous_answers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-running is a fresh configuration, not a merge onto the old one."""
        _run_wizard(monkeypatch, answers=("5", "b1gold", "", "2"), secrets=("t1.old",))

        saved = _run_wizard(monkeypatch, answers=("1", "b1gnew", "2"))

        assert saved["auth"] == "metadata"
        assert saved["folder_id"] == "b1gnew"
        assert "iam_token" not in saved


class TestTheLanguageModelSection:
    def test_declining_it_leaves_no_llm_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = _run_wizard(monkeypatch, answers=("1", "", "2"))

        assert "llm" not in saved
        assert config.llm_settings() == {}

    def test_accepting_it_stores_the_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved = _run_wizard(monkeypatch, answers=("1", "", "1", "yandexgpt-5.1"))

        assert saved["llm"] == {"enabled": True, "model": "yandexgpt-5.1"}
        assert config.llm_settings()["model"] == "yandexgpt-5.1"

    def test_an_empty_answer_takes_the_default_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = _run_wizard(monkeypatch, answers=("1", "", "1", ""))

        assert saved["llm"]["model"] == "gpt-oss-120b"


class TestChoosingFromAList:
    def test_an_empty_answer_takes_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", _answering(""))

        chosen = cli._choose("pick", [("a", "A"), ("b", "B")])

        assert chosen == "a"

    def test_the_default_can_be_any_position(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", _answering(""))

        chosen = cli._choose("pick", [("a", "A"), ("b", "B")], default_index=1)

        assert chosen == "b"

    def test_a_number_picks_that_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", _answering("2"))

        assert cli._choose("pick", [("a", "A"), ("b", "B")]) == "b"

    def test_nonsense_is_rejected_and_asked_again(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Out-of-range and non-numeric answers must not fall through to a default."""
        monkeypatch.setattr("builtins.input", _answering("nine", "0", "3", "2"))

        chosen = cli._choose("pick", [("a", "A"), ("b", "B")])

        assert chosen == "b"
        assert capsys.readouterr().out.count("Enter a number from the list.") == 3


class TestPrompting:
    def test_it_trims_what_was_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", _answering("  b1gfolder  "))

        assert cli._prompt("Folder ID") == "b1gfolder"

    def test_an_empty_answer_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", _answering(""))

        assert cli._prompt("Model", "gpt-oss-120b") == "gpt-oss-120b"

    def test_a_secret_is_not_shown_the_default_either(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli.getpass, "getpass", _secret_answering("y0_secret"))

        assert cli._prompt("OAuth token", secret=True) == "y0_secret"
        assert "y0_secret" not in capsys.readouterr().out


class TestCommandDispatch:
    def test_no_arguments_prints_usage(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli.sys, "argv", ["opensre-yc"])

        assert cli.main() == 0
        assert "usage:" in capsys.readouterr().out

    @pytest.mark.parametrize("flag", ["-h", "--help"])
    def test_help_prints_usage(
        self, flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli.sys, "argv", ["opensre-yc", flag])

        assert cli.main() == 0
        assert "usage:" in capsys.readouterr().out

    def test_an_unknown_command_fails_loudly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli.sys, "argv", ["opensre-yc", "investigate"])

        assert cli.main() == 2
        assert "unknown command: investigate" in capsys.readouterr().out

    def test_configure_is_dispatched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        def _configure(args: list[str]) -> int:
            seen["args"] = args
            return 0

        monkeypatch.setattr(cli, "configure", _configure)
        monkeypatch.setattr(cli.sys, "argv", ["opensre-yc", "configure"])

        assert cli.main() == 0
        assert seen["args"] == []

    def test_run_gets_everything_after_the_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def _run(args: list[str]) -> int:
            seen["args"] = args
            return 0

        monkeypatch.setattr(cli, "run", _run)
        monkeypatch.setattr(cli.sys, "argv", ["opensre-yc", "run", "investigate", "-i", "a.json"])

        assert cli.main() == 0
        assert seen["args"] == ["investigate", "-i", "a.json"]


class TestRunDelegatesToOpenSRE:
    def test_the_plugin_is_installed_before_opensre_starts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Order matters: OpenSRE reads the registry as it starts."""
        order: list[str] = []

        def _install() -> None:
            order.append("install")

        def _opensre_main() -> int:
            order.append("opensre")
            return 0

        import yc_plugin

        monkeypatch.setattr(yc_plugin, "install", _install)
        monkeypatch.setattr("surfaces.cli.app.main", _opensre_main)

        assert cli.run(["investigate"]) == 0
        assert order == ["install", "opensre"]

    def test_the_arguments_are_handed_over_as_opensres_own(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def _opensre_main() -> int:
            seen["argv"] = list(cli.sys.argv)
            return 0

        import yc_plugin

        monkeypatch.setattr(yc_plugin, "install", lambda: None)
        monkeypatch.setattr("surfaces.cli.app.main", _opensre_main)

        cli.run(["investigate", "-i", "alert.json"])

        assert seen["argv"] == ["opensre", "investigate", "-i", "alert.json"]

    def test_a_nonzero_exit_from_opensre_is_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yc_plugin

        monkeypatch.setattr(yc_plugin, "install", lambda: None)
        monkeypatch.setattr("surfaces.cli.app.main", lambda: 3)

        assert cli.run(["investigate"]) == 3

    def test_a_missing_opensre_says_so_instead_of_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Installing the plugin beside the wrong environment is an easy mistake."""
        import sys
        import types

        import yc_plugin

        monkeypatch.setattr(yc_plugin, "install", lambda: None)
        # ``from ... import main`` raises ImportError when the name is absent.
        empty = types.ModuleType("surfaces.cli.app")
        monkeypatch.setitem(sys.modules, "surfaces.cli.app", empty)

        assert cli.run(["investigate"]) == 1
        assert "not importable" in capsys.readouterr().out
