# Report to NLM: retracted papers that PubMed neither flags nor links

**Status:** draft, not filed. Hand checked by Tanay on 2026-11-16: all 11
headline PubMed pages show no retraction banner or link, and each retraction is
confirmed on the publisher's page, in PubMed's own title or abstract, or in the
publisher's Crossref record; four other candidates were dropped (reasons in
`build.py`). Send through the NLM Support Center (support.nlm.nih.gov, "Write to
the Help Desk"); check that this is still the right channel for PubMed data
corrections.

Numbers: `summary.json` and `pubmed_unlinked_retractions.csv` in this folder,
made by `build.py` (landed PubMed and Retraction Watch data plus live
E-utilities and Crossref, checked 2026-11-16).

**Subject:**

Retracted papers missing the Retracted Publication type because their retraction notice has no "Retraction of" link

**Message:**

Hello,

I study how retraction status is recorded across PubMed, PMC, Crossref,
OpenAlex, and Retraction Watch. I found PubMed records for retracted papers
that carry no "Retracted Publication" type and no "Retraction in" link, even
though PubMed holds the retraction notice. In each case the notice record has
no "Retraction of" entry with the paper's PMID, so the paper's own record never
learns it was retracted. Its PubMed page shows no retraction banner and no
"Retraction in" link, and a search with "retracted publication"[pt] misses it.
For 6 of the 11 papers below the title is unchanged, so a reader sees no sign
of the retraction at all; the other 5 say RETRACTED or WITHDRAWN only in the
title.

What I measured:

- Of the 34,110 records typed "Retraction Notice" (fetched 2026-10-29), 1,222
  have no "Retraction of" entry naming another PMID.
- For 11 of them I could confirm the retracted paper on three independent
  sources: the notice title names the paper, Retraction Watch links the notice
  to the paper's PMID, and the publisher has deposited the retraction with
  Crossref. On 2026-11-16 all 11 papers still lack the Retracted Publication
  type and all 11 notices still lack the link.

Examples (paper PMID, notice PMID), all with unchanged titles:

- 25670032, notice 39437835 (Endocr Relat Cancer 2015; the publisher page
  says "A retraction has been published")
- 31555577, notice 33425773 (Front Oncol 2019; the publisher page title
  reads "RETRACTED:")
- 35017985, notice 36110643 (J Pharm Bioallied Sci 2021)

Separately, four notices name themselves in their only "Retraction of" entry,
so the paper they retract is not linked: 28856689, 28940543, 29278445,
31393786.

Correct values: on each paper, add the Retracted Publication type and a
"Retraction in" link to the notice; on each notice, add "Retraction of" with
the paper's PMID.

The full list of 11, with DOIs, notice PMIDs, Retraction Watch record IDs, and
Crossref retraction DOIs, is here (the 11 are the rows with
notice_newer_than_paper and crossref_confirms_same_publisher true, cochrane
false, and hand_excluded empty):
https://github.com/TanayPratapSingh/afterlife/blob/main/reports/pubmed-unlinked-notices/pubmed_unlinked_retractions.csv
The other 1,211 unlinked notices may hide more cases; I could not confirm
those from public data alone.

Method, code, and data: https://github.com/TanayPratapSingh/afterlife
(reports/pubmed-unlinked-notices). Related preprint:
https://doi.org/10.31222/osf.io/9wgpy_v2

Thank you for PubMed; this work depends on it.

Tanay Pratap Singh
tanayyps@gmail.com
ORCID 0009-0004-8617-0218
