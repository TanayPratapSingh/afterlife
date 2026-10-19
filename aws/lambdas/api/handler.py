"""The public lookup API: is this paper retracted, and according to whom?

API Gateway HTTP API, payload format 2.0, routes:

    GET /v1/lookup/{id+}   a DOI, PMID, PMCID, or OpenAlex work id
    GET /v1/coverage       what each registry contributed to the loaded export
    GET /health            liveness, does not touch DynamoDB

The lookup route is greedy (`{id+}`) because a DOI contains a slash. Callers
may send `10.1000/xyz` as is or percent encoded as `10.1000%2Fxyz`.

The answer for an identifier no registry flags is never "ok". It is
`not_flagged`, dated by the export it was checked against, with a caveat that
absence of a flag is not evidence of good standing. Coverage differs by
source: PubMed only flags what NLM indexes, OpenAlex only what it has matched,
and Retraction Watch only what its editors have recorded. A system that turned
"no flag found" into "fine to cite" would make exactly the silent error this
project exists to measure.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
from typing import Any

import boto3

from afterlife.core import ids

log = logging.getLogger()
log.setLevel(logging.INFO)

COVERAGE_PK = "meta#coverage"
COVERAGE_TTL_SECONDS = 60.0
MAX_QUERY_CHARS = 512
LOOKUP_ROUTES = {"GET /v1/lookup/{id+}", "GET /v1/lookup/{id}"}

SOURCE_NAMES = {"openalex": "OpenAlex", "pubmed": "PubMed", "pmc": "PMC"}
RW_PHRASES = {
    "retracted": "a retraction",
    "concern": "an expression of concern",
    "corrected": "a correction",
    "reinstated": "a reinstatement",
}

_table = None
_coverage: tuple[float, dict | None] = (0.0, None)


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def reset() -> None:
    """Drop cached clients and coverage. For tests."""
    global _table, _coverage
    _table = None
    _coverage = (0.0, None)


def coverage() -> dict | None:
    """The meta#coverage item, cached briefly. It changes once a day."""
    global _coverage
    fetched_at, item = _coverage
    if item is None or time.monotonic() - fetched_at > COVERAGE_TTL_SECONDS:
        item = table().get_item(Key={"pk": COVERAGE_PK}).get("Item")
        _coverage = (time.monotonic(), item)
    return item


def respond(status: int, body: dict[str, Any], *, cache_seconds: int = 0) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        # API Gateway's CORS configuration is authoritative once deployed;
        # these keep direct invocations and tests honest about the contract.
        "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
        "Access-Control-Allow-Methods": "GET",
        "Cache-Control": f"public, max-age={cache_seconds}" if cache_seconds else "no-store",
    }
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body, sort_keys=True, default=str),
    }


