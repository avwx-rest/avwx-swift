# Captured FNS-NDS NOTAM payloads

Live FNS-NDS messages captured from an SCDS subscription on 2026-08-26, kept whole so the
parser is exercised against real inputs rather than guesses.

These were captured because `Notam.from_fil` raised on every one of them. **They all parse
now** — see below. The directory keeps its name so the capture tooling in `report-workers`
keeps writing here; the tests that cover it are `TestCapturedCorpus` in `tests/test_fns.py`.

## What is here

One `.xml` per message with the exact bytes off the wire, plus a `.json` sidecar carrying
the original error and the Solace message properties. Files are named
`{status}-{correlation-id}`, so re-capturing the same message overwrites rather than
duplicates.

| Dimension | Spread across the 41 samples |
| --- | --- |
| `NOTAMStatus` | 37 PUBLISHED, 4 CANCELLED |
| `SourceType` | 28 International, 12 Domestic, 1 F |

## What the cause turned out to be

Not a NOTAM type, and not the multi-member envelope — normalizing the envelope down to the
`Event` member was tried and changed nothing, which was correct but for the wrong reason.

All 41 failed on one line, in `get_raw_text`:

```python
raw = data["NOTAMTranslation"]["formattedText"]["div"]["#text"]
```

`xmltodict` only builds a dict for an element that carries attributes or child elements. The
bulk FIL file's `html:div` does, so the text sits under `#text`. Over SCDS the div is bare —
`<html:div>J6389/26 NOTAMN ...</html:div>` — so xmltodict yields a plain `str`, and indexing
it with `"#text"` raised `TypeError: string indices must be integers`. `get_div_text` now
accepts both shapes.

Two adjacent fixes went with it: the `next(...)` picking a translation out of a list no
longer raises `StopIteration` when none carries `formattedText`, and `avwx-engine`'s
`notam.sanitize` now repairs keys written without a trailing space (`E)TWY CLSD`), which
3 of these use.

## Geometry

`shapes` is empty for all 41, which is the correct result rather than a gap.

A NOTAM's geometry belongs to its text. The only geometry these messages carry outside
the `Event` is a single `pos` per member (40 `AirportHeliport`, 1 `Unit`) holding a
reference point, and it is the `Q)` line coordinate restated at higher precision:

| | reference point | `Q)` line coordinate |
| --- | --- | --- |
| `published-12100932` | 16.4667, 102.7833 | `1628N10247E` -> 16.4667, 102.7833 |
| `cancelled-12100976` | 32.8972, -97.0377 | `3253N09702W` -> 32.8833, -97.0333 |

So it adds no area. Promoting it would hand a consumer a single point where, for
`published-12100932`, an 84NM region belongs. `from_fil` promotes member geometry only
when the event has location info and the text has none of its own; every one of these
gives a `Q)` line coordinate, so none of them promote. Anything promoted is tagged
`Geometry inherited from {member}` so it is not mistaken for the affected area.

`from_fil` used to stop scanning at the `Event`, which dropped later members entirely.
That is fixed, so the rule above is what decides the outcome now.

## Regenerating

Set `UNPARSED_DIR` in `report-workers` and run its NOTAM worker against a live
subscription; anything that fails to parse is written out automatically.
