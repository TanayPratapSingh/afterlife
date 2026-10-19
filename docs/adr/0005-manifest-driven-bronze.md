# ADR 0005: Bronze is manifest driven batch, not Auto Loader

## Context

On Databricks the default way to ingest files from cloud storage is Auto
Loader (`cloudFiles`), which tracks which files it has seen and applies one
reader configuration to the stream.

The Retraction Watch snapshots do not share one reader configuration: one is
Windows-1252, the rest are UTF-8, and the encoding is only known by
inspecting the bytes. The landing manifests already record each file's
encoding, sha256, and record count.

## Decision

Bronze reads the landing manifests, skips objects already ingested (by object
key), reads each new file with the encoding its manifest records, and refuses
to write if the Spark row count differs from the count Python's `csv` module
produced at landing time. JSON sources go through the same path even though
Auto Loader would handle them, so the code CI runs is the code production
runs.

## Consequences

No Auto Loader checkpoint to manage, and no schema inference. The cost is
that bronze lists manifests on every run, which is cheap at one manifest per
source per day. If a source ever lands thousands of files a day, Auto Loader
with per directory reader options becomes the better choice and this ADR
should be revisited.
