"""NOTAM data structures."""

import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from itertools import batched
from typing import Any, NamedTuple, Self

import geojson  # type: ignore
from geojson import Point, Polygon

geojson.geometry.DEFAULT_PRECISION = 13


def format_dt(data: str | dict) -> datetime:
    """Format a date string or dict into a datetime object."""
    if isinstance(data, dict):
        data = data["#text"]
    return datetime.fromisoformat(data)  # type: ignore


def optional_dt(data: str | dict) -> datetime | None:
    """Like format_dt, but returns None if the key is not found."""
    try:
        return format_dt(data)
    except KeyError:
        return None


def get_raw_text(data: dict | list[dict]) -> str:
    """Extract the raw text from the NOTAM data."""
    # If both simple and formatted are given, prefer formatted since it has more data
    if isinstance(data, list):
        data = next(item for item in data if "formattedText" in item["NOTAMTranslation"])
    raw: str
    try:
        raw = data["NOTAMTranslation"]["formattedText"]["div"]
        # Replace newlines and remove all other HTML tags
        raw = raw.replace("<BR>", "\n")
        raw = re.sub(r"<.*?>", "", raw)
    except KeyError:
        raw = data["NOTAMTranslation"]["simpleText"]
    return raw.strip()


@dataclass(frozen=True)
class TextNotam:
    """Represents a textual NOTAM."""

    id: str
    number: str
    year: str
    issued: str
    location: str
    start: str
    end: str
    text: str
    raw: str
    series: str | None
    type: str | None
    affected_fir: str | None
    selection_code: str | None
    scope: str | None
    purpose: str | None
    traffic: str | None
    schedule: str | None
    upper_limit: str | None
    lower_limit: str | None
    min_fl: str | None
    max_fl: str | None
    coordinates: str | None
    radius: str | None

    @classmethod
    def from_fil(cls, data: dict[str, str]) -> Self:
        """Create a TextNotam instance from FIL data."""
        # try:
        return cls(
            id=data["@id"],
            series=data.get("series"),
            number=data["number"],
            year=data["year"],
            type=data.get("type"),
            issued=data["issued"],
            affected_fir=data.get("affectedFIR"),
            selection_code=data.get("selectionCode"),
            scope=data.get("scope"),
            purpose=data.get("purpose"),
            traffic=data.get("traffic"),
            schedule=data.get("schedule"),
            upper_limit=data.get("upperLimit"),
            lower_limit=data.get("lowerLimit"),
            min_fl=data.get("minimumFL"),
            max_fl=data.get("maximumFL"),
            coordinates=data.get("coordinates"),
            radius=data.get("radius"),
            location=data["location"],
            start=data["effectiveStart"],
            end=data["effectiveEnd"],
            text=data["text"],
            raw=get_raw_text(data["translation"]),  # type: ignore
        )
        # except KeyError as exc:
        #     # pprint(data)
        #     raise ValueError from exc


class _Features(NamedTuple):
    notes: list[str]
    shapes: list[Point | Polygon]


def _extract_features(features: _Features, data: dict[str, Any]) -> None:
    """Recursively extract specific field types from the data."""
    for key, val in data.items():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            for item in val:
                _extract_features(features, item)
        elif isinstance(val, dict):
            _extract_features(features, val)
        elif not isinstance(val, str):
            continue
        elif key in ("note", "operationalStatus", "status"):
            features.notes.append(val)
        elif key == "pos":
            lat, lon = map(float, val.split(" "))
            features.shapes.append(Point((lon, lat)))
        elif key == "posList":
            coords = [(lon, lat) for lat, lon in batched(map(float, val.split(" ")), 2)]
            features.shapes.append(Polygon(coords))


def check_event(item: dict) -> dict | None:
    """There are two different event types. We want the one with the extension"""
    with suppress(KeyError):
        event: dict = item["Event"]["timeSlice"]["EventTimeSlice"]
        _ = event["extension"]
        return event
    return None


@dataclass(frozen=True)
class Notam:
    """Represents a FAA SWIFT NOTAM."""

    id: str
    issued: datetime
    updated: datetime
    start: datetime
    end: datetime | None

    classification: str
    icao: str | None
    name: str | None

    notes: list[str]
    shapes: list[Point | Polygon]
    text: TextNotam

    @classmethod
    def from_fil(cls, data: dict[str, Any]) -> Self:
        """Create a Notam instance from FIL data."""
        # try:
        root = data["hasMember"]
        event: dict[str, Any]
        features = _Features(notes=[], shapes=[])
        if isinstance(root, list):
            for item in root:
                if "Event" in item:
                    if checked_event := check_event(item):
                        event = checked_event
                        break
                else:
                    _extract_features(features, item)
        else:
            event = root["Event"]["timeSlice"]["EventTimeSlice"]
        times: dict = event["validTime"]["TimePeriod"]
        notam: dict = event["textNOTAM"]["NOTAM"]
        extension: dict = event["extension"]["EventExtension"]
        return cls(
            id=event["@id"],
            issued=format_dt(notam["issued"]),
            updated=format_dt(extension["lastUpdated"]),
            start=format_dt(times["beginPosition"]),
            end=optional_dt(times["endPosition"]),
            classification=extension["classification"],
            icao=extension.get("icaoLocation"),
            name=extension.get("airportname"),
            notes=features.notes,
            shapes=features.shapes,
            text=TextNotam.from_fil(notam),
        )
        # except Exception as exc:
        #     # pprint(data)
        #     raise ValueError from exc


# Worth a second look:

# "RadioFrequencyArea": {
#     "@id": "RFA01_66789677",
#     "boundedBy": {
#       "@nil": "true",
#       "@xmlns": {
#         "xsi": "http://www.w3.org/2001/XMLSchema-instance"
#       }
#     },
#     "timeSlice": {
#       "RadioFrequencyAreaTimeSlice": {
#         "@id": "RFA01_TS01_66789677",
#         "extension": {
#           "RadioFrequencyAreaExtension": {
#             "@id": "RFAE_EVENT_1_66789677",
#             "theEvent": {
#               "@href": "#Event_1_66789677"
#             }
#           }
#         },
#         "interpretation": "TEMPDELTA",
#         "sector": {
#           "CircleSector": {
#             "@id": "CS01_66789677",
#             "angleType": "RDL",
#             "arcDirection": "CWA",
#             "fromAngle": "228",
#             "outerDistance": {
#               "#text": "26",
#               "@uom": "NM"
#             },
#             "toAngle": "284",
#             "upperLimit": {
#               "#text": "15900",
#               "@uom": "FT"
#             },
#             "upperLimitReference": "SFC"
#           }
#         },
#         "validTime": {
#           "TimePeriod": {
#             "@id": "RFA01_TS02_TP01_66789677",
#             "beginPosition": "2023-01-05T18:51:00.000Z",
#             "endPosition": {
#               "@indeterminatePosition": "unknown"
#             }
#           }
#         }
#       }
#     }
#   },
