"""Fixture backend for the Yandex Cloud tools.

Every Yandex Cloud tool accepts a ``yc_backend`` and short-circuits to it
instead of making a request, which is what lets a scenario serve an entire
incident's evidence without credentials or network.
"""

from tests.mock_yc_backend.backend import FixtureYandexCloudBackend, YandexCloudBackend

__all__ = ["FixtureYandexCloudBackend", "YandexCloudBackend"]
