# Asking Retraction Watch Twice: Retraction Data Revise Their Own Past

Tanay Pratap Singh
School of Information Studies, Syracuse University
tanayyps@gmail.com
ORCID https://orcid.org/0009-0004-8617-0218

## Abstract

Since Crossref opened the Retraction Watch database in 2023, it has been
republished daily, and studies cite it as if it were one fixed dataset. We
replayed 39 snapshots spanning its public history (2024-09-02 to 2026-10-16)
and asked each the same four questions about retractions dated 2023 or
earlier, so that any change is the database revising the past. Over two years
such retractions grew 9.1%, those citing "Paper Mill" grew 80.6%, the median
time to retraction rose from 379 to 420 days, and records citing "Fake Peer
Review" fell from 4,654 to zero when the label was renamed. Nearly all the
growth is late cataloguing: 4,482 records added after the first snapshot,
3,921 of them Hindawi retractions. Joining Retraction Watch with OpenAlex,
PubMed, and PubMed Central, all eligible registries agree on 51.5% of 89,681
flagged papers, and 49.3% of the works OpenAlex marks as retracted are
retraction notices, which its ingest rule flags along with the papers they
retract. A blind hand audit of 200 papers that only one registry flags
estimates that 95.0% (91.5% to 98.6%) were retracted, so most
disagreement is a registry missing a retraction. We recommend citing and
archiving the snapshot a study uses.

**Keywords:** retraction; Retraction Watch; OpenAlex; research integrity;
reproducibility; metascience

<!-- as_of.series[0], as_of.series[-1], as_of.late_records, registries.papers,
registries.all_eligible_agree, registries.excluded_as_notices -->

## 1. Introduction

A study that reports "N retractions for paper mill activity through 2023"
reports a property of the database on the day it was queried, not a property
of the literature. That would not matter if retraction databases only ever
appended new notices. They do not. Records are added years after the
retraction, reasons are renamed in bulk, and dates and identifiers are
corrected.

We ask how different the answers are when two researchers put the same
question about the past to Retraction Watch on different days, and why. That
needs the database's history as well as its current state. Crossref publishes the Retraction Watch database as a CSV
in a public GitLab repository, regenerated daily, and every regeneration is a
commit. The history therefore exists, and the maintainers log taxonomy
changes, but no study we found reads it as a time series of records. The
closest work compares two time points across indexes (Salami et al., 2024b) or
tracks Crossref DOIs that lost their retracted flag, and names longitudinal
study of data quality as future work (Si et al., 2026).

We make four contributions.

1. A replay of 39 Retraction Watch snapshots covering the whole public history,
   keeping every version of every record, so the database can be queried as
   of any snapshot date.
2. A fixed set of questions asked of every snapshot, showing how much and
   through which mechanisms the answers move (Section 4.1).
3. A four registry comparison (Retraction Watch, OpenAlex, PubMed, PubMed
   Central) on current data, which replicates the disagreement that earlier
   studies found across other indexes, and independently measures how often
   OpenAlex's retraction flag marks the notice rather than the paper
   (Sections 4.2 and 4.3).
4. A blind hand audit of papers that only one registry flags, judged
   against publishers rather than against the union of the registries, with
   its codebook committed before labelling (Section 4.4).

## 2. Related work

**Indexes disagree about what is retracted.** This is well established.
Schneider et al. (2023) compared Crossref, Retraction Watch, Scopus, and Web
of Science and found only 3% of 49,924 DOIs flagged by all four. Salami et al.
(2024a, corrected 2026) extended the comparison to 11 sources and found at
least one disagreement on 92.64% of 83,317 items. Ortega and Delgado-Quirós
(2024) compared coverage across seven databases including OpenAlex and
PubMed, and Sebo and Sebo (2025) compared Retraction Watch, PubMed, and Web of
Science on 878 retracted publications in 131 high impact medical journals. We
do not claim disagreement as a finding. Section 4.2 replicates
it with OpenAlex and PMC, which the 11 source study did not include.

