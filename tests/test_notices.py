"""The notice link measurement on synthetic JATS and PubMed XML. No network."""

from __future__ import annotations

import gzip
import json

from afterlife import notices
from afterlife.core import jats
from afterlife.core.landing import LocalStore, land
from afterlife.sources import pmc, pubmed


def _xml(related: str = "", body: str = "", own_doi: str = "10.1000/notice") -> str:
    return (
        '<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="retraction">'
        '<front><journal-meta><journal-id journal-id-type="nlm-ta">J Test</journal-id>'
        "<publisher><publisher-name>Pub &amp; Co</publisher-name></publisher></journal-meta>"
        f'<article-meta><article-id pub-id-type="doi">{own_doi}</article-id>'
        '<article-id pub-id-type="pmcid">PMC9</article-id>'
        '<pub-date pub-type="epub"><year>2024</year></pub-date>'
        f"{related}</article-meta></front><body><p>{body}</p></body></article>"
    )


PMC_LINK = (
    '<related-article related-article-type="retracted-article" ext-link-type="pmc" '
    'xlink:href="PMC100">'
)
FORWARD = '<related-article related-article-type="retraction-forward" xlink:href="PMC9"/>'


def test_notice_reads_link_metadata_and_skips_its_own_ids():
    n = jats.notice(_xml(PMC_LINK + FORWARD, body="See 10.1000/notice and doi 10.2000/other."))
    assert n["article_type"] == "retraction"
    assert n["year"] == 2024 and n["publisher"] == "Pub & Co" and n["journal"] == "J Test"
    assert [jats.target(r) for r in jats.retracted(n)] == [("pmcid", "PMC100")]
    assert n["ids_in_text"] == [("doi", "10.2000/other")]
    assert jats.link_class(n) == "linked"


def test_link_classes():
    no_href = '<related-article related-article-type="retracted-article">'
    assert jats.link_class(jats.notice(_xml(no_href))) == "linked_without_identifier"
    other = '<related-article related-article-type="corrected-article" xlink:href="PMC5">'
    assert jats.link_class(jats.notice(_xml(other))) == "other_related_type"
    # PMC's own forward link does not count, and neither does the notice's own DOI.
    in_text = jats.notice(_xml(FORWARD, body="Retracted: PMID: 123."))
    assert jats.link_class(in_text) == "identifier_in_text"
    bare = jats.notice(_xml(FORWARD, body="The article by Smith is retracted. 10.1000/notice"))
    assert jats.link_class(bare) == "no_identifier"


def test_target_normalizes_each_identifier_kind():
    assert jats.target({"id_type": "pubmed", "href": "31072911"}) == ("pmid", "31072911")
    assert jats.target({"id_type": "doi", "href": "https://doi.org/10.5000/X"}) == (
        "doi",
        "10.5000/x",
    )
    assert jats.target({"id_type": None, "href": "PMC77"}) == ("pmcid", "PMC77")
    assert jats.target({"id_type": None, "href": "4567"}) == ("pmid", "4567")
    assert jats.target({"id_type": "uri", "href": "not an id"}) is None


EFETCH = """<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID Version="1">900</PMID>
<CommentsCorrectionsList>
<CommentsCorrections RefType="RetractionOf"><RefSource>J 1</RefSource><PMID Version="1">111</PMID>
</CommentsCorrections>
<CommentsCorrections RefType="RetractionOf"><RefSource>J 2, no PMID</RefSource>
</CommentsCorrections>
<CommentsCorrections RefType="CommentOn"><PMID Version="1">222</PMID></CommentsCorrections>
</CommentsCorrectionsList></MedlineCitation></PubmedArticle>
<PubmedArticle><MedlineCitation><PMID Version="1">901</PMID></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


def test_retraction_of_reads_only_retraction_links():
    assert pubmed.retraction_of(EFETCH) == {
        "900": {"pmids": ["111"], "refs_without_pmid": 1},
        "901": {"pmids": [], "refs_without_pmid": 0},
    }


HEADER = "Journal Title,ISSN,eISSN,Year,Volume,Issue,Page,DOI,PMCID,PMID,Manuscript Id,Release Date"


def _land_ids(store, rows):
    raw = gzip.compress(("\n".join([HEADER, *rows]) + "\n").encode(), mtime=0)
    land(
        store,
        source="pmc_ids",
        snapshot_date="2026-10-19",
        name="PMC-ids.csv.gz",
        raw=raw,
        origin_url="u",
        origin_version="v",
        compress=False,
    )


def test_lookup_finds_each_kind_of_identifier(tmp_path):
    store = LocalStore(tmp_path)
    _land_ids(store, ["J,,,2020,1,1,1,10.9000/A,PMC100,111,,live", "J,,,2021,1,1,1,,PMC200,,,live"])
    hits = pmc.lookup(store, pmids=["111"], dois=["10.9000/a"], pmcids=["PMC200", "PMC404"])
    assert hits[("pmid", "111")]["pmcid"] == "PMC100"
    assert hits[("doi", "10.9000/a")]["pmcid"] == "PMC100"
    assert hits[("pmcid", "PMC200")]["pmid"] is None
    assert ("pmcid", "PMC404") not in hits


def _land_jsonl(store, source, name, rows, manifest="_manifest.json"):
    land(
        store,
        source=source,
        snapshot_date="2026-10-29",
        name=name,
        raw="".join(json.dumps(r) + "\n" for r in rows).encode(),
        origin_url="u",
        origin_version="v",
        manifest_name=manifest,
    )


def test_run_counts_links_agreement_and_flags(tmp_path):
    store = LocalStore(tmp_path)
    _land_jsonl(
        store,
        "pubmed_notices",
        "efetch.jsonl",
        [{"pmids": ["900"], "xml": EFETCH}],
        "_manifest_efetch.json",
    )
    docs = [
        # Linked in XML to PMC100 (PMID 111), which PubMed agrees with.
        {
            "pmcid": "PMC1",
            "pmid": 900,
            "in_open_data": True,
            "found_via": ["pubmed"],
            "xml": _xml(PMC_LINK),
        },
        # No structured link at all, and not in the PubMed set.
        {
            "pmcid": "PMC2",
            "pmid": 902,
            "in_open_data": True,
            "found_via": ["retraction_watch"],
            "xml": _xml(FORWARD, body="nothing here"),
        },
        # Not a notice: the paper itself, reached through a shared DOI.
        {
            "pmcid": "PMC3",
            "pmid": 903,
            "in_open_data": True,
            "found_via": ["retraction_watch"],
            "xml": _xml().replace('article-type="retraction"', 'article-type="research-article"'),
        },
        {"pmcid": "PMC4", "in_open_data": False, "found_via": ["pubmed"]},
    ]
    _land_jsonl(store, "pmc_notices", "notices.jsonl", docs)
    targets = [
        {
            "pmcid": "PMC100",
            "pmid": 111,
            "in_open_data": True,
            "is_retracted": True,
            "requested_as": ["pmcid:PMC100", "pmid:111"],
        }
    ]
    _land_jsonl(store, "pmc_notice_targets", "targets.jsonl", targets)

    r = notices.run(store)
    assert r["frame"]["notices"] == 2 and r["frame"]["article_types"]["research-article"] == 1
    assert r["link_classes"]["linked"]["k"] == 1
    assert r["link_classes"]["no_identifier"]["k"] == 1
    assert r["pubmed_agreement"] == {"agree": 1, "notice_not_in_pubmed_set": 1}
    assert r["target_flagged_retracted"] == {"both": {"n": 1, "k": 1, "share": 1.0}}
    assert r["examples"]["no_identifier"] == ["PMC2"]
