# ruff: noqa: E501  (the JATS fixture keeps real one line elements)
"""Citing sentence extraction and the pilot draw. No network, no warehouse."""

from __future__ import annotations

from collections import Counter

from afterlife import citances
from afterlife.core import jats
from afterlife.core.budget import Budget
from afterlife.sources import openalex

XML = """<article><body>
<p>Early work reported a large effect (Smith et al. <xref ref-type="bibr" rid="R1">1</xref>). Later trials disagreed <xref ref-type="bibr" rid="R2">2</xref>.</p>
<p>That trial was retracted for fabricated data <xref ref-type="bibr" rid="R1 R2">1,2</xref>. See also Fig. 2 for the dose response.</p>
<p>Unrelated sentence <xref ref-type="fig" rid="R1">Fig 1</xref>.</p>
</body><back><ref-list>
<ref id="R1"><element-citation><article-title>A large effect</article-title><pub-id pub-id-type="doi">10.1000/ABC.1</pub-id></element-citation></ref>
<ref id="R2"><mixed-citation>Jones. Another paper. PMID: 999</mixed-citation></ref>
<ref id="R3"><mixed-citation>Lee. Hydroxychloroquine or chloroquine with or without a macrolide for treatment of COVID-19. Lancet 2020.</mixed-citation></ref>
</ref-list></back></article>"""


def test_citances_by_doi_keeps_et_al_and_skips_non_bibr_xrefs():
    c = jats.citances(XML, doi="https://doi.org/10.1000/abc.1", pmid=None, title=None)
    assert c["ref_ids"] == ["R1"] and c["matched_by"] == ["doi"]
    assert c["references"] == ["A large effect 10.1000/ABC.1"]
    assert c["xrefs"] == 2  # the fig xref with rid R1 is not a citation
    assert c["sentences"] == [
        "Early work reported a large effect (Smith et al. [CITED] ).",
        "That trial was retracted for fabricated data [CITED] .",
    ]


def test_citances_by_title_when_the_reference_has_no_identifier():
    title = (
        "Hydroxychloroquine or chloroquine with or without a macrolide for treatment of COVID-19"
    )
    c = jats.citances(XML, doi="10.9/none", pmid=None, title=title)
    # Matched in the reference list but never cited in text: no sentence.
    assert c["matched_by"] == ["title"] and c["xrefs"] == 0 and c["sentences"] == []


def test_short_titles_never_match_and_no_body_is_reported():
    assert jats.citances(XML, doi=None, pmid=None, title="A large effect")["ref_ids"] == []
    c = jats.citances(
        "<article><back><ref-list></ref-list></back></article>", doi="10.1/x", pmid=None, title=None
    )
    assert c["has_body"] is False and c["sentences"] == []


def test_draw_is_seeded_and_proportional_to_citations():
    rows = [{"paper_key": "a", "citations_after": 1}, {"paper_key": "b", "citations_after": 9}]
    picks = citances.draw(rows, 2000)
    assert picks == citances.draw(rows, 2000)
    share_b = Counter(p["paper_key"] for p in picks)["b"] / 2000
    assert 0.86 < share_b < 0.94


def test_sample_citing_filters_after_the_retraction_year_and_meters_spend():
    seen = {}

    class R:
        def json(self):
            return {"meta": {"cost_usd": 0.0001}, "results": [{"id": "https://openalex.org/W9"}]}

    def get(url, params):
        seen.update(params)
        return R()

    b = Budget(0.01)
    work = openalex.sample_citing("W1", 2020, 3, b, get=get)
    assert work["id"].endswith("W9")
    assert seen["filter"] == "cites:W1,publication_year:>2020" and seen["sample"] == 1
    assert b.calls == 1


RANGE_AND_TABLE = """<article><body>
<p>Background first <xref ref-type="bibr" rid="R3">3</xref>.</p>
<p>Several trials found benefit [<xref ref-type="bibr" rid="R1">1</xref>&#x2013;<xref ref-type="bibr" rid="R3">3</xref>]. Others did not [<xref ref-type="bibr" rid="R2">2</xref>-<xref ref-type="bibr" rid="R3">3</xref>].</p>
</body><back><ref-list>
<ref id="R1"><mixed-citation>doi:10.1000/one</mixed-citation></ref>
<ref id="R2"><mixed-citation>doi:10.1000/two</mixed-citation></ref>
<ref id="R3"><mixed-citation>doi:10.1000/three</mixed-citation></ref>
</ref-list></back><floats-group><table-wrap><table><tbody>
<tr><td>Wang et al. (<xref ref-type="bibr" rid="R2">2</xref>)</td><td>Rats</td><td>Improved</td></tr>
<tr><td>Other (<xref ref-type="bibr" rid="R3">3</xref>)</td><td>Mice</td><td>None</td></tr>
</tbody></table></table-wrap></floats-group></article>"""


def test_a_collapsed_range_that_brackets_the_target_cites_it():
    c = jats.citances(RANGE_AND_TABLE, doi="10.1000/two", pmid=None, title=None)
    # "1-3" brackets R2; "2-3" starts at R2, so its own xref is marked instead.
    assert c["ranges"] == 1 and c["xrefs"] == 2
    assert c["sentences"] == [
        "Several trials found benefit [ [CITED] ].",
        "Others did not [ [CITED] - 3 ].",
    ]


def test_table_rows_outside_the_body_are_returned_whole():
    c = jats.citances(RANGE_AND_TABLE, doi="10.1000/two", pmid=None, title=None)
    assert c["table_rows"] == ["Wang et al. ( [CITED] ) | Rats | Improved"]


def test_a_range_endpoint_is_cited_by_its_own_xref_not_by_the_range():
    c = jats.citances(RANGE_AND_TABLE, doi="10.1000/one", pmid=None, title=None)
    assert c["ranges"] == 0 and c["xrefs"] == 1
    assert c["sentences"] == ["Several trials found benefit [ [CITED] \u2013 3 ]."]
