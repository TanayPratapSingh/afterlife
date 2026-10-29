"""How often a retraction notice in PubMed Central links to what it retracts.

PMC marks an article retracted only when a notice links to it in structured
XML (`afterlife/core/jats.py`). This module measures that link across every
notice PMC holds in its open data bucket, compares it with PubMed's own
"Retraction of" link, and checks whether the paper ends up flagged.

    afterlife fetch pubmed-notices   every PubMed notice and its full record
    afterlife fetch pmc-notices      the frame, each notice's XML, and its targets
    afterlife notices                the measurement, as a run report

The frame is every notice PubMed types as a retraction notice, plus every
Retraction Watch retraction notice identifier, mapped to a PMCID through
PMC-ids.csv. A notice in PMC that neither knows about is outside it.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from afterlife.core import http, ids, jats
from afterlife.core.landing import Store, land, manifests, read_raw
from afterlife.core.text import decode
from afterlife.sources import pmc, pubmed

EXAMPLES = 25


def _newest(store: Store, source: str, name: str):
    found = [m for m in manifests(store, source) if m.object_key.endswith(name)]
    if not found:
        raise SystemExit(f"nothing landed for {source}/{name}; run the fetch first")
    return max(found, key=lambda m: m.snapshot_date)


def _jsonl(store: Store, source: str, name: str) -> list[dict]:
    raw = read_raw(store, _newest(store, source, name))
    return [json.loads(line) for line in raw.decode().splitlines() if line]


def _retraction_of(store: Store) -> dict[str, dict]:
    links: dict[str, dict] = {}
    for b in _jsonl(store, "pubmed_notices", "efetch.jsonl.gz"):
        links.update(pubmed.retraction_of(b["xml"]))
    return links


def frame(store: Store) -> dict:
    """PMCIDs of candidate notices, each with the sources that named it."""
    found = json.loads(
        read_raw(store, _newest(store, "pubmed_notices", "retraction_notice_pmids.json.gz"))
    )
    pubmed_pmids = set(found["pmids"])
    rw = max(manifests(store, "retraction_watch"), key=lambda m: m.snapshot_date)
    rw_pmids, rw_dois = set(), set()
    for r in csv.DictReader(io.StringIO(decode(read_raw(store, rw)).text, newline="")):
        if r["RetractionNature"] == "Retraction":
            if p := ids.pmid(r["RetractionPubMedID"]):
                rw_pmids.add(p)
            if d := ids.doi(r["RetractionDOI"]):
                rw_dois.add(d)
    hits = pmc.lookup(store, pmids=pubmed_pmids | rw_pmids, dois=rw_dois)
    via: dict[str, set[str]] = defaultdict(set)
    for name, kind, values in (
        ("pubmed", "pmid", pubmed_pmids),
        ("retraction_watch", "pmid", rw_pmids),
        ("retraction_watch", "doi", rw_dois),
    ):
        for v in values:
            if h := hits.get((kind, v)):
                via[h["pmcid"]].add(name)
    return {
        "via": {p: sorted(s) for p, s in via.items()},
        "counts": {
            "pubmed_notice_pmids": len(pubmed_pmids),
            "pubmed_complete": found.get("expected") == len(pubmed_pmids),
            "rw_snapshot": rw.snapshot_date,
            "rw_notice_pmids": len(rw_pmids),
            "rw_notice_dois": len(rw_dois),
            "pmcids": len(via),
            "by_source": dict(Counter("+".join(sorted(s)) for s in via.values())),
        },
    }


def _targets(n: dict) -> list[tuple[str, str]]:
    return [t for r in jats.retracted(n) if (t := jats.target(r))]


def fetch(
    store: Store, *, workers: int = 24, snapshot_date: date | None = None, get=http.get
) -> dict:
    day = snapshot_date or date.today()
    f = frame(store)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        docs = list(pool.map(lambda p: pmc.fetch_document(p, get=get), sorted(f["via"])))
    for d in docs:
        d["found_via"] = f["via"][d["pmcid"]]
    with_xml = [d for d in docs if d.get("xml")]
    m1, new1 = land(
        store,
        source="pmc_notices",
        snapshot_date=day,
        name="notices.jsonl",
        raw="".join(json.dumps(d, sort_keys=True) + "\n" for d in docs).encode(),
        origin_url=f"{pmc.BUCKET}/",
        origin_version="s3-open-data",
        records=len(docs),
        cost_usd=0.0,
        notes={
            **f["counts"],
            "in_open_data": sum(d["in_open_data"] for d in docs),
            "with_xml": len(with_xml),
        },
    )
    # What each notice points at, by its XML and by PubMed, resolved to PMCIDs.
    want = {t for d in with_xml for t in _targets(jats.notice(d["xml"]))}
    links = _retraction_of(store)
    for d in docs:
        for p in links.get(str(d.get("pmid")), {}).get("pmids", []):
            want.add(("pmid", p))
    hits = pmc.lookup(
        store,
        pmids=[v for k, v in want if k == "pmid"],
        dois=[v for k, v in want if k == "doi"],
        pmcids=[v for k, v in want if k == "pmcid"],
    )
    asked: dict[str, set[str]] = defaultdict(set)
    for k, v in want:
        if h := hits.get((k, v)):
            asked[h["pmcid"]].add(f"{k}:{v}")
    rows = pmc.fetch_metadata(sorted(asked), workers=workers, get=get)
    for r in rows:
        r["requested_as"] = sorted(asked[r["pmcid"]])
    m2, new2 = land(
        store,
        source="pmc_notice_targets",
        snapshot_date=day,
        name="targets.jsonl",
        raw="".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode(),
        origin_url=f"{pmc.BUCKET}/metadata/",
        origin_version="s3-open-data",
        records=len(rows),
        cost_usd=0.0,
        notes={
            "identifiers": len(want),
            "in_pmc_ids": len(hits),
            "targets": len(rows),
            "in_open_data": sum(r["in_open_data"] for r in rows),
            "flagged_retracted": sum(1 for r in rows if r.get("is_retracted")),
        },
    )
    return {
        "notices": {"object_key": m1.object_key, "new": new1, "notes": m1.notes},
        "targets": {"object_key": m2.object_key, "new": new2, "notes": m2.notes},
    }


def _share(k: int, n: int) -> dict:
    return {"n": n, "k": k, "share": round(k / n, 4) if n else None}


def run(store: Store) -> dict:
    docs = _jsonl(store, "pmc_notices", "notices.jsonl.gz")
    targets = _jsonl(store, "pmc_notice_targets", "targets.jsonl.gz")
    links = _retraction_of(store)
    key_to_pmcid = {k: t["pmcid"] for t in targets for k in t["requested_as"]}
    meta = {t["pmcid"]: t for t in targets}

    parsed = {d["pmcid"]: jats.notice(d["xml"]) for d in docs if d.get("xml")}
    types = Counter(n["article_type"] for n in parsed.values())
    notices = [d for d in docs if parsed.get(d["pmcid"], {}).get("article_type") == "retraction"]

    classes: Counter = Counter()
    id_types: Counter = Counter()
    related_types: Counter = Counter()
    agreement: Counter = Counter()
    resolved: Counter = Counter()
    flags: dict[str, Counter] = defaultdict(Counter)
    by_year: dict[int | None, Counter] = defaultdict(Counter)
    by_publisher: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)

    for d in notices:
        n = parsed[d["pmcid"]]
        cls = jats.link_class(n)
        classes[cls] += 1
        related_types.update((r["type"] or "none") for r in n["related"])
        id_types.update((r["id_type"] or "none") for r in jats.retracted(n) if r["href"])
        by_year[n["year"]][cls == "linked"] += 1
        by_publisher[n["publisher"] or "unknown"][cls == "linked"] += 1
        if cls != "linked" and len(examples[cls]) < EXAMPLES:
            examples[cls].append(d["pmcid"])

        xml_keys = {f"{k}:{v}" for k, v in _targets(n)}
        xml_pmcids = {key_to_pmcid[k] for k in xml_keys if k in key_to_pmcid}
        if xml_keys:
            resolved["linked_with_target"] += 1
            resolved["target_in_pmc"] += bool(xml_pmcids)

        # Against PubMed's "Retraction of" link for the same notice.
        own = str(d["pmid"]) if d.get("pmid") else None
        pm = links.get(own) if own else None
        pm_pmids = set(pm["pmids"]) if pm else set()
        xml_pmids = {k.split(":", 1)[1] for k in xml_keys if k.startswith("pmid:")}
        xml_pmids |= {str(meta[p]["pmid"]) for p in xml_pmcids if meta[p].get("pmid")}
        if pm is None:
            verdict = "notice_not_in_pubmed_set"
        elif not pm_pmids and not xml_keys:
            verdict = "neither_links"
        elif not pm_pmids:
            verdict = "pmc_only"
        elif not xml_keys:
            verdict = "pubmed_only"
        elif not xml_pmids:
            verdict = "pmc_target_has_no_pmid"
        else:
            verdict = "agree" if pm_pmids & xml_pmids else "disagree"
        agreement[verdict] += 1
        if verdict in ("disagree", "pubmed_only") and len(examples[verdict]) < EXAMPLES:
            examples[verdict].append(d["pmcid"])

        # Is the paper flagged in PMC? Split by which link names it.
        pm_pmcids = {key_to_pmcid[f"pmid:{p}"] for p in pm_pmids if f"pmid:{p}" in key_to_pmcid}
        for p in xml_pmcids | pm_pmcids:
            t = meta[p]
            if not t.get("in_open_data"):
                continue
            if p in xml_pmcids and p in pm_pmcids:
                how = "both"
            elif p in xml_pmcids:
                how = "xml_only"
            else:
                how = "pubmed_only"
            flags[how][bool(t.get("is_retracted"))] += 1
            if how == "pubmed_only" and not t.get("is_retracted"):
                if len(examples["unflagged_pubmed_only_target"]) < EXAMPLES:
                    examples["unflagged_pubmed_only_target"].append(f"{d['pmcid']}->{p}")

    total = len(notices)
    top = sorted(by_publisher.items(), key=lambda kv: -sum(kv[1].values()))[:15]
    return {
        "frame": {
            "candidates": len(docs),
            "in_open_data": sum(d["in_open_data"] for d in docs),
            "with_xml": len(parsed),
            "article_types": dict(types.most_common()),
            "notices": total,
            "notices_by_source": dict(Counter("+".join(d["found_via"]) for d in notices)),
            "notices_flagged_retracted_themselves": sum(
                1 for d in notices if d.get("is_retracted")
            ),
            "not_notices_by_source": dict(
                Counter(
                    "+".join(d["found_via"])
                    for d in docs
                    if d["pmcid"] in parsed and parsed[d["pmcid"]]["article_type"] != "retraction"
                )
            ),
            "not_in_open_data_by_source": dict(
                Counter("+".join(d["found_via"]) for d in docs if not d["in_open_data"])
            ),
        },
        "link_classes": {c: _share(k, total) for c, k in classes.most_common()},
        "linked_id_types": dict(id_types.most_common()),
        "related_article_types": dict(related_types.most_common()),
        "linked_target_in_pmc": _share(resolved["target_in_pmc"], resolved["linked_with_target"]),
        "pubmed_agreement": dict(agreement.most_common()),
        "target_flagged_retracted": {
            how: _share(c[True], c[True] + c[False]) for how, c in sorted(flags.items())
        },
        "linked_by_year": {
            str(y): _share(c[True], c[True] + c[False])
            for y, c in sorted(by_year.items(), key=lambda kv: kv[0] or 0)
        },
        "linked_by_publisher": {p: _share(c[True], c[True] + c[False]) for p, c in top},
        "examples": dict(examples),
    }
