from __future__ import annotations

import gzip
import itertools
from datetime import date

import pytest

from afterlife.core import ids
from afterlife.core.budget import Budget, BudgetExhausted
from afterlife.core.landing import ImmutableWriteError, LocalStore, land, manifests, read_raw
from afterlife.core.status import Nature, Status, closed_form, fold, order_key, parse_nature
from afterlife.core.text import decode

# ids ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10.1000/ABC.123", "10.1000/abc.123"),
        ("https://doi.org/10.1000/abc", "10.1000/abc"),
        ("http://dx.doi.org/10.1000/abc", "10.1000/abc"),
        ("doi:10.1000/abc", "10.1000/abc"),
        ("https:/doi.org/10.1000/abc", "10.1000/abc"),
        ("  10.1000/abc  ", "10.1000/abc"),
        # A real DOI shape that ends in a parenthesis must survive intact.
        ("10.1061/(ASCE)0733-9399(2010)136:2(174)", "10.1061/(asce)0733-9399(2010)136:2(174)"),
        ("10.1000.10/abc", "10.1000.10/abc"),
        ("Unavailable", None),
        ("unavailable", None),
        ("", None),
        (None, None),
        ("not a doi", None),
        ("11.1000/abc", None),
        ("10.1000/", None),
    ],
)
def test_doi(raw, expected):
    assert ids.doi(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("12345", "12345"),
        (12345, "12345"),
        ("0", None),
        ("", None),
        (None, None),
        ("12345.0", "12345"),
        ("https://pubmed.ncbi.nlm.nih.gov/12345/", "12345"),
        ("0123", None),
        ("12a45", None),
    ],
)
def test_pmid(raw, expected):
    assert ids.pmid(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("PMC123", "PMC123"),
        ("pmc123", "PMC123"),
        ("123", "PMC123"),
        ("PMC123.2", "PMC123"),
        ("https://pmc.ncbi.nlm.nih.gov/articles/PMC123/", "PMC123"),
        ("PMC", None),
        (None, None),
    ],
)
def test_pmcid(raw, expected):
    assert ids.pmcid(raw) == expected


def test_classify_prefers_pmid_for_bare_numbers():
    assert ids.classify("12345") == ("pmid", "12345")
    assert ids.classify("PMC12345") == ("pmcid", "PMC12345")
    assert ids.classify("https://doi.org/10.1/x") is None  # registrant too short
    assert ids.classify("10.1000/X") == ("doi", "10.1000/x")
    assert ids.classify("https://openalex.org/W123") == ("openalex", "W123")
    assert ids.classify("hello") is None


# status ------------------------------------------------------------------

R, E, C, RI = Nature.RETRACTION, Nature.CONCERN, Nature.CORRECTION, Nature.REINSTATEMENT


@pytest.mark.parametrize(
    "events, expected",
    [
        ([], Status.OK),
        ([(C, date(2020, 1, 1))], Status.CORRECTED),
        ([(R, date(2020, 1, 1))], Status.RETRACTED),
        ([(E, date(2020, 1, 1)), (R, date(2021, 1, 1))], Status.RETRACTED),
        ([(R, date(2020, 1, 1)), (E, date(2021, 1, 1))], Status.RETRACTED),
        ([(R, date(2020, 1, 1)), (RI, date(2021, 1, 1))], Status.REINSTATED),
        ([(R, date(2020, 1, 1)), (RI, date(2021, 1, 1)), (E, date(2022, 1, 1))], Status.CONCERN),
        ([(R, date(2020, 1, 1)), (C, date(2021, 1, 1))], Status.RETRACTED),
        # Same day: the more severe notice wins.
        ([(R, date(2020, 1, 1)), (C, date(2020, 1, 1))], Status.RETRACTED),
        # A reinstatement with nothing to reinstate changes nothing.
        ([(RI, date(2020, 1, 1))], Status.OK),
        # An undated retraction cannot override a dated reinstatement.
        ([(RI, date(2020, 1, 1)), (R, None)], Status.REINSTATED),
    ],
)
def test_fold(events, expected):
    assert fold(events) == expected


def _closed(events):
    def last(kind):
        keys = [order_key(n, d) for n, d in events if n is kind]
        return max(keys) if keys else None

    return closed_form(last(R), last(E), last(RI), any(n is C for n, _ in events))


