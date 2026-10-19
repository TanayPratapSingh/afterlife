"""The AWS layer against moto. No network and no AWS account.

The handlers live under aws/lambdas/<name>/handler.py and all share the module
name `handler`, which is what the Lambda runtime imports. They are loaded here
by path under distinct names so the three can coexist in one test session.

moto does not evaluate IAM, so these tests prove the handlers' logic and their
use of the S3, DynamoDB, and Secrets Manager APIs, not that the deployed
policies allow those calls.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from afterlife.core import http
from afterlife.core.http import Response
from afterlife.sources import pubmed
from afterlife.sources import retraction_watch as rw

pytestmark = pytest.mark.aws

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "afterlife-lake-test"
TABLE = "afterlife-registry-test"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _handler(name: str):
    return _load(ROOT / "aws" / "lambdas" / name / "handler.py", f"afterlife_aws_{name}")


def _resp(obj, headers=None, url="u"):
    return Response(url, 200, headers or {}, json.dumps(obj).encode())


@pytest.fixture
def aws(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(key, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    with moto.mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield boto3


def _keys(boto3, prefix: str) -> list[str]:
    listed = boto3.client("s3").list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return sorted(o["Key"] for o in listed.get("Contents", []))


# fetch -----------------------------------------------------------------------

HEADER = ",".join(rw.EXPECTED_COLUMNS) + ","
ROW = "1,A paper,,,,,,,,,1/2/2020 0:00,,0,1/1/2019 0:00,10.1000/x,0,Retraction,,No,,"


class FakeGitLab:
    """The commits API (two snapshots) and the raw CSV for each commit."""

    def __init__(self):
        self.raw_fetches: list[str] = []

    def __call__(self, url, params=None, **kw):
        if url == rw.API:
            commits = [
                {
                    "id": "new",
                    "created_at": "2026-10-16T23:00:00Z",
                    "title": "Retraction Watch data for 2026-10-16",
                },
                {
                    "id": "old",
                    "created_at": "2026-10-15T23:00:00Z",
                    "title": "Retraction Watch data for 2026-10-15",
                },
            ]
            return _resp(commits if params["page"] == 1 else [], {"X-Next-Page": ""})
        self.raw_fetches.append(url)
        return Response(url, 200, {}, (HEADER + "\n" + ROW + "\n").encode())


def test_fetch_lands_newest_retraction_watch_snapshot_once(aws, monkeypatch):
    fetch = _handler("fetch")
    fake = FakeGitLab()
    monkeypatch.setattr(http, "get", fake)
    monkeypatch.setenv("LANDING_BUCKET", BUCKET)

    first = fetch.handler({"source": "retraction-watch"})
    assert first["newest"] == "2026-10-16" and first["available"] == 2
    assert [x["new"] for x in first["landed"]] == [True]
    assert first["landed"][0]["records"] == 1
    assert _keys(aws, "landing/retraction_watch/") == [
        "landing/retraction_watch/snapshot_date=2026-10-16/_manifest.json",
        "landing/retraction_watch/snapshot_date=2026-10-16/retraction_watch.csv.gz",
    ]
    assert fake.raw_fetches == [rw.RAW.format(sha="new")]

    # Idempotent: the snapshot date is already landed, so nothing is downloaded.
    second = fetch.handler({"source": "retraction-watch"})
    assert second["landed"] == [] and second["already_landed"] is True
    assert len(fake.raw_fetches) == 1


def test_fetch_pubmed_skips_a_second_run_on_the_same_day(aws, monkeypatch):
    fetch = _handler("fetch")
    calls = []

    def fake(url, params=None, **kw):
        calls.append(params)
        if "mindate" not in params:
            return _resp({"esearchresult": {"count": "2", "idlist": []}})
        ids_ = ["11", "12"] if params["mindate"].startswith("2020/") else []
        return _resp({"esearchresult": {"count": str(len(ids_)), "idlist": ids_}})

    monkeypatch.setattr(http, "get", fake)
    monkeypatch.setattr(pubmed.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("LANDING_BUCKET", BUCKET)

    first = fetch.handler({"source": "pubmed"})
    today = datetime.now(UTC).date().isoformat()
    assert first["landed"][0]["new"] and first["landed"][0]["records"] == 2
    assert first["landed"][0]["snapshot_date"] == today
    assert first["notes"]["complete"] is True
    made = len(calls)

    second = fetch.handler({"source": "pubmed"})
    assert second["already_landed"] is True and len(calls) == made


def test_fetch_openalex_reads_api_key_from_secrets_manager(aws, monkeypatch):
    fetch = _handler("fetch")
    arn = aws.client("secretsmanager").create_secret(
        Name="afterlife/openalex", SecretString=json.dumps({"api_key": "k-test"})
    )["ARN"]
    seen = []

    def fake(url, params=None, **kw):
        seen.append(params)
        first_page = params["cursor"] == "*"
        works = [{"id": f"https://openalex.org/W{i}", "ids": {}} for i in (1, 2)]
        return _resp(
            {
                "meta": {
                    "count": 2,
                    "cost_usd": 0.0001,
                    "next_cursor": "c2" if first_page else None,
                },
                "results": works if first_page else [],
            }
        )

    monkeypatch.setattr(http, "get", fake)
    monkeypatch.setenv("LANDING_BUCKET", BUCKET)
    monkeypatch.setenv("OPENALEX_SECRET_ARN", arn)
    monkeypatch.setenv("OPENALEX_BUDGET_USD", "0.01")
    monkeypatch.setenv("OPENALEX_API_KEY", "")  # registered so monkeypatch removes it after
    monkeypatch.delenv("OPENALEX_API_KEY")

    out = fetch.handler({"source": "openalex"})
    assert out["keyed"] is True and out["complete"] is True
    assert out["landed"][0]["records"] == 2
    assert out["notes"]["mode"] == "cursor" and out["notes"]["spent_usd"] == pytest.approx(0.0002)
    assert all(p.get("api_key") == "k-test" for p in seen)


def test_fetch_rejects_unknown_source(aws, monkeypatch):
    monkeypatch.setenv("LANDING_BUCKET", BUCKET)
    with pytest.raises(ValueError, match="unknown source"):
        _handler("fetch").handler({"source": "crossref"})


# load_registry ---------------------------------------------------------------

AS_OF = "2026-10-19"


def _doc(paper_key, ids_, verdict="retracted", flags=None, as_of=AS_OF, **extra):
    return {
        "paper_key": paper_key,
        "ids": {"doi": None, "pmid": None, "pmcid": None, "openalex": None, **ids_},
        "title": f"Title of {paper_key}",
        "verdict": verdict,
        "flags": flags or {"retraction_watch": None, "openalex": None, "pubmed": None, "pmc": None},
        "retraction_watch": None,
        "agreement": {"flagging": 0, "observed": 0},
        "as_of": as_of,
        **extra,
    }


RETRACTED = _doc(
    "doi:10.1000/xyz",
    {"doi": "10.1000/XYZ", "pmid": "12345", "pmcid": "PMC123", "openalex": "W123"},
    flags={"retraction_watch": "retracted", "openalex": True, "pubmed": True, "pmc": None},
    retraction_watch={
        "status": "retracted",
        "first_retraction_date": "2020-01-02",
        "record_ids": [123],
        "natures": ["Retraction"],
        "reasons": ["Plagiarism of Text"],
    },
    agreement={"flagging": 3, "observed": 3},
)
# Shares PMID 12345 with RETRACTED, as some Retraction Watch records do.
CONCERN = _doc(
    "doi:10.1000/other",
    {"doi": "10.1000/other", "pmid": "12345"},
    "concern",
    flags={"retraction_watch": "concern", "openalex": False, "pubmed": None, "pmc": None},
)
CORRECTED = _doc("openalex:W999", {"openalex": "W999"}, "corrected")
NO_IDS = _doc("title:untraceable", {}, "retracted")


def _put_export(boto3, docs, as_of=AS_OF, coverage=True) -> str:
    s3 = boto3.client("s3")
    prefix = f"export/registry/as_of={as_of}"
    if coverage:
        cov = {
            "as_of": as_of,
            "sources": {"retraction_watch": {"papers": 2, "snapshot_date": "2026-10-16"}},
        }
        s3.put_object(Bucket=BUCKET, Key=f"{prefix}/_coverage.json", Body=json.dumps(cov))
    body = "".join(json.dumps(d) + "\n" for d in docs).encode()
    key = f"{prefix}/registry.jsonl.gz"
    s3.put_object(Bucket=BUCKET, Key=key, Body=gzip.compress(body))
    return key


def _s3_event(key: str) -> dict:
    # S3 URL encodes keys in notifications, so as_of= arrives as as_of%3D.
    encoded = key.replace("=", "%3D")
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": encoded}}}]}


def _table(boto3):
    ddb = boto3.resource("dynamodb")
    ddb.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return ddb.Table(TABLE)


@pytest.fixture
def loaded(aws, monkeypatch):
    monkeypatch.setenv("TABLE_NAME", TABLE)
    table = _table(aws)
    loader = _handler("load_registry")
    key = _put_export(aws, [RETRACTED, CONCERN, CORRECTED, NO_IDS])
    result = loader.handler(_s3_event(key))
    return aws, table, loader, result


def test_loader_writes_one_item_per_alias(loaded):
    _, table, _, result = loaded
    (export,) = result["exports"]
    assert export["documents"] == 4 and export["documents_without_ids"] == 1
    assert export["shared_aliases"] == 1
    assert result["items_written"] == 6
    pks = sorted(i["pk"] for i in table.scan()["Items"])
    assert pks == [
        "doi#10.1000/other",
        "doi#10.1000/xyz",
        "meta#coverage",
        "openalex#W123",
        "openalex#W999",
        "pmcid#PMC123",
        "pmid#12345",
    ]
    item = table.get_item(Key={"pk": "doi#10.1000/xyz"})["Item"]
    assert item["paper_key"] == "doi:10.1000/xyz" and item["as_of"] == AS_OF
    assert json.loads(item["doc"])["verdict"] == "retracted"
    assert int(item["expires_at"]) > int(datetime(2026, 10, 19, tzinfo=UTC).timestamp())


def test_loader_keeps_the_more_severe_verdict_on_a_shared_alias(loaded):
    _, table, _, _ = loaded
    item = table.get_item(Key={"pk": "pmid#12345"})["Item"]
    assert item["paper_key"] == "doi:10.1000/xyz"
    assert item["also_paper_keys"] == ["doi:10.1000/other"]


def test_loader_writes_coverage_item(loaded):
    _, table, _, _ = loaded
    meta = table.get_item(Key={"pk": "meta#coverage"})["Item"]
    doc = json.loads(meta["doc"])
    assert meta["as_of"] == AS_OF and doc["coverage_file_present"] is True
    assert doc["coverage"]["sources"]["retraction_watch"]["papers"] == 2
    assert doc["load"]["items_written"] == 6


def test_loader_rejects_a_bad_export_before_writing(aws, monkeypatch):
    monkeypatch.setenv("TABLE_NAME", TABLE)
    table = _table(aws)
    loader = _handler("load_registry")
    key = _put_export(aws, [RETRACTED, _doc("doi:10.1000/bad", {"doi": "10.1000/bad"}, "ok")])
    with pytest.raises(loader.ContractError, match="verdict 'ok'"):
        loader.handler(_s3_event(key))
    assert table.scan()["Items"] == []


def test_loader_skips_an_export_older_than_the_loaded_one(loaded):
    aws, _, loader, _ = loaded
    key = _put_export(aws, [RETRACTED], as_of="2026-10-18")
    (export,) = loader.handler(_s3_event(key))["exports"]
    assert export["skipped"].startswith("older than loaded")


def test_loader_without_coverage_file_still_dates_the_load(aws, monkeypatch):
    monkeypatch.setenv("TABLE_NAME", TABLE)
    table = _table(aws)
    key = _put_export(aws, [CORRECTED], coverage=False)
    _handler("load_registry").handler(_s3_event(key))
    doc = json.loads(table.get_item(Key={"pk": "meta#coverage"})["Item"]["doc"])
    assert doc["as_of"] == AS_OF and doc["coverage_file_present"] is False


# api -------------------------------------------------------------------------


def _get(api, route, id_=None):
    event = {"version": "2.0", "routeKey": route, "requestContext": {"http": {"method": "GET"}}}
    if id_ is not None:
        event["pathParameters"] = {"id": id_}
    resp = api.handler(event)
    return resp["statusCode"], resp["headers"], json.loads(resp["body"])


@pytest.fixture
def api(loaded):
    module = _handler("api")
    module.reset()
    return module


LOOKUP = "GET /v1/lookup/{id+}"


def test_api_found_explains_who_says_what(api):
    status, headers, body = _get(api, LOOKUP, "10.1000%2FXYZ")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert headers["Access-Control-Allow-Methods"] == "GET"
    assert body["found"] is True and body["current"] is True
    assert (body["kind"], body["normalized"]) == ("doi", "10.1000/xyz")
    assert body["verdict"] == "retracted" and body["as_of"] == AS_OF
    assert body["doc"]["paper_key"] == "doi:10.1000/xyz"
    assert body["explanation"].startswith(
        "Retraction Watch records a retraction dated 2020-01-02. OpenAlex and PubMed agree. "
        "PMC has no record of it."
    )


def test_api_found_by_every_alias(api):
    # "https:/doi.org/..." is how API Gateway delivers a DOI URL in the path
    # after collapsing "//"; the first live lookup returned 400 for it.
    for raw in (
        "12345",
        "PMC123",
        "https://openalex.org/W123",
        "https://doi.org/10.1000/xyz",
        "https:/doi.org/10.1000/xyz",
    ):
        status, _, body = _get(api, LOOKUP, raw)
        assert status == 200 and body["doc"]["paper_key"] == "doi:10.1000/xyz", raw


def test_api_shared_alias_is_disclosed(api):
    _, _, body = _get(api, LOOKUP, "12345")
    assert body["also_paper_keys"] == ["doi:10.1000/other"]
    assert "also attached to 1 other paper record" in body["explanation"]


def test_api_not_flagged_is_never_ok(api):
    status, _, body = _get(api, LOOKUP, "10.1000/never-seen")
    assert status == 200
    assert body["found"] is False and body["verdict"] == "not_flagged"
    assert body["caveat"] == (
        f"No registry in this system flags this identifier as of {AS_OF}. Absence of a flag "
        "is not evidence of good standing; coverage differs by source, see /v1/coverage."
    )
    assert "ok" not in body.values()


def test_api_rejects_unclassifiable_ids(api):
    status, _, body = _get(api, LOOKUP, "hello")
    assert status == 400 and body["error"] == "unrecognized_identifier"
    assert "DOI" in body["message"] and "PMID" in body["message"]
    assert _get(api, LOOKUP, "")[0] == 400
    assert _get(api, LOOKUP, "1" * 600)[0] == 400


def test_api_coverage_health_and_unknown_route(api):
    status, _, body = _get(api, "GET /v1/coverage")
    assert status == 200 and body["as_of"] == AS_OF and body["coverage"]["sources"]
    assert _get(api, "GET /health")[0] == 200
    assert _get(api, "POST /v1/lookup/{id+}")[0] == 404


def test_api_refuses_to_answer_before_anything_is_loaded(aws, monkeypatch):
    monkeypatch.setenv("TABLE_NAME", TABLE)
    _table(aws)
    module = _handler("api")
    module.reset()
    status, _, body = _get(module, LOOKUP, "10.1000/xyz")
    assert status == 503 and body["error"] == "registry_not_loaded"


def test_api_marks_records_missing_from_the_latest_export(api, loaded):
    aws, _, loader, _ = loaded
    newer = "2026-10-20"
    docs = [{**d, "as_of": newer} for d in (RETRACTED, CONCERN)]
    loader.handler(_s3_event(_put_export(aws, docs, as_of=newer)))
    api.reset()
    _, _, body = _get(api, LOOKUP, "W999")
    assert body["found"] is True and body["current"] is False
    assert f"latest export ({newer}) no longer contains" in body["explanation"]
    assert _get(api, LOOKUP, "10.1000/xyz")[2]["current"] is True


# build -----------------------------------------------------------------------


def _build_module():
    return _load(ROOT / "aws" / "build.py", "afterlife_aws_build")


def test_build_zips_exclude_spark_and_are_reproducible(tmp_path):
    build = _build_module()
    first = build.build_all(tmp_path / "a")
    second = build.build_all(tmp_path / "b")
    for name in build.FUNCTIONS:
        a, b = first[name].read_bytes(), second[name].read_bytes()
        assert a == b, f"{name}.zip differs between two builds"
        with zipfile.ZipFile(first[name]) as zf:
            names = zf.namelist()
            assert names == sorted(names)
            assert not [n for n in names if n.startswith("afterlife/spark")]
            assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
            assert "afterlife/core/landing.py" in names and "afterlife/core/ids.py" in names
            handler = (ROOT / "aws" / "lambdas" / name / "handler.py").read_bytes()
            assert zf.read("handler.py") == handler
            assert {i.date_time for i in zf.infolist()} == {build.FIXED_TIME}
