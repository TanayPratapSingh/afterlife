"""Hand audit of the papers that exactly one registry flags.

`afterlife audit sample` draws a seeded, stratified sample from the papers in
gold.registry that exactly one registry flags and writes audit/labels.csv for
a person to label. The sheet is blind: it never says which registry flagged a
paper, and a leading "RETRACTED:" is stripped from titles because it gives
both the source and the answer away. The key (sample id to paper and stratum)
goes only into the run report.

`afterlife audit score` reads the labels back, validates them against the
codebook in docs/audit-codebook.md, and writes per registry precision with
Wilson intervals plus a stratified estimate over the whole frame.
"""

from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from urllib.parse import quote

from afterlife.gold import ROOT

LABELS = ("retracted", "notice", "concern", "corrected", "not_retracted", "cannot_determine")
COLUMNS = [
    "sample_id",
    "title",
    "year",
    "doi_url",
    "pubmed_url",
    "pmc_url",
    "search_url",
    "label",
    "evidence_url",
    "notes",
]
SEED = "afterlife-audit-2026-10-26"
SHEET = ROOT / "audit" / "labels.csv"

_PREFIX = re.compile(
    r"^\s*(?:\[\s*(?:retracted|withdrawn)(?:\s+article)?\s*\]|(?:retracted|withdrawn)(?:\s+article)?\s*[:.\-])\s*",
    re.IGNORECASE,
)


