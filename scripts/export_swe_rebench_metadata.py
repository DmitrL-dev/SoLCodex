"""Export four columns from one pinned SWE-rebench V2 Parquet file.

Requires duckdb==1.3.2 and its official httpfs extension. The CLI source URI
is fixed. Query projection excludes patches, tests, descriptions, and metadata
objects; no row values are printed. Private JSONL and provenance are mode 0600.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

try:
    from scripts.rank_confirm_candidates import (EXPECTED_ROWS, REVISION, SOURCE_URI,
                                                  outside_repository, publish_no_replace, validate_row)
except ModuleNotFoundError:
    from rank_confirm_candidates import (EXPECTED_ROWS, REVISION, SOURCE_URI,
                                         outside_repository, publish_no_replace, validate_row)


PROJECTION = ("instance_id", "repo", "language", "license")


def export(source_uri, output, runtime_dir, expected_rows):
    import duckdb
    if duckdb.__version__ != "1.3.2":
        raise ValueError("duckdb==1.3.2 is required")
    runtime_dir.mkdir(mode=0o700, exist_ok=False)
    info = runtime_dir.stat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or
            info.st_uid != os.getuid() or (runtime_dir / "extensions").exists() or
            (runtime_dir / "tmp").exists()):
        raise ValueError("runtime directory must be fresh, owned by caller, and mode 0700")
    con = duckdb.connect(":memory:", config={
        "extension_directory": str(runtime_dir / "extensions"),
        "temp_directory": str(runtime_dir / "tmp")})
    try:
        if source_uri.startswith(("hf://", "https://")):
            con.execute("INSTALL httpfs")
            con.execute("LOAD httpfs")
        source_count = con.execute("SELECT count(*) FROM read_parquet(?)", [source_uri]).fetchone()[0]
        if source_count != expected_rows:
            raise ValueError("source row count differs from pinned expectation")
        cursor = con.execute(
            "SELECT instance_id, repo, language, license FROM read_parquet(?)", [source_uri])
        count = 0
        with output.open("w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            while batch := cursor.fetchmany(512):
                for values in batch:
                    row = dict(zip(PROJECTION, values))
                    validate_row(row)
                    stream.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")
                    count += 1
            stream.flush()
            os.fsync(stream.fileno())
        if count != source_count:
            raise ValueError("projected row count differs from source")
        return count
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--runtime-dir", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("this private-file workflow requires POSIX")
    for path in (args.out, args.manifest, args.runtime_dir):
        outside_repository(path)
    if args.out.exists() or args.manifest.exists():
        parser.error("private export or manifest already exists")
    if args.out.resolve() == args.manifest.resolve():
        parser.error("export and manifest paths must differ")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".swe-metadata-", dir=str(args.out.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        count = export(SOURCE_URI, temp, args.runtime_dir, EXPECTED_ROWS)
        metadata_bytes = temp.read_bytes()
        manifest = {"schema": "solcodex.metadata-export.v1", "source_uri": SOURCE_URI,
                    "source_revision": REVISION, "split": "train",
                    "projection": list(PROJECTION), "source_rows": EXPECTED_ROWS,
                    "export_rows": count,
                    "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                    "exporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "duckdb_version": "1.3.2"}
        fd, manifest_temp_name = tempfile.mkstemp(prefix=".swe-export-manifest-",
                                                  dir=str(args.manifest.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(manifest, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            publish_no_replace(temp, args.out)
            publish_no_replace(manifest_temp_name, args.manifest)
        finally:
            if os.path.exists(manifest_temp_name):
                os.unlink(manifest_temp_name)
        print(json.dumps({"export_rows": count, "projection": list(PROJECTION),
                          "source_revision": REVISION}, sort_keys=True))
    finally:
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