**Notices indexed as retracted papers.** Hand checks by the same group found
that a retraction index often holds the notice rather than the paper: 59 of 201
DOIs sampled from those on which the four sources disagreed in Schneider et
al. (2023), and 661 of 925 sampled
Crossref DOIs (71.46%) in Si et al. (2026). For OpenAlex, Hauschke and
Nazarovets (2025) reported a different failure: between 2023-12-22 and
2024-03-19 OpenAlex set `is_retracted` for any work carrying an update
relation, such as a correction or an expression of concern, and OpenAlex
reported fixing about 2,300 works. A company report (Tesseract Semantics,
2026), not peer reviewed, states that OpenAlex "flags 94.5 per cent of Retraction Watch notice DOIs as
retracted". Section 4.3 measures the same thing independently.

**Retraction data as a moving target.** Si et al. (2026) found that between
April 2023 and July 2024 Crossref removed the retracted flag from 208 DOIs, none
of which was in fact retracted, and concluded that "retraction metadata are
dynamic rather than static". Their study covers Crossref only, and they name
longitudinal assessment of data quality as future work. Salami et al. (2024b)
compared union lists built in April 2023 and July 2024. Their own 11 source
dataset was later corrected because it had included Retraction Watch records
whose nature is not a retraction (expressions of concern, corrections,
reinstatements) (Salami and McCumber, 2026), so even specialists can misread
the database's structure. Retraction Watch documents its
taxonomy changes in a dated changelog (Retraction Watch, n.d.). We found no
study that compares Retraction Watch releases record by record, or that
measures what those changes do to the answers a study gets.

**Citation after retraction.** Retracted papers keep being cited, mostly
without acknowledgement of the retraction (Bar-Ilan and Halevi, 2017;
Schneider et al., 2020); Hsiao and Schneider (2021) found that only 722 of
13,252 (5.4%) post retraction citation contexts in the PubMed Central Open
Access Subset acknowledged the retraction.
Our citation counts (Section 4.5) measure volume at year granularity and do
not classify contexts.

## 3. Data and methods

### 3.1 Sources

All four sources were fetched on 2026-10-19 from public endpoints: the
Retraction Watch Database (2026), OpenAlex (Priem et al., 2022), PubMed, and
the PMC Article Datasets (National Library of Medicine, 2026).

| Source | What we fetched |
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

**Validation against the maintainers' changelog.** Retraction Watch logs its
taxonomy and format changes with dates (Retraction Watch, n.d.). Every change
it logs inside our window appears in the first replayed snapshot after its
logged date, classified the right way:

| Logged | Change | First replayed snapshot | What the replay recorded |
| --- | --- | --- | --- |
| 2024-09-23 | "Withdrawal" renamed "Removed" | 2024-10-01 | relabel, 3,798 records |
| 2025-02-28 | about a hundred reason renames | 2025-03-03 | relabels, 28,223 updated records |
| 2025-03-06 | "Unreliable Results" renamed | 2025-04-01 | relabel, 16,379 records |
| 2025-08-21 | "+" prefix removed from reasons | 2025-09-02 | format only, 65,898 records |
| 2025-11-25 | "Concerns/Issues with Peer Review" renamed | 2025-12-01 | relabel, 10,557 records |
| 2025-12-12 | "Fake Peer Review" renamed | 2026-01-02 | relabel, 10,426 records |

<!-- upstream.top_reason_relabels, upstream.largest_days -->

The replay recovered every documented change without being told about it,
which gives us grounds to treat the undocumented changes (late records, DOI
and date edits, deletions) as real.

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
Retraction Watch records; and 19 of the 22 PubMed only papers have a PMID
but no DOI. A labeller could infer the source from which links exist.

Precision per stratum is the share labelled `retracted` among papers not
labelled `cannot_determine`, with Wilson 95% intervals. The frame estimate
weights strata by population with a finite population correction, so the
PubMed census adds no sampling variance.

## 4. Results

