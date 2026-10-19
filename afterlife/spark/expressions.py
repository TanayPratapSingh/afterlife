"""Native Spark column expressions for the identifier rules in core/ids.py.

A Python UDF would be the obvious way to reuse core/ids.py, and it would cost
a serialization round trip per row and hide the logic from the optimizer.
These expressions are the fast path. They are allowed to exist only because
tests/test_spark.py runs them and the Python reference over every identifier
in the landed data and requires identical output.

Java regex and Python regex differ in small ways (`\\S`, `\\d` under Unicode,
possessive quantifiers), so the patterns are written in the subset where the
two agree: explicit ASCII classes only.
"""

from __future__ import annotations

from pyspark.sql import Column
from pyspark.sql import functions as F

from afterlife.core import ids

# Whitespace exactly as Python's str.strip() and `\s` define it: Unicode
# White_Space under Java's (?U) flag, plus U+001C to U+001F, which Python counts
# and Java does not. F.trim strips only U+0020, so it is not used on ids. The
# Databricks layer review caught the difference on a non breaking space.
_WS = r"[\s\x{1C}-\x{1F}]"
_DOI_SHAPE = r"(?U)^10\.[0-9]{4,9}(\.[0-9]+)*/[^\s\x{1C}-\x{1F}]+$"
_PREFIX = r"^(https?://(dx\.)?doi\.org/|doi\.org/|doi:)"


def strip(col: Column) -> Column:
    """Python's str.strip(), as a native expression."""
    return F.regexp_replace(col, rf"(?U)^{_WS}+|{_WS}+$", "")


def doi(col: Column | str) -> Column:
    c = F.col(col) if isinstance(col, str) else col
    v = F.lower(strip(c))
    v = strip(F.regexp_replace(v, _PREFIX, ""))
    placeholders = F.array(*[F.lit(p) for p in sorted(ids.PLACEHOLDERS)])
    return (
        F.when(c.isNull(), F.lit(None))
        .when(F.array_contains(placeholders, v), F.lit(None))
        .when(v.rlike(_DOI_SHAPE), v)
        .otherwise(F.lit(None))
        .cast("string")
    )


def pmid(col: Column | str) -> Column:
    c = F.col(col) if isinstance(col, str) else col
    v = strip(c.cast("string"))
    v = F.regexp_replace(v, r"^https?://pubmed\.ncbi\.nlm\.nih\.gov/", "")
    v = F.regexp_replace(v, r"/+$", "")
    v = F.regexp_replace(v, r"\.0$", "")
    return F.when(v.rlike(r"^[1-9][0-9]{0,8}$"), v).otherwise(F.lit(None)).cast("string")


def pmcid(col: Column | str) -> Column:
    c = F.col(col) if isinstance(col, str) else col
    v = F.upper(strip(c))
    v = F.regexp_replace(
        v,
        r"^HTTPS://(WWW\.NCBI\.NLM\.NIH\.GOV/PMC/ARTICLES/|PMC\.NCBI\.NLM\.NIH\.GOV/ARTICLES/)",
        "",
    )
    v = F.regexp_replace(v, r"/+$", "")
    v = F.when(v.rlike(r"^[0-9]+$"), F.concat(F.lit("PMC"), v)).otherwise(v)
    v = F.split(v, r"\.").getItem(0)
    return F.when(v.rlike(r"^PMC[1-9][0-9]{0,9}$"), v).otherwise(F.lit(None)).cast("string")


def openalex_id(col: Column | str) -> Column:
    c = F.col(col) if isinstance(col, str) else col
    v = F.upper(F.element_at(F.split(strip(c), "/"), -1))
    return F.when(v.rlike(r"^W[0-9]+$"), v).otherwise(F.lit(None)).cast("string")


def rw_date(col: Column | str) -> Column:
    """Retraction Watch writes dates as `M/d/yyyy H:mm`, always at midnight."""
    c = F.col(col) if isinstance(col, str) else col
    return F.to_date(F.split(F.trim(c), " ").getItem(0), "M/d/yyyy")


def norm_text(col: Column | str) -> Column:
    """Whitespace insensitive text: trimmed, runs of whitespace collapsed,
    empty as NULL. The 2026-10-15 export put line breaks inside cells that
    used to hold `; `, which is not an edit."""
    c = F.col(col) if isinstance(col, str) else col
    v = F.regexp_replace(F.trim(c), r"\s+", " ")
    return F.when(v == "", F.lit(None)).otherwise(v)


def norm_list(col: Column | str, *, ordered: bool = False, casefold: bool = False) -> Column:
    """A `;` separated list as a canonical string: items trimmed, the legacy
    `+` prefix on reason labels removed (dropped upstream on 2025-09-02),
    empties and duplicates removed, sorted unless order carries meaning.

    casefold is for controlled vocabularies (reasons, subjects, countries,
    article types), where "Concerns/Issues About Data" and "Concerns/Issues
    about Data" are one label. Upstream changed that capitalization on 12,854
    records on 2025-12-01. Free text lists such as authors keep their case."""
    c = F.col(col) if isinstance(col, str) else col
    items = F.transform(
        F.split(F.coalesce(c, F.lit("")), ";"),
        lambda x: F.regexp_replace(F.regexp_replace(F.trim(x), r"^\+", ""), r"\s+", " "),
    )
    if casefold:
        items = F.transform(items, lambda x: F.lower(x))
    items = F.array_distinct(F.filter(items, lambda x: x != ""))
    if not ordered:
        items = F.array_sort(items)
    joined = F.concat_ws(";", items)
    return F.when(joined == "", F.lit(None)).otherwise(joined)


# Characters Windows-1252 can represent: ASCII, Latin-1, and 27 extras in 0x80
# to 0x9F. Anything else is what a cp1252 export replaces with "?".
_CP1252_EXTRAS = bytes(range(0x80, 0xA0)).decode("cp1252", errors="ignore")
NOT_CP1252 = r"[^\x{00}-\x{7F}\x{A0}-\x{FF}" + _CP1252_EXTRAS + "]"


def lossy_transcode(old: Column, new: Column) -> Column:
    """True when `new` is `old` with every character outside cp1252 replaced
    by '?'. That is the signature of a UTF-8 to cp1252 export with
    replacement, and it destroys information rather than reformatting it."""
    return (
        old.isNotNull()
        & new.isNotNull()
        & (old != new)
        & (F.length(old) == F.length(new))
        & (F.regexp_replace(old, NOT_CP1252, "?") == new)
    )


def semicolon_list(col: Column | str) -> Column:
    """`a;b;c;` into an array of trimmed, non empty, distinct values.

    Subject entries carry a code prefix, `(BLS) Genetics`; it is kept, since
    the code is the only thing that separates two fields with one name.
    """
    c = F.col(col) if isinstance(col, str) else col
    parts = F.transform(F.split(F.coalesce(c, F.lit("")), ";"), lambda x: F.trim(x))
    return F.array_distinct(F.filter(parts, lambda x: x != ""))