def test_closed_form_matches_fold_exhaustively():
    """Every history of up to four notices over three dates (one undated).

    This is what licenses the Spark job to compute status with a CASE
    expression over grouped maxima instead of a per row Python fold.
    """
    dates = [None, date(2020, 1, 1), date(2021, 1, 1)]
    atoms = [(n, d) for n in Nature for d in dates]
    checked = 0
    for k in range(0, 5):
        for events in itertools.product(atoms, repeat=k):
            assert _closed(list(events)) == fold(events), events
            checked += 1
    assert checked == sum(len(atoms) ** k for k in range(5))


def test_parse_nature():
    assert parse_nature("Expression of concern") is E
    assert parse_nature("retraction") is R
    assert parse_nature("Other") is None
    assert parse_nature(None) is None


# text --------------------------------------------------------------------


def test_decode_strict_utf8_first():
    d = decode("Zürich".encode())
    assert (d.text, d.encoding, d.invalid_utf8_bytes) == ("Zürich", "utf-8", 0)


def test_decode_falls_back_to_cp1252_and_counts():
    raw = "People’s Hospital".encode("cp1252")
    d = decode(raw)
    assert d.text == "People’s Hospital"
    assert d.encoding == "cp1252"
    assert d.invalid_utf8_bytes == 1


def test_decode_strips_bom():
    assert decode(b"\xef\xbb\xbfa,b").text == "a,b"


# budget ------------------------------------------------------------------


def test_budget_stops_before_overspend():
    b = Budget(0.0003)
    for _ in range(3):
        b.require(0.0001)
        b.charge(0.0001)
    with pytest.raises(BudgetExhausted):
        b.require(0.0001)
    assert b.summary()["calls"] == 3


# landing -----------------------------------------------------------------


def test_land_is_idempotent_and_immutable(tmp_path):
    store = LocalStore(tmp_path)
    kw = dict(
        source="s", snapshot_date="2026-01-02", name="x.csv", origin_url="u", origin_version="v"
    )
    m1, new1 = land(store, raw=b"a,b\n1,2\n", **kw)
    m2, new2 = land(store, raw=b"a,b\n1,2\n", **kw)
    assert new1 and not new2 and m1.sha256 == m2.sha256
    with pytest.raises(ImmutableWriteError):
        land(store, raw=b"a,b\n1,3\n", **kw)
    assert read_raw(store, m1) == b"a,b\n1,2\n"
    assert [m.snapshot_date for m in manifests(store, "s")] == ["2026-01-02"]


def test_land_stored_bytes_are_deterministic(tmp_path):
    a, b = LocalStore(tmp_path / "a"), LocalStore(tmp_path / "b")
    kw = dict(
        source="s",
        snapshot_date="2026-01-02",
        name="x",
        raw=b"same",
        origin_url="u",
        origin_version="v",
    )
    land(a, **kw)
    land(b, **kw)
    key = "landing/s/snapshot_date=2026-01-02/x.gz"
    assert a.read(key) == b.read(key)
    assert gzip.decompress(a.read(key)) == b"same"


def test_land_recovers_from_crash_between_object_and_manifest(tmp_path):
    store = LocalStore(tmp_path)
    key = "landing/s/snapshot_date=2026-01-02/x.gz"
    store.write_new(key, gzip.compress(b"payload", mtime=0))
    m, new = land(
        store,
        source="s",
        snapshot_date="2026-01-02",
        name="x",
        raw=b"payload",
        origin_url="u",
        origin_version="v",
    )
    assert new and m.object_key == key


def test_land_rejects_bad_dates(tmp_path):
    with pytest.raises(ValueError):
        land(
            LocalStore(tmp_path),
            source="s",
            snapshot_date="2026-13-01",
            name="x",
            raw=b"",
            origin_url="u",
            origin_version="v",
        )


def test_s3_store_conditional_writes():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    from afterlife.core.landing import S3Store

    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="landing")
        store = S3Store("landing", client=client)
        kw = dict(
            source="s", snapshot_date="2026-01-02", name="x", origin_url="u", origin_version="v"
        )
        _, new1 = land(store, raw=b"one", **kw)
        _, new2 = land(store, raw=b"one", **kw)
        assert new1 and not new2
        with pytest.raises(FileExistsError):
            store.write_new("landing/s/snapshot_date=2026-01-02/x.gz", b"other")
        with pytest.raises(ImmutableWriteError):
            land(store, raw=b"two", **kw)
