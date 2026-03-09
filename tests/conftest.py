"""Shared test fixtures."""

import pytest

from octopus_export_optimizer.config.settings import (
    AppSettings,
    BatterySettings,
    ThresholdSettings,
)
from octopus_export_optimizer.storage.database import Database


@pytest.fixture
def db():
    """In-memory SQLite database with migrations applied."""
    database = Database(":memory:")
    database.connect()
    yield database
    database.close()


@pytest.fixture
def thresholds():
    """Default threshold settings for tests."""
    return ThresholdSettings()


@pytest.fixture
def battery():
    """Default battery settings for tests."""
    return BatterySettings()
