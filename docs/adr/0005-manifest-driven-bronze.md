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
key), and refuses to write if the Spark row count differs from the count
Python's `csv` module produced at landing time. A file whose manifest records
an encoding other than UTF-8 is transcoded in Python into a derived UTF-8 copy
under `staging/`, and Spark reads that copy; Spark never reads anything but
UTF-8. The first version passed `encoding=windows-1252` to Spark's CSV reader,
which worked on Spark 3.5 and failed on Spark 4.0 (INVALID_PARAMETER_VALUE.
CHARSET), the version under Databricks Runtime 17 and serverless. CI caught it
on its first run. JSON sources go through the same path even though
Auto Loader would handle them, so the code CI runs is the code production
runs.

## Consequences

No Auto Loader checkpoint to manage, and no schema inference. The cost is
that bronze lists manifests on every run, which is cheap at one manifest per
source per day. If a source ever lands thousands of files a day, Auto Loader
with per directory reader options becomes the better choice and this ADR
should be revisited.
