"""Scheduled fetchers on Lambda: land one source snapshot in the S3 landing zone.

Invoked by EventBridge Scheduler (or the `afterlife-daily` state machine) with

    {"source": "retraction-watch" | "pubmed" | "openalex"}

and answers with a JSON summary of what was landed. The handler is thin on
purpose: every decision about what a snapshot is and how it is stored lives in
`afterlife.core` and `afterlife.sources`, which run unchanged on a laptop. This
file only adapts them to the Lambda environment (bucket from the environment,
API key from Secrets Manager).

Idempotency comes from the landing zone, not from this file. `land()` refuses
to overwrite, `S3Store` writes with `If-None-Match: *`, and Retraction Watch
skips snapshot dates already present. Two concurrent invocations for the same
source therefore cannot both land, and a retried invocation lands nothing new.

One check is added here because the source modules cannot make it cheaply:
PubMed and OpenAlex snapshots are dated by the day of the fetch, so a second
invocation on the same UTC day would repeat the whole fetch only to be refused
at the end. For OpenAlex that repeat costs real money (the keyless fetch of all
retracted works took 709 metered calls and $0.0709), so the handler checks
for today's manifest first and returns without calling the API.

The scheduled OpenAlex run goes to ECS Fargate, not here, because it takes
9 minutes 42 seconds on a laptop against Lambda's 15 minute ceiling (see
aws/README.md). The branch below stays so the event contract covers every
source and a keyed run can still be invoked by hand. A Lambda that times out
lands nothing: `land()` is called only after the fetch has finished.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, date, datetime
from typing import Any

from afterlife.core import http
from afterlife.core.landing import Manifest, S3Store, Store, partition

log = logging.getLogger()
log.setLevel(logging.INFO)

SOURCES = ("retraction-watch", "pubmed", "openalex")
DEFAULT_OPENALEX_BUDGET_USD = 0.09

# Loaded once per execution environment, on the first OpenAlex invocation.
# Retraction Watch and PubMed invocations never touch Secrets Manager.
_openalex_key_loaded = False


def _today() -> date:
    # Lambda runs in UTC, but the date is passed to the source modules
    # explicitly so the check and the landed partition always agree.
    return datetime.now(UTC).date()


def _manifest_summary(m: Manifest, new: bool) -> dict[str, Any]:
    return {
        "snapshot_date": m.snapshot_date,
        "object_key": m.object_key,
        "records": m.records,
        "sha256": m.sha256,
        "stored_bytes": m.stored_bytes,
        "cost_usd": m.cost_usd,
        "new": new,
    }


def _landed_today(store: Store, source_dir: str) -> bool:
    return store.exists(f"{partition(source_dir, _today())}/_manifest.json")


def load_openalex_key(client=None) -> bool:
    """Copy the OpenAlex API key from Secrets Manager into the environment.

    The source module reads `OPENALEX_API_KEY` from the environment, which is
    the same contract the CLI and the Fargate task (where ECS injects the
    secret itself) use. Returns whether a key is now set.

    A configured secret that cannot be read is an error, not a fallback to
    keyless mode: keyless paging is a different fetch with a different cost
    profile, and switching to it silently would hide a broken deployment.
    """
    global _openalex_key_loaded
    arn = os.environ.get("OPENALEX_SECRET_ARN", "")
    if _openalex_key_loaded or not arn:
        return bool(os.environ.get("OPENALEX_API_KEY"))
    if client is None:
        import boto3  # in the Lambda runtime; not a dependency of afterlife.core

        client = boto3.client("secretsmanager")
    secret = client.get_secret_value(SecretId=arn)["SecretString"].strip()
    if secret.startswith("{"):
        secret = json.loads(secret)["api_key"]
    if not secret:
        raise ValueError(f"secret {arn} is empty")
    os.environ["OPENALEX_API_KEY"] = secret
    _openalex_key_loaded = True
    return True


def fetch_retraction_watch(store: Store) -> dict[str, Any]:
    from afterlife.sources import retraction_watch as rw

    # `get` is looked up on the module at call time (the source functions bind
    # their default at import), so a test can replace afterlife.core.http.get.
    snapshots = rw.list_snapshots(get=http.get)
    if not snapshots:
        return {"available": 0, "newest": None, "landed": []}
    newest = snapshots[-1]
    landed = [_manifest_summary(m, new) for m, new in rw.fetch_many(store, [newest], get=http.get)]
    return {
        "available": len(snapshots),
        "newest": newest.snapshot_date.isoformat(),
        "newest_commit": newest.sha,
        "landed": landed,
        "already_landed": not landed,
    }


def fetch_pubmed(store: Store) -> dict[str, Any]:
    from afterlife.sources import pubmed

    if _landed_today(store, pubmed.SOURCE):
        return {"snapshot_date": _today().isoformat(), "landed": [], "already_landed": True}
    m, new = pubmed.land_flagged(store, snapshot_date=_today(), get=http.get)
    return {"landed": [_manifest_summary(m, new)], "already_landed": not new, "notes": m.notes}


def fetch_openalex(store: Store) -> dict[str, Any]:
    from afterlife.core.budget import Budget
    from afterlife.sources import openalex

    if _landed_today(store, openalex.SOURCE):
        return {"snapshot_date": _today().isoformat(), "landed": [], "already_landed": True}
    keyed = load_openalex_key()
    budget = Budget(float(os.environ.get("OPENALEX_BUDGET_USD", DEFAULT_OPENALEX_BUDGET_USD)))
    m, new = openalex.land_retracted(store, budget, snapshot_date=_today(), get=http.get)
    # The slice lists are in the manifest; the summary keeps only their size.
    notes = {k: v for k, v in m.notes.items() if k not in {"slices", "pending"}}
    notes["pending_slices"] = len(m.notes.get("pending") or [])
    return {
        "landed": [_manifest_summary(m, new)],
        "already_landed": not new,
        "keyed": keyed,
        "complete": m.notes.get("complete"),
        "notes": notes,
    }


FETCHERS = {
    "retraction-watch": fetch_retraction_watch,
    "pubmed": fetch_pubmed,
    "openalex": fetch_openalex,
}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    source = (event or {}).get("source")
    if source not in FETCHERS:
        # A malformed schedule is a deployment bug. Raising makes it count as
        # a Lambda error, which is alarmed, instead of a quiet no op.
        raise ValueError(f"unknown source {source!r}; expected one of {', '.join(SOURCES)}")
    bucket = os.environ["LANDING_BUCKET"]
    started = datetime.now(UTC)
    result = FETCHERS[source](S3Store(bucket))
    summary = {
        "source": source,
        "bucket": bucket,
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        **result,
    }
    log.info(json.dumps(summary, default=str))
    return summary
