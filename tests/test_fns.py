"""Reading FNS-NDS messages."""

import json
from pathlib import Path

import pytest

from avwx_swift.fns import (
    STATUS_PROPERTY,
    FnsMessage,
    MessageStatus,
    parse_payload,
    read_message,
)
from avwx_swift.jms import JmsMessage

UNPARSED = Path(__file__).parent / "data" / "unparsed_notams"


class TestMessageStatus:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("PUBLISHED", MessageStatus.PUBLISHED),
            ("ACTIVE", MessageStatus.ACTIVE),
            ("CANCELLED", MessageStatus.CANCELLED),
            ("cancelled", MessageStatus.CANCELLED),
        ],
    )
    def test_reads_the_feed_spellings(self, value: str, expected: MessageStatus) -> None:
        assert MessageStatus.parse(value) is expected

    def test_an_unrecognized_status_does_not_raise(self) -> None:
        """A new status should degrade the consumer, not stop the drain."""
        assert MessageStatus.parse("SOMETHING_NEW") is MessageStatus.UNKNOWN
        assert MessageStatus.parse(None) is MessageStatus.UNKNOWN

    def test_only_cancellation_is_a_withdrawal(self) -> None:
        assert MessageStatus.CANCELLED.is_withdrawal
        assert not MessageStatus.ACTIVE.is_withdrawal
        assert not MessageStatus.PUBLISHED.is_withdrawal
        assert not MessageStatus.UNKNOWN.is_withdrawal


class TestReadMessage:
    def test_a_failure_is_attached_not_raised(self) -> None:
        """One unreadable message must not end a drain, and its payload is worth keeping."""
        message = read_message(
            JmsMessage(payload="<nonsense/>", properties={STATUS_PROPERTY: "ACTIVE"})
        )

        assert message.notam is None
        assert message.error
        assert message.payload == "<nonsense/>"
        assert message.status is MessageStatus.ACTIVE

    def test_status_comes_from_the_message_property(self) -> None:
        message = read_message(JmsMessage(payload="<nonsense/>", properties={STATUS_PROPERTY: "CANCELLED"}))
        assert message.is_withdrawal

    def test_a_missing_status_is_unknown_not_an_error(self) -> None:
        assert read_message(JmsMessage(payload="<nonsense/>", properties={})).status is MessageStatus.UNKNOWN


class TestKnownUnparsedCorpus:
    """The captured payloads `Notam.from_fil` still cannot read.

    These assert the *current* behavior. When the parser is fixed they will fail, which is
    the point — that is the signal to promote them into real parsing tests.
    """

    @staticmethod
    def payloads() -> list[Path]:
        return sorted(UNPARSED.glob("*.xml"))

    def test_the_corpus_is_present(self) -> None:
        assert len(self.payloads()) >= 40

    def test_every_captured_payload_still_fails(self) -> None:
        failures = 0
        for path in self.payloads():
            try:
                parse_payload(path.read_text())
            except Exception:  # noqa: BLE001 - the whole point is that these raise
                failures += 1
        assert failures == len(self.payloads()), "a payload started parsing; promote it to a real test"

    def test_reading_one_records_the_failure_rather_than_raising(self) -> None:
        path = self.payloads()[0]
        sidecar = json.loads(path.with_suffix(".json").read_text())

        message = read_message(
            JmsMessage(payload=path.read_text(), properties={STATUS_PROPERTY: sidecar["status"]})
        )

        assert isinstance(message, FnsMessage)
        assert message.notam is None
        assert message.error
