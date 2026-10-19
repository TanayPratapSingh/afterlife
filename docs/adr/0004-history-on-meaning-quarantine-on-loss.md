# ADR 0004: History is cut on meaning, bytes are refreshed in place, lossy snapshots are quarantined

## Context

Replaying 39 real Retraction Watch snapshots through a byte level SCD type 2
produced versions that nobody edited:

- 2025-09-02: 65,946 records "changed" because the export dropped a `+` prefix
  from every reason label.
- 2026-10-15: 73,214 records "changed" and 73,253 "changed back" the next day.
  That snapshot used 12 hour timestamps, zero padded PMIDs, line breaks
  inside cells, and Windows-1252, and it replaced every character outside
  Windows-1252 with `?` (2,298 titles, 2,922 author lists).

A history like that is useless for the question it exists to answer: what did
the source actually change after publishing a record?

## Decision

1. Each business column has a semantic form (`SEMANTIC` in silver.py):
   whitespace collapsed, dates parsed, identifiers normalized, list columns
   trimmed, deduplicated, stripped of the legacy `+`, and sorted unless order
   carries meaning (authors). A new version is cut only when the semantic hash
   changes.
2. A change that is only formatting updates the current version's raw strings
   in place (type 1), so the next day's `format_only` count measures one day
   of drift, not the drift since the record first appeared.
3. Before any MERGE, a quality gate compares the snapshot with the current
   versions. If more than `LOSSY_RECORDS_MAX` records show the lossy
   transcoding signature (the new value equals the old one with every non
   cp1252 character replaced by `?`), the snapshot is not merged. The decision
   and its evidence go to `silver.rw_quarantine`. The landed bytes stay.
4. The threshold was set from data: the replay report lists the lossy count
   for every snapshot, and it is zero for all 38 snapshots other than
   2026-10-15 (5,520).

## Consequences

Genuine relabels, such as the 2025 reason taxonomy edits, still cut versions,
because the words changed. They are reported as such (`top_reason_relabels`
in the findings). The gate only compares against current versions, so a
lossy value on a currently deleted record would pass; that limit is documented
in CLAUDE.md.
