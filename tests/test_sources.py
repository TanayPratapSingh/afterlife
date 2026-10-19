"""The fetchers against fake APIs. No network."""

from __future__ import annotations

import json
from datetime import date

import pytest

from afterlife.core.budget import Budget
from afterlife.core.http import Response
from afterlife.core.landing import LocalStore
from afterlife.sources import openalex, pubmed
from afterlife.sources import retraction_watch as rw


def _resp(obj, headers=None):
    return Response("u", 200, headers or {}, json.dumps(obj).encode())


# Retraction Watch -----------------------------------------------------------

HEADER = ",".join(rw.EXPECTED_COLUMNS) + ","


def test_list_snapshots_dedupes_by_data_date_and_follows_pages():
    pages = {
        1: [
            {"id": "c3", "created_at": "2026-01-03T23:00:00Z", "title": "Retraction Watch data for 2026-01-03"},
            {"id": "c2b", "created_at": "2026-01-02T23:30:00Z", "title": "Retraction Watch data for 2026-01-02"},
        ],
        2: [
            {"id": "c2a", "created_at": "2026-01-02T23:00:00Z", "title": "Retraction Watch data for 2026-01-02"},
            {"id": "c1", "created_at": "2026-01-01T09:00:00Z", "title": "manual fix"},
        ],
    }

    def get(url, params):
        page = params["page"]
        return _resp(pages.get(page, []), {"X-Next-Page": "2" if page == 1 else ""})

    snaps = rw.list_snapshots(get=get)
    assert [s.sha for s in snaps] == ["c1", "c2b", "c3"]
    assert snaps[0].snapshot_date == date(2026, 1, 1)


def test_select_monthly_plus_recent_always_includes_newest():
    snaps = [rw.Snapshot(f"s{i}", "", date(2026, m, d)) for i, (m, d) in
             enumerate([(1, 1), (1, 15), (2, 3), (2, 20), (3, 1), (3, 2)])]
    picked = rw.select(snaps, monthly=True, last_days=1)
    assert [s.snapshot_date for s in picked] == [date(2026, 1, 1), date(2026, 2, 3),
                                                 date(2026, 3, 1), date(2026, 3, 2)]


def test_validate_header_rejects_schema_drift():
    assert rw.validate_header((HEADER + "\n").encode())[0] == "Record ID"
    with pytest.raises(ValueError, match="RetractionNature"):
        rw.validate_header(HEADER.replace("RetractionNature", "Nature").encode())


def test_fetch_snapshot_records_encoding(tmp_path):
    row = '1,"People’s paper",,,,,,,,,1/2/2020 0:00,,0,1/1/2019 0:00,10.1/x,0,Retraction,,No,,'
    raw = (HEADER + "\n" + row + "\n").encode("cp1252")
    snap = rw.Snapshot("abc", "2026-01-01T00:00:00Z", date(2026, 1, 1))
    m, new = rw.fetch_snapshot(LocalStore(tmp_path), snap,
                               get=lambda url, timeout: Response(url, 200, {}, raw))
    assert new and m.records == 1
    assert m.notes["encoding"] == "cp1252" and m.notes["invalid_utf8_bytes"] == 1


# OpenAlex -------------------------------------------------------------------


class FakeOpenAlex:
    """Answers basic paged queries over a fixed set of works, by date."""

    def __init__(self, works, ceiling=4):
        self.works = works
        self.ceiling = ceiling
        self.calls = 0

    def __call__(self, url, params):
        self.calls += 1
        filt = dict(part.split(":", 1) for part in params["filter"].split(","))
        lo = filt.get("from_publication_date", "0000-00-00")
        hi = filt.get("to_publication_date", "9999-99-99")
        hits = [w for w in self.works if lo <= w["publication_date"] <= hi]
        per, page = params["per-page"], params.get("page", 1)
        if page * per > self.ceiling and per > 1:
            raise AssertionError("fetcher paged past the basic paging ceiling")
        rows = hits[(page - 1) * per: page * per]
        return _resp({"meta": {"count": len(hits), "cost_usd": 0.0001}, "results": rows})


def _work(i, day):
    return {"id": f"https://openalex.org/W{i}", "publication_date": day, "doi": None, "ids": {},
            "counts_by_year": [{"year": 2025, "cited_by_count": 1}], "primary_topic": None}


def test_openalex_partitions_until_slices_fit(monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.setattr(openalex, "BASIC_PAGING_CEILING", 4)
    works = [_work(i, f"2023-0{1 + i % 9}-1{i % 9}") for i in range(11)] + [_work(99, "1995-05-05")]
    fake = FakeOpenAlex(works, ceiling=4)
    rows, info = openalex.fetch_retracted(Budget(1.0), per_page=2, get=fake, today=date(2024, 1, 1))
    assert info["complete"] and info["expected"] == 12
    assert {r["id"] for r in rows} == {w["id"] for w in works}
    assert info["uncovered_by_date_slices"] == 0
    assert all(s["count"] <= 4 for s in info["slices"])


def test_openalex_stops_cleanly_on_budget(monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    works = [_work(i, "2023-01-01") for i in range(3)]
    rows, info = openalex.fetch_retracted(Budget(0.0002), per_page=1,
                                          get=FakeOpenAlex(works, ceiling=10), today=date(2001, 1, 1))
    assert info["complete"] is False and info["pending"]
    assert info["calls"] == 2


def test_openalex_slim_keeps_only_used_fields():
    w = {"id": "W1", "ids": {"pmid": "p", "mag": "m"}, "primary_topic":
         {"field": {"display_name": "Medicine"}, "domain": {"display_name": "Health Sciences"}}}
    s = openalex._slim(w)
    assert s["ids"] == {"pmid": "p"} and s["field"] == "Medicine" and "primary_topic" not in s


# PubMed ---------------------------------------------------------------------


def test_pubmed_splits_and_reports_overlap():
    # Record 3 has print and electronic dates in different years, so it is
    # returned by two slices. The union must still equal the total.
    by_year = {2020: ["1", "2", "3"], 2021: ["3", "4"]}

    def get(url, params):
        if "mindate" not in params:
            return _resp({"esearchresult": {"count": "4", "idlist": []}})
        y = int(params["mindate"][:4])
        ids_ = by_year.get(y, []) if params["mindate"][5:] == "01/01" else []
        return _resp({"esearchresult": {"count": str(len(ids_)), "idlist": ids_}})

    result = pubmed.fetch_flagged(today=date(2021, 6, 1), get=get, pause=lambda: None)
    c = pubmed.completeness(result)
    assert result["pmids"] == ["1", "2", "3", "4"]
    assert c["complete"] and c["records_in_more_than_one_slice"] == 1
