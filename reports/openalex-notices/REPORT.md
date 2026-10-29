# Report to OpenAlex: retraction notices flagged as retracted

Paste the subject and message below into https://openalex.org/contact (log in
with a free OpenAlex account first). The form has no attachments, so the
evidence is linked.

**Subject** (under 150 characters):

is_retracted is true on retraction notices themselves, not only on retracted papers

**Message:**

Hello OpenAlex team,

OpenAlex sets is_retracted to true on most retraction notices, the documents
that announce a retraction, as well as on the papers they retract. Your field
reference defines is_retracted as "True if the work is known to be retracted,
per the Retraction Watch database". A notice is not itself retracted, so
anyone who counts or filters retracted papers with this field also counts the
notices.

What I measured, on works fetched from the API on 2026-10-19:

- Of the 44,651 distinct retraction notice DOIs in the Retraction Watch
  database that are not also the DOI of the retracted paper, 41,460 (92.9%)
  are OpenAlex works with is_retracted true.
- Of those 41,460, you type 24,225 as retraction and 16,396 as article, so for
  the second group a notice looks like a retracted research article on both
  fields.
- Of all 136,873 works with is_retracted true that I fetched, 53,618 (39.2%)
  are notices.

Likely cause: notebooks/ingest/Crossref.py in openalex-walden, lines 507 to
510 at commit 888f557, sets is_retracted when either the Crossref update-to
relation or the updated-by relation carries a retraction label. A notice is
the record that carries update-to, pointing at the paper it retracts, so the
rule flags the notice as well as the paper.
https://github.com/ourresearch/openalex-walden/blob/888f557e3eb5b6ca155bb71f29b9b1d202768e02/notebooks/ingest/Crossref.py#L507-L510

Suggested fix: set is_retracted only from updated-by (the work that a
retraction points at), and type notices as retraction.

Field: is_retracted. Correct value for these works: false.

Examples:
- https://doi.org/10.1001/archdermatol.2012.1796, https://openalex.org/W2018706885, typed article
- https://doi.org/10.1001/archinte.166.16.1787-a, https://openalex.org/W227976872, typed article

Evidence, one row per notice with the notice DOI, OpenAlex work, OpenAlex
type, Retraction Watch record id, and notice date:
https://github.com/TanayPratapSingh/afterlife/blob/main/reports/openalex-notices/openalex_retraction_notices_flagged_retracted.csv

This is different from the March 2024 issue (corrections and expressions of
concern flagged as retracted), which you fixed. Preprint: https://doi.org/10.31222/osf.io/9wgpy_v1. Method, code, and data:
https://github.com/TanayPratapSingh/afterlife and
https://doi.org/10.17605/OSF.IO/D7GSF

Thank you for OpenAlex; this work depends on it.

Tanay Pratap Singh
tanayyps@gmail.com
ORCID 0009-0004-8617-0218
