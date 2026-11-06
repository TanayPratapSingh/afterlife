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
from pathlib import Path

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


LABELS = ("acknowledges", "relies", "uses_method", "disputes", "mentions", "cannot_determine")
PRACTICE = OUT / "practice.xlsx"
_HEADER = (
    "ID",
    "Retracted paper",
    "Retracted in",
    "Citing article",
    "Cited in",
    "Citing text",
    "Reference entry",
    "Label",
    "Notes",
)
_GUIDE = [
    ("What this is", "Practice only. These 53 rows never count in any result."),
    ("", "Every cited paper here was retracted. Label what the citing article does with it."),
    ("", "Full rules: docs/citations-codebook.md. When two labels fit, the higher one wins."),
    (
        "acknowledges",
        "The citing text or reference entry says the paper was retracted or withdrawn.",
    ),
    (
        "relies",
        "States a finding of the paper as true, or uses it as evidence; a table row of results.",
    ),
    ("uses_method", "Uses a method, instrument, dataset, cell line, protocol, or code from it."),
    ("disputes", "Questions or contradicts its findings without saying it was retracted."),
    ("mentions", "Cited without using its finding: an example of a topic, a pointer."),
    ("cannot_determine", "Too short, garbled, or ambiguous. Say why in Notes."),
    ("Notes tags", "Start Notes with any that apply: self, table, range, extraction, language."),
    (
        "Confusing row?",
        "Write what confused you in Notes. That is the point of the practice round.",
    ),
]


def _practice_rows(pilot: Path) -> list[dict]:
    """The readable pilot citations, refetched so each carries its reference entry."""
    draws = [json.loads(line) for line in pilot.read_text().splitlines()]
    keep = [d for d in draws if d["sentences"] or d["table_rows"]]
    with ThreadPoolExecutor(max_workers=16) as pool:
        docs = dict(
            zip(
                [d["citing_pmcid"] for d in keep],
                pool.map(pmc.fetch_document, [d["citing_pmcid"] for d in keep]),
                strict=True,
            )
        )
    rows = []
    for i, d in enumerate(keep, 1):
        c = jats.citances(
            docs[d["citing_pmcid"]]["xml"], doi=d["doi"], pmid=d["pmid"], title=d["title"]
        )
        text = c["sentences"] + [f"Table row: {r}" for r in c["table_rows"]]
        rows.append(
            {
                "id": f"P{i:02d}",
                "title": d["title"],
                "paper_url": f"https://doi.org/{d['doi']}" if d["doi"] else None,
                "retraction_year": d["retraction_year"],
                "citing_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{d['citing_pmcid']}/",
                "citing_year": d["citing_year"],
                "text": "\n\n".join(text),
                "reference": "\n\n".join(c["references"]),
            }
        )
    return rows


def write_practice(rows: list[dict], path: Path = PRACTICE) -> dict:
    if path.exists():
        raise SystemExit(f"{path} exists. It may hold labels; move it aside first.")
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    base, bold = Font(name="Arial", size=10), Font(name="Arial", size=10, bold=True)
    link = Font(name="Arial", size=10, color="1F5FBF", underline="single")
    fill_in = PatternFill("solid", fgColor="FFF4C2")
    first, last = 4, 4 + len(rows) - 1
    wb = Workbook()
    ws = wb.active
    ws.title = "Label"
    ws["A1"] = "Practice: does each citation of a retracted paper warn, rely, or just mention it?"
    ws["A1"].font = Font(name="Arial", size=14, bold=True)
    ws["A2"] = f"=COUNTA(H{first}:H{last})"
    ws["B2"] = f"labelled so far, out of {len(rows)}. Rules on the Guide tab."
    ws["A2"].font, ws["B2"].font = bold, base
    ws["A2"].alignment = Alignment(horizontal="left")
    for col, name in enumerate(_HEADER, 1):
        cell = ws.cell(row=first - 1, column=col, value=name)
        cell.font = bold
        if name in ("Label", "Notes"):
            cell.fill = fill_in
    for i, r in enumerate(rows, first):
        values = (
            r["id"],
            r["title"],
            r["retraction_year"],
            "Open citing article",
            r["citing_year"],
            r["text"],
            r["reference"],
            None,
            None,
        )
        for col, v in enumerate(values, 1):
            cell = ws.cell(row=i, column=col, value=v)
            cell.font = base
            cell.alignment = Alignment(vertical="top", wrap_text=col in (2, 6, 7, 9))
        for col, url in ((2, r["paper_url"]), (4, r["citing_url"])):
            if url:
                ws.cell(row=i, column=col).hyperlink = url
                ws.cell(row=i, column=col).font = link
        for col in (8, 9):
            ws.cell(row=i, column=col).fill = fill_in
    dv = DataValidation(
        type="list",
        formula1='"' + ",".join(LABELS) + '"',
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Not a label",
        error="Pick one of the six labels from the list.",
    )
    ws.add_data_validation(dv)
    dv.add(f"H{first}:H{last}")
    ws.conditional_formatting.add(
        f"A{first}:I{last}",
        FormulaRule(formula=[f"LEN($H{first})>0"], fill=PatternFill("solid", fgColor="DDF0DD")),
    )
    for col, width in zip("ABCDEFGHI", (6, 36, 9, 14, 8, 70, 40, 16, 30), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = f"B{first}"
    guide = wb.create_sheet("Guide")
    guide.column_dimensions["A"].width = 18
    guide.column_dimensions["B"].width = 90
    for i, (a, b) in enumerate(_GUIDE, 1):
        guide.cell(row=i, column=1, value=a or None).font = bold
        guide.cell(row=i, column=2, value=b).font = base
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {
        "workbook": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
        "rows": len(rows),
    }


def practice() -> dict:
    pilot = max(OUT.glob("*-pilot.jsonl"))
    out = write_practice(_practice_rows(pilot))
    return {**out, "from_pilot": str(pilot.relative_to(ROOT))}
