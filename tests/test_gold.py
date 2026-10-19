"""The pure parts of gold, export, and conformance. No warehouse needed."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from afterlife import conformance, export, gold


def _row(**kw):
    base = dict(
        paper_key="doi:10.1000/x",
        doi="10.1000/x",
        pmid="123",
        pmcid="PMC9",
        openalex_id="W1",
        title="T",
        rw_status="retracted",
        first_retraction_date=date(2020, 1, 2),
        flag_openalex=True,
        flag_pubmed=False,
        flag_pmc=None,
        n_flagging=2,
        verdict="retracted",
        record_ids=[7, 3],
        rw_natures=["Retraction"],
        reasons=["Plagiarism", None],
    )
    base.update(kw)
    return base


def test_document_matches_the_serving_contract():
    d = export.document(_row(), "2026-10-19")
    assert set(d) == {
        "paper_key",
        "ids",
        "title",
        "verdict",
        "flags",
        "retraction_watch",
        "agreement",
        "as_of",
    }
    assert d["ids"] == {"doi": "10.1000/x", "pmid": "123", "pmcid": "PMC9", "openalex": "W1"}
    assert d["flags"] == {
        "retraction_watch": "retracted",
        "openalex": True,
        "pubmed": False,
        "pmc": None,
    }
    assert d["retraction_watch"]["first_retraction_date"] == "2020-01-02"
    assert d["retraction_watch"]["reasons"] == ["Plagiarism"]
    # RW and OpenAlex always eligible, PubMed because there is a PMID, PMC not linked.
    assert d["agreement"] == {"flagging": 2, "observed": 3}
    json.dumps(d)


def test_document_without_retraction_watch_record():
    d = export.document(
        _row(rw_status=None, record_ids=None, rw_natures=None, reasons=None, pmid=None),
        "2026-10-19",
    )
    assert d["retraction_watch"] is None and d["agreement"]["observed"] == 2


def test_every_verdict_maps_to_an_evidenceops_status():
    # The serving contract (aws/lambdas/load_registry) accepts exactly these.
    verdicts = {"retracted", "disputed", "concern", "corrected", "reinstated"}
    assert set(export.EVIDENCEOPS_STATUS) == verdicts
    assert set(export.EVIDENCEOPS_STATUS.values()) <= {"ok", "corrected", "concerned", "retracted"}
    assert export.EVIDENCEOPS_STATUS["disputed"] == "concerned", "disagreement is not citable"


def test_compare_identical_after_normalization():
    a = conformance.compare(
        ["k", "v", "d"],
        [(1, Decimal("0.1000001"), date(2020, 1, 1))],
        ["d", "k", "v"],
        [(date(2020, 1, 1), 1, 0.1000001)],
    )
    assert a["identical"] and a["rows_only_in_duckdb"] == 0


def test_compare_reports_differences_and_column_drift():
    a = conformance.compare(
        ["k", "v"], [(1, "a"), (2, "b")], ["k", "v", "extra"], [(1, "a", 0), (2, "c", 0)]
    )
    assert not a["identical"]
    assert a["rows_only_in_duckdb"] == 1 and a["rows_only_in_spark"] == 1
    assert a["columns_only_in_spark"] == ["extra"]


def test_compare_counts_duplicates():
    a = conformance.compare(["k"], [(1,), (1,)], ["k"], [(1,)])
    assert not a["identical"] and a["rows_only_in_duckdb"] == 1


def test_summarize_dbt_run_results(tmp_path):
    p = tmp_path / "run_results.json"
    p.write_text(
        json.dumps(
            {
                "elapsed_time": 3.21,
                "results": [
                    {
                        "unique_id": "model.afterlife.registry",
                        "status": "success",
                        "execution_time": 1.5,
                    },
                    {
                        "unique_id": "test.afterlife.unique_registry_paper_key",
                        "status": "pass",
                        "execution_time": 0.1,
                    },
                    {
                        "unique_id": "test.afterlife.not_null_x",
                        "status": "fail",
                        "execution_time": 0.1,
                        "message": "Got 3 results",
                    },
                ],
            }
        )
    )
    s = gold.summarize(p)
    assert s["models"] == 1 and s["tests"] == 2
    assert s["status_counts"] == {"success": 1, "pass": 1, "fail": 1}
    assert s["failures"][0]["node"].endswith("not_null_x")


def test_export_verdicts_match_the_loader_contract():
    """The loader Lambda refuses a whole export if any verdict is unknown. On
    the first real load it refused 126 documents with verdict 'ok', a value
    gold produced and the contract did not list. Read the loader's accepted
    set from its source, without importing boto3, and require agreement."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "aws/lambdas/load_registry/handler.py"
    tree = ast.parse(src.read_text())
    accepted = next(
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "VERDICTS" for t in node.targets)
    )
    assert set(export.EVIDENCEOPS_STATUS) == set(accepted)
