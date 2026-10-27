# Hand audit codebook

The registry table scores each source against the union of all four, and the
union is not ground truth (ADR 0001). This audit measures how much of the union
is real: of the papers that exactly one registry flags, what share has the
publisher actually retracted?

This codebook was written and committed before any label was entered. Changes
after labelling starts go in the log at the bottom, with the date and the
sample ids already labelled at the time.

## The sample

`afterlife audit sample` draws 200 papers from the 36,401 that exactly one
registry flags, stratified by that registry with equal allocation: every
PubMed only paper (there are 22), then the rest split evenly between Retraction
Watch only and OpenAlex only. No paper is flagged by PMC alone. The seed is
fixed in `afterlife/audit.py`, so the draw is reproducible.

Label in `audit/labels.xlsx`, built from the committed blank sheet
`audit/labels.csv` by `afterlife audit sheet`: same rows, same order, a
dropdown limited to the six labels, and one Open link per row. Scoring reads
the workbook when it exists. Both show a title, a year, and links, and
nothing else. A leading "RETRACTED:" or "WITHDRAWN:" is stripped from titles
because it reveals both the answer and the source.

## Blinding

- Do not open `runs/*-audit-sample.json` until all 200 rows are labelled. It
  holds the key from sample id to registry.
- Do not use Retraction Watch, OpenAlex, PubMed's retraction banner, or PMC's
  retraction banner as evidence. They are the registries under test. PubMed
  and PMC links are in the sheet only to help find the article.
- Label in sheet order, A001 to A200.

## Evidence

Decide from the publisher, in this order:

1. The version of record on the publisher's site (the `doi_url`): a
   retraction or withdrawal watermark, banner, or linked notice.
2. A retraction or withdrawal notice published by the journal or publisher
   that names the paper. A search for the title plus "retraction" on the
   publisher's site or on Google Scholar is fine for finding it.
3. If the version of record was reached and shows no mark, and no notice turns
   up, the paper is `not_retracted`. If the version of record could not be
   reached, it is `cannot_determine`, never `not_retracted`.

Put the URL that decided the label in `evidence_url`.

Time box: about five minutes per row. Past ten minutes, `cannot_determine`
with a note on what was tried.

## Labels

| Label | Use when |
| --- | --- |
| `retracted` | The publisher has retracted, withdrawn, or removed the item. Includes "retract and replace" (note it), partial retractions (note it), and Elsevier style withdrawals. |
| `notice` | The item itself is a retraction or withdrawal notice, not the paper it retracts. |
| `concern` | An expression of concern stands on the paper and it is not retracted. |
| `corrected` | A correction or erratum exists and the paper is not retracted. |
| `not_retracted` | The version of record was reached, carries no retraction mark, and no notice was found. |
| `cannot_determine` | The version of record could not be reached, or the evidence conflicts. |

The item counts even if it is not a research article (front matter, an
editorial, a book chapter). Say what it is in `notes`.

## Notes tags

Start `notes` with any that apply, separated by spaces, then free text:
`front_matter`, `book_chapter`, `conference`, `retract_and_replace`,
`partial`, `paywalled`, `non_english`, `dead_link`, `wrong_paper`
(the links lead to a different paper than the title).

## Scoring

`afterlife audit score` validates every label against this table and writes
per registry precision (share `retracted` among rows not `cannot_determine`)
with Wilson 95% intervals, the share that are notices, and, once all 200 are
labelled, a stratified estimate over all 36,401 with a finite population
correction. It runs on a partial sheet too, to check progress.

## Change log

- 2026-10-26, before any label was entered: added `audit/labels.xlsx` (above)
  and, on its Guide tab, the rule that a PubMed or PMC page is used only to
  find the publisher's full text. No label definition or sample changed.
