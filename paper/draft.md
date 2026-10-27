# Asking Retraction Watch Twice: Retraction Data Revise Their Own Past

Tanay Pratap Singh
School of Information Studies, Syracuse University

Draft of 2026-10-26. Every number traces to a run report in the afterlife
repository; the key is given in an HTML comment beside the number. Bracketed
items marked AUDIT are waiting on the hand labels.

## Abstract

Retraction data are increasingly treated as infrastructure: metascience
studies count retractions by year and reason, reference managers warn
authors who cite retracted work, and retrieval systems filter retracted
papers out. Since Crossref opened the Retraction Watch database in 2023, it
has been republished daily, and studies cite it as a single source. We
replayed 39 snapshots spanning its entire public history (2024-09-02 to
2026-10-16) and asked the same four questions of each, all about retractions
dated 2023 or earlier, so that any change is the database revising the past.
Over two years the number of such retractions grew 9.1%, the number citing
"Paper Mill" grew 80.6%, the median time from publication to retraction rose
from 379 to 420 days, and the count citing "Fake Peer Review", 4,654 in the
first snapshot, fell to zero when the label was renamed. Almost all of the
growth is late cataloguing: 4,482 records added after the first snapshot,
3,921 of them Hindawi retractions. We also join Retraction Watch with
OpenAlex, PubMed, and PubMed Central. Of 89,681 papers that at least one
registry flags, all eligible registries agree on 51.5%, and 49.3% of the works
OpenAlex marks as retracted are retraction notices rather than retracted
papers. A blind hand audit of 200 papers flagged by a single registry
estimates that [AUDIT] of them were actually retracted. We recommend that
studies cite and archive the snapshot they used, and we release the pipeline
that rebuilds any snapshot on demand.

<!-- as_of.series[0], as_of.series[-1], as_of.late_records, registries.papers,
registries.all_eligible_agree, registries.excluded_as_notices -->

## 1. Introduction

A study that reports "N retractions for paper mill activity through 2023"
reports a property of the database on the day it was queried, not a property
of the literature. That would not matter if retraction databases only ever
appended new notices. They do not. Records are added years after the
retraction, reasons are renamed in bulk, and dates and identifiers are
corrected.

The question this paper asks is simple: if two researchers ask Retraction
Watch the same question about the past on different days, how different are
their answers, and why? Answering it needs the database's history, not just
its current state. Crossref publishes the Retraction Watch database as a CSV
in a public GitLab repository, regenerated daily, and every regeneration is a
commit. The history therefore exists, but nothing we know of reads it as a
time series.

We make four contributions.

1. A replay of 39 Retraction Watch snapshots covering the whole public history,
   with every record historized, so the database can be queried as of any
   snapshot date.
2. A fixed set of questions asked of every snapshot, showing how much and
   through which mechanisms the answers move (Section 4.1).
3. A four registry comparison (Retraction Watch, OpenAlex, PubMed, PubMed
   Central) on current data, which extends earlier agreement studies to
   OpenAlex and PMC and shows that OpenAlex's retraction flag still marks
   notices as retracted (Sections 4.2 and 4.3).
4. A blind, preregistered hand audit of the papers that only one registry
   flags, so that coverage is measured against publishers rather than against
   the union of the registries (Section 4.4).

## 2. Related work

[PENDING: filled from a full reading of the prior papers, not abstracts.
Must cover Salami, McCumber and Schneider on retraction indexing across 11
sources and their earlier four source study; Hauschke and Nazarovets on
OpenAlex is_retracted; Ortega and Delgado-Quirós on coverage across seven
databases; Sebo and Sebo on Retraction Watch, PubMed, and Web of Science; and
the post retraction citation literature. The positioning claim, that no prior
study treats Retraction Watch as a versioned dataset, must be checked here
before it appears anywhere else.]

## 3. Data and methods

### 3.1 Sources

All four sources were fetched on 2026-10-19 from public endpoints.

