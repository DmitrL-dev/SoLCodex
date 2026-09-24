"""Privately rank reviewed candidate lineages before ranking their tasks.

This is a structural ranking gate, not ancestry attestation or task selection.
Inputs and output contain candidate IDs and must stay outside the public repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

try:
    from scripts.rank_confirm_candidates import (EXPOSURE_PATH, SEED as TASK_SEED,
        canonical_repo, outside_repository, publish_no_replace, validate_row)
except ModuleNotFoundError:
    from rank_confirm_candidates import (EXPOSURE_PATH, SEED as TASK_SEED,
        canonical_repo, outside_repository, publish_no_replace, validate_row)


SEED = "solcodex-lineage-confirm-2026-09-24-v1"
PINNED_RANK_SHA256 = "f0ce40acd96554d66554e0896152eb3e5fcc77b3e8fc741a626be74da29bc884"
CANDIDATE_FIELDS = frozenset({"instance_id", "repo", "language", "license", "rank_sha256", "lineage_review"})
APPROVAL_PATH = Path(__file__).resolve().parent.parent / "docs/research/data/2026-09-24-lineage-map-approval.json"


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_map_approval(map_bytes, candidate_sha256, exposure_sha256, approval):
    if not isinstance(approval, dict) or approval.get("schema") != "solcodex.lineage-map-approval.v1" or \
            approval.get("status") != "approved" or \
            approval.get("candidate_ranking_sha256") != candidate_sha256 or \
            approval.get("exposure_manifest_sha256") != exposure_sha256 or \
            approval.get("lineage_map_sha256") != hashlib.sha256(map_bytes).hexdigest():
        raise ValueError("lineage map is not pinned by an approved public manifest")
    reviewers = approval.get("independent_reviewers")
    if not isinstance(reviewers, list) or len(reviewers) != 2 or \
            any(not isinstance(name, str) or not name.strip() for name in reviewers) or \
            reviewers[0] == reviewers[1]:
        raise ValueError("two distinct independent reviewers must be recorded")


def rank_lineages(ranked, lineage_map, exposed_repositories):
    if not isinstance(ranked, dict) or ranked.get("schema") != "solcodex.candidate-rank.v1":
        raise ValueError("invalid candidate ranking")
    if ranked.get("export_manifest_checks_passed") is not True:
        raise ValueError("candidate export manifest was not checked")
    candidates = ranked.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates are required")
    by_repo = {}
    seen_ids = set()
    for item in candidates:
        if not isinstance(item, dict) or set(item) != CANDIDATE_FIELDS:
            raise ValueError("candidate contains missing or outcome-bearing fields")
        instance_id, repo, _, _ = validate_row({key: item[key] for key in
                                                ("instance_id", "repo", "language", "license")})
        if item["repo"] != repo or instance_id in seen_ids:
            raise ValueError("duplicate or noncanonical candidate")
        seen_ids.add(instance_id)
        if item["lineage_review"] != "required":
            raise ValueError("unexpected candidate lineage state")
        if item["rank_sha256"] != digest(TASK_SEED + "\n" + instance_id):
            raise ValueError("candidate task hash does not match the pinned ranking rule")
        by_repo.setdefault(repo, []).append({key: item[key] for key in
                                             ("instance_id", "repo", "language", "license")})

    if not isinstance(lineage_map, dict) or set(lineage_map) != {"schema", "lineages"} or \
            lineage_map["schema"] != "solcodex.lineage-map.v1":
        raise ValueError("invalid lineage map")
    groups = lineage_map["lineages"]
    if not isinstance(groups, list) or not groups:
        raise ValueError("lineage groups are required")
    covered = set()
    result = []
    excluded_count = 0
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"repositories", "exposure_witnesses"}:
            raise ValueError("invalid lineage group")
        names = group["repositories"]
        witnesses = group["exposure_witnesses"]
        if not isinstance(names, list) or not names:
            raise ValueError("lineage repositories are required")
        if not isinstance(witnesses, list):
            raise ValueError("exposure witnesses must be a list")
        normalized = [canonical_repo(name) for name in names]
        normalized_witnesses = [canonical_repo(name) for name in witnesses]
        if any(name != canonical for name, canonical in zip(names, normalized)) or \
                len(set(normalized)) != len(normalized) or covered.intersection(normalized) or \
                any(name != canonical for name, canonical in zip(witnesses, normalized_witnesses)) or \
                len(set(normalized_witnesses)) != len(normalized_witnesses) or \
                not set(normalized_witnesses) <= exposed_repositories:
            raise ValueError("noncanonical or duplicated lineage repository")
        if set(normalized).intersection(exposed_repositories) and not normalized_witnesses:
            raise ValueError("directly exposed repository requires an exposure witness")
        covered.update(normalized)
        ordered_repos = sorted(normalized)
        lineage_key = "\n".join(ordered_repos)
        tasks = []
        for repo in ordered_repos:
            tasks.extend(by_repo.get(repo, []))
        tasks = [{**task, "task_rank_sha256": digest(SEED + "\ntask\n" + task["instance_id"])}
                 for task in tasks]
        tasks.sort(key=lambda item: (item["task_rank_sha256"], item["instance_id"]))
        excluded = bool(normalized_witnesses)
        excluded_count += excluded
        result.append({"repositories": ordered_repos,
                       "exposure_witnesses": sorted(normalized_witnesses),
                       "selection_eligible": not excluded,
                       "lineage_rank_sha256": digest(SEED + "\nlineage\n" + lineage_key),
                       "candidates": tasks})
    if covered != set(by_repo):
        raise ValueError("lineage map must cover exactly the candidate repositories")
    result.sort(key=lambda item: (item["lineage_rank_sha256"], item["repositories"]))
    return {"schema": "solcodex.lineage-rank.v1", "selection_seed": SEED,
            "lineage_count": len(result), "candidate_count": len(candidates),
            "excluded_exposure_lineages": excluded_count,
            "eligible_lineage_count": len(result) - excluded_count,
            "ancestry_independently_verified": False,
            "family_qualification_complete": False, "sample_selected": False,
            "lineages": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-ranking", required=True, type=Path)
    parser.add_argument("--lineage-map", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("private publication requires POSIX")
    for path in (args.candidate_ranking, args.lineage_map, args.out):
        outside_repository(path)
    if args.out.exists():
        parser.error("output already exists")
    candidate_bytes = args.candidate_ranking.read_bytes()
    map_bytes = args.lineage_map.read_bytes()
    exposure_bytes = EXPOSURE_PATH.read_bytes()
    candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
    exposure_hash = hashlib.sha256(exposure_bytes).hexdigest()
    if candidate_hash != PINNED_RANK_SHA256:
        parser.error("candidate ranking does not match the pinned private export")
    try:
        verify_map_approval(map_bytes, candidate_hash, exposure_hash,
                            json.loads(APPROVAL_PATH.read_bytes()))
    except ValueError as error:
        parser.error(str(error))
    exposure = json.loads(exposure_bytes)
    if exposure.get("schema") != "solcodex.development-exposure.v1":
        parser.error("invalid development exposure manifest")
    exposed = {canonical_repo(repo) for group in exposure["lineages"]
               for repo in group["repositories"]}
    report = rank_lineages(json.loads(candidate_bytes), json.loads(map_bytes), exposed)
    report["candidate_ranking_sha256"] = candidate_hash
    report["lineage_map_sha256"] = hashlib.sha256(map_bytes).hexdigest()
    report["exposure_manifest_sha256"] = exposure_hash
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".lineage-rank-", dir=str(args.out.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        publish_no_replace(temp_path, args.out)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    print(json.dumps({"candidate_count": report["candidate_count"],
                      "lineage_count": report["lineage_count"],
                      "excluded_exposure_lineages": report["excluded_exposure_lineages"],
                      "ancestry_independently_verified": False,
                      "sample_selected": False}, sort_keys=True))


if __name__ == "__main__":
    main()