### 4.1 Retraction Watch revises its past

Table 1 gives the four answers on the first and last snapshots; Figure 1
shows every snapshot.

**Table 1.** The same four questions, asked 25 months apart.

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

These are not directly comparable with Salami et al. (2024a), who measure
agreement over the union of 11 sources without conditioning on whether a
source could have held a paper. The one registry both studies include
behaves alike: in Salami et al. (2024a) PubMed had the highest agreement
score, indexing as retracted 22,766 (87.69%) of the 25,962 union list items it
covers, and here it covers 88.8% of the eligible union. Retraction Watch sits
lowest here (75.5%) because the union includes OpenAlex, whose flag reaches many papers no other
registry marks; Section 4.4 asks how many of those are real.

### 4.3 Half of OpenAlex's retracted works are notices

Of the 136,873 OpenAlex works with `is_retracted: true`, 67,544 (49.3%) are
retraction notices: 38,161 have type `retraction`, and 29,383 more carry a DOI
or PMID that Retraction Watch records as a notice. Counted as papers, they
would nearly double the registry. PMC handles the same distinction well: of
the 17,951 notice articles it holds, 187 carry its retracted flag.
<!-- registries.excluded_as_notices, registries.pmc_notice_articles -->

From the notice's side the picture is the same. Of the 44,651 Retraction
Watch retraction notice DOIs that are not also some paper's DOI, OpenAlex
flags 41,460 (92.9%) as retracted. This independently reproduces, within two
points, the 94.5% in the Tesseract Semantics (2026) report, on data fetched
two months later. <!-- registries.rw_notice_dois_flagged_by_openalex,
runs/20261027T005839Z-findings.json -->

OpenAlex's public ingest code shows why. It sets `is_retracted` when either a
work's Crossref `update-to` relation or its `updated-by` relation carries a
retraction label (OpenAlex, 2026). A retraction notice is the record that
carries `update-to`, pointing at the paper it retracts, so the rule flags the
notice along with the paper.

This differs from the failure Hauschke and Nazarovets (2025) reported, which
flagged corrected papers and ran for three months in early 2024. This one
flags the notice that announces a retraction, is current, and affects nine in
ten notices.

### 4.4 How many single registry flags are real

Of the 200 sampled papers, 183 could be decided from the publisher's page.
The other 17 could not: 14 of them are papers only Retraction Watch flags,
and 12 of those 14 have no DOI, PMID, or PMCID to follow. Across all 200, the
labels were `retracted` 173 times, `notice` 6, `not_retracted` 3, and `concern` once.
<!-- runs/20261027T221642Z-audit-score.json -->

**Table 3.** Precision of a flag that no other registry shares: the share the
publisher actually retracted, among papers that could be decided.

| Flagged only by | Papers in frame | Sampled | Decided | Precision (95% CI) | Notices |
| --- | ---: | ---: | ---: | --- | ---: |
| OpenAlex | 16,120 | 89 | 86 | 98.8% (93.7% to 99.8%) | 1 |
| PubMed | 22 | 22 | 22 | 86.4% (66.7% to 95.2%) | 1 |
| Retraction Watch | 20,259 | 89 | 75 | 92.0% (83.6% to 96.3%) | 4 |

<!-- audit score strata -->

Weighted by stratum size, with a finite population correction for the PubMed
census, 95.0% (91.5% to 98.6%) of the 36,401 single registry flags are real
retractions. <!-- audit score frame_estimate --> Most disagreement, then,
comes from one registry holding a retraction the others miss; few single
flags point at papers that were never retracted. Treated as a measure of
coverage, the union in Table 2 is close to right.

Three cautions apply. The OpenAlex figure is precision after the notice filter of
Section 3.4, not of the raw `is_retracted` flag, which Section 4.3 shows is
half notices. Papers flagged only by OpenAlex may still be in Retraction Watch
under a record with no DOI or PMID, which one hop identity resolution cannot
match. And the Retraction Watch stratum rests on 75 decided papers: counting
all 14 undecided ones as not retracted, the worst case, it would be 69 of 89.