| Source | What was landed |
| --- | --- |
| Retraction Watch (Crossref GitLab) | 39 snapshots, 2024-09-02 to 2026-10-16; 55,804 records in the first, 73,283 in the last |
| OpenAlex | 136,873 of 136,970 works with `is_retracted: true` |
| PubMed | 35,169 PMIDs with publication type "Retracted Publication" |
| PubMed Central | metadata for 43,616 articles the other registries mention |

<!-- sources.retraction_watch, sources.openalex, sources.pubmed, sources.pmc_metadata -->

The GitLab history begins on 2024-09-02 and held 518 daily snapshots when we
listed it on 2026-10-26. We replayed the first snapshot of each month through
2026-09-01 and every snapshot from 2026-09-29 on.

### 3.2 Snapshot replay

Each snapshot is landed unchanged and replayed in date order. A record is
keyed by its Retraction Watch Record ID. When a record's content changes
between consecutive snapshots, its current version is closed and a new one
opened, so every version carries a validity interval (`valid_from` inclusive,
`valid_to` exclusive). The view of the database as of a date *d* is the set of
versions valid at *d*. Changes that only alter formatting (whitespace, a
dropped `+` prefix on every reason, capitalization) are classified separately
from semantic edits, so a reformatting of the whole file does not read as
143,447 edits. <!-- upstream.format_only_total -->

One snapshot, 2026-10-15, arrived in cp1252 rather than UTF-8 and had lost
characters in 5,520 records. It is quarantined rather than replayed as edits.
<!-- upstream.quarantined -->

### 3.3 The same question on every snapshot

We fixed four questions before computing any of them, and report all four:

1. How many retractions are dated 2023 or earlier?
2. How many of those cite the reason "Paper Mill"?
3. How many of those cite the reason "Fake Peer Review"?
4. What is their median time from publication to retraction?

Restricting every question to retractions dated 2023 or earlier means a
correct, append only database would give the same answer on every snapshot
after early 2024 (allowing for notices dated late in 2023 that take a few
weeks to be entered). Any larger change is the database revising the past.
Reason matching is case insensitive substring matching on the reason field.

### 3.4 Cross registry identity and agreement

Papers are matched across sources on DOI, PMID, and PMCID, using one hop of
the PMC identifier map. A paper is in the registry if Retraction Watch holds a
record of it or any source flags it as retracted. Retraction and withdrawal
notices are excluded before matching: OpenAlex works of type `retraction`, and
any work whose DOI or PMID Retraction Watch records as a notice.

A registry is eligible for a paper only if it could have flagged it: PubMed
for papers with a PMID, PMC for papers with a linked open copy, OpenAlex and
Retraction Watch for every paper. "All eligible agree" means every eligible
registry flags the paper.

### 3.5 Hand audit

Agreement against the union of registries says nothing about whether the
union is right. We drew a sample from the 36,401 papers that exactly one
registry flags, stratified by that registry with equal allocation: all 22
papers that only PubMed flags, and 89 each from those that only Retraction
Watch (20,259) or only OpenAlex (16,120) flags. No paper is flagged by PMC
alone. The draw is seeded and reproducible. <!-- audit sample run
20261027T004212Z-audit-sample.json -->

Labels follow a codebook (`docs/audit-codebook.md`) committed, with the blank
sheet, before any label was entered (commit 772bdad, 2026-10-26). Labels are
assigned from the publisher's version of record or the publisher's own notice,
never from the four registries under test. The sheet withholds the flagging
registry and strips leading "RETRACTED:" and "WITHDRAWN:" markers from titles
(86 of 200), which would reveal both the source and the answer. Blinding is
partial: 27 papers have no DOI, PMID, or PMCID, and nearly all of those are
Retraction Watch records, which a labeller could infer.

Precision per stratum is the share labelled `retracted` among papers not
labelled `cannot_determine`, with Wilson 95% intervals. The frame estimate
weights strata by population with a finite population correction, so the
PubMed census adds no sampling variance.

