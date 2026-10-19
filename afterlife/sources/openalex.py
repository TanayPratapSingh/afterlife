"""OpenAlex: the open scholarly graph, queried for every work it marks retracted.

OpenAlex is the only one of the four registries that also carries citation
counts by year, which is what the afterlife measurement needs: how many times
a paper was cited in the years after it was withdrawn.

Two properties of the API shape this module.

1. Every request has a price, returned in `meta.cost_usd`, and each key (or IP,
   without a key) has a daily dollar cap. The fetcher meters spend against a
   `Budget` and stops cleanly instead of running into a 429 halfway through.
2. `counts_by_year` only covers recent years. A citation count "after
   retraction" is therefore a lower bound for older retractions, and the
   window is recorded in the manifest so no downstream number forgets it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import date, timedelta

from afterlife.core import http
from afterlife.core.budget import Budget, BudgetExhausted
from afterlife.core.landing import Manifest, Store, land

SOURCE = "openalex"
API = "https://api.openalex.org/works"
FIELDS = (
    "id",
    "doi",
    "ids",
    "display_name",
    "publication_year",
    "publication_date",
    "type",
    "cited_by_count",
    "counts_by_year",
    "is_retracted",
    "primary_topic",
)
PRICE_ESTIMATE_USD = 0.0001  # observed list query price; the real charge is read from meta


def _params(cursor: str, per_page: int, filter_: str) -> dict:
    p = {
        "filter": filter_,
        "per-page": per_page,
        "cursor": cursor,
        "select": ",".join(FIELDS),
    }
    return p


def _slim(work: dict) -> dict:
    """Keep the fields the pipeline reads. primary_topic is large; only its
    field and domain names are used."""
    topic = work.get("primary_topic") or {}
    out = {k: work.get(k) for k in FIELDS if k != "primary_topic"}
    out["ids"] = {k: v for k, v in (work.get("ids") or {}).items() if k in {"pmid", "pmcid", "doi"}}
    out["field"] = ((topic.get("field") or {}).get("display_name")) or None
    out["domain"] = ((topic.get("domain") or {}).get("display_name")) or None
    return out


BASIC_PAGING_CEILING = 10_000  # page * per_page may not exceed this without a key


def _call(get, budget: Budget, params: dict) -> dict:
    budget.require(PRICE_ESTIMATE_USD)
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        params = {**params, "api_key": key}
    body = get(API, params).json()
    budget.charge(float(body.get("meta", {}).get("cost_usd") or 0.0))
    return body


def _cursor_pages(get, budget: Budget, filter_: str, per_page: int) -> tuple[list[dict], dict]:
    """Cursor paging. Needs an API key since OpenAlex changed its access
    model; kept because it is one call per page with no partition overhead."""
    rows: list[dict] = []
    cursor: str | None = "*"
    expected = None
    try:
        while cursor:
            body = _call(get, budget, _params(cursor, per_page, filter_))
            expected = body["meta"].get("count", expected)
            rows.extend(_slim(w) for w in body.get("results", []))
            cursor = body["meta"].get("next_cursor") if body.get("results") else None
    except BudgetExhausted:
        return rows, {"mode": "cursor", "expected": expected, "complete": False}
    return rows, {"mode": "cursor", "expected": expected, "complete": True}


def _basic_params(filter_: str, lo: date, hi: date, page: int, per_page: int) -> dict:
    return {
        "filter": f"{filter_},from_publication_date:{lo},to_publication_date:{hi}",
        "per-page": per_page,
        "page": page,
        "select": ",".join(FIELDS),
    }


def _partitions(start: date, end: date) -> list[tuple[date, date]]:
    """One range before 2000, then one per calendar year."""
    out = [(start, date(1999, 12, 31))]
    out += [(date(y, 1, 1), date(y, 12, 31)) for y in range(2000, end.year + 1)]
    return out


def _basic_pages(
    get, budget: Budget, filter_: str, per_page: int, today: date
) -> tuple[list[dict], dict]:
    """Keyless fallback: split by publication date until every slice fits
    under the basic paging ceiling, then page each slice.

    The split is checked, not trusted. The total count for the unpartitioned
    filter is fetched first, and the slice counts must add up to it; any gap
    (works with no publication date, or dated outside the ranges) is reported
    in the run info rather than silently dropped.
    """
    total = _call(get, budget, {"filter": filter_, "per-page": 1, "select": "id"})["meta"]["count"]
    rows: list[dict] = []
    slices: list[dict] = []
    stack = list(reversed(_partitions(date(1800, 1, 1), today)))
    try:
        while stack:
            lo, hi = stack.pop()
            first = _call(get, budget, _basic_params(filter_, lo, hi, 1, per_page))
            n = first["meta"]["count"]
            if n > BASIC_PAGING_CEILING and hi > lo:
                mid = lo + (hi - lo) // 2
                stack.append((mid + timedelta(days=1), hi))
                stack.append((lo, mid))
                continue
            got = [_slim(w) for w in first.get("results", [])]
            page = 2
            while len(got) < min(n, BASIC_PAGING_CEILING):
                body = _call(get, budget, _basic_params(filter_, lo, hi, page, per_page))
                if not body.get("results"):
                    break
                got.extend(_slim(w) for w in body["results"])
                page += 1
            rows.extend(got)
            slices.append({"from": str(lo), "to": str(hi), "count": n, "fetched": len(got)})
    except BudgetExhausted:
        pending = [{"from": str(lo), "to": str(hi)} for lo, hi in reversed(stack)]
        return rows, {
            "mode": "basic",
            "expected": total,
            "complete": False,
            "slices": slices,
            "pending": pending,
        }
    covered = sum(s["count"] for s in slices)
    return rows, {
        "mode": "basic",
        "expected": total,
        "complete": True,
        "slice_count_sum": covered,
        "uncovered_by_date_slices": total - covered,
        "slices": slices,
    }


def fetch_retracted(
    budget: Budget,
    *,
    per_page: int = 200,
    filter_: str = "is_retracted:true",
    get=http.get,
    today: date | None = None,
) -> tuple[list[dict], dict]:
    """Every work matching the filter. Returns (rows, run_info).

    run_info carries `complete` and `expected` so a run that stopped on budget
    is never mistaken for a full result.
    """
    if os.environ.get("OPENALEX_API_KEY"):
        rows, info = _cursor_pages(get, budget, filter_, per_page)
    else:
        rows, info = _basic_pages(get, budget, filter_, per_page, today or date.today())
    # Basic paging over overlapping or shifting slices can repeat a work.
    unique = {r["id"]: r for r in rows}
    info.update(
        {
            "fetched": len(rows),
            "unique": len(unique),
            "filter": filter_,
            "counts_by_year_min_year": min(
                (c["year"] for r in unique.values() for c in (r.get("counts_by_year") or [])),
                default=None,
            ),
            **budget.summary(),
        }
    )
    return list(unique.values()), info


def to_jsonl(rows: Iterable[dict]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode()


def land_retracted(
    store: Store, budget: Budget, snapshot_date: date | None = None, get=http.get
) -> tuple[Manifest, bool]:
    rows, info = fetch_retracted(budget, get=get)
    return land(
        store,
        source=SOURCE,
        snapshot_date=snapshot_date or date.today(),
        name="works_retracted.jsonl",
        raw=to_jsonl(rows),
        origin_url=f"{API}?filter={info['filter']}",
        origin_version="api",
        records=len(rows),
        cost_usd=info["spent_usd"],
        notes=info,
    )
