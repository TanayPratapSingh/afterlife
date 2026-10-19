# ADR 0002: The landing zone is immutable, and everything rebuilds from it

## Context

Every source here is a moving target. Retraction Watch republishes the whole
database daily and edits old records. OpenAlex changed its access model during
this build. One published snapshot (2026-10-15) turned out to be a lossy
Windows-1252 export. If ingestion rewrote stored data in place, there would be
no way to tell a source change from a pipeline bug.

## Decision

- Raw bytes are landed exactly as fetched under
  `landing/<source>/snapshot_date=YYYY-MM-DD/`, with a manifest recording the
  origin URL and version, sha256 of the raw bytes, byte counts, record count,
  dollar cost of the fetch, and the encoding used.
- Writes refuse to overwrite: `O_EXCL` locally, `If-None-Match: *` on S3. A
  second fetch of identical bytes is a no op. Different bytes for an existing
  snapshot raise `ImmutableWriteError`; that is a finding about the source.
- Compression is deterministic (`gzip` with `mtime=0`), so stored bytes are a
  pure function of the raw bytes and the operation is idempotent.
- Bronze, silver, gold, the export, and the serving table can all be rebuilt
  from the landing prefix alone. `afterlife silver --rebuild` does exactly
  that for the history.

## Consequences

Storage grows with every snapshot (about 15 MB compressed per Retraction Watch
day). S3 lifecycle moves `landing/` to Glacier Instant Retrieval after 90 days.
Bad upstream data is never deleted; it is quarantined downstream (ADR 0004),
and the evidence stays reproducible.