## 4. Results

### 4.1 Retraction Watch revises its past

Table 1 and Figure 1 give the four answers on the first and last snapshots.

**Table 1.** The same question, asked twenty five months apart.

| Question | 2024-09-02 | 2026-10-16 | Change |
| --- | ---: | ---: | ---: |
| Retractions dated 2023 or earlier | 48,789 | 53,227 | +9.1% |
| Of those, citing "Paper Mill" | 4,868 | 8,790 | +80.6% |
| Of those, citing "Fake Peer Review" | 4,654 | 0 | -100% |
| Median days, publication to retraction | 379 | 420 | +10.8% |

<!-- as_of.series[0], as_of.series[-1] -->

![Figure 1](figures/fig1_same_question.pdf)

**Figure 1.** Each panel asks one question of all 39 snapshots.

Three mechanisms account for the movement.

**Late cataloguing.** 4,482 records with a retraction date in 2023 or earlier
were added after the first snapshot. 4,406 of them carry a Record ID above the
first snapshot's highest, so they are new catalogue entries rather than rows
the first file happened to omit. 4,315 are dated 2023, and Hindawi accounts
for 3,921. Retractions dated 2023 alone went from 9,030 to 13,337: a study of
2023 run in September 2024 would have seen about two thirds of what the
database now holds. The late records also explain the other movements: 3,924
of them cite "Paper Mill" (against a net increase of 3,922), and their median
lag is 561 days, which pulls the cohort median up. Most arrived between
October 2024 and March 2025 (Figure 2), but they were still arriving in
October 2026. <!-- as_of.late_records, as_of.retractions_dated_2023 -->

![Figure 2](figures/fig2_late_records.pdf)

**Figure 2.** When the late records first appeared.

**Relabelling.** The reason taxonomy was renamed in bulk several times. On
2026-01-02 "Fake Peer Review" became "Compromised Peer Review" on 10,426
records; on 2025-04-01 "Unreliable Results" became "Unreliable Results and/or
Conclusions" on 16,379. A query written against the old label silently
returns zero. Of the 73,334 records ever seen, 50,518 (68.9%) were edited
after they first appeared, mostly through these relabels.
<!-- upstream.top_reason_relabels, upstream.records_edited_after_first_appearance -->

**Edits and deletions.** 3,840 records (5.2%) changed in something other than
reasons: 96 changed their original paper DOI, 54 their retraction date, and
27 their nature (for example, from expression of concern to retraction).
54 records were deleted and 3 reappeared. Within the 2023 cohort these are
small: 22 records deleted and 22 moved out by a date or nature edit.
<!-- upstream.records_edited_after_first_appearance, upstream.deleted_total,
as_of.left_cohort -->

### 4.2 The registries disagree

89,681 papers are flagged retracted, or disputed, by at least one registry.
All eligible registries agree on 46,225 (51.5%), and exactly one flags 36,401
(40.6%). <!-- registries -->

**Table 2.** Each registry against the union. The union is not ground truth;
Section 4.4 measures it.

| Registry | Eligible papers | Flags | Share of the union |
| --- | ---: | ---: | ---: |
| PMC | 19,499 | 18,748 | 96.1% |
| PubMed | 37,495 | 33,302 | 88.8% |
| OpenAlex | 84,162 | 68,945 | 81.9% |
| Retraction Watch | 89,681 | 67,672 | 75.5% |

<!-- registries.agreement -->

[PENDING: one paragraph comparing these with the agreement figures in
Salami et al., once read in full.]

### 4.3 Half of OpenAlex's retracted works are notices

Of the 136,873 OpenAlex works with `is_retracted: true`, 67,544 (49.3%) are
retraction notices: 38,161 have type `retraction`, and 29,383 more carry a DOI
or PMID that Retraction Watch records as a notice. Counted as papers, they
would nearly double the registry. PMC handles the same distinction well: of
the 17,951 notice articles it holds, 187 carry its retracted flag.
<!-- registries.excluded_as_notices, registries.pmc_notice_articles -->

