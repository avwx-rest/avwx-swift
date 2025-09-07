"""Module defining data structures for FAA FNS messages and NOTAMs."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, auto


class NotamStatus(StrEnum):
    """Enumeration for NOTAM status."""

    ACTIVE = auto()
    CANCELLED = auto()
    EXPIRED = auto()


@dataclass
class FnsMessage:
    id: int
    correlation_id: int
    issued: datetime
    updated: datetime
    valid_from: datetime
    valid_to: datetime
    classification: str
    location_designator: str

    @classmethod
    def from_dict(cls, correlation_id: int, data: dict) -> "FnsMessage":
        """Create an instance from a dictionary."""
        raise NotImplementedError


class FnsNotam(FnsMessage):
    """A NOTAM message from the FAA FNS system."""

    text: str
    status: NotamStatus
    accountability: str
    aixm_message: str

    @classmethod
    def from_dict(cls, correlation_id: int, data: dict) -> "FnsNotam":
        """Create an instance from a dictionary."""
        raise NotImplementedError
