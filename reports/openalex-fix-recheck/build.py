"""After the is_retracted fix: what changed in OpenAlex, and what is left.

Compares the works OpenAlex flagged is_retracted on 2026-10-19 (before the fix)
with 2026-11-16 (after), both landed by `afterlife fetch openalex`, against
Retraction Watch (landed 2026-10-16). Writes summary.json and three CSVs:

- leftover_notices.csv: still flagged, and a notice (typed retraction with a
  retraction title, or a Retraction Watch notice DOI or PMID that is not also a
  paper's)
- withdrawn_typed_retraction.csv: still flagged, typed retraction, titled
  "WITHDRAWN:" (the withdrawn article itself, so the flag fits but the type
  does not)
- new_flags_not_in_rw.csv: flagged only after the fix and unknown to Retraction
  Watch; a list to look at, not a claim

Also checks, by DOI on the live API, every Retraction Watch paper that dropped
out of the flagged set, to see whether the fix unflagged real retractions.

Run from the repo root: .venv/bin/python reports/openalex-fix-recheck/build.py
"""
import csv, gzip, io, json, re, time, urllib.parse, urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).parent
LAND = Path("data/landing")
BEFORE, AFTER = "2026-10-19", "2026-11-16"
RW = LAND / "retraction_watch/snapshot_date=2026-10-16/retraction_watch.csv.gz"


def load(day):
    return {w["id"]: w for w in map(json.loads, gzip.open(LAND / f"openalex/snapshot_date={day}/works_retracted.jsonl.gz"))}


def doi(s):
    return (s or "").strip().lower().removeprefix("https://doi.org/")


def pmid(w):
    return str((w.get("ids") or {}).get("pmid") or "").rsplit("/", 1)[-1]


raw = gzip.open(RW).read()
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    text = raw.decode("cp1252")
paper, notice = set(), set()
for r in csv.DictReader(io.StringIO(text)):
    if r["RetractionNature"].strip() == "Retraction":
        paper |= {doi(r["OriginalPaperDOI"]), r["OriginalPaperPubMedID"].strip()}
        notice |= {doi(r["RetractionDOI"]), r["RetractionPubMedID"].strip()}
for s in (paper, notice):
    s -= {"", "0", "unavailable"}


def rw_kind(w):
    keys = {doi(w.get("doi")), pmid(w)} - {""}
    p, n = bool(keys & paper), bool(keys & notice)
    return "paper" if p and not n else "notice" if n and not p else "both" if p else "none"


WITHDRAWN = re.compile(r"^\s*withdrawn\b", re.I)
RETRACTED_PAPER = re.compile(r"^\s*retracted\b", re.I)  # a retracted paper retitled, not a notice
A, B = load(BEFORE), load(AFTER)
gone, new = A.keys() - B.keys(), B.keys() - A.keys()


def row(w):
    return {"openalex_id": w["id"], "doi": doi(w.get("doi")), "pmid": pmid(w), "type": w.get("type"),
            "publication_date": w.get("publication_date"), "title": w.get("display_name"), "rw": rw_kind(w)}


leftover, withdrawn = [], []
for w in B.values():
    t = w.get("display_name") or ""
    if w.get("type") == "retraction" and WITHDRAWN.match(t):
        withdrawn.append(row(w))
    elif (w.get("type") == "retraction" and not RETRACTED_PAPER.match(t)) or rw_kind(w) == "notice":
        leftover.append(row(w))
new_unknown = [row(B[i]) for i in sorted(new) if rw_kind(B[i]) == "none"]

# Did the fix unflag real retractions? Look the dropped Retraction Watch papers up by DOI, live.
dropped = [A[i] for i in sorted(gone) if rw_kind(A[i]) == "paper"]
live = {}
dois = [doi(w.get("doi")) for w in dropped if w.get("doi")]
for i in range(0, len(dois), 40):
    time.sleep(0.3)
    q = urllib.parse.quote("|".join(dois[i:i + 40]), safe="|:/")
    url = f"https://api.openalex.org/works?per-page=100&select=id,doi,is_retracted&filter=doi:{q}"
    for w in json.loads(urllib.request.urlopen(url, timeout=60).read())["results"]:
        live.setdefault(doi(w["doi"]), []).append(w)
fate = Counter()
for w in dropped:
    d = doi(w.get("doi"))
    ws = live.get(d, []) if d else None
    fate["no doi" if ws is None else "doi no longer in OpenAlex" if not ws
         else "still flagged under another work id" if any(x["is_retracted"] for x in ws) else "UNFLAGGED"] += 1

counts = {
    "flagged_before": len(A), "flagged_after": len(B), "dropped_out": len(gone), "newly_flagged": len(new),
    "dropped_out_by_rw_kind": dict(Counter(rw_kind(A[i]) for i in gone)),
    "dropped_out_typed_retraction": sum(A[i].get("type") == "retraction" for i in gone),
    "rw_papers_dropped_out_live_fate": dict(fate),
    "leftover_notices": len(leftover),
    "leftover_notices_typed_retraction": sum(r["type"] == "retraction" for r in leftover),
    "leftover_rw_notices_not_typed_retraction": sum(r["type"] != "retraction" for r in leftover),
    # clear: Retraction Watch calls it a notice or does not know it; the rest share a DOI or PMID with a
    # paper Retraction Watch lists as retracted, where a true flag may be right
    "leftover_clear": sum(r["rw"] in ("notice", "none") for r in leftover),
    "leftover_sharing_an_id_with_a_rw_paper": sum(r["rw"] in ("paper", "both") for r in leftover),
    "withdrawn_typed_retraction": len(withdrawn),
    "withdrawn_typed_retraction_elsevier": sum(r["doi"].startswith("10.1016/") for r in withdrawn),
    "newly_flagged_not_in_rw": len(new_unknown),
}
for name, rows in (("leftover_notices", leftover), ("withdrawn_typed_retraction", withdrawn),
                   ("new_flags_not_in_rw", new_unknown)):
    with open(OUT / f"{name}.csv", "w", newline="") as f:
        out = csv.DictWriter(f, fieldnames=list(row(next(iter(B.values())))))
        out.writeheader()
        out.writerows(rows)
json.dump({"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "snapshots": {"before": BEFORE, "after": AFTER, "retraction_watch": "2026-10-16"}, "counts": counts},
          open(OUT / "summary.json", "w"), indent=2)
print(json.dumps(counts, indent=2))
