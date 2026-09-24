"""Rank narrowly validated metadata, with source provenance still unverified.

Input is private JSONL with a strict allowlist of metadata columns. This step
does not qualify task family, verifier, license, image, or repository lineage.
Keep its output private until the once-only analysis is complete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ALLOWED_FIELDS = frozenset({"instance_id", "repo", "language", "license"})
REQUIRED_FIELDS = frozenset({"instance_id", "repo"})
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
INSTANCE_SUFFIX = re.compile(r"[0-9]{1,12}\Z")
LANGUAGE = re.compile(r"[A-Za-z0-9+_.#-]{1,32}\Z")
LICENSE = re.compile(r"[A-Za-z0-9+._-]{1,64}\Z")
SCHEMA = "solcodex.candidate-rank.v1"
SOURCE = "nebius/SWE-rebench-V2"
REVISION = "475dd5e8703bb5fb22dd3c60b5d038b019eba1e0"
SEED = "solcodex-confirm-2026-09-24-v1"
EXPOSURE_PATH = Path(__file__).resolve().parent.parent / "docs/research/data/2026-09-24-development-exposure.json"
SOURCE_URI = ("hf://datasets/nebius/SWE-rebench-V2@" + REVISION +
              "/data/train-00000-of-00001.parquet")
EXPECTED_ROWS = 32079


def canonical_repo(value):
    if not isinstance(value, str):
        raise ValueError("repo must be a string")
    name = value.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.rstrip("/")
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not REPO.fullmatch(name):
        raise ValueError("repo must identify one GitHub owner/repository")
    return name.lower()


def rank(rows, exposure):
    if exposure.get("schema") != "solcodex.development-exposure.v1":
        raise ValueError("invalid exposure schema")
    if (exposure.get("selection_seed") != SEED or
            exposure.get("candidate_revision") != REVISION or
            exposure.get("candidate_source") != SOURCE):
        raise ValueError("exposure manifest does not match frozen source, revision, and seed")
    lineages = exposure.get("lineages")
    if not isinstance(lineages, list) or not lineages:
        raise ValueError("exposure lineages are required")
    excluded = set()
    lineage_ids = set()
    for lineage in lineages:
        if not isinstance(lineage, dict) or not isinstance(lineage.get("id"), str) or not lineage["id"]:
            raise ValueError("invalid lineage entry")
        if lineage["id"] in lineage_ids:
            raise ValueError("duplicated lineage id")
        lineage_ids.add(lineage["id"])
        repositories = lineage.get("repositories")
        if not isinstance(repositories, list) or not repositories:
            raise ValueError("lineage has no repositories")
        for repo in repositories:
            normalized = canonical_repo(repo)
            if normalized in excluded:
                raise ValueError("duplicated exposed repository")
            excluded.add(normalized)
    seen_ids = set()
    seen_issues = set()
    ranked = []
    excluded_count = 0
    for row in rows:
        instance_id, repo, language, license_name = validate_row(row)
        if instance_id in seen_ids:
            raise ValueError("duplicate instance_id")
        seen_ids.add(instance_id)
        issue_key = (repo, int(instance_id.rsplit("-", 1)[1]))
        if issue_key in seen_issues:
            raise ValueError("duplicate logical repository issue")
        seen_issues.add(issue_key)
        if repo in excluded:
            excluded_count += 1
            continue
        digest = hashlib.sha256((SEED + "\n" + instance_id).encode("utf-8")).hexdigest()
        ranked.append({"instance_id": instance_id, "repo": repo,
                       "rank_sha256": digest,
                       "language": language, "license": license_name,
                       "lineage_review": "required"})
    ranked.sort(key=lambda row: (row["rank_sha256"], row["instance_id"]))
    return {"schema": SCHEMA, "candidate_source": SOURCE,
            "candidate_revision": REVISION, "selection_seed": SEED,
            "input_rows": len(seen_ids), "directly_exposed_repositories_excluded": excluded_count,
            "forks_and_related_lineages_reviewed": False,
            "export_manifest_checks_passed": False,
            "source_origin_independently_attested": False, "candidates": ranked}


def validate_row(row):
    if not isinstance(row, dict) or not REQUIRED_FIELDS <= row.keys() or not row.keys() <= ALLOWED_FIELDS:
        raise ValueError("input contains missing or outcome-bearing fields")
    instance_id = row["instance_id"]
    if not isinstance(instance_id, str) or len(instance_id) > 128:
        raise ValueError("invalid instance_id")
    repo = canonical_repo(row["repo"])
    prefix = repo.replace("/", "__") + "-"
    if not instance_id.lower().startswith(prefix) or not INSTANCE_SUFFIX.fullmatch(instance_id[len(prefix):]):
        raise ValueError("instance_id does not match repository and numeric issue")
    language = row.get("language")
    license_name = row.get("license")
    if language is not None and (not isinstance(language, str) or not LANGUAGE.fullmatch(language)):
        raise ValueError("invalid language metadata")
    if license_name is not None and (not isinstance(license_name, str) or not LICENSE.fullmatch(license_name)):
        raise ValueError("invalid license metadata")
    return instance_id, repo, language, license_name


def verify_export_manifest(manifest, metadata_bytes, row_count, expected_rows=EXPECTED_ROWS):
    if row_count != expected_rows:
        raise ValueError("metadata export is not the full pinned source")
    exporter = Path(__file__).resolve().parent / "export_swe_rebench_metadata.py"
    expected_exporter_hash = hashlib.sha256(exporter.read_bytes()).hexdigest()
    expected = {"schema": "solcodex.metadata-export.v1", "source_uri": SOURCE_URI,
                "source_revision": REVISION, "split": "train",
                "projection": ["instance_id", "repo", "language", "license"],
                "source_rows": expected_rows, "export_rows": row_count,
                "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                "exporter_sha256": expected_exporter_hash, "duckdb_version": "1.3.2"}
    if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("metadata export manifest does not match the pinned exporter and full source")


def outside_repository(path):
    root = Path(__file__).resolve().parent.parent
    resolved = path.resolve()
    if resolved == root or root in resolved.parents:
        raise ValueError("candidate metadata and ranked IDs must remain outside public repository")


def publish_no_replace(temp_path, destination):
    """Publish a completed private file without clobbering an existing one."""
    os.link(temp_path, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--export-manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("this private-file workflow requires POSIX")
    outside_repository(args.metadata)
    outside_repository(args.export_manifest)
    outside_repository(args.out)
    if args.out.exists():
        parser.error("output already exists; do not overwrite a frozen ranking")
    exposure_bytes = EXPOSURE_PATH.read_bytes()
    metadata_bytes = args.metadata.read_bytes()
    rows = [json.loads(line) for line in metadata_bytes.splitlines() if line.strip()]
    verify_export_manifest(json.loads(args.export_manifest.read_bytes()), metadata_bytes, len(rows))
    report = rank(rows, json.loads(exposure_bytes))
    report["export_manifest_checks_passed"] = True
    report["metadata_sha256"] = hashlib.sha256(metadata_bytes).hexdigest()
    report["exposure_sha256"] = hashlib.sha256(exposure_bytes).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".candidate-rank-", dir=str(args.out.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        publish_no_replace(temp_path, args.out)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    print(json.dumps({"input_rows": report["input_rows"],
                      "direct_exclusions": report["directly_exposed_repositories_excluded"],
                      "ranked_count": len(report["candidates"]),
                      "lineage_review_required": True}, sort_keys=True))


if __name__ == "__main__":
    main()
