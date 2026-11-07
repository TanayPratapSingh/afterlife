# Post retraction citation codebook

FROZEN on 2026-11-07, after 23 practice labels and before any label of the
main sample. Changes from here on go in the log at the bottom with the date
and the rows already labelled.

Each row is one citation: the sentences and table rows in one citing article
that cite one retracted paper, shown with the citation marked [CITED]. Every
cited paper in the sheet has been retracted; the question is what the citing
text does with it.

The reference entry is not labelled by hand. Whether it shows the retraction
("RETRACTED:" in the title, or a cited "Retraction Note") is a separate field
detected by code, because it is mechanical and because it often appears
without the author noticing: a reference manager copies a title the publisher
renamed. The hand label is about the text.

## Labels

Pick exactly one. When more than one fits, the first in this list wins.

| Label | Use when |
| --- | --- |
| `acknowledges` | The citing text says the paper was retracted or withdrawn, or cites it as an example of a retraction. This wins even if the text also uses the finding. |
| `relies` | The citing text states this paper's own specific result as true: a number, an effect it measured, "X et al. found ...". A table row listing its results counts. |
| `uses_method` | The citing article uses a dose, threshold, procedure, protocol, instrument, dataset, cell line, or code from the paper. |
| `disputes` | The citing text questions, criticises, or contradicts the paper's findings without saying it was retracted. |
| `mentions` | Background or topic: a general fact, usually cited with other papers, or a pointer ("X has been studied [CITED]"). Nothing specific to this paper is passed on. |
| `cannot_determine` | The text is too short, garbled, or ambiguous to decide. Say why in notes. |

## Decision rules

1. **Read only the citing text.** Not the reference entry, the retracted
   paper's notice, or the citing article's full text. The label is about what
   a reader of the sentence sees.
2. **This paper's own result is `relies`**: "a 24% reduction in geographic
   atrophy [CITED]", "Cellini et al. found that 24.2% ...". The test: is
   the sentence passing on something this paper measured or found?
3. **General background is `mentions`**, even when stated as fact: "frailty
   and cognitive decline share common mechanisms [9 to CITED]", "defects ...
   are closely associated with PMOP [CITED, 6, 7]". A general fact cited to
   a group of papers does not depend on this one. So is a topic pointer:
   "analyses focused on lung cancer [CITED]".
4. **A dose, threshold, protocol, criterion, or procedure taken from the
   paper is `uses_method`**: "25 mg/kg was given 30 min before injury
   [CITED]".
5. **Several citing sentences, different labels:** take the first that
   applies in the order of the table above (so one acknowledging sentence
   makes the whole citation `acknowledges`).
6. **Table rows:** a row in a table of studies or results is `relies` unless
   the row or the table note marks the study as retracted.
7. **Self citation** (an author of the retracted paper also wrote the citing
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
- 2026-11-07 (third): practice ends at 23 labels. Tanay drew the line
  between `relies` and `mentions` at "this paper's own result" versus
  "general background" on every row, including after the second revision
  said otherwise. That line measures the harm better (a background fact
  cited to a group of papers does not rest on this one) and a careful
  labeller drew it consistently, so the codebook now uses it: `relies` is
  this paper's specific result or a table row of its results; background
  facts are `mentions`. Under it, about 17 of 23 practice labels match; the
  rest are covered by rules 2 to 4 and rule 6 (tables). This supersedes the
  second revision.
- 2026-11-07 (second): after 9 more practice labels (P05, P06, P12 to P18),
  8 of which used `mentions` for background facts stated as true. `relies`
  now says outright that background facts count, with the test "could the
  sentence be false?", and `mentions` is limited to sentences that claim
  nothing.
- 2026-11-07: after 9 practice labels (P01 to P04, P07 to P11; practice rows
  never count). The reference entry is no longer hand labelled: in 3 of 9 it
  said "RETRACTED" or "Retraction Note" while the text used the paper as
  normal, so it is now a separate field detected by code. Added the signal
  words to rules 2 and 3 and the `uses_method` rule 4, after two practice
  mix ups of relies with mentions and one of uses_method with mentions.
