"""The pure parts of the hand audit. No warehouse needed."""

from __future__ import annotations

import pytest

from afterlife import audit


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("RETRACTED: A New Approach", "A New Approach"),
        ("Retracted Article: Soil carbon", "Soil carbon"),
        ("[Retracted] Gut flora", "Gut flora"),
        ("WITHDRAWN: Dedication", "Dedication"),
        ("Retracted: 1. Grundlagen", "1. Grundlagen"),
        # A title that merely starts with the word keeps it.
        ("Withdrawn behaviour in adolescents", "Withdrawn behaviour in adolescents"),
        ("Retraction note to: Liver injury", "Retraction note to: Liver injury"),
        (None, ""),
    ],
)
def test_clean_title(raw, clean):
    assert audit.clean_title(raw) == clean


def test_allocate_caps_small_strata_and_spreads_the_rest():
    sizes = {"retraction_watch": 20259, "openalex": 16120, "pubmed": 22}
    alloc = audit.allocate(sizes, 200)
    assert alloc == {"retraction_watch": 89, "openalex": 89, "pubmed": 22}
    assert audit.allocate({"a": 3, "b": 0}, 10) == {"a": 3, "b": 0}
    assert sum(audit.allocate({"a": 5, "b": 5, "c": 5}, 7).values()) == 7


def test_wilson():
    assert audit.wilson(0, 0) is None
    lo, hi = audit.wilson(45, 50)
    assert lo < 0.9 < hi and 0 <= lo and hi <= 1
    assert audit.wilson(10, 10)[1] == 1.0


def _report():
    return {
        "stratum_sizes": {"a": 1000, "b": 2},
        "allocation": {"a": 3, "b": 2},
        "key": [
            {"sample_id": f"A00{i}", "paper_key": f"k{i}", "stratum": s}
            for i, s in enumerate("aaabb", 1)
        ],
    }


def _labels(*values):
    return [{"sample_id": f"A00{i}", "label": v} for i, v in enumerate(values, 1)]


def test_score_partial_then_complete():
    part = audit.score(_labels("retracted", "", "", "", ""), _report())
    assert not part["complete"] and part["frame_estimate"] is None
    assert part["strata"]["a"]["labelled"] == 1

    full = audit.score(
        _labels("retracted", "notice", "cannot_determine", "Retracted ", "not_retracted"),
        _report(),
    )
    a, b = full["strata"]["a"], full["strata"]["b"]
    assert full["complete"]
    assert (a["decided"], a["precision"], a["notice_share"]) == (2, 0.5, 0.5)
    assert b["precision"] == 0.5
    # b is a census, so it adds no variance; the estimate weights by population.
    est = full["frame_estimate"]
    assert est["precision"] == 0.5 and est["frame_size"] == 1002


def test_pubmed_summaries_parses_esummary():
    from afterlife.sources import pubmed

    class R:
        def json(self):
            return {"result": {"uids": ["1"], "1": {"title": "T.", "pubdate": "2019 Jan 4"}}}

    assert pubmed.summaries(["1"], get=lambda url, params: R()) == {
        "1": {"title": "T.", "year": "2019"}
    }
    assert pubmed.summaries([]) == {}


def test_score_rejects_labels_outside_the_codebook():
    with pytest.raises(SystemExit, match="A001"):
        audit.score(_labels("maybe", "", "", "", ""), _report())
    with pytest.raises(SystemExit, match="Z9"):
        audit.score([{"sample_id": "Z9", "label": ""}], _report())
