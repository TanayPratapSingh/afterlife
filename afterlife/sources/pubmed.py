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
import re
import time
from datetime import date, timedelta

from afterlife.core import http
from afterlife.core.landing import Manifest, Store, land

SOURCE = "pubmed"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
TERM = '"retracted publication"[pt]'
# The notices themselves. NLM renamed the type: "retraction of publication"[pt]
# returned 0 records on 2026-10-29, this term 34,110.
NOTICE_TERM = '"retraction notice"[pt]'
CEILING = 9_999

_RECORD = re.compile(r"<PubmedArticle>(.*?)</PubmedArticle>", re.S)
_FIRST_PMID = re.compile(r"<PMID[^>]*>(\d+)</PMID>")
_RETRACTION_OF = re.compile(
    r'<CommentsCorrections\s+RefType="RetractionOf"\s*>(.*?)</CommentsCorrections>', re.S
)


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


def _default_pause():
    interval = 0.11 if os.environ.get("NCBI_API_KEY") else 0.35
    return lambda: time.sleep(interval)


def fetch_flagged(term: str = TERM, *, today: date | None = None, get=http.get, pause=None) -> dict:
    """All PMIDs matching the term, with a record of how they were sliced."""
    pause = pause or _default_pause()
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


def summaries(pmids: list[str], get=http.get) -> dict[str, dict]:
    """Title and publication year per PMID, from esummary (one call, up to 200 ids).
    The landed PubMed set is ids only; the hand audit sheet needs a title."""
    if not pmids:
        return {}
    params: dict[str, object] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
        "tool": "afterlife",
    }
    if key := os.environ.get("NCBI_API_KEY"):
        params["api_key"] = key
    result = get(ESUMMARY, params).json()["result"]
    return {
        uid: {"title": result[uid].get("title", ""), "year": result[uid].get("pubdate", "")[:4]}
        for uid in result.get("uids", [])
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


def retraction_of(xml: str) -> dict[str, dict]:
    """Per record in an efetch response: the PMIDs its "Retraction of" links
    name, and how many such links carry no PMID at all."""
    out = {}
    for block in _RECORD.findall(xml):
        own = _FIRST_PMID.search(block)
        if not own:
            continue
        refs = _RETRACTION_OF.findall(block)
        pmids = sorted({m for r in refs for m in _FIRST_PMID.findall(r)}, key=int)
        out[own.group(1)] = {
            "pmids": pmids,
            "refs_without_pmid": sum(1 for r in refs if not _FIRST_PMID.search(r)),
        }
    return out


def fetch_records(pmids: list[str], *, get=http.get, pause=None, batch: int = 200) -> list[dict]:
    """Full PubMed XML for the PMIDs, one efetch call per batch, kept as fetched."""
    pause = pause or _default_pause()
    out = []
    for i in range(0, len(pmids), batch):
        chunk = pmids[i : i + batch]
        params: dict[str, object] = {
            "db": "pubmed",
            "id": ",".join(chunk),
            "retmode": "xml",
            "tool": "afterlife",
        }
        if key := os.environ.get("NCBI_API_KEY"):
            params["api_key"] = key
        pause()
        out.append({"pmids": chunk, "xml": get(EFETCH, params, timeout=180).body.decode()})
    return out


def land_notices(
    store: Store, snapshot_date: date | None = None, get=http.get, pause=None
) -> list[tuple[Manifest, bool]]:
    """Every PubMed retraction notice and its full record, for the link study."""
    day = snapshot_date or date.today()
    found = fetch_flagged(NOTICE_TERM, get=get, pause=pause)
    ids_landed = land(
        store,
        source="pubmed_notices",
        snapshot_date=day,
        name="retraction_notice_pmids.json",
        raw=json.dumps(found, sort_keys=True).encode(),
        origin_url=f"{ESEARCH}?db=pubmed&term={NOTICE_TERM}",
        origin_version="esearch",
        records=len(found["pmids"]),
        cost_usd=0.0,
        notes={"term": NOTICE_TERM, **completeness(found)},
    )
    batches = fetch_records(found["pmids"], get=get, pause=pause)
    links: dict[str, dict] = {}
    for b in batches:
        links.update(retraction_of(b["xml"]))
    records_landed = land(
        store,
        source="pubmed_notices",
        snapshot_date=day,
        name="efetch.jsonl",
        raw="".join(json.dumps(b, sort_keys=True) + "\n" for b in batches).encode(),
        origin_url=EFETCH,
        origin_version="efetch",
        records=len(links),
        cost_usd=0.0,
        notes={
            "requested": len(found["pmids"]),
            "returned": len(links),
            "with_retraction_of_pmid": sum(1 for v in links.values() if v["pmids"]),
            "with_retraction_of_without_pmid": sum(
                1 for v in links.values() if v["refs_without_pmid"]
            ),
        },
        manifest_name="_manifest_efetch.json",
    )
    return [ids_landed, records_landed]
