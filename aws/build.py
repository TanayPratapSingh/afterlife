"""Build the Lambda deployment zips, byte for byte reproducibly.

    python aws/build.py            # writes aws/build/{fetch,load_registry,api}.zip

Each zip holds one handler at the root plus the `afterlife` package, minus
`afterlife/spark` (Spark runs on Databricks, never in Lambda). No third party
code is bundled: the core is standard library only, and boto3 comes with the
Lambda Python runtime.

Terraform redeploys a function when `source_code_hash` changes, and that hash
is the sha256 of the zip. A zip that embeds file mtimes, or lists entries in
filesystem order, changes on every checkout and redeploys every function on
every apply. So entries are sorted, timestamps and permissions are fixed, and
only `.py` sources go in (no `__pycache__`, which embeds source mtimes).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAMBDAS = ROOT / "aws" / "lambdas"
PACKAGE = ROOT / "afterlife"
FUNCTIONS = ("fetch", "load_registry", "api")
EXCLUDED_DIRS = {"spark", "__pycache__"}
FIXED_TIME = (1980, 1, 1, 0, 0, 0)  # the earliest timestamp a zip entry can hold
FILE_MODE = 0o644 << 16


def package_entries() -> list[tuple[str, Path]]:
    entries = []
    for path in PACKAGE.rglob("*.py"):
        rel = path.relative_to(PACKAGE)
        if EXCLUDED_DIRS.intersection(rel.parts[:-1]):
            continue
        entries.append((f"afterlife/{rel.as_posix()}", path))
    return entries


def entries_for(function: str) -> list[tuple[str, Path]]:
    handler = LAMBDAS / function / "handler.py"
    if not handler.is_file():
        raise FileNotFoundError(handler)
    return sorted([("handler.py", handler), *package_entries()])


def zip_bytes(entries: list[tuple[str, Path]]) -> bytes:
    with tempfile.SpooledTemporaryFile() as buf:
        with zipfile.ZipFile(buf, "w") as zf:
            for arcname, path in entries:
                info = zipfile.ZipInfo(arcname, date_time=FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = FILE_MODE
                info.create_system = 3  # unix, so the mode bits above are honoured
                zf.writestr(info, path.read_bytes(), compresslevel=9)
        buf.seek(0)
        return buf.read()


def build(function: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{function}.zip"
    data = zip_bytes(entries_for(function))
    # Write then rename, so a reader never sees a half written zip.
    fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".zip.tmp")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, target)
    return target


def build_all(out_dir: Path) -> dict[str, Path]:
    return {name: build(name, out_dir) for name in FUNCTIONS}


def describe(path: Path) -> dict:
    data = path.read_bytes()
    with zipfile.ZipFile(path) as zf:
        files = len(zf.namelist())
    return {
        "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "bytes": len(data),
        "files": files,
        "sha256": hashlib.sha256(data).hexdigest(),
        # What Terraform's filebase64sha256() reports for the same file.
        "source_code_hash": base64.b64encode(hashlib.sha256(data).digest()).decode(),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=ROOT / "aws" / "build")
    p.add_argument("functions", nargs="*", help=f"any of {', '.join(FUNCTIONS)} (default: all)")
    args = p.parse_args(argv)
    unknown = sorted(set(args.functions) - set(FUNCTIONS))
    if unknown:
        p.error(f"unknown function(s) {unknown}; choose from {list(FUNCTIONS)}")
    built = {name: describe(build(name, args.out)) for name in args.functions or FUNCTIONS}
    print(json.dumps(built, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
