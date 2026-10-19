# ADR 0003: Python reference implementations, Spark native fast paths, tested against each other

## Context

Two pieces of logic are needed in two places. Identifier normalization (DOI,
PMID, PMCID) runs in the API Lambda and in the Spark jobs. The status rule
that collapses a paper's notice history runs in tests, in the API, and over
tens of thousands of papers in Spark.

A Python UDF would reuse the code directly, at the price of a serialization
round trip per row and logic the optimizer cannot see. Native Spark
expressions are fast but are a second implementation, and Java regex is not
Python regex.

## Decision

- `afterlife/core/ids.py` and `afterlife/core/status.py` are the reference
  implementations, standard library only.
- `afterlife/spark/expressions.py` reimplements the identifier rules as native
  column expressions, using only regex constructs that mean the same thing in
  both dialects.
- The status rule is rewritten as a closed form over per paper maxima
  (`status.closed_form`), proved equal to the fold by an exhaustive test over
  every notice history of up to four events (22,621 histories), and then
  expressed in Spark as one aggregation and one CASE.
- `tests/test_spark.py` runs the Spark expressions and the Python reference
  over every DOI and PMID in the landed data, and the Spark status against the
  Python fold over every paper, and requires zero differences.

## Consequences

Every change to a rule must be made twice and the equivalence tests must stay
green. That is deliberate friction: the alternative is two implementations
that drift and a join that silently loses rows.
