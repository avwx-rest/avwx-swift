"""Tests for FilService.should_update property."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from avwx_swift.fil import FilService


def make_service(server_time: datetime | None = None, updated: datetime | None = None) -> FilService:
    """Helper to make a FilService with specific times."""
    service = FilService(url="example.com", user="user", cert_path=Path("/tmp/cert.pem"))
    service.server_time = server_time
    service.updated = updated
    return service


def test_should_update_no_server_time() -> None:
    """Should raise if server_time is None."""
    service = make_service(server_time=None, updated=None)
    with pytest.raises(ValueError, match="Server time has not been fetched."):
        _ = service.should_update


def test_should_update_no_updated() -> None:
    """Should return True if never updated."""
    service = make_service(server_time=datetime.now(UTC), updated=None)
    assert service.should_update is True


def test_should_update_updated_before_server_time() -> None:
    """Should return True if server is more recent than last update."""
    now = datetime.now(UTC)
    before = now - timedelta(hours=1)
    service = make_service(server_time=now, updated=before)
    assert service.should_update is True


def test_should_update_updated_equal_server_time() -> None:
    """Should return False if server time equals last update."""
    now = datetime.now(UTC)
    service = make_service(server_time=now, updated=now)
    assert service.should_update is False


def test_should_update_updated_after_server_time() -> None:
    """Should return False if last update is more recent than server time."""
    now = datetime.now(UTC)
    after = now + timedelta(hours=1)
    service = make_service(server_time=now, updated=after)
    assert service.should_update is False
