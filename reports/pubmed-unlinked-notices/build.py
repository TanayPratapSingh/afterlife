"""Retracted papers that PubMed neither flags nor links to their notice.

Frame: PubMed retraction notices (landed 2026-10-29) whose "Retraction of"
entry names no PMID, or only the notice's own PMID. Retraction Watch (landed
2026-10-16) names the paper each such notice retracts. A paper is reported
when, on live PubMed today: it lacks the "Retracted Publication" type, it is
not itself typed "Retraction Notice", the notice title names it (word overlap
at least 0.6), no other PubMed record shares its DOI, and the notice still has
no "Retraction of" PMID link.

Run from the repo root: .venv/bin/python reports/pubmed-unlinked-notices/build.py
Writes pubmed_unlinked_retractions.csv and summary.json next to this file.
"""
import csv, gzip, io, json, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from afterlife.sources.pubmed import _FIRST_PMID, _RECORD, retraction_of

OUT = Path(__file__).parent
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
LAND = Path("data/landing")
FLAGGED = LAND / "pubmed/snapshot_date=2026-10-19/retracted_publication_pmids.json.gz"
NOTICES = LAND / "pubmed_notices/snapshot_date=2026-10-29/efetch.jsonl.gz"
RW = LAND / "retraction_watch/snapshot_date=2026-10-16/retraction_watch.csv.gz"
STOP = {"retraction", "retracted", "withdrawn", "the", "and", "for", "with", "notice", "article"}


def post(tool, **p):
    for attempt in range(4):  # E-utilities return the odd 502; back off and retry
        time.sleep(0.4 + 3 * attempt)  # 3 requests a second without a key
        try:
            return urllib.request.urlopen(EU + tool, data=urllib.parse.urlencode(p).encode(), timeout=120).read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 3:
                raise


def summaries(ids):
    out = {}
    for i in range(0, len(ids), 150):
        out.update(json.loads(post("esummary.fcgi", db="pubmed", id=",".join(ids[i:i + 150]), retmode="json"))["result"])
    return out


def words(t):
    return set(re.findall(r"[a-z0-9]{3,}", (t or "").lower())) - STOP


def bump(counts, key):
    counts[key] = counts.get(key, 0) + 1


flagged = set(json.loads(gzip.open(FLAGGED).read())["pmids"])
links = {}
for line in gzip.open(NOTICES):
    links.update(retraction_of(json.loads(line)["xml"]))
orphans = {n for n, v in links.items() if not v["pmids"] or v["pmids"] == [n]}

raw = gzip.open(RW).read()
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    text = raw.decode("cp1252")
pairs = set()
for r in csv.DictReader(io.StringIO(text)):
    n, o = r["RetractionPubMedID"].strip(), r["OriginalPaperPubMedID"].strip()
    if r["RetractionNature"].strip() == "Retraction" and n in orphans and o not in ("", "0") and o != n:
        pairs.add((o, n, r["Record ID"].strip()))

cand = [(o, n, rid) for o, n, rid in sorted(pairs, key=lambda t: int(t[0])) if o not in flagged]
s = summaries(sorted({x for o, n, _ in cand for x in (o, n)}, key=int))
counts = {"notices_landed": len(links), "notices_without_a_link_to_another_pmid": len(orphans),
          "rw_pairs_for_those_notices": len(pairs), "paper_not_flagged_2026_10_19": len(cand)}
rows = []
for o, n, rid in cand:
    po, pn = s.get(o, {}), s.get(n, {})
    if not po or "error" in po:
        bump(counts, "dropped_paper_pmid_missing"); continue
    if "Retracted Publication" in po.get("pubtype", []):
        bump(counts, "dropped_flagged_live"); continue
    if "Retraction Notice" in po.get("pubtype", []):
        bump(counts, "dropped_paper_record_typed_notice"); continue
    a = words(po.get("title"))
    if len(a & words(pn.get("title"))) / max(1, len(a)) < 0.6:
        bump(counts, "dropped_notice_title_does_not_name_paper"); continue
    doi = next((i["value"] for i in po.get("articleids", []) if i["idtype"] == "doi"), "")
    rows.append({"paper_pmid": o, "paper_doi": doi, "journal": po.get("source"), "paper_date": po.get("pubdate"),
                 "paper_title": po.get("title"), "paper_types": "; ".join(po.get("pubtype", [])),
                 "notice_pmid": n, "notice_date": pn.get("pubdate"), "notice_title": pn.get("title"),
                 "rw_record_id": rid, "cochrane": "Cochrane" in (po.get("source") or "")})

