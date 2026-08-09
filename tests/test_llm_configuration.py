"""Pointing OpenSRE's language model at Yandex AI Studio.

AI Studio speaks the OpenAI API, and OpenSRE's ``ollama`` provider takes its
endpoint from the environment, so a model is a matter of setting four variables
rather than adding a provider. That makes this code short and easy to get
subtly wrong: it writes to the process environment, and a wrong value does not
fail here — it fails later, inside an investigation, as an authentication error
from a vendor nobody was expecting.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

import yc_plugin
from yc_plugin import metadata

_LLM_ENV = ("LLM_PROVIDER", "OLLAMA_HOST", "OLLAMA_MODEL", "OLLAMA_API_KEY")

FOLDER = "b1gapqc3kb2vii7cs9i3"


@pytest.fixture(autouse=True)
def _isolated_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the variables so pytest restores them, then start from empty.

    ``_configure_llm`` writes to ``os.environ`` directly. Setting each name
    through monkeypatch first is what makes the write reversible: without it a
    variable created by the code would outlive the test.
    """
    for name in _LLM_ENV:
        monkeypatch.setenv(name, "sentinel")
        monkeypatch.delenv(name)


def _settings(**overrides: Any) -> Any:
    """Return an ``llm_settings`` replacement reporting the model as enabled."""
    payload: dict[str, Any] = {"enabled": True, "model": "gpt-oss-120b"}
    payload.update(overrides)

    def _llm_settings() -> dict[str, Any]:
        return payload

    return _llm_settings


def _credentials(**overrides: Any) -> Any:
    """Return a ``resolved_credentials`` replacement with the given fields."""
    payload: dict[str, Any] = {"folder_id": FOLDER, "iam_token": "t1.configured"}
    payload.update(overrides)

    def _resolved_credentials() -> dict[str, Any]:
        return payload

    return _resolved_credentials


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    settings: Any = None,
    credentials: Any = None,
) -> None:
    from yc_plugin import config

    monkeypatch.setattr(config, "llm_settings", settings or _settings())
    monkeypatch.setattr(config, "resolved_credentials", credentials or _credentials())
    yc_plugin._configure_llm()


class TestWhatItSetsUp:
    def test_it_points_the_ollama_provider_at_ai_studio(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch)

        assert os.environ["LLM_PROVIDER"] == "ollama"
        assert os.environ["OLLAMA_HOST"] == "https://ai.api.cloud.yandex.net"

    def test_the_model_is_a_full_folder_scoped_uri(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AI Studio names models by URI; a bare name is rejected."""
        _configure(monkeypatch)

        assert os.environ["OLLAMA_MODEL"] == f"gpt://{FOLDER}/gpt-oss-120b"

    def test_a_chosen_model_is_carried_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, settings=_settings(model="yandexgpt-5.1"))

        assert os.environ["OLLAMA_MODEL"] == f"gpt://{FOLDER}/yandexgpt-5.1"

    def test_a_missing_model_falls_back_to_a_working_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, settings=_settings(model=""))

        assert os.environ["OLLAMA_MODEL"] == f"gpt://{FOLDER}/gpt-oss-120b"


class TestWhichCredentialBecomesTheBearer:
    def test_an_iam_token_is_used_as_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch)

        assert os.environ["OLLAMA_API_KEY"] == "t1.configured"

    def test_an_oauth_token_is_used_when_there_is_no_iam_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, credentials=_credentials(iam_token="", oauth_token="y0_x"))

        assert os.environ["OLLAMA_API_KEY"] == "y0_x"

    def test_on_an_instance_the_token_is_minted_from_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing is stored on a VM: the instance account supplies the token."""

        def _fetch_token() -> Any:
            return metadata.MetadataToken("t1.from-metadata", 3000)

        monkeypatch.setattr(metadata, "fetch_token", _fetch_token)
        _configure(
            monkeypatch,
            credentials=_credentials(iam_token="", use_metadata=True),
        )

        assert os.environ["OLLAMA_API_KEY"] == "t1.from-metadata"

    def test_a_configured_token_wins_over_the_instance_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _never() -> Any:
            raise AssertionError("metadata should not be consulted")

        monkeypatch.setattr(metadata, "fetch_token", _never)
        _configure(monkeypatch, credentials=_credentials(use_metadata=True))

        assert os.environ["OLLAMA_API_KEY"] == "t1.configured"


class TestWhenItDeclinesToConfigureAnything:
    def test_nothing_happens_when_the_model_was_not_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Declining the model during setup must leave the environment alone."""

        def _llm_settings() -> dict[str, Any]:
            return {}

        _configure(monkeypatch, settings=_llm_settings)

        assert not any(name in os.environ for name in _LLM_ENV)

    def test_a_missing_folder_configures_nothing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Half a configuration is worse than none: it fails inside a run."""
        _configure(monkeypatch, credentials=_credentials(folder_id=""))

        assert not any(name in os.environ for name in _LLM_ENV)
        assert "missing folder or credential" in caplog.text

    def test_a_missing_credential_configures_nothing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _configure(monkeypatch, credentials=_credentials(iam_token=""))

        assert not any(name in os.environ for name in _LLM_ENV)
        assert "missing folder or credential" in caplog.text

    def test_an_unreachable_metadata_service_configures_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Off an instance the address is unroutable; asking returns nothing."""

        def _fetch_token() -> Any:
            return None

        monkeypatch.setattr(metadata, "fetch_token", _fetch_token)
        _configure(
            monkeypatch,
            credentials=_credentials(iam_token="", use_metadata=True),
        )

        assert "OLLAMA_API_KEY" not in os.environ


class TestTheFolderOnAnInstance:
    def test_it_is_discovered_when_none_was_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fetch_folder_id() -> str:
            return "b1gdiscovered"

        monkeypatch.setattr(metadata, "fetch_folder_id", _fetch_folder_id)
        _configure(
            monkeypatch,
            credentials=_credentials(folder_id="", use_metadata=True),
        )

        assert os.environ["OLLAMA_MODEL"] == "gpt://b1gdiscovered/gpt-oss-120b"

    def test_a_configured_folder_skips_the_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _never() -> str:
            raise AssertionError("metadata should not be consulted")

        monkeypatch.setattr(metadata, "fetch_folder_id", _never)
        _configure(monkeypatch, credentials=_credentials(use_metadata=True))

        assert os.environ["OLLAMA_MODEL"] == f"gpt://{FOLDER}/gpt-oss-120b"
