"""The headline numbers, computed from gold and silver, never typed by hand.

`afterlife findings` writes runs/<timestamp>-findings.json. RESULTS.md and the
README quote that file, and every quoted number names the key it came from.
If a number is not produced here, it does not go in a document.
"""

from __future__ import annotations

import json
from pathlib import Path

from afterlife.export import _connect, _lake
from afterlife.gold import ROOT


def _rows(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _one(con, sql: str) -> dict:
    return _rows(con, sql)[0]


def registries(con) -> dict:
    by_verdict = {
        r["verdict"]: r["n"]
        for r in _rows(
            con, "select verdict, count(*) n from gold.registry group by 1 order by 2 desc"
        )
    }
    agreement = _rows(
        con,
        """
        select source, eligible, flagged, round(flagged / eligible, 4) as recall_vs_union
        from gold.registry_agreement order by source""",
    )
    combos = _rows(
        con,
        """
        select
          rw_status = 'retracted' as rw, flag_openalex as openalex, flag_pubmed as pubmed,
          flag_pmc as pmc, count(*) as papers
        from gold.registry where verdict in ('retracted', 'disputed')
        group by all order by papers desc limit 12""",
    )
    unanimity = _one(
        con,
        """
        with e as (
          select
            (case when rw_status = 'retracted' then 1 else 0 end)
              + (case when flag_openalex then 1 else 0 end)
              + (case when pmid is not null and flag_pubmed then 1 else 0 end)
              + (case when flag_pmc then 1 else 0 end) as flagged,
            2 + (case when pmid is not null then 1 else 0 end)
              + (case when flag_pmc is not null then 1 else 0 end) as eligible
          from gold.registry where verdict in ('retracted', 'disputed'))
        select count(*) as papers,
               sum(case when flagged = eligible then 1 else 0 end) as all_eligible_agree,
               sum(case when flagged = 1 then 1 else 0 end) as flagged_by_exactly_one
        from e""",
    )
    exclusions = _rows(
        con,
        """select source, notice_reason, rows_excluded from gold.flag_exclusions
           order by rows_excluded desc""",
    )
    oa_types = _rows(
        con,
        f"""select type, count(*) as works from delta_scan('{_lake()}/silver/openalex_works')
            group by 1 order by 2 desc limit 6""",
    )
    pmc_notices = _one(
        con,
        """select count(*) as notice_articles,
                  sum(case when source_flag then 1 else 0 end) as flagged_is_retracted
           from gold.int_paper_identity
           where source = 'pmc' and notice_reason is not null""",
    )
    return {
        "pmc_notice_articles": pmc_notices,
        "by_verdict": by_verdict,
        "agreement": agreement,
        "combinations": combos,
        "excluded_as_notices": exclusions,
        "openalex_retracted_by_type": oa_types,
        **unanimity,
    }


def lag(con) -> dict:
    overall = _one(
        con,
        """
        select count(*) as notices,
               sum(case when negative_lag then 1 else 0 end) as negative_lag_excluded,
               quantile_cont(lag_days, 0.5) filter (where not negative_lag) as median_days,
               quantile_cont(lag_days, 0.25) filter (where not negative_lag) as p25_days,
               quantile_cont(lag_days, 0.75) filter (where not negative_lag) as p75_days
        from gold.retraction_lag""",
    )
    by_year = _rows(
        con,
        """
        select retraction_year, count(*) as notices,
               quantile_cont(lag_days, 0.5) as median_days
        from gold.retraction_lag where not negative_lag and retraction_year >= 2005
        group by 1 order by 1""",
    )
    return {**overall, "by_retraction_year": by_year}


def afterlife(con) -> dict:
    """Post retraction citations, headline excluding retract and replace.

    A paper retracted and republished in corrected form keeps collecting
    citations that may be meant for the correction, so it is reported
    separately rather than mixed into the headline.
    """

    def summary(where: str) -> dict:
        return _one(
            con,
            f"""
            select count(*) as papers,
                   sum(case when citations_after > 0 then 1 else 0 end) as with_any_after,
                   sum(citations_after) as citations_after,
                   sum(citations_through_retraction_year) as citations_through_retraction_year,
                   quantile_cont(citations_after, 0.5) as median_after,
                   quantile_cont(citations_after, 0.9) as p90_after
            from gold.afterlife_citations where window_complete and {where}""",
        )

    top = _rows(
        con,
        """
        select a.paper_key, r.title, a.retraction_year, a.citations_after,
               a.citations_through_retraction_year
        from gold.afterlife_citations a join gold.registry r using (paper_key)
        where a.window_complete and not a.retract_and_replace
        order by a.citations_after desc limit 10""",
    )
    all_papers = _one(
        con,
        """select count(*) as papers,
                  sum(case when window_complete then 1 else 0 end) as window_complete
           from gold.afterlife_citations""",
    )
    return {
        **all_papers,
        "headline_excluding_retract_and_replace": summary("not retract_and_replace"),
        "retract_and_replace_only": summary("retract_and_replace"),
        "top_by_citations_after": top,
    }


def reasons(con) -> dict:
    latest = _one(con, "select max(retraction_year) - 1 as y from gold.reason_trends")["y"]
    top = _rows(
        con,
        f"""
        select reason, notices from gold.reason_trends
        where retraction_year = {latest} order by notices desc limit 10""",
    )
    watch = _rows(
        con,
        """
        select retraction_year, reason, notices from gold.reason_trends
        where retraction_year >= 2015
          and (reason ilike '%computer-generated%' or reason ilike '%paper mill%'
               or reason ilike '%peer review%')
        order by reason, retraction_year""",
    )
    return {"last_complete_year": latest, "top_last_complete_year": top, "series": watch}


def upstream(con, lake: str) -> dict:
    """What the source itself did, from the silver replay report and history."""
    reports = sorted((ROOT / "runs").glob("*-silver.json"))
    replay = []
    for p in reversed(reports):
        rep = json.loads(p.read_text())["report"]
        if rep.get("replay") and rep["replay"][0].get("snapshot_date") == "2024-09-02":
            replay = rep["replay"]
            source = p.name
            break
    else:
        source = None
    relabels = _rows(
        con,
        f"""
        with h as (select record_id, valid_from, reason,
                          lag(reason) over (partition by record_id order by valid_from) as prev
                   from delta_scan('{lake}/silver/rw_history')),
        c as (select record_id, change_date from delta_scan('{lake}/silver/rw_changes')
              where change_type = 'update' and changed_columns = ['reason']),
        pairs as (
          select h.valid_from as change_date,
                 list_sort(list_filter(string_split(lower(replace(h.prev, '+', '')), ';'),
                   x -> x <> '' and not list_contains(
                     string_split(lower(replace(h.reason, '+', '')), ';'), x))) as removed,
                 list_sort(list_filter(string_split(lower(replace(h.reason, '+', '')), ';'),
                   x -> x <> '' and not list_contains(
                     string_split(lower(replace(h.prev, '+', '')), ';'), x))) as added
          from h join c on h.record_id = c.record_id and h.valid_from = c.change_date)
        select change_date, removed, added, count(*) as records
        from pairs group by all order by records desc limit 12""",
    )
    edited = _one(
        con,
        f"""
        with c as (select record_id, changed_columns from delta_scan('{lake}/silver/rw_changes')
                   where change_type = 'update'),
             seen as (select count(distinct record_id) as n
                      from delta_scan('{lake}/silver/rw_history'))
        select (select n from seen) as records_ever_seen,
               count(distinct record_id) as records_ever_updated,
               count(distinct case when len(list_filter(changed_columns, x -> x <> 'reason')) > 0
                     then record_id end) as records_updated_beyond_reason,
               count(distinct case when list_contains(changed_columns, 'original_paper_doi')
                     then record_id end) as records_doi_changed,
               count(distinct case when list_contains(changed_columns, 'retraction_date')
                     then record_id end) as records_retraction_date_changed,
               count(distinct case when list_contains(changed_columns, 'retraction_nature')
                     then record_id end) as records_nature_changed
        from c""",
    )
    conflicting = _one(
        con,
        f"""select count(*) as papers_with_conflicting_pmids
            from delta_scan('{lake}/silver/rw_paper_status') where distinct_pmids > 1""",
    )
    return {
        "records_edited_after_first_appearance": edited,
        **conflicting,
        "replay_report": source,
        "snapshots": len(replay),
        "quarantined": [
            {k: r[k] for k in ("snapshot_date", "lossy_records", "lossy_by_column")}
            for r in replay
            if r.get("quarantined")
        ],
        "blank_rows_dropped": sum(r.get("blank_rows_dropped") or 0 for r in replay),
        "format_only_total": sum(r.get("format_only") or 0 for r in replay),
        "semantic_updates_total": sum(r.get("update") or 0 for r in replay[1:]),
        "deleted_total": sum(r.get("deleted") or 0 for r in replay),
        "reappeared_total": sum(r.get("reappear") or 0 for r in replay),
        "largest_days": sorted(
            (
                {
                    "snapshot_date": r["snapshot_date"],
                    "update": r.get("update", 0),
                    "format_only": r.get("format_only", 0),
                }
                for r in replay[1:]
            ),
            key=lambda r: -(r["update"] + r["format_only"]),
        )[:6],
        "top_reason_relabels": relabels,
    }


def sources() -> dict:
    """What each fetch landed and what it cost, read from the landing
    manifests, so numbers about the fetches trace to this file too."""
    import os

    from afterlife.core.landing import LocalStore, manifests

    store = LocalStore(os.environ.get("AFTERLIFE_LANDING", str(ROOT / "data")))
    rw = sorted(manifests(store, "retraction_watch"), key=lambda m: m.snapshot_date)
    out = {
        "retraction_watch": {
            "snapshots": len(rw),
            "first": rw[0].snapshot_date if rw else None,
            "last": rw[-1].snapshot_date if rw else None,
            "records_first": rw[0].records if rw else None,
            "records_last": rw[-1].records if rw else None,
            "raw_bytes_last": rw[-1].raw_bytes if rw else None,
            "non_utf8_snapshots": [
                {
                    "snapshot_date": m.snapshot_date,
                    **{k: m.notes[k] for k in ("encoding", "invalid_utf8_bytes") if k in m.notes},
                }
                for m in rw
                if (m.notes or {}).get("encoding") not in (None, "utf-8")
            ],
        }
    }
    for source in ("openalex", "pubmed", "pmc_ids", "pmc_metadata"):
        ms = sorted(manifests(store, source), key=lambda m: m.snapshot_date)
        if ms:
            m = ms[-1]
            notes = {k: v for k, v in (m.notes or {}).items() if k not in {"slices", "pending"}}
            out[source] = {
                "snapshot_date": m.snapshot_date,
                "records": m.records,
                "raw_bytes": m.raw_bytes,
                "cost_usd": m.cost_usd,
                **notes,
            }
    return out


def run() -> dict:
    con = _connect()
    lake = _lake()
    return {
        "sources": sources(),
        "caveat": "Measured on landed public snapshots; see coverage notes in the export.",
        "registries": registries(con),
        "retraction_lag": lag(con),
        "afterlife": afterlife(con),
        "reasons": reasons(con),
        "upstream": upstream(con, lake),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2, default=str))
    Path("runs").mkdir(exist_ok=True)
