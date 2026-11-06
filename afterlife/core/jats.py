"""Retraction notice links in PubMed Central's JATS XML.

PMC can mark an article as retracted only when the retraction notice names it
in a structured element, `<related-article related-article-type=
"retracted-article">`, with an identifier PMC can follow. This module reads
those elements from one article's XML, and the identifiers a notice mentions
only in its text, so the two can be counted apart.

The XML in PMC's open data bucket is PMC's processed copy, not the publisher's
deposit: PMC rewrites a link it can resolve into `ext-link-type="pmc"` with a
PMCID, and adds `retraction-forward` elements of its own. What this measures is
the link in PMC's record, the one readers and PMC's `is_retracted` flag see.

Regular expressions, not an XML parser: the tags read here are machine written,
and a parser refuses a whole file over one undeclared entity.
"""

from __future__ import annotations

import html
import re

from afterlife.core import ids

RETRACTED = "retracted-article"
FORWARD = "retraction-forward"  # added by PMC, usually pointing at the notice itself

_RELATED = re.compile(r"<related-article\b([^>]*)>", re.S)
_ATTR = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')
_ARTICLE = re.compile(r"<article\b([^>]*)>", re.S)
_OWN_ID = re.compile(r'<article-id\s+pub-id-type="([^"]+)"[^>]*>([^<]+)</article-id>')
_YEAR = re.compile(r"<pub-date\b[^>]*>.*?<year>(\d{4})</year>", re.S)
_PUBLISHER = re.compile(r"<publisher-name>(.*?)</publisher-name>", re.S)
_JOURNAL = re.compile(r'<journal-id\s+journal-id-type="nlm-ta">([^<]+)</journal-id>')
_TEXT_DOI = re.compile(r"\b10\.\d{4,9}/[^\s<>\"'&]+")
_TEXT_PMCID = re.compile(r"\bPMC\d{3,9}\b")
_TEXT_PMID = re.compile(r"\bPMID:?\s*(\d{1,9})\b", re.I)


def _attrs(tag: str) -> dict[str, str]:
    return {k.removeprefix("xlink:"): html.unescape(v) for k, v in _ATTR.findall(tag)}