Notices counted as papers make up 6 of the 200, the same failure as Section
4.3 at a smaller scale, and they turn up in all three strata.

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
does. The difficulty lies with the studies that use it. A count from this
database is a count as of a date, so a study that reports neither the date
nor the file cannot be repeated.

Si et al. (2026) suggest assessing data quality over time. This paper does
that for Retraction Watch, and agrees with their conclusion that researchers
"may obtain different results about retraction status depending on when the
data are collected". It adds a measure of how much the results differ and a
split of that difference into causes.

Six practices would make retraction studies easier to repeat.

1. **Cite the snapshot.** Report the snapshot date and the Crossref commit
   that produced the file, not "Retraction Watch, accessed in 2025".
2. **Archive the file.** The CSV is about 68 MB; deposit the exact copy used.
3. **Match reasons on a versioned taxonomy.** Map old labels to new before
   counting, or a query for "Fake Peer Review" returns zero after January 2026.
4. **Treat recent years as provisional.** In September 2024 the database held
   about two thirds of the 2023 retractions it held in October 2026. A study
   should report how its counts for the
   last two years have moved across recent snapshots.
5. **Filter by nature before counting.** Retraction Watch holds expressions
   of concern, corrections, and reinstatements beside retractions; counting
   all of them as retractions is the error behind the 2026 correction of
   Salami et al. (2024a).
6. **Filter notices before using OpenAlex's flag.** Half of what it marks
   retracted is a notice, and it flags nine in ten notice DOIs.

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
- The hand audit has one labeller, so agreement between labellers is not
  measured, and its blinding is partial (Section 3.5). Three clarifications
  of the codebook were made during labelling; each is logged with its date.

## Use of AI tools

