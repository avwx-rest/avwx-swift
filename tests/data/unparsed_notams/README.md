# NOTAM payloads avwx-swift cannot parse

Live FNS-NDS messages captured from an SCDS subscription on 2026-08-26 that
`avwx_swift.notam.Notam.from_fil` raises on. Kept whole so the parser can be fixed against
real inputs rather than guesses.

Captured while building the NOTAM worker in `report-workers`, which records each failure
and carries on rather than dropping the message.

Every one fails the same way:

```
TypeError: string indices must be integers, not 'str'
```

Roughly half the live feed is affected, so this is not an edge case — it is the single
biggest gap between what the subscription delivers and what reaches the store.

## What is here

One `.xml` per message with the exact bytes off the wire, plus a `.json` sidecar carrying
the error and the Solace message properties. Files are named
`{status}-{correlation-id}`, so re-capturing the same message overwrites rather than
duplicates.

| Dimension | Spread across the 41 samples |
| --- | --- |
| `NOTAMStatus` | 37 PUBLISHED, 4 CANCELLED |
| `SourceType` | 28 International, 12 Domestic, 1 F |

## What is known about the cause

Not the multi-member envelope. Many of these carry `hasMember` as a list —
`[{Event: …}, {AirportHeliport: …}]` — where the FIL file always holds a single `Event`,
and `from_fil` does handle the list form. Normalizing the envelope down to just the `Event`
member was tried and changed nothing, so the failure is deeper in the `Event` itself.

## Regenerating

Set `UNPARSED_DIR` in `report-workers` and run its NOTAM worker against a live
subscription; anything that fails to parse is written out automatically.
