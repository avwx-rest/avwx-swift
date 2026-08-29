"""Reading FNS-NDS messages."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from geojson import Point

from avwx_swift.fns import (
    NUMBER_PROPERTY,
    STATUS_PROPERTY,
    FnsMessage,
    MessageStatus,
    parse_payload,
    read_message,
)
from avwx_swift.jms import JmsMessage
from avwx_swift.notam import INHERITED_NOTE

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
    def test_reads_the_feed_spellings(
        self, value: str, expected: MessageStatus
    ) -> None:
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
        message = read_message(
            JmsMessage(payload="<nonsense/>", properties={STATUS_PROPERTY: "CANCELLED"})
        )
        assert message.is_withdrawal

    def test_a_missing_status_is_unknown_not_an_error(self) -> None:
        assert (
            read_message(JmsMessage(payload="<nonsense/>", properties={})).status
            is MessageStatus.UNKNOWN
        )


class TestCapturedCorpus:
    """The captured FNS-NDS payloads, which `Notam.from_fil` now reads.

    These were kept as failing captures until the parser could read them; they are real
    parsing tests now. Everything asserted here is checked against the source XML.
    """

    @staticmethod
    def payloads() -> list[Path]:
        return sorted(UNPARSED.glob("*.xml"))

    def test_the_corpus_is_present(self) -> None:
        assert len(self.payloads()) >= 40

    def test_every_captured_payload_parses(self) -> None:
        failures: list[str] = []
        for path in self.payloads():
            try:
                parse_payload(path.read_text())
            except Exception as exc:  # noqa: BLE001 - report every failure, not just the first
                failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
        assert not failures, "payloads stopped parsing:\n" + "\n".join(failures)

    def test_every_payload_yields_usable_text(self) -> None:
        """A NOTAM that parses but carries no readable text is no better than a failure."""
        for path in self.payloads():
            notam = parse_payload(path.read_text())
            raw = notam.text.raw
            assert raw, f"{path.name} has empty raw text"
            assert "<" not in raw, (
                f"{path.name} leaked markup into raw text: {raw[:80]}"
            )
            assert "#text" not in raw, (
                f"{path.name} leaked an xmltodict key into raw text"
            )

    def test_a_bare_text_div_is_read(self) -> None:
        """The div holding formattedText is a plain string when it has no attributes.

        This is the shape the live feed sends and the one that used to raise.
        """
        notam = parse_payload((UNPARSED / "published-12100932.xml").read_text())

        assert notam.text.raw.startswith("J6389/26 NOTAMN")
        assert "AIRSPACE RESERVATION RAINMAKING" in notam.text.raw

    def test_fields_match_the_source_xml(self) -> None:
        notam = parse_payload((UNPARSED / "published-12100932.xml").read_text())

        assert notam.id == "Event_TS_1_1787800202861000"
        assert notam.issued == datetime(2026, 8, 27, 3, 9, tzinfo=UTC)
        assert notam.updated == datetime(2026, 8, 27, 3, 10, tzinfo=UTC)
        assert notam.start == datetime(2026, 8, 27, 3, 15, tzinfo=UTC)
        assert notam.end == datetime(2026, 8, 27, 5, 15, tzinfo=UTC)
        assert notam.classification == "INTL"
        assert notam.icao == "VTBB"
        assert notam.name == "BANGKOK ACC/FIC/COM CENTRE"

        text = notam.text
        assert (text.series, text.number, text.year, text.type) == (
            "J",
            "6389",
            "2026",
            "N",
        )
        assert text.location == "VTBB"
        assert text.affected_fir == "VTBB"
        assert text.selection_code == "QRALW"
        assert text.coordinates == "1628N10247E"
        assert text.radius == "84"
        assert text.lower_limit == "7000FT AMSL"
        assert text.upper_limit == "7500FT AMSL"

    def test_the_number_matches_the_sidecar_property(self) -> None:
        """The parsed number must agree with what the feed put on the message.

        The feed writes the number two ways. An international NOTAM is
        ``{series}{number}/{yy}`` (``J6389/26``); a domestic or FDC one has no series and
        is prefixed with the issuing month instead (``08/574``, ``6/8518``).
        """
        for path in self.payloads():
            sidecar = json.loads(path.with_suffix(".json").read_text())
            expected = sidecar["properties"].get(NUMBER_PROPERTY)
            if not expected:
                continue
            text = parse_payload(path.read_text()).text
            if text.series:
                assert f"{text.series}{text.number}/{text.year[2:]}" == expected, (
                    path.name
                )
            else:
                assert expected.split("/")[1] == text.number, path.name

    def test_the_text_location_wins_over_the_members(self) -> None:
        """Sibling members restate the NOTAM's own location rather than adding to it.

        Every captured payload gives a Q) line coordinate, and the AirportHeliport
        reference point beside it is that same point at higher precision, so none of
        them promote.
        """
        for path in self.payloads():
            notam = parse_payload(path.read_text())
            assert notam.text.coordinates, f"{path.name} has no text coordinate"
            assert notam.shapes == [], (
                f"{path.name} promoted geometry over its own text"
            )
            assert not [n for n in notam.notes if n.startswith("Geometry inherited")]

    def test_member_geometry_is_promoted_when_the_text_has_none(self) -> None:
        """With no location in the text, the event's own geometry is worth having."""
        source = (UNPARSED / "published-12100932.xml").read_text()
        stripped = re.sub(r"<event:coordinates>.*?</event:coordinates>", "", source)

        notam = parse_payload(stripped)

        assert notam.text.coordinates is None
        assert notam.shapes == [Point((102.783333, 16.466667))]
        assert INHERITED_NOTE.format("AirportHeliport") in notam.notes

    def test_promoted_geometry_says_where_it_came_from(self) -> None:
        """Promoted geometry is marked so it is not mistaken for the affected area."""
        stripped = re.sub(
            r"<event:coordinates>.*?</event:coordinates>",
            "",
            (UNPARSED / "published-12100939.xml").read_text(),
        )

        notam = parse_payload(stripped)

        assert notam.shapes
        assert [n for n in notam.notes if n.startswith("Geometry inherited from")]

    def test_a_message_with_no_event_is_rejected(self) -> None:
        """A missing Event must raise, not fall through to an unbound local."""
        payload = (
            '<message:AIXMBasicMessage xmlns:message="http://www.aixm.aero/schema/5.1/message">'
            "<message:hasMember><Nothing/></message:hasMember>"
            "</message:AIXMBasicMessage>"
        )
        with pytest.raises(ValueError, match="No Event member"):
            parse_payload(payload)

    def test_reading_one_returns_a_parsed_notam(self) -> None:
        path = self.payloads()[0]
        sidecar = json.loads(path.with_suffix(".json").read_text())

        message = read_message(
            JmsMessage(
                payload=path.read_text(),
                properties={STATUS_PROPERTY: sidecar["status"]},
            )
        )

        assert isinstance(message, FnsMessage)
        assert message.error is None
        assert message.notam is not None

    def test_cancelled_captures_are_still_withdrawals(self) -> None:
        """Parsing must not change how a cancellation is read off the message."""
        for path in sorted(UNPARSED.glob("cancelled-*.xml")):
            sidecar = json.loads(path.with_suffix(".json").read_text())
            message = read_message(
                JmsMessage(
                    payload=path.read_text(),
                    properties={STATUS_PROPERTY: sidecar["status"]},
                )
            )
            assert message.is_withdrawal, path.name
            assert message.notam is not None, path.name
