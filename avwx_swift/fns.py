"""The FNS NOTAM Distribution Service over SWIM.

FNS-NDS publishes one AIXM message per NOTAM event onto the subscription's queue. This
module turns those messages into `Notam` objects, and is the push counterpart to `fil`,
which pulls the same data as a bulk snapshot.

Two differences from the bulk file shape the consumer:

**Messages carry a status.** A NOTAM can arrive PUBLISHED, ACTIVE, or CANCELLED. A bulk
snapshot only ever holds active NOTAMs, so a consumer built against `fil` has no notion of
withdrawal — but over the live feed, a cancellation is the only signal that a NOTAM should
be removed.

**Not every message parses.** `Notam.from_fil` raises on a substantial share of live
traffic; the shapes it cannot read are collected in `tests/data/unparsed_notams`. Rather
than raise mid-drain, `read_message` returns the failure attached to the message so a
consumer can record it and keep going.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Self

import xmltodict

from avwx_swift.fil import _NS_MAP
from avwx_swift.jms import DEFAULT_BATCH_SIZE, DEFAULT_IDLE_TIMEOUT, JmsConfig, JmsService
from avwx_swift.notam import Notam

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType

    from avwx_swift.jms import JmsMessage

log = logging.getLogger(__name__)

#: The root element of every FNS message, the same envelope the bulk file repeats.
MESSAGE_ROOT = "AIXMBasicMessage"

#: Solace message properties FNS sets. The status one drives whether a NOTAM is stored or
#: removed, so it is worth reading from here rather than parsing it out of the topic.
STATUS_PROPERTY = "us_gov_dot_faa_aim_fns_nds_NOTAMStatus"
NUMBER_PROPERTY = "us_gov_dot_faa_aim_fns_nds_NOTAMNumber"
LOCATION_PROPERTY = "us_gov_dot_faa_aim_fns_nds_LocationDesignator"
CORRELATION_PROPERTY = "us_gov_dot_faa_aim_fns_nds_CorrelationID"
SOURCE_TYPE_PROPERTY = "us_gov_dot_faa_aim_fns_nds_SourceType"


class MessageStatus(StrEnum):
    """What the feed says has happened to a NOTAM.

    Distinct from `message.NotamStatus`, which describes a NOTAM's own lifecycle. This is
    the status of the *message*, and uses the feed's own spellings.
    """

    PUBLISHED = "PUBLISHED"
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: str | None) -> Self:
        """Read a status, falling back to UNKNOWN rather than raising on a new one."""
        try:
            return cls(str(value).upper())
        except ValueError:
            log.warning("Unrecognized NOTAM message status %r", value)
            return cls.UNKNOWN

    @property
    def is_withdrawal(self) -> bool:
        """Whether this message means the NOTAM should be removed."""
        return self is MessageStatus.CANCELLED


@dataclass(frozen=True, slots=True)
class FnsMessage:
    """One feed message, parsed as far as it could be.

    `notam` is None when parsing failed; `error` then says why and `payload` holds the
    original bytes so the message can be kept for repair.
    """

    status: MessageStatus
    payload: str
    properties: dict[str, str] = field(default_factory=dict)
    notam: Notam | None = None
    error: str | None = None

    @property
    def is_withdrawal(self) -> bool:
        return self.status.is_withdrawal

    @property
    def correlation_id(self) -> str | None:
        return self.properties.get(CORRELATION_PROPERTY)

    @property
    def label(self) -> str:
        """A readable identifier for a message, usable even when it did not parse."""
        location = self.properties.get(LOCATION_PROPERTY) or "?"
        number = self.properties.get(NUMBER_PROPERTY) or "?"
        return f"{location} {number}"


def parse_payload(payload: str) -> Notam:
    """Parse one message body into a `Notam`.

    Raises:
        KeyError: if the body is not an AIXM basic message.
        Exception: whatever `Notam.from_fil` raises on a shape it cannot read.
    """
    root = xmltodict.parse(payload, process_namespaces=True, namespaces=_NS_MAP)[MESSAGE_ROOT]
    return Notam.from_fil(root)


def read_message(message: JmsMessage) -> FnsMessage:
    """Turn a queue message into an `FnsMessage`, recording failures instead of raising.

    A drain should not stop because one message is malformed, and the payload is worth
    keeping when it does — see this module's docstring.
    """
    status = MessageStatus.parse(message.properties.get(STATUS_PROPERTY))
    try:
        notam = parse_payload(message.payload)
    except Exception as exc:  # noqa: BLE001 - the feed defines its own shapes
        return FnsMessage(
            status=status,
            payload=message.payload,
            properties=message.properties,
            error=f"{type(exc).__name__}: {exc}",
        )
    return FnsMessage(status=status, payload=message.payload, properties=message.properties, notam=notam)


class FnsSubscription:
    """A held subscription to FNS-NDS.

    Thin over `JmsService`: the same session and acknowledgement rules, with NOTAM parsing
    applied on the way out.

    ```python
    with FnsSubscription(JmsConfig.from_env()) as feed:
        messages = list(feed.drain())
        store(messages)
        feed.acknowledge_all()
    ```
    """

    def __init__(self, config: JmsConfig) -> None:
        self.service = JmsService(config)

    def __enter__(self) -> Self:
        self.service.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.service.close()

    def connect(self) -> None:
        self.service.connect()

    def drain(self, limit: int = DEFAULT_BATCH_SIZE, timeout: int = DEFAULT_IDLE_TIMEOUT) -> Iterator[FnsMessage]:
        """Yield parsed messages until the queue goes quiet or `limit` is reached."""
        for message in self.service.drain(limit, timeout):
            yield read_message(message)

    def acknowledge_all(self) -> int:
        """Acknowledge everything drained so far. Call only once the work is durably done."""
        return self.service.acknowledge_all()

    def close(self) -> None:
        self.service.close()