def _join(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def explain(doc: dict[str, Any]) -> str:
    """One plain English paragraph saying which registry says what.

    True means the source flags the paper as retracted, False means the source
    has the paper and does not flag it, None means the source has no record of
    it at all. The last two are different facts and are worded differently.
    """
    flags = doc.get("flags") or {}
    rw = flags.get("retraction_watch")
    rw_detail = doc.get("retraction_watch") or {}
    sentences = []
    if rw:
        phrase = RW_PHRASES.get(rw, f"the status '{rw}'")
        dated = rw_detail.get("first_retraction_date") if rw == "retracted" else None
        sentences.append(
            f"Retraction Watch records {phrase}" + (f" dated {dated}." if dated else ".")
        )
    else:
        sentences.append("Retraction Watch has no record of this paper.")

    flagging = [SOURCE_NAMES[s] for s in SOURCE_NAMES if flags.get(s) is True]
    unflagged = [SOURCE_NAMES[s] for s in SOURCE_NAMES if flags.get(s) is False]
    unknown = [SOURCE_NAMES[s] for s in SOURCE_NAMES if flags.get(s) is None]
    if flagging:
        verb = "agree" if len(flagging) > 1 else "agrees"
        if rw == "retracted":
            sentences.append(f"{_join(flagging)} {verb}.")
        else:
            plural = "flag" if len(flagging) > 1 else "flags"
            sentences.append(f"{_join(flagging)} {plural} it as retracted.")
    if unflagged:
        verb = "have" if len(unflagged) > 1 else "has"
        sentences.append(f"{_join(unflagged)} {verb} the paper but no retraction flag.")
    if unknown:
        verb = "have" if len(unknown) > 1 else "has"
        sentences.append(f"{_join(unknown)} {verb} no record of it.")
    agreement = doc.get("agreement") or {}
    if agreement.get("observed"):
        sentences.append(
            f"{agreement.get('flagging', 0)} of {agreement['observed']} registries that know "
            f"this paper flag it; the combined verdict is {doc.get('verdict')}."
        )
    return " ".join(sentences)


def not_flagged_caveat(as_of: str) -> str:
    return (
        f"No registry in this system flags this identifier as of {as_of}. Absence of a flag "
        "is not evidence of good standing; coverage differs by source, see /v1/coverage."
    )


def lookup(raw_id: str) -> dict[str, Any]:
    query = urllib.parse.unquote(raw_id or "").strip()
    if not query or len(query) > MAX_QUERY_CHARS:
        return respond(
            400,
            {
                "error": "bad_request",
                "message": "Give one identifier, at most "
                f"{MAX_QUERY_CHARS} characters, e.g. /v1/lookup/10.1000/xyz.",
            },
        )
    classified = ids.classify(query)
    if classified is None:
        return respond(
            400,
            {
                "error": "unrecognized_identifier",
                "query": query[:200],
                "message": "Not a DOI (10.<registrant>/<suffix>), PMID (digits), PMCID "
                "(PMC followed by digits), or OpenAlex work id (W followed by digits).",
            },
        )
    kind, normalized = classified
    meta = coverage()
    if meta is None:
        # With nothing loaded, "not flagged" would be a claim about an empty
        # table. Refuse instead.
        return respond(503, {"error": "registry_not_loaded", "query": query})
    loaded_as_of = meta["as_of"]
    item = table().get_item(Key={"pk": f"{kind}#{normalized}"}).get("Item")
    base = {"query": query, "kind": kind, "normalized": normalized}
    if item is None:
        return respond(
            200,
            {
                **base,
                "found": False,
                "verdict": "not_flagged",
                "as_of": loaded_as_of,
                "caveat": not_flagged_caveat(loaded_as_of),
            },
            cache_seconds=300,
        )
    doc = json.loads(item["doc"])
    explanation = explain(doc)
    body = {
        **base,
        "found": True,
        "as_of": item["as_of"],
        "verdict": doc.get("verdict"),
        "doc": doc,
        "explanation": explanation,
    }
    others = list(item.get("also_paper_keys") or [])
    if others:
        body["also_paper_keys"] = others
        body["explanation"] += (
            f" This {kind} is also attached to {len(others)} other paper record"
            f"{'s' if len(others) > 1 else ''} ({', '.join(others)}); the most severe "
            "verdict among them is shown."
        )
    if item["as_of"] < loaded_as_of:
        body["current"] = False
        body["explanation"] += (
            f" This record is from the export of {item['as_of']}; the latest export "
            f"({loaded_as_of}) no longer contains this {kind}, so treat it as historical."
        )
    else:
        body["current"] = True
    return respond(200, body, cache_seconds=300)


def coverage_route() -> dict[str, Any]:
    meta = coverage()
    if meta is None:
        return respond(503, {"error": "registry_not_loaded"})
    return respond(200, json.loads(meta["doc"]), cache_seconds=300)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    route = event.get("routeKey", "")
    try:
        if route in LOOKUP_ROUTES:
            return lookup((event.get("pathParameters") or {}).get("id", ""))
        if route == "GET /v1/coverage":
            return coverage_route()
        if route == "GET /health":
            return respond(200, {"status": "ok", "service": "afterlife-api"})
        return respond(404, {"error": "not_found", "route": route})
    except Exception:
        log.exception("unhandled error on %s", route)
        return respond(500, {"error": "internal_error"})
