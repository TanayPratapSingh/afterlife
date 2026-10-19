"""Decoding raw source bytes without guessing silently.

The Retraction Watch snapshot for 2026-10-15 was published in Windows-1252
rather than UTF-8: 35,954 bytes that are not valid UTF-8, in a file that was
UTF-8 the day before and the day after. A decoder with `errors="replace"`
would have turned every accented author name in that snapshot into U+FFFD,
and the change capture downstream would have reported tens of thousands of
records as edited, then edited back a day later.

So decoding is an explicit, recorded step. UTF-8 is tried strictly first.
If it fails, Windows-1252 is tried strictly. Latin-1 decodes any byte string
and is the last resort, and the encoding actually used travels with the data
so a reader can see which snapshots needed a fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

FALLBACKS = ("utf-8", "cp1252", "latin-1")


@dataclass(frozen=True)
class Decoded:
    text: str
    encoding: str
    invalid_utf8_bytes: int


def count_invalid_utf8(raw: bytes) -> int:
    s = raw.decode("utf-8", errors="surrogateescape")
    return sum(1 for ch in s if "\udc80" <= ch <= "\udcff")


def decode(raw: bytes) -> Decoded:
    body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    for enc in FALLBACKS:
        try:
            text = body.decode(enc)
        except UnicodeDecodeError:
            continue
        invalid = 0 if enc == "utf-8" else count_invalid_utf8(body)
        return Decoded(text, enc, invalid)
    raise AssertionError("latin-1 decodes every byte string")  # pragma: no cover
