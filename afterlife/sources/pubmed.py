"""PubMed: the National Library of Medicine's own retraction flag.

When NLM indexes a retraction notice it adds the publication type
"Retracted Publication" to the original article's record. That flag is what
most biomedical search tools, and most clinicians, actually see. It is free,
keyless, and authoritative for MEDLINE, and it covers only what NLM indexes.

E-utilities `esearch` returns at most 9,999 ids per query, so the fetch splits
the date range until every slice fits, then checks that the slice counts add
up to the unsplit total. NCBI asks for no more than three requests a second
without a key; the client sleeps between calls to respect that.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta

from afterlife.core import http
from afterlife.core.landing import Manifest, Store, land

SOURCE = "pubmed"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
TERM = '"retracted publication"[pt]'
CEILING = 9_999


def _search(term: str, lo: date | None, hi: date | None, retmax: int, get, pause) -> dict:
    params: dict[str, object] = {
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "retmax": retmax,
        "tool": "afterlife",
    }
    if lo and hi:
        params.update(
            {
                "datetype": "pdat",
                "mindate": lo.strftime("%Y/%m/%d"),
                "maxdate": hi.strftime("%Y/%m/%d"),
            }
        )
    key = os.environ.get("NCBI_API_KEY")
    if key:
        params["api_key"] = key
    pause()
    body = get(ESEARCH, params).json()["esearchresult"]
    if "ERROR" in body:
        raise RuntimeError(f"esearch error: {body['ERROR']}")
    return body


def fetch_flagged(term: str = TERM, *, today: date | None = None, get=http.get, pause=None) -> dict:
    """All PMIDs matching the term, with a record of how they were sliced."""
    if pause is None:
        interval = 0.11 if os.environ.get("NCBI_API_KEY") else 0.35

        def pause() -> None:
            time.sleep(interval)

    today = today or date.today()
    total = int(_search(term, None, None, 0, get, pause)["count"])
    ranges = [(date(1800, 1, 1), date(1989, 12, 31))]
    ranges += [(date(y, 1, 1), date(y, 12, 31)) for y in range(1990, today.year + 1)]
    stack = list(reversed(ranges))
    pmids: set[str] = set()
    slices: list[dict] = []
    while stack:
        lo, hi = stack.pop()
        res = _search(term, lo, hi, CEILING, get, pause)
        n = int(res["count"])
        if n > CEILING and hi > lo:
            mid = lo + (hi - lo) // 2
            stack.append((mid + timedelta(days=1), hi))
            stack.append((lo, mid))
            continue
        ids = res.get("idlist", [])
        pmids.update(ids)
        slices.append({"from": str(lo), "to": str(hi), "count": n, "fetched": len(ids)})
    return {
        "term": term,
        "expected": total,
        "slices": slices,
        "pmids": sorted(pmids, key=int),
    }


def completeness(result: dict) -> dict:
    """Derived facts about a fetch, kept out of the landed bytes.

    The slice counts add up to more than the total because `pdat` matches
    both the print and the electronic publication date, so a record whose two
    dates fall in different years is counted in two slices. The union of ids
    is what has to equal the total; the overlap is reported, not hidden.
    """
    unique = len(result["pmids"])
    slice_sum = sum(s["count"] for s in result["slices"])
    return {
        "expected": result["expected"],
        "unique_pmids": unique,
        "slice_count_sum": slice_sum,
        "records_in_more_than_one_slice": slice_sum - unique,
        "complete": unique == result["expected"]
        and all(s["count"] == s["fetched"] for s in result["slices"]),
    }


def land_flagged(
    store: Store, snapshot_date: date | None = None, get=http.get, pause=None
) -> tuple[Manifest, bool]:
    result = fetch_flagged(get=get, pause=pause)
    raw = json.dumps(result, sort_keys=True).encode()
    notes = {"term": result["term"], **completeness(result)}
    return land(
        store,
        source=SOURCE,
        snapshot_date=snapshot_date or date.today(),
        name="retracted_publication_pmids.json",
        raw=raw,
        origin_url=f"{ESEARCH}?db=pubmed&term={TERM}",
        origin_version="esearch",
        records=len(result["pmids"]),
        cost_usd=0.0,
        notes=notes,
    )