Generative AI (Anthropic's Claude) assisted with writing code, drafting and
editing text, and checking cited sources against their originals. The author
designed the study, labelled every audit row by hand from publishers' pages,
traced every reported number to a run report, and takes full responsibility
for the content.

## Author contributions

Tanay Pratap Singh: Conceptualization, Data curation, Formal analysis,
Investigation, Methodology, Software, Validation, Visualization, Writing
(original draft), Writing (review and editing).

## Competing interests

The author has no competing interests.

## Funding information

This work received no specific funding.

## Data availability

Code, run reports, the audit codebook, and the labelled sheet:
https://github.com/TanayPratapSingh/afterlife. Findings in this paper come
from `runs/20261027T005839Z-findings.json`; the audit sample from
`runs/20261027T004212Z-audit-sample.json`; the audit scores from
`runs/20261027T221642Z-audit-score.json`. `afterlife findings` rebuilds every
number from the landed snapshots. The 39 Retraction Watch snapshots and every
other source file, exactly as fetched, with checksums, are archived at
https://doi.org/10.17605/OSF.IO/D7GSF (Singh, 2026).

## References

- Bar-Ilan, J., and Halevi, G. (2017). Post retraction citations in context:
  a case study. *Scientometrics*, 113(1), 547 to 565.
  https://doi.org/10.1007/s11192-017-2242-0
- Hauschke, C., and Nazarovets, S. (2025). (Non-)retracted academic papers in
  OpenAlex. *Journal of Information Science*, 52(4), 993 to 997. First
  published online 2025. https://doi.org/10.1177/01655515251322478
  (preprint arXiv:2403.13339)
- Hsiao, T.-K., and Schneider, J. (2021). Continued use of retracted papers:
  temporal trends in citations and (lack of) awareness of retractions shown
  in citation contexts in biomedicine. *Quantitative Science Studies*, 2(4),
  1144 to 1169. https://doi.org/10.1162/qss_a_00155
- National Library of Medicine (2026). PMC Article Datasets and PMC-ids.csv,
  NIH NLM NCBI PubMed Central. Accessed 2026-10-19.
  https://registry.opendata.aws/ncbi-pmc
- OpenAlex (2026). openalex-walden, notebooks/ingest/Crossref.py, lines 507
  to 510, commit 888f557. GitHub.
  https://github.com/ourresearch/openalex-walden/blob/888f557e3eb5b6ca155bb71f29b9b1d202768e02/notebooks/ingest/Crossref.py#L507-L510
- Ortega, J. L., and Delgado-Quirós, L. (2024). The indexation of retracted
  literature in seven principal scholarly databases: a coverage comparison
  of dimensions, OpenAlex, PubMed, Scilit, Scopus, The Lens and Web of
  Science. *Scientometrics*,
  129(7), 3769 to 3785. https://doi.org/10.1007/s11192-024-05034-y
- Priem, J., Piwowar, H., and Orr, R. (2022). OpenAlex: A fully-open index
  of scholarly works, authors, venues, institutions, and concepts.
  arXiv:2205.01833. https://arxiv.org/abs/2205.01833
- Retraction Watch Database (2026). The Center for Scientific Integrity,
  distributed by Crossref. Snapshots 2024-09-02 to 2026-10-16.
  https://doi.org/10.13003/c23rw1d9 and
  https://gitlab.com/crossref/retraction-watch-data
- Retraction Watch (n.d.). Retraction Watch Database User Guide, Appendix D:
  Changes. Accessed 2026-10-26.
  https://retractionwatch.com/retraction-watch-database-user-guide/retraction-watch-database-user-guide-appendix-d-changes/
- Salami, M. O., and McCumber, C. (2026). Redacted dataset for Analyzing the
  Consistency of Retraction Indexing (version 2, corrected 2026-07-10).
  University of Illinois Urbana-Champaign Databank.
  https://doi.org/10.13012/B2IDB-8114408_V2
- Salami, M. O., McCumber, C., and Schneider, J. (2024a). Analyzing the
  Consistency of Retraction Indexing. MetaArXiv, version 4 of 2026-08-05.
  https://doi.org/10.31222/osf.io/gvfk5_v4
- Salami, M. O., McCumber, C., and Schneider, J. (2024b). Reassessment of
  the agreement in retraction indexing across 4 multidisciplinary sources:
  Crossref, Retraction Watch, Scopus, and Web of Science. ASIS&T MET-STI 2024
  workshop. Corrected 2026-07-31. https://doi.org/10.5281/zenodo.21657305
- Schneider, J., Lee, J., Zheng, H., and Salami, M. O. (2023). Assessing the
  agreement in retraction indexing across 4 multidisciplinary sources:
  Crossref, Retraction Watch, Scopus, and Web of Science. 27th
  International Conference on Science, Technology and Innovation Indicators
  (STI 2023), Leiden. https://doi.org/10.55835/6441e5cae04dbe5586d06a5f
- Schneider, J., Ye, D., Hill, A. M., and Whitehorn, A. S. (2020). Continued
  post-retraction citation of a fraudulent clinical trial report, 11 years
  after it was retracted for falsifying data. *Scientometrics*, 125(3),
  2877 to 2913. https://doi.org/10.1007/s11192-020-03631-1
- Sebo, P., and Sebo, M. (2025). Comparing the performance of Retraction
  Watch Database, PubMed, and Web of Science in identifying retracted
  publications in medicine. *Accountability in Research*, 33(2), 2484555.
  https://doi.org/10.1080/08989621.2025.2484555
- Singh, T. P. (2026). afterlife: Retraction Watch snapshots and four
  registry data [Data set]. OSF. https://doi.org/10.17605/OSF.IO/D7GSF
- Si, L., Salami, M. O., and Schneider, J. (2026). Tracking the data quality
  landscape of retracted papers: flag usage in titles and changes in DOI
  retraction status. https://jodischneider.com/pubs/stiend2026.pdf
- Tesseract Semantics (2026). Science has no Shepard's: what happens when the
  registers of retraction disagree. Company report, August 2026.
  https://tesseractsemantics.com/research/scholarly-record-ontology
