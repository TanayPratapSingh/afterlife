"""Post retraction citations: do the citing sentences warn or rely?

Second study. The first paper counted citations after retraction; this one
reads them. `afterlife citances pilot` measures whether that is feasible at
all before any labelling starts: how many post retraction citations have a
citing article whose full text is in PMC's open data, and in how many the
sentence that cites the retracted paper can be found.

The draw is two stage. A retracted paper is picked with probability
proportional to its post retraction citations (gold.afterlife_citations, the
headline frame of the first paper), then OpenAlex's seeded sampler returns one
of its citing works published after the retraction year. Every post
retraction citation is then equally likely, so shares over the draws estimate
shares over all 241,815 citations.

Citing sentences are other people's text, so they stay in data/citances/
(git ignored). The run report holds counts only.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from afterlife.audit import wilson
from afterlife.core import ids, jats
from afterlife.core.budget import Budget, BudgetExhausted
from afterlife.gold import ROOT
from afterlife.sources import openalex, pmc

SEED = "afterlife-citances-pilot-2026-11-06"
OUT = ROOT / "data" / "citances"


def frame(con) -> list[dict]:
    rows = con.execute(
        """
        select a.paper_key, a.openalex_id, a.retraction_year, a.citations_after,
               r.doi, r.pmid, r.title
        from gold.afterlife_citations a join gold.registry r using (paper_key)
        where a.window_complete and not a.retract_and_replace and a.citations_after > 0
        order by a.paper_key"""
    ).fetchall()
    cols = (
        "paper_key",
        "openalex_id",
        "retraction_year",
        "citations_after",
        "doi",
        "pmid",
        "title",
    )
    return [dict(zip(cols, r, strict=True)) for r in rows]


def draw(rows: list[dict], n: int, seed: str = SEED) -> list[dict]:
    """n papers with replacement, each in proportion to its post retraction citations."""
    return random.Random(seed).choices(rows, weights=[r["citations_after"] for r in rows], k=n)


def _share(k: int, n: int) -> dict:
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "ci95": wilson(k, n)}


def pilot(n: int = 200, budget_usd: float = 0.05, workers: int = 16, store=None) -> dict:
    from afterlife.export import _connect

    rows = frame(_connect())
    picks = draw(rows, n)
    budget = Budget(budget_usd)
    draws: list[dict] = []
    for i, p in enumerate(picks, start=1):
        try:
            work = openalex.sample_citing(p["openalex_id"], p["retraction_year"], i, budget)
        except BudgetExhausted:
            break
        w = work or {}
        wid = w.get("ids") or {}
        draws.append(
            {
                "draw": i,
                **p,
                "citing_id": w.get("id"),
                "citing_type": w.get("type"),
                "citing_year": w.get("publication_year"),
                "citing_doi": ids.doi(w.get("doi")),
                "citing_pmid": ids.pmid(wid.get("pmid")),
                "citing_pmcid": ids.pmcid(wid.get("pmcid")),
                "pmcid_from": "openalex" if wid.get("pmcid") else None,
            }
        )

    # OpenAlex lacks many PMCIDs; NCBI's PMC id map fills them in from DOI or PMID.
    gaps = [d for d in draws if d["citing_id"] and not d["citing_pmcid"]]
    if gaps and store is not None:
        hits = pmc.lookup(
            store,
            dois=[d["citing_doi"] for d in gaps if d["citing_doi"]],
            pmids=[d["citing_pmid"] for d in gaps if d["citing_pmid"]],
        )
        for d in gaps:
            h = hits.get(("doi", d["citing_doi"])) or hits.get(("pmid", d["citing_pmid"]))
            if h:
                d["citing_pmcid"], d["pmcid_from"] = h["pmcid"], "pmc_ids"

    want = sorted({d["citing_pmcid"] for d in draws if d["citing_pmcid"]})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        docs = {r["pmcid"]: r for r in pool.map(pmc.fetch_document, want)}
    for d in draws:
        doc = docs.get(d["citing_pmcid"]) or {}
        d["in_open_data"] = bool(doc.get("in_open_data"))
        d["license_code"] = doc.get("license_code")
        d["has_xml"] = bool(doc.get("xml"))
        c = (
            jats.citances(doc["xml"], doi=d["doi"], pmid=d["pmid"], title=d["title"])
            if doc.get("xml")
            else None
        )
        d["has_body"] = c["has_body"] if c else None
        d["matched_by"] = c["matched_by"] if c else []
        for k in ("xrefs", "ranges"):
            d[k] = c[k] if c else 0
        for k in ("sentences", "table_rows", "references"):
            d[k] = c[k] if c else []

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows_path = OUT / f"{stamp}-pilot.jsonl"
    rows_path.write_text("".join(json.dumps(d, sort_keys=True) + "\n" for d in draws))

    done = len(draws)
    found = [d for d in draws if d["citing_id"]]
    return {
        "seed": SEED,
        "frame": {"papers": len(rows), "citations_after": sum(r["citations_after"] for r in rows)},
        "drawn": done,
        "requested": n,
        "distinct_papers": len({d["paper_key"] for d in draws}),
        "citing_work_found": _share(len(found), done),
        "citing_types": dict(Counter(d["citing_type"] for d in found).most_common()),
        "with_pmcid": _share(sum(1 for d in draws if d["citing_pmcid"]), done),
        "pmcid_from": dict(Counter(d["pmcid_from"] for d in draws if d["citing_pmcid"])),
        "in_pmc_open_data": _share(sum(d["in_open_data"] for d in draws), done),
        "with_xml": _share(sum(d["has_xml"] for d in draws), done),
        "licenses": dict(Counter(d["license_code"] for d in draws if d["in_open_data"])),
        "reference_matched": _share(sum(1 for d in draws if d["matched_by"]), done),
        "matched_by": dict(Counter(m for d in draws for m in d["matched_by"])),
        "with_citing_text": _share(
            sum(1 for d in draws if d["sentences"] or d["table_rows"]), done
        ),
        "with_sentence": sum(1 for d in draws if d["sentences"]),
        "only_in_table": sum(1 for d in draws if d["table_rows"] and not d["sentences"]),
        "found_through_range": sum(1 for d in draws if d["ranges"]),
        "matched_but_no_text": sum(
            1 for d in draws if d["matched_by"] and not (d["sentences"] or d["table_rows"])
        ),
        "rows": str(rows_path.relative_to(ROOT)),
        "openalex": budget.summary(),
    }
