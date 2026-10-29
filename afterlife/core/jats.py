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