for r in rows:  # a second PubMed record for the same DOI would explain the missing flag
    r["other_pmids_for_doi"] = ""
    if r["paper_doi"]:
        ids = json.loads(post("esearch.fcgi", db="pubmed", term=f'{r["paper_doi"]}[aid]', retmode="json"))["esearchresult"]["idlist"]
        r["other_pmids_for_doi"] = ";".join(i for i in ids if i != r["paper_pmid"])
counts["dropped_other_record_for_doi"] = sum(bool(r["other_pmids_for_doi"]) for r in rows)
rows = [r for r in rows if not r["other_pmids_for_doi"]]

xml = post("efetch.fcgi", db="pubmed", id=",".join(sorted({r["notice_pmid"] for r in rows}, key=int)), retmode="xml").decode()
linked_now = {p for p, v in retraction_of(xml).items() if v["pmids"] not in ([], [p])}
counts["dropped_notice_linked_live"] = sum(r["notice_pmid"] in linked_now for r in rows)
rows = [r for r in rows if r["notice_pmid"] not in linked_now]
counts["reported"] = len(rows)
counts["reported_not_cochrane"] = sum(not r["cochrane"] for r in rows)

for r in rows:  # independent check: did the publisher deposit a retraction with Crossref?
    r["notice_newer_than_paper"] = int(r["notice_pmid"]) > int(r["paper_pmid"])
    r["crossref_updated_by"] = ""
    if r["paper_doi"]:
        time.sleep(0.3)
        req = urllib.request.Request("https://api.crossref.org/works/" + urllib.parse.quote(r["paper_doi"]),
                                     headers={"User-Agent": "afterlife-retraction-check/0.1"})
        msg = json.loads(urllib.request.urlopen(req, timeout=60).read())["message"]
        r["crossref_updated_by"] = ";".join(f'{u.get("type")}:{u.get("DOI")}' for u in msg.get("updated-by", [])
                                             if u.get("type") in ("retraction", "withdrawal"))
    # same publisher: the notice DOI shares the paper DOI's prefix
    r["crossref_confirms_same_publisher"] = any(
        x.split(":", 1)[1].split("/")[0] == r["paper_doi"].split("/")[0] for x in r["crossref_updated_by"].split(";") if x)
# Hand check by Tanay on 2026-11-16 (PubMed pages, then Crossref for the doubtful ones).
HAND_EXCLUDED = {
    "20421270": "the Crossref 'retraction' is a same title record (10.3324/haematol.2009.021923) and PubMed's notice "
                "is titled Erratum; the paper was printed in Sep 2010, after the May 2010 deposit, and is in PMC. "
                "Likely an erratum deposited as a retraction, so the error is upstream of PubMed.",
    "37134001": "the ACS article page shows no retraction, although the notice 10.1021/acschemneuro.3c00080 "
                "(\"Retraction of ...\", 2023-05-05, two days after the paper) is in Crossref and PubMed. "
                "Publisher side unclear, so left out; a lead for a report to ACS.",
    "18927159": "the BMJ page shows the article with no retraction, and the Crossref retraction DOI "
                "(10.1136/bjsm.2007.045476retract) names a different manuscript; possibly a duplicate was retracted.",
    "25449499": "the Elsevier page shows the editorial in its issue with no withdrawal, and the Crossref retraction DOI "
                "(10.1016/j.ijcard.2014.09.137) is a different article; likely a duplicate copy was withdrawn.",
}
for r in rows:
    r["hand_excluded"] = HAND_EXCLUDED.get(r["paper_pmid"], "")
# headline: notice came after the paper, not a Cochrane version, the publisher's own retraction is in Crossref,
# and the hand check did not exclude it
counts["headline"] = sum(r["notice_newer_than_paper"] and not r["cochrane"] and r["crossref_confirms_same_publisher"]
                         and not r["hand_excluded"] for r in rows)
assert counts["reported"] == len({r["paper_pmid"] for r in rows}), "one row per paper"

with open(OUT / "pubmed_unlinked_retractions.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
json.dump({"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "counts": counts},
          open(OUT / "summary.json", "w"), indent=2)
print(json.dumps(counts, indent=2))
