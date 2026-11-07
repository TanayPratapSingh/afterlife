# Second study plan: do post retraction citations warn or rely?

Frozen with the codebook on 2026-11-07, before any label of the main sample.
Changes from here on are logged in the codebook's change log.

## Question

The first paper found that 60.0% of 54,737 retracted papers were cited in a
year after their retraction year, 241,815 citations in all
(`runs/20261029T201514Z-findings.json`, key `afterlife`). It counted those
citations and did not read them. This study reads them.

1. **Primary.** Of citations made after a paper's retraction, what share
   acknowledges the retraction, and what share relies on the retracted work as
   valid evidence?
2. **Secondary.** Does that change with years since retraction, retraction
   reason, or where the citation sits (prose or an evidence table)?
3. **Method.** Can a model reproduce the hand labels well enough, with
   calibrated confidence, to classify every readable citation in the frame?

## What is already known

Hsiao and Schneider (2021) found that 722 of 13,252 (5.4%) post retraction
citation contexts in the PubMed Central Open Access Subset acknowledged the
retraction. Bar-Ilan and Halevi (2017) and Schneider et al. (2020) report that
most post retraction citations do not acknowledge it. So acknowledgement alone
is not new. What this study adds:

- **Reliance, not only acknowledgement.** Whether the citing text uses the
  retracted finding as evidence, which is the harm, as distinct from
  mentioning the paper in passing.
- **Evidence tables.** Citations inside table rows, where reviews summarise
  studies as evidence. The pilot found 5 of 53 readable citations only in a
  table, which a sentence based method misses.
- **An equal probability sample** of all post retraction citations in a cross
  registry frame, so each estimate is a share of a defined population with a
  confidence interval.
- **Model triage with calibration,** checked against hand labels, so the
  method can scale past what one person can label.

Before freezing: read Hsiao and Schneider (2021) and the scoping review of
post retraction citation by White, Bakker, and colleagues (WCRI 2026) in full,
and align the acknowledgement label with Hsiao and Schneider's definition so
the two rates are comparable.

## Population and frame

- **Frame.** Citations by works published in a year after the cited paper's
  retraction year, to the 32,848 retracted papers in gold.afterlife_citations
  with a complete OpenAlex citation window, excluding retract and replace
  (241,815 citations; `runs/20261029T201514Z-findings.json`).
- **Readable subset.** A citation is readable when the citing article's JATS
  XML is in PMC's open data and the citing text can be located. In the pilot,
  53 of 200 draws were readable: 26.5% (95% CI 20.9% to 33.0%)
  (`runs/20261106T205748Z-citances-pilot.json`). Estimates describe readable
  citations, which are mostly biomedical; the paper must say so and report
  how readable and unreadable draws differ on citing work type and year.

## Sampling

Two stages. A retracted paper is drawn with probability proportional to its
post retraction citations, then OpenAlex's seeded sampler returns one of its
citing works published after the retraction year. Every citation in the
frame is then equally likely (`afterlife/citances.py`).

- **Pilot (done).** Seed `afterlife-citances-pilot-2026-11-06`, 200 draws, 53
  readable. Used only to develop the codebook and the extractor. Never part of
  any estimate.
- **Practice (done).** 23 pilot rows labelled on 2026-11-07 set where the
  line between `relies` (this paper's own result) and `mentions` (general
  background) falls; see the codebook change log. Practice labels are never
  part of any estimate.
- **Main sample.** A new seed, drawn after the codebook is frozen, enough
  draws for 200 readable citations (about 760 at the pilot's 26.5%).
  Labelled by Tanay by hand, 30 rows a sitting. 200 gives a 95% interval of
  at most about plus or minus 6.9 points on any share.
- **Second labeller.** 40 of the 200, blind to the first labels; Cohen's
  kappa, as in the first paper's audit.

## Unit of labelling

One citation: every citing sentence and table row in one citing article that
points at one retracted paper. The sheet shows the citing text with the
citation marked [CITED], the retracted paper's title, and links to both
articles. Whether the citing article's reference entry shows the retraction
is detected by code and reported as its own share, beside the hand labels,
never merged into `acknowledges`.

## Analysis

- Shares of each label with Wilson intervals.
- The same shares by years since retraction (1, 2 to 3, 4 or more), by
  Retraction Watch reason group (misconduct or error), and by location (prose
  or table).
- A secondary question tied to the first paper: whether the retraction was
  already in Retraction Watch when the citing work appeared, using the
  replayed snapshot history where it covers the citing date.

## Model triage

After hand labelling: a model labels the same 200 from the same codebook
text, never seeing the hand labels. Report agreement with Tanay (kappa and per
label precision and recall), then calibrate its confidence on half and check
it on the other half (expected calibration error). Only if both hold up does
it label the full readable frame, and that result is reported as a model
estimate beside the hand estimate, never in place of it.

## Threats

- Year granularity: a citation in the year after retraction may have been
  written before it. Report the share by years since retraction so this is
  visible.
- OpenAlex reference lists are incomplete and its citing works can be wrong;
  the reference match in the XML is the check (54 of 55 pilot XML files had
  the reference).
- Citations inside collapsed ranges ("10 to 13") are recovered by reference
  list order (8 in the pilot); a range written as plain text with no links is
  not.
- One pilot citation was in the reference list but never cited in the text
  (draw 139). Such cases are counted and excluded, not labelled.

## Timeline

- November: applications first. Read the two papers above; revise the
  codebook on the pilot rows; freeze.
- December: draw the main sample and label 200.
- January: second labeller, model triage, calibration.
- February: write. Target venues: Quantitative Science Studies, Scientometrics,
  or JASIST.
