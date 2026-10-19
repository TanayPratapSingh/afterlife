# ADR 0001: No registry is ground truth, and absence is not good standing

## Context

Four public sources say whether a paper was retracted: Retraction Watch (now
distributed by Crossref), OpenAlex's `is_retracted`, PubMed's "Retracted
Publication" publication type, and the `is_retracted` field in PMC's open
data metadata. Each has a different scope (PubMed only covers PMID bearing
papers, PMC only its open subset), a different update path, and a different
error profile. They disagree, and the disagreement is the subject of this
project.

A downstream system (EvidenceOps, or any retrieval tool) wants one answer.

## Decision

1. Keep every registry's flag separately, all the way to the serving
   document. The verdict is derived, never stored in place of the flags.
2. The verdict has a value, `disputed`, for papers where Retraction Watch
   holds a record that is not a standing retraction while another registry
   says retracted. Disagreement is surfaced, not resolved by fiat.
3. The API never returns "ok". A lookup with no flag returns `not_flagged`
   with the snapshot date and a caveat that coverage differs by source.
4. Agreement is measured per source only over papers that source was
   eligible to flag (PubMed needs a PMID; PMC needs a linked open copy), with
   the union of all flags as the reference. The union is the most generous
   reading of "somebody says this is retracted", not ground truth.

## Consequences

The registry is larger and noisier than any single source, and `disputed`
needs explaining. In exchange, a consumer can apply its own policy (EvidenceOps
treats `disputed` as not citable), and the coverage gap of each source is a
measured number instead of an assumption.