def clean_title(title: str | None) -> str:
    return _PREFIX.sub("", title or "").strip()


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Equal allocation, capped at each stratum's size, the remainder spread
    over the strata that still have papers left."""
    alloc = dict.fromkeys(sizes, 0)
    open_ = sorted(s for s, size in sizes.items() if size > 0)
    left = n
    while left and open_:
        share = max(left // len(open_), 1)
        for s in list(open_):
            take = min(share, sizes[s] - alloc[s], left)
            alloc[s] += take
            left -= take
            if alloc[s] == sizes[s]:
                open_.remove(s)
            if not left:
                break
    return alloc


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(centre - half, 4), round(centre + half, 4))


def _frame(con) -> list[dict]:
    from afterlife.export import _lake
    from afterlife.findings import FLAGGED_SQL, _rows

    return _rows(
        con,
        f"""
        with e as (
          select *, {FLAGGED_SQL} as flagged
          from gold.registry where verdict in ('retracted', 'disputed')),
        oa as (
          select openalex_id, max(publication_year) as year
          from delta_scan('{_lake()}/silver/openalex_works') group by 1)
        select
          e.paper_key, e.doi, e.pmid, e.pmcid, e.title,
          coalesce(year(e.original_date), oa.year) as year,
          case when rw_status = 'retracted' then 'retraction_watch'
               when flag_openalex then 'openalex'
               when pmid is not null and flag_pubmed then 'pubmed'
               else 'pmc' end as stratum,
          md5('{SEED}' || e.paper_key) as draw
        from e left join oa using (openalex_id)
        where flagged = 1
        order by stratum, draw""",
    )


def _sheet_row(sample_id: str, r: dict) -> dict:
    title = clean_title(r["title"])
    return {
        "sample_id": sample_id,
        "title": title,
        "year": r["year"] or "",
        "doi_url": f"https://doi.org/{r['doi']}" if r["doi"] else "",
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r["pmid"] else "",
        "pmc_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{r['pmcid']}/" if r["pmcid"] else "",
        "search_url": "https://scholar.google.com/scholar?q=" + quote(f'"{title}"')
        if title
        else "",
        "label": "",
        "evidence_url": "",
        "notes": "",
    }


def sample(n: int = 200, sheet: Path = SHEET) -> dict:
    if sheet.exists():
        # Labels are hours of hand work; never overwrite them.
        raise SystemExit(f"{sheet} exists. Move it aside to draw a new sample.")
    from afterlife.export import _connect

    frame = _frame(_connect())
    sizes: dict[str, int] = {}
    for r in frame:
        sizes[r["stratum"]] = sizes.get(r["stratum"], 0) + 1
    alloc = allocate(sizes, n)
    taken = dict.fromkeys(sizes, 0)
    chosen = []
    for r in frame:  # ordered by stratum, then by the seeded hash
        if taken[r["stratum"]] < alloc[r["stratum"]]:
            taken[r["stratum"]] += 1
            chosen.append(r)
    # Shuffle across strata so the sheet's order says nothing about the source.
    chosen.sort(key=lambda r: r["draw"][::-1])
    # PubMed only papers have no title or year in gold (the landed PubMed set is
    # ids only), and a blank title would give the source away.
    from afterlife.sources import pubmed

    missing = [r["pmid"] for r in chosen if r["pmid"] and not (r["title"] and r["year"])]
    found = pubmed.summaries(missing)
    for r in chosen:
        if r["pmid"] in found:
            r["title"] = r["title"] or found[r["pmid"]]["title"]
            r["year"] = r["year"] or found[r["pmid"]]["year"]
    key = []
    sheet.parent.mkdir(parents=True, exist_ok=True)
    with sheet.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for i, r in enumerate(chosen, 1):
            sid = f"A{i:03d}"
            w.writerow(_sheet_row(sid, r))
            key.append({"sample_id": sid, "paper_key": r["paper_key"], "stratum": r["stratum"]})
    return {
        "seed": SEED,
        "frame": "gold.registry, verdict retracted or disputed, flagged by exactly one registry",
        "frame_size": len(frame),
        "stratum_sizes": sizes,
        "allocation": alloc,
        "sheet": str(sheet.relative_to(ROOT)),
        "filled_from_pubmed_esummary": len(found),
        "titles_prefix_stripped": sum(
            clean_title(r["title"]) != (r["title"] or "").strip() for r in chosen
        ),
        "key": key,
    }


def score(labels: list[dict], sample_report: dict) -> dict:
    key = {k["sample_id"]: k["stratum"] for k in sample_report["key"]}
    sizes = sample_report["stratum_sizes"]
    bad = [
        f"{r['sample_id']}: {r['label']!r}"
        for r in labels
        if r["sample_id"] not in key
        or (r["label"].strip() and r["label"].strip().lower() not in LABELS)
    ]
    if bad:
        raise SystemExit("labels not in the codebook or unknown sample ids: " + ", ".join(bad))
    strata = {}
    for s in sizes:
        got = [
            r["label"].strip().lower()
            for r in labels
            if key[r["sample_id"]] == s and r["label"].strip()
        ]
        counts = {lab: got.count(lab) for lab in LABELS}
        decided = len(got) - counts["cannot_determine"]
        strata[s] = {
            "population": sizes[s],
            "sampled": sample_report["allocation"][s],
            "labelled": len(got),
            "counts": counts,
            "decided": decided,
            "precision": round(counts["retracted"] / decided, 4) if decided else None,
            "precision_ci95": wilson(counts["retracted"], decided),
            "notice_share": round(counts["notice"] / decided, 4) if decided else None,
        }
    complete = all(v["labelled"] == v["sampled"] for v in strata.values())
    est = None
    if complete and all(v["decided"] for v in strata.values() if v["sampled"]):
        # Stratified estimate with a finite population correction, so a stratum
        # labelled in full (a census) adds no sampling variance.
        total = sum(v["population"] for v in strata.values() if v["sampled"])
        p = var = 0.0
        for v in strata.values():
            if not v["sampled"]:
                continue
            w = v["population"] / total
            ph = v["precision"]
            p += w * ph
            fpc = 1 - v["decided"] / v["population"]
            var += w * w * ph * (1 - ph) / v["decided"] * fpc
        half = 1.96 * math.sqrt(var)
        est = {
            "precision": round(p, 4),
            "ci95": (round(max(p - half, 0), 4), round(min(p + half, 1), 4)),
            "frame_size": total,
        }
    return {"complete": complete, "strata": strata, "frame_estimate": est}


def run_score(sample_run: str | None = None, sheet: Path = SHEET) -> dict:
    path = (
        Path(sample_run) if sample_run else sorted((ROOT / "runs").glob("*-audit-sample.json"))[-1]
    )
    report = json.loads(path.read_text())["report"]
    with sheet.open(newline="") as fh:
        labels = list(csv.DictReader(fh))
    return {"sample_run": path.name, **score(labels, report)}