def _clean(s: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def notice(xml: str) -> dict:
    """Everything the link measurement needs from one article's XML."""
    head = _ARTICLE.search(xml)
    own = {}
    for kind, value in _OWN_ID.findall(xml):
        own.setdefault(kind, value.strip())
    self_ids = {
        ("doi", ids.doi(own.get("doi"))),
        ("pmcid", ids.pmcid(own.get("pmcid") or own.get("pmc"))),
        ("pmid", ids.pmid(own.get("pmid"))),
    }
    related = []
    for tag in _RELATED.findall(xml):
        a = _attrs(tag)
        related.append(
            {
                "type": a.get("related-article-type"),
                "id_type": a.get("ext-link-type"),
                "href": a.get("href"),
            }
        )
    # Identifiers anywhere else in the file, hyperlinks included, minus its own.
    rest = _RELATED.sub(" ", xml)
    found = {("doi", ids.doi(d.rstrip(".,;)]"))) for d in _TEXT_DOI.findall(rest)}
    found |= {("pmcid", p) for p in _TEXT_PMCID.findall(rest)}
    found |= {("pmid", p) for p in _TEXT_PMID.findall(rest)}
    in_text = sorted((k, v) for k, v in found - self_ids if v)
    years = [int(y) for y in _YEAR.findall(xml)]
    publisher = _PUBLISHER.search(xml)
    journal = _JOURNAL.search(xml)
    return {
        "article_type": _attrs(head.group(1)).get("article-type") if head else None,
        "related": related,
        "ids_in_text": in_text,
        "year": min(years) if years else None,
        "publisher": _clean(publisher.group(1)) if publisher else None,
        "journal": journal.group(1).strip() if journal else None,
    }


_REF = re.compile(r"<ref(?=[\s>])([^>]*)>(.*?)</ref>", re.S)  # not <ref-list>
_PUB_ID = re.compile(r'<pub-id\s+pub-id-type="(doi|pmid)"[^>]*>([^<]+)</pub-id>')
_BODY = re.compile(r"<body\b[^>]*>(.*?)</body>", re.S)
_XREF = re.compile(r"<xref\b([^>]*?)(?:/>|>(.*?)</xref>)", re.S)
_PARA = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S)
_REF_LIST = re.compile(r"<ref-list\b.*?</ref-list>", re.S)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S)
_CELL_BREAK = re.compile(r"</t[dh]>\s*<t[dh]\b[^>]*>")
_DASH = r"(?:\u2013|&#x2013;|&#8211;|&ndash;|-|\u2014)"
_INNER = r"((?:(?!</?xref\b).)*?)"  # an xref's content, never running into the next xref
_RANGE = re.compile(
    rf"<xref\b([^>]*?)>{_INNER}</xref>\s*{_DASH}\s*<xref\b([^>]*?)>{_INNER}</xref>", re.S
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_NO_BREAK_AFTER = ("et al.", "e.g.", "i.e.", "Fig.", "Figs.", "vs.", "cf.", "No.", "Ref.", "Refs.")
CITED = "[CITED]"


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def _ref_matches(body: str, doi: str | None, pmid: str | None, title: str | None) -> str | None:
    """How a reference entry identifies the target paper: doi, pmid, or title."""
    pub = {k: v.strip() for k, v in _PUB_ID.findall(body)}
    dois = {ids.doi(pub.get("doi"))} | {ids.doi(d.rstrip(".,;)]")) for d in _TEXT_DOI.findall(body)}
    target_doi, target_pmid = ids.doi(doi), ids.pmid(pmid)
    if target_doi and target_doi in dois:
        return "doi"
    if target_pmid and ids.pmid(pub.get("pmid")) == target_pmid:
        return "pmid"
    # shortcut: the first eight title words in order, enough to tell papers apart in one
    # reference list; a title shorter than six words is too generic to match on.
    head = _words(title or "")[:8]
    if len(head) >= 6 and " ".join(head) in " ".join(_words(_clean(body))):
        return "title"
    return None


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for piece in _SENTENCE_END.split(text):
        if out and out[-1].endswith(_NO_BREAK_AFTER):
            out[-1] += " " + piece
        else:
            out.append(piece)
    return out


def citances(xml: str, *, doi: str | None, pmid: str | None, title: str | None) -> dict:
    """The sentences and table rows of an article that cite one target paper.

    The target is found in the reference list by DOI, then PMID, then title.
    Each `<xref ref-type="bibr">` pointing at a matching reference is marked,
    and so is a collapsed range ("10-13") whose two ends bracket the target in
    reference list order, since the target then has no xref of its own. Marks
    in table rows come back as `table_rows` (tables can sit outside `<body>`,
    in `<floats-group>`); marks in prose as `sentences`, shown as [CITED].
    """
    order, matched, entries = [], {}, []
    for tag, body in _REF.findall(xml):
        rid = _attrs(tag).get("id")
        order.append(rid)
        if rid and (how := _ref_matches(body, doi, pmid, title)):
            matched[rid] = how
            entries.append(_clean(body))
    out = {
        "has_body": _BODY.search(xml) is not None,
        "ref_ids": sorted(matched),
        "matched_by": sorted(set(matched.values())),
        "references": entries,
        "xrefs": 0,
        "ranges": 0,
        "sentences": [],
        "table_rows": [],
    }
    if not matched:
        return out
    pos = {r: i for i, r in enumerate(order) if r}
    inside = [pos[r] for r in matched if r in pos]

    def bibr(tag_attrs: str) -> list[str]:
        a = _attrs(tag_attrs)
        return (a.get("rid") or "").split() if a.get("ref-type") == "bibr" else []

    def mark_range(m: re.Match) -> str:
        lo, hi = bibr(m.group(1)), bibr(m.group(3))
        if lo and hi and lo[-1] in pos and hi[0] in pos:
            a, b = pos[lo[-1]], pos[hi[0]]
            if any(a < i < b for i in inside):
                out["ranges"] += 1
                return f" {CITED} "
        return m.group(0)

    def mark(m: re.Match) -> str:
        if set(bibr(m.group(1))) & matched.keys():
            out["xrefs"] += 1
            return f" {CITED} "
        return m.group(0)

    text = _REF_LIST.sub(" ", xml)
    text = _RANGE.sub(mark_range, text)
    text = _XREF.sub(mark, text)

    def take_row(m: re.Match) -> str:
        if CITED in (row := _clean(_CELL_BREAK.sub(" | ", m.group(1)))):
            out["table_rows"].append(row.strip(" |"))
        return " "

    text = _ROW.sub(take_row, text)
    for para in _PARA.findall(text):
        out["sentences"] += [s for s in _sentences(_clean(para)) if CITED in s]
    return out


def retracted(n: dict) -> list[dict]:
    return [r for r in n["related"] if (r["type"] or "").lower() == RETRACTED]


def link_class(n: dict) -> str:
    """One of: linked, linked_without_identifier, other_related_type,
    identifier_in_text, no_identifier."""
    ra = retracted(n)
    if any(r["href"] for r in ra):
        return "linked"
    if ra:
        return "linked_without_identifier"
    if any(r["href"] and (r["type"] or "").lower() != FORWARD for r in n["related"]):
        return "other_related_type"
    return "identifier_in_text" if n["ids_in_text"] else "no_identifier"


def target(r: dict) -> tuple[str, str] | None:
    """The identifier a related-article element points at, as (kind, value)."""
    kind, href = (r.get("id_type") or "").lower(), (r.get("href") or "").strip()
    if kind == "pmc" and (v := ids.pmcid(href)):
        return "pmcid", v
    if kind == "doi" and (v := ids.doi(href)):
        return "doi", v
    if kind in ("pubmed", "pmid") and (v := ids.pmid(href)):
        return "pmid", v
    if v := ids.doi(href):
        return "doi", v
    if href.upper().startswith("PMC") and (v := ids.pmcid(href)):
        return "pmcid", v
    if v := ids.pmid(href):
        return "pmid", v
    return None
