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


# How many registries flag a gold.registry row. Shared with the hand audit's
# sampling frame so "flagged by exactly one" means the same thing in both.
FLAGGED_SQL = """(case when rw_status = 'retracted' then 1 else 0 end)
              + (case when flag_openalex then 1 else 0 end)
              + (case when pmid is not null and flag_pubmed then 1 else 0 end)
              + (case when flag_pmc then 1 else 0 end)"""


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
        f"""
        with e as (
          select
            {FLAGGED_SQL} as flagged,
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
    # From the notice's side: of Retraction Watch's retraction notice DOIs, how
    # many does OpenAlex flag as retracted? A notice DOI that is also some
    # paper's DOI (common for IEEE) cannot be told apart, so it is left out.
    lake = _lake()
    con.execute(
        f"""create or replace temp table notice_dois as
        select distinct n.notice_doi as d from delta_scan('{lake}/silver/rw_notices') n
        where n.nature = 'Retraction' and n.notice_doi is not null"""
    )
    con.execute(
        f"""create or replace temp table paper_dois as select distinct doi as d
            from delta_scan('{lake}/silver/rw_notices') where doi is not null"""
    )
    con.execute(
        f"""create or replace temp table oa_flagged_dois as select distinct doi as d
            from delta_scan('{lake}/silver/openalex_works') where is_retracted"""
    )
    notice_side = _one(
        con,
        """with nd as (select * from notice_dois anti join paper_dois using (d))
           select (select count(*) from nd) as notice_dois,
                  (select count(*) from nd join oa_flagged_dois using (d))
                    as flagged_by_openalex""",
    )
    return {
        "pmc_notice_articles": pmc_notices,
        "rw_notice_dois_flagged_by_openalex": notice_side,
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
    # One publisher's batch can set a year's median (IEEE conference
    # retractions in 2009 to 2011), so each year also reports its largest
    # publisher and the median without it.
    by_year_top = _rows(
        con,
        """
        with l as (select * from gold.retraction_lag
                   where not negative_lag and retraction_year >= 2005),
        top as (select retraction_year, arg_max(publisher, n) as publisher, max(n) as notices
                from (select retraction_year, publisher, count(*) as n from l group by all)
                group by 1)
        select t.retraction_year, t.publisher as top_publisher, t.notices as top_notices,
               quantile_cont(l.lag_days, 0.5) filter (where l.publisher = t.publisher)
                 as top_median_days,
               quantile_cont(l.lag_days, 0.5)
                 filter (where l.publisher is distinct from t.publisher)
                 as median_days_without_top
        from l join top t using (retraction_year)
        group by all order by 1""",
    )
    return {**overall, "by_retraction_year": by_year, "by_year_top_publisher": by_year_top}


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


def as_of(con, lake: str) -> dict:
    """The same questions asked of every replayed snapshot.

    Each question looks only at retractions dated 2023 or earlier, so any
    change between snapshots is the database revising the past. The four
    questions were fixed before any of them was computed, and all four are
    reported: how many, how many cite "Paper Mill", how many cite "Fake Peer
    Review", and the median days from publication to retraction.
    """
    con.execute(
        f"""create or replace temp table asof_h as
        select cast(record_id as int) as id, valid_from, valid_to, publisher,
               retraction_nature = 'Retraction' as is_retraction, lower(reason) as reason,
               cast(strptime(split_part(retraction_date, ' ', 1), '%m/%d/%Y') as date) as rd,
               cast(strptime(split_part(original_paper_date, ' ', 1), '%m/%d/%Y') as date) as od
        from delta_scan('{lake}/silver/rw_history')"""
    )
    snaps = [
        r["d"]
        for r in _rows(
            con,
            f"""select distinct cast(snapshot_date as date) as d
                from delta_scan('{lake}/silver/rw_replay_log') order by 1""",
        )
    ]
    first, last = snaps[0], snaps[-1]
    series = _rows(
        con,
        f"""
        with s as (select unnest({[d.isoformat() for d in snaps]}::date[]) as d)
        select s.d as snapshot_date,
               count(*) as retractions_through_2023,
               count_if(h.reason like '%paper mill%') as paper_mill,
               count_if(h.reason like '%fake peer review%') as fake_peer_review,
               median(h.rd - h.od) as median_lag_days
        from s join asof_h h on h.valid_from <= s.d and (h.valid_to is null or h.valid_to > s.d)
        where h.is_retraction and year(h.rd) <= 2023
        group by 1 order by 1""",
    )
    con.execute(
        f"""create or replace temp table asof_first as select * from asof_h
            where valid_from <= date '{first}'
              and (valid_to is null or valid_to > date '{first}')"""
    )
    con.execute(
        "create or replace temp table asof_last as select * from asof_h where valid_to is null"
    )
    con.execute(
        """create or replace temp table asof_late as
           select l.* from asof_last l anti join asof_first f using (id)
           where l.is_retraction and year(l.rd) <= 2023"""
    )
    late = _one(
        con,
        """select count(*) as records,
                  count_if(id > (select max(id) from asof_first)) as id_above_first_snapshot_max,
                  count_if(reason like '%paper mill%') as paper_mill,
                  count_if(year(rd) = 2023) as dated_2023,
                  median(rd - od) as median_lag_days
           from asof_late""",
    )
    late["by_publisher"] = _rows(
        con,
        "select publisher, count(*) as records from asof_late group by 1 order by 2 desc limit 5",
    )
    late["by_month_first_seen"] = _rows(
        con,
        """with seen as (
             select h.id, strftime(min(h.valid_from), '%Y-%m') as month
             from asof_h h join asof_late l on h.id = l.id group by h.id)
           select month, count(*) as records from seen group by 1 order by 1""",
    )
    cohort_2023 = _one(
        con,
        """select
             (select count(*) from asof_first where is_retraction and year(rd) = 2023) as first,
             (select count(*) from asof_last where is_retraction and year(rd) = 2023) as last""",
    )
    left = _one(
        con,
        """with f as (select * from asof_first where is_retraction and year(rd) <= 2023)
           select count_if(l.id is null) as deleted,
                  count_if(l.id is not null and not (l.is_retraction and year(l.rd) <= 2023))
                    as moved_out_of_cohort
           from f left join asof_last l using (id)""",
    )
    return {
        "first_snapshot": first,
        "last_snapshot": last,
        "series": series,
        "retractions_dated_2023": cohort_2023,
        "late_records": late,
        "left_cohort": left,
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
        "as_of": as_of(con, lake),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2, default=str))
    Path("runs").mkdir(exist_ok=True)
