"""Retraction Watch, via the Crossref mirror on GitLab.

Crossref acquired the Retraction Watch database in 2023 and publishes it as a
single CSV, regenerated daily, in a public GitLab repository. Every daily
regeneration is a commit, so the repository history is a free time series of
full snapshots. This module treats it that way: it lists commits that touched
the CSV, picks the snapshots a run asks for, and lands each one unchanged.

That history is what makes the change data capture work in this project real.
The silver layer diffs consecutive snapshots to find records that were added,
edited, or deleted after they were first published.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date

from afterlife.core import http
from afterlife.core.landing import Manifest, Store, land
from afterlife.core.text import decode

SOURCE = "retraction_watch"
PROJECT = "crossref%2Fretraction-watch-data"
CSV_PATH = "retraction_watch.csv"
API = f"https://gitlab.com/api/v4/projects/{PROJECT}/repository/commits"
RAW = "https://gitlab.com/crossref/retraction-watch-data/-/raw/{sha}/" + CSV_PATH

EXPECTED_COLUMNS = (
    "Record ID",
    "Title",
    "Subject",
    "Institution",
    "Journal",
    "Publisher",
    "Country",
    "Author",
    "URLS",
    "ArticleType",
    "RetractionDate",
    "RetractionDOI",
    "RetractionPubMedID",
    "OriginalPaperDate",
    "OriginalPaperDOI",
    "OriginalPaperPubMedID",
    "RetractionNature",
    "Reason",
    "Paywalled",
    "Notes",
)


@dataclass(frozen=True)
class Snapshot:
    sha: str
    committed_at: str  # ISO 8601 from GitLab
    snapshot_date: date  # the date the commit title says the data is for

    @property
    def url(self) -> str:
        return RAW.format(sha=self.sha)


def _snapshot_date(title: str, committed_at: str) -> date:
    # Titles read "Retraction Watch data for 2026-10-16". Fall back to the
    # commit timestamp when a title does not follow the pattern.
    tail = title.rsplit(" ", 1)[-1]
    try:
        return date.fromisoformat(tail)
    except ValueError:
        return date.fromisoformat(committed_at[:10])


def list_snapshots(get=http.get, per_page: int = 100) -> list[Snapshot]:
    """Every commit that touched the CSV, oldest first.

    When two commits claim the same data date, the later commit wins, since it
    is the one a reader of the repository on that day would have seen last.
    """
    by_date: dict[date, Snapshot] = {}
    page = 1
    while True:
        resp = get(API, {"path": CSV_PATH, "per_page": per_page, "page": page})
        commits = resp.json()
        if not commits:
            break
        for c in commits:
            snap = Snapshot(c["id"], c["created_at"], _snapshot_date(c["title"], c["created_at"]))
            prior = by_date.get(snap.snapshot_date)
            if prior is None or snap.committed_at > prior.committed_at:
                by_date[snap.snapshot_date] = snap
        nxt = resp.headers.get("X-Next-Page") or resp.headers.get("x-next-page")
        if not nxt:
            break
        page = int(nxt)
    return [by_date[d] for d in sorted(by_date)]


def select(
    snapshots: list[Snapshot],
    *,
    monthly: bool = False,
    last_days: int = 0,
    since: date | None = None,
) -> list[Snapshot]:
    """Choose a replay schedule: the first snapshot of each month, the most
    recent N days, or both. Always includes the newest snapshot."""
    pool = [s for s in snapshots if since is None or s.snapshot_date >= since]
    chosen: dict[date, Snapshot] = {}
    if monthly:
        seen: set[tuple[int, int]] = set()
        for s in pool:
            ym = (s.snapshot_date.year, s.snapshot_date.month)
            if ym not in seen:
                seen.add(ym)
                chosen[s.snapshot_date] = s
    if last_days:
        for s in pool[-last_days:]:
            chosen[s.snapshot_date] = s
    if pool:
        chosen[pool[-1].snapshot_date] = pool[-1]
    return [chosen[d] for d in sorted(chosen)]


def validate_header(raw: bytes) -> list[str]:
    """Return the header, or raise if the published schema has drifted.

    The file has a trailing comma on every line, which produces one empty
    column name. That is tolerated; a missing or renamed column is not.
    """
    first = decode(raw.split(b"\n", 1)[0]).text.rstrip("\r")
    header = next(csv.reader([first]))
    named = [h for h in header if h != ""]
    missing = [c for c in EXPECTED_COLUMNS if c not in named]
    if missing:
        raise ValueError(f"Retraction Watch schema drift, missing columns: {missing}")
    return named


def count_records(raw: bytes) -> int:
    reader = csv.reader(io.StringIO(decode(raw).text, newline=""))
    next(reader, None)
    return sum(1 for _ in reader)


def fetch_snapshot(store: Store, snap: Snapshot, get=http.get) -> tuple[Manifest, bool]:
    raw = get(snap.url, timeout=300).body
    validate_header(raw)
    decoded = decode(raw)
    return land(
        store,
        source=SOURCE,
        snapshot_date=snap.snapshot_date,
        name="retraction_watch.csv",
        raw=raw,
        origin_url=snap.url,
        origin_version=snap.sha,
        records=count_records(raw),
        notes={
            "committed_at": snap.committed_at,
            "encoding": decoded.encoding,
            "invalid_utf8_bytes": decoded.invalid_utf8_bytes,
        },
    )


def landed_dates(store: Store) -> set[str]:
    return {
        k.split("snapshot_date=")[1].split("/")[0]
        for k in store.list(f"landing/{SOURCE}/")
        if k.endswith("/_manifest.json")
    }


def fetch_many(
    store: Store, snapshots: Iterable[Snapshot], get=http.get
) -> Iterator[tuple[Manifest, bool]]:
    done = landed_dates(store)
    for snap in snapshots:
        if snap.snapshot_date.isoformat() in done:
            continue
        yield fetch_snapshot(store, snap, get=get)
