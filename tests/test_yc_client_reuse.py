"""One client per credential set, reused for the whole run.

A tool receives its credentials injected into each call and builds a client
from them, so nothing carried over between calls: the IAM token cache lives on
the client, and the client did not outlive the call that made it. Every tool
call therefore minted a token, and on a VM that is a metadata round-trip each
time — around fifteen of them in an ordinary investigation.

What must not change is who gets which client: two different credential sets
must never share one, or a read would run against the wrong folder.
"""

from __future__ import annotations

from typing import Any

import pytest

from yc_plugin.yandex_cloud import availability
from yc_plugin.yandex_cloud.availability import (
    _CLIENT_CACHE_SIZE,
    client_from_params,
    reset_client_cache,
)

FOLDER = "b1gapqc3kb2vii7cs9i3"


def _credentials(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"folder_id": FOLDER, "iam_token": "t1.token"}
    base.update(overrides)
    return base


class TestTheClientIsReused:
    def test_the_same_credentials_get_the_same_client(self) -> None:
        first = client_from_params(_credentials())
        second = client_from_params(_credentials())

        assert first is not None
        assert first is second

    def test_the_token_is_minted_once_across_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point of the reuse: the token cache finally outlives a tool call."""
        mints = {"n": 0}

        def _mint(config: Any) -> Any:
            mints["n"] += 1
            from yc_plugin.yandex_cloud.auth import MintedToken

            return MintedToken("t1.minted", 3000)

        monkeypatch.setattr("yc_plugin.yandex_cloud.auth.mint_iam_token_with_ttl", _mint)

        for _ in range(5):
            client = client_from_params(_credentials(iam_token="", oauth_token="y0_x"))
            assert client is not None
            client.auth.token()

        assert mints["n"] == 1

    def test_a_cold_cache_mints_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reuse must not outlive an explicit reset, or tests would leak state."""
        mints = {"n": 0}

        def _mint(config: Any) -> Any:
            mints["n"] += 1
            from yc_plugin.yandex_cloud.auth import MintedToken

            return MintedToken("t1.minted", 3000)

        monkeypatch.setattr("yc_plugin.yandex_cloud.auth.mint_iam_token_with_ttl", _mint)

        for _ in range(2):
            client = client_from_params(_credentials(iam_token="", oauth_token="y0_x"))
            assert client is not None
            client.auth.token()
            reset_client_cache()

        assert mints["n"] == 2


class TestCredentialsAreNotShared:
    def test_a_different_folder_gets_its_own_client(self) -> None:
        first = client_from_params(_credentials())
        second = client_from_params(_credentials(folder_id="b1gother"))

        assert first is not second
        assert first is not None and second is not None
        assert first.config.folder_id == FOLDER
        assert second.config.folder_id == "b1gother"

    def test_a_different_token_gets_its_own_client(self) -> None:
        first = client_from_params(_credentials())
        second = client_from_params(_credentials(iam_token="t1.other"))

        assert first is not second

    def test_a_different_auth_mode_gets_its_own_client(self) -> None:
        by_token = client_from_params(_credentials())
        by_metadata = client_from_params({"use_metadata": True})

        assert by_token is not by_metadata

    def test_unusable_credentials_still_yield_nothing(self) -> None:
        """A missing folder is a setup problem; caching must not paper over it."""
        assert client_from_params({"iam_token": "t1.token"}) is None


class TestTheCacheStaysBounded:
    def test_a_rotating_token_does_not_grow_it_without_limit(self) -> None:
        for index in range(_CLIENT_CACHE_SIZE * 3):
            assert client_from_params(_credentials(iam_token=f"t1.{index}")) is not None

        assert len(availability._client_cache) == _CLIENT_CACHE_SIZE

    def test_the_least_recently_used_one_is_evicted(self) -> None:
        oldest = client_from_params(_credentials(iam_token="t1.oldest"))
        for index in range(_CLIENT_CACHE_SIZE - 1):
            client_from_params(_credentials(iam_token=f"t1.{index}"))

        # Still resident, and touching it makes it the most recent.
        assert client_from_params(_credentials(iam_token="t1.oldest")) is oldest

        client_from_params(_credentials(iam_token="t1.overflow"))

        assert client_from_params(_credentials(iam_token="t1.oldest")) is oldest
