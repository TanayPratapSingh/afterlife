# Post retraction citation codebook

DRAFT. Revise it on the 53 pilot citations, then freeze it in a commit before
the first label of the main sample. Changes after that go in the log at the
bottom with the date and the rows already labelled.

Each row is one citation: the sentences and table rows in one citing article
that cite one retracted paper, shown with the citation marked [CITED], plus
that article's reference entry for the paper. Every cited paper in the sheet
has been retracted; the question is what the citing article does with it.

## Labels

Pick exactly one. When more than one fits, the first in this list wins.

| Label | Use when |
| --- | --- |
| `acknowledges` | The citing text or the reference entry says the paper was retracted or withdrawn (or calls it a retraction, a retracted study, or similar). This wins even if the text also uses the finding. |
| `relies` | The citing text states a finding, number, or claim of the paper as true, or uses it to support a point the citing article makes. A table row that lists the paper's result among the evidence counts. |
| `uses_method` | The citing article uses a method, instrument, dataset, cell line, protocol, or code from the paper, without relying on its findings. |
| `disputes` | The citing text questions, criticises, or contradicts the paper's findings without saying it was retracted. |
| `mentions` | The paper is cited without its finding being used: one of several examples of a topic, a historical note, or a general pointer ("has been studied [CITED]"). |
| `cannot_determine` | The text is too short, garbled, or ambiguous to decide. Say why in notes. |

## Decision rules

1. **Read only the citing text and the reference entry.** Do not look up the
   retracted paper's notice or the citing article's full text to decide. The
   label is about what a reader of this citation sees.
2. **A finding stated as fact is `relies`**, even if one of several
   references: "Drug X reduced mortality [CITED, 4, 5]" relies on each.
3. **A topic pointer is `mentions`**: "Drug X has been studied in COVID-19
   [CITED, 4, 5]" asserts no finding.
4. **Several citing sentences, different labels:** take the first that
   applies in the order of the table above (so one acknowledging sentence
   makes the whole citation `acknowledges`).
5. **Table rows:** a row in a table of studies or results is `relies` unless
   the row or the table note marks the study as retracted.
6. **Self citation** (an author of the retracted paper also wrote the citing
   article) gets its normal label; note `self` in notes.

## Notes tags

Free text, but start with one of these where it applies, so the notes can be
counted: `self`, `table`, `range`, `extraction` (the text looks cut or
merged wrongly), `language` (not English), `written_before` (the text itself
shows it was written before the retraction).

## Scoring

Shares of each label over the labelled rows with Wilson 95% intervals. Rows
labelled `cannot_determine` are reported and left out of the denominators.

## Change log

- 2026-11-06: first draft, before any label.
