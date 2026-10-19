"""Identifier normalization: the join keys for four registries.

Four registries describe the same papers with three kinds of identifier, and
each writes them differently:

    Retraction Watch  10.1016/J.X.2020.1   PMID "0" for missing, "Unavailable"
    OpenAlex          https://doi.org/10.1016/j.x.2020.1   pmid as a URL
    PubMed            PMID only
    PMC               pmcid "PMC123", doi may be null

These functions are the reference implementation. The Spark jobs use native
column expressions for speed (afterlife/spark/expressions.py), and a test runs
both over every identifier in the landed data and requires them to agree.
That test is the reason this module exists as plain Python: a regex that means
one thing in Python's `re` and another in Java's `java.util.regex` is exactly
the kind of bug that produces a silently smaller join.

Rules, each one forced by a value seen in the real data:

* DOIs are case insensitive (the DOI handbook says so), so they are lowercased.
* URL and `doi:` prefixes are removed.
* Surrounding whitespace is removed. Trailing punctuation is NOT removed:
  `10.1061/(ASCE)0733-9399(2010)136:2(174)` is a real DOI ending in `)`.
* Placeholders (`Unavailable`, `N/A`, empty) become None.
* A DOI must start with `10.` followed by a registrant code and a slash.
* PMID `0` is Retraction Watch's way of writing "none".
"""

from __future__ import annotations

import re

DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi.org/",
    "doi:",
)
PLACEHOLDERS = {"", "unavailable", "not available", "n/a", "na", "none", "null", "0"}
DOI_SHAPE = re.compile(r"^10\.[0-9]{4,9}(\.[0-9]+)*/\S+$")
PMID_SHAPE = re.compile(r"^[1-9][0-9]{0,8}$")
PMCID_SHAPE = re.compile(r"^PMC[1-9][0-9]{0,9}$")


def doi(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    for prefix in DOI_PREFIXES:
        if v.startswith(prefix):
            v = v[len(prefix) :].strip()
            break
    if v in PLACEHOLDERS:
        return None
    return v if DOI_SHAPE.match(v) else None


def pmid(value: str | int | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip()
    for prefix in ("https://pubmed.ncbi.nlm.nih.gov/", "http://pubmed.ncbi.nlm.nih.gov/"):
        if v.startswith(prefix):
            v = v[len(prefix) :].strip("/")
    if v.endswith(".0"):  # a PMID that passed through a float column
        v = v[:-2]
    return v if PMID_SHAPE.match(v) else None


def pmcid(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().upper()
    for prefix in (
        "HTTPS://WWW.NCBI.NLM.NIH.GOV/PMC/ARTICLES/",
        "HTTPS://PMC.NCBI.NLM.NIH.GOV/ARTICLES/",
    ):
        if v.startswith(prefix):
            v = v[len(prefix) :].strip("/")
    if v.isdigit():
        v = "PMC" + v
    v = v.split(".", 1)[0]  # PMC123.2 is version 2 of PMC123
    return v if PMCID_SHAPE.match(v) else None


def openalex_id(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().rsplit("/", 1)[-1].upper()
    return v if re.fullmatch(r"W[0-9]+", v) else None


def classify(value: str) -> tuple[str, str] | None:
    """Guess which identifier a free text lookup is, for the public API.

    Returns (kind, normalized) or None. Order matters: a bare number is a
    PMID, never a PMCID, because PubMed is the larger namespace.
    """
    v = value.strip()
    if d := doi(v):
        return ("doi", d)
    if v.upper().startswith("PMC") and (c := pmcid(v)):
        return ("pmcid", c)
    if p := pmid(v):
        return ("pmid", p)
    if w := openalex_id(v):
        return ("openalex", w)
    return None
