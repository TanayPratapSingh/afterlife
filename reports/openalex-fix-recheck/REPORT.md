# Follow up to OpenAlex: rerun after the is_retracted fix

**Status:** draft, not sent. Reply in the OpenAlex Support ticket (the thread
with Jason, "Re: is_retracted..."), which asked for leftovers.

Numbers: `summary.json` and the CSVs in this folder, made by `build.py` from
the snapshots landed on 2026-10-19 (before the fix) and 2026-11-16 (after),
run report `runs/20261116T230137Z-fetch-openalex.json`.

**Message:**

Hi Jason,

Thank you again for the fix. I reran the whole is_retracted:true set and
compared it with my October snapshot.

The fix did what it should. 48,374 works dropped out of the flagged set,
including 41,081 that Retraction Watch records as notices. I also checked
whether it unflagged any real retractions: of the 82 Retraction Watch papers
that dropped out, 57 are still flagged under another work id after a merge, 15
DOIs are no longer in OpenAlex, and 10 had no DOI to check. I found no
retracted paper that lost its flag.

Leftovers you asked about:

1. 538 notices still read is_retracted true: works OpenAlex types as
   retraction with a notice title, or that Retraction Watch records as a
   notice, and that Retraction Watch does not also list as the retracted
   paper. Three examples, all true on the live API today:
   - https://openalex.org/W1915577792 (RETRACTION OF PUBLICATION, Mol Pharmacol 1998)
   - https://openalex.org/W1878417068 (Retraction Note, EMBO J)
   - https://openalex.org/W1479496540 (Retraction, J Exp Med 1997)
   Another 956 works look like notices but share a DOI or PMID with a paper
   Retraction Watch lists as retracted, so the flag may be right there. Both
   groups are in one file, column rw: notice or none for the 538, paper or
   both for the 956.
   https://github.com/TanayPratapSingh/afterlife/blob/main/reports/openalex-fix-recheck/leftover_notices.csv

2. A type question rather than a flag error: 6,112 works typed retraction are
   titled "WITHDRAWN:", 5,920 of them Elsevier. These look like the withdrawn
   articles themselves, so is_retracted true fits, but the retraction type
   makes them look like notices, and anyone who drops type:retraction to
   remove notices drops these withdrawn articles too.
   https://github.com/TanayPratapSingh/afterlife/blob/main/reports/openalex-fix-recheck/withdrawn_typed_retraction.csv

Separately, 2,214 works became flagged after the fix that my October
Retraction Watch copy does not list as retractions. Most look like recent
retractions, so I am not reporting them; the list is in the same folder if it
helps.

Method and code: https://github.com/TanayPratapSingh/afterlife/tree/main/reports/openalex-fix-recheck

Best,
Tanay