[PENDING: relate to Hauschke and Nazarovets, who report the same failure for
data served between 2023-12-22 and 2024-03-19. Our data were fetched on
2026-10-19.]

### 4.4 What the single registry flags really are

[AUDIT: per stratum precision with intervals, notice shares, and the frame
estimate, from `afterlife audit score`. Rendered into RESULTS.md section 9.]

### 4.5 Two secondary observations

**Publisher batches set yearly medians.** Across 67,845 dated notices the
median time to retraction is 493 days (interquartile range 156 to 1,074). But
one publisher's batch can set a year's median. IEEE conference retractions
make up 4,410 of 5,011 notices in 2010 and 4,091 of 4,937 in 2011, with
medians of 54 and 34 days; without IEEE those years sit at 548 and 553 days.
In 2023, Hindawi's 9,673 notices have a median of 467 days against 954 for
everyone else (Figure 3). A yearly trend in retraction speed is largely a
trend in which publishers retracted in bulk that year.
<!-- retraction_lag, retraction_lag.by_year_top_publisher -->

![Figure 3](figures/fig3_lag_by_year.pdf)

**Figure 3.** Median days from publication to retraction by notice year.

**Retracted papers keep being cited.** Of 44,557 retracted papers whose whole
post retraction period falls inside OpenAlex's citation window, 29,024 (65.1%)
were cited in at least one year after their retraction year, 217,376 citations
in all; the median paper received 1 and the 90th percentile 11. Year
granularity undercounts, and these counts do not distinguish citations that
acknowledge the retraction from those that do not.
<!-- afterlife.headline_excluding_retract_and_replace -->

## 5. Discussion

The headline numbers in Section 4.1 are not errors in Retraction Watch. Late
cataloguing is what a curated database does when a publisher retracts eight
thousand papers at once, and renaming a reason is what a maintained taxonomy
does. The problem is on the side of the studies that use it: a count from this
database is a count as of a date, and most studies give neither the date nor
the file.

Five practices would make retraction studies reproducible.

1. **Cite the snapshot.** Report the snapshot date and the Crossref commit
   that produced the file, not "Retraction Watch, accessed in 2025".
2. **Archive the file.** The CSV is about 68 MB; deposit the exact copy used.
3. **Match reasons on a versioned taxonomy.** Map old labels to new before
   counting, or a query for "Fake Peer Review" returns zero after January 2026.
4. **Treat recent years as provisional.** In September 2024 the record of 2023
   was about two thirds complete. A study should report how its counts for the
   last two years have moved across recent snapshots.
5. **Filter notices before using OpenAlex's flag.** Half of what it marks
   retracted is a notice.

## 6. Limitations

- Snapshots are monthly before 2026-09-29, so an edit made and reverted
  within a month is invisible, and the history cannot be extended before
  2024-09-02, where the public repository begins.
- Identity resolution is one hop. A paper linked only through a chain of
  mappings appears twice, which can undercount agreement but never
  overcounts it.
- OpenAlex and PubMed were fetched as positive sets, so "not flagged" there
  mixes "known and fine" with "unknown to that source".
- PMC coverage is targeted at the 43,616 articles other registries mention,
  not every PMC article.
- Citation counts are per year and start in 2012.
- The hand audit has one labeller, and its blinding is partial (Section 3.5).
  [AUDIT: add a second labeller on a subset if time allows, and report
  agreement.]

## 7. Data and code availability

Code, run reports, the audit codebook, and the labelled sheet:
https://github.com/TanayPratapSingh/afterlife. Findings in this draft come
from `runs/20261027T004647Z-findings.json`; the audit sample from
`runs/20261027T004212Z-audit-sample.json`. `afterlife findings` rebuilds every
number from the landed snapshots. [PENDING: Zenodo DOI for the landed
snapshots and the run reports.]
