"""Select a private 120/120/120 confirmation sample from reviewed lineages.

The CLI refuses to publish until a public approval manifest binds the lineage
rank and complete task review. This does not qualify verifiers or infrastructure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

try:
    from scripts.rank_confirm_candidates import (EXPOSURE_PATH, canonical_repo,
        outside_repository, publish_no_replace, validate_row)
    from scripts.rank_confirm_lineages import APPROVAL_PATH as MAP_APPROVAL_PATH
except ModuleNotFoundError:
    from rank_confirm_candidates import (EXPOSURE_PATH, canonical_repo,
        outside_repository, publish_no_replace, validate_row)
    from rank_confirm_lineages import APPROVAL_PATH as MAP_APPROVAL_PATH


FAMILIES = ("state", "build", "api")
REASONS = frozenset({"not_repair", "unsupported_language", "license", "image_unavailable",
                     "fix_leakage", "unverifiable", "outside_families"})
APPROVAL_PATH = Path(__file__).resolve().parent.parent / "docs/research/data/2026-09-24-sample-approval.json"
RULES_PATH = Path(__file__).resolve().parent.parent / "docs/research/2026-09-24-sample-selector.md"
HEX = re.compile(r"[0-9a-f]{64}\Z")


def verify_approval(lineage_bytes, review_bytes, selector_sha256, rules_sha256, approval):
    if not isinstance(approval, dict) or approval.get("schema") != "solcodex.sample-approval.v1" or \
            approval.get("status") != "approved" or \
            approval.get("lineage_ranking_sha256") != hashlib.sha256(lineage_bytes).hexdigest() or \
            approval.get("task_review_sha256") != hashlib.sha256(review_bytes).hexdigest() or \
            approval.get("selector_sha256") != selector_sha256 or \
            approval.get("rules_sha256") != rules_sha256:
        raise ValueError("sample inputs are not pinned by an approved public manifest")
    curators = approval.get("independent_curators")
    if not isinstance(curators, list) or len(curators) != 2 or \
            any(not isinstance(name, str) or not name.strip() for name in curators) or \
            curators[0] == curators[1]:
        raise ValueError("two distinct independent curators must be recorded")


def verify_current_map_approval(ranked, approval, current_exposure_sha256, current_ranker_sha256):
    provenance = ("candidate_ranking_sha256", "lineage_map_sha256",
                  "exposure_manifest_sha256", "lineage_ranker_sha256")
    if not isinstance(ranked, dict) or any(
            not isinstance(ranked.get(key), str) or not HEX.fullmatch(ranked[key])
            for key in provenance):
        raise ValueError("lineage ranking lacks complete provenance hashes")
    if not isinstance(approval, dict) or approval.get("schema") != "solcodex.lineage-map-approval.v1" or \
            approval.get("status") != "approved" or \
            approval.get("candidate_ranking_sha256") != ranked.get("candidate_ranking_sha256") or \
            approval.get("lineage_map_sha256") != ranked.get("lineage_map_sha256") or \
            approval.get("exposure_manifest_sha256") != current_exposure_sha256 or \
            ranked.get("exposure_manifest_sha256") != current_exposure_sha256 or \
            approval.get("lineage_ranker_sha256") != current_ranker_sha256 or \
            ranked.get("lineage_ranker_sha256") != current_ranker_sha256:
        raise ValueError("lineage ranking no longer matches current map approval")
    reviewers = approval.get("independent_reviewers")
    if not isinstance(reviewers, list) or len(reviewers) != 2 or \
            any(not isinstance(name, str) or not name.strip() for name in reviewers) or \
            reviewers[0] == reviewers[1]:
        raise ValueError("current map approval lacks two distinct reviewers")


def validate_lineage_entries(lineages):
    """Reject duplicate identities and non-metadata payloads across the pool."""
    seen_repos = set()
    seen_ids = set()
    for lineage in lineages:
        if not isinstance(lineage, dict) or set(lineage) != {
                "repositories", "exposure_witnesses", "selection_eligible",
                "lineage_rank_sha256", "candidates"}:
            raise ValueError("invalid lineage ranking entry")
        repos = lineage["repositories"]
        witnesses = lineage["exposure_witnesses"]
        eligible = lineage["selection_eligible"]
        rank_hash = lineage["lineage_rank_sha256"]
        if not isinstance(repos, list) or not repos or \
                any(not isinstance(repo, str) or canonical_repo(repo) != repo for repo in repos) or \
                repos != sorted(set(repos)) or seen_repos.intersection(repos) or \
                not isinstance(witnesses, list) or \
                any(not isinstance(repo, str) or canonical_repo(repo) != repo for repo in witnesses) or \
                len(set(witnesses)) != len(witnesses) or \
                type(eligible) is not bool or eligible == bool(witnesses) or \
                not isinstance(rank_hash, str) or not HEX.fullmatch(rank_hash):
            raise ValueError("invalid or duplicated lineage identity")
        seen_repos.update(repos)
        candidates = lineage["candidates"]
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("lineage candidates are required")
        covered_repos = set()
        for task in candidates:
            if not isinstance(task, dict) or set(task) != {"instance_id", "repo", "language",
                    "license", "task_rank_sha256"}:
                raise ValueError("invalid ranked candidate")
            instance_id, repo, _, _ = validate_row({key: task[key] for key in
                                                    ("instance_id", "repo", "language", "license")})
            task_hash = task["task_rank_sha256"]
            if task["repo"] != repo or repo not in repos or instance_id in seen_ids or \
                    not isinstance(task_hash, str) or not HEX.fullmatch(task_hash):
                raise ValueError("duplicate or foreign task in lineage")
            seen_ids.add(instance_id)
            covered_repos.add(repo)
        if covered_repos != set(repos) or \
                candidates != sorted(candidates, key=lambda task: (task["task_rank_sha256"], task["instance_id"])):
            raise ValueError("lineage tasks do not match repositories or rank order")
    if lineages != sorted(lineages, key=lambda item: (item["lineage_rank_sha256"], item["repositories"])):
        raise ValueError("lineages are not in frozen rank order")


def augment_matching(new_index, choices, assignments, members, quota):
    """Extend a maximum cardinality lineage-to-family matching by one vertex.

    Family order is state, build, API. When a family is full, try moving its
    latest-ranked incumbent first. Failed paths do not mutate the matching.
    """
    seen_families = set()

    def place(index):
        for family in FAMILIES:
            if family not in choices[index] or family in seen_families:
                continue
            seen_families.add(family)
            if len(members[family]) < quota:
                members[family].append(index)
                assignments[index] = family
                return True
            for incumbent in sorted(members[family], reverse=True):
                if place(incumbent):
                    members[family].remove(incumbent)
                    members[family].append(index)
                    assignments[index] = family
                    return True
        return False

    return place(new_index)


def select_sample(ranked, review, quota=120):
    if type(quota) is not int or quota <= 0:
        raise ValueError("quota must be a positive integer")
    if not isinstance(ranked, dict) or ranked.get("schema") != "solcodex.lineage-rank.v1" or \
            ranked.get("public_map_approval_checked") is not True:
        raise ValueError("lineage rank lacks public map approval")
    lineages = ranked.get("lineages")
    if not isinstance(lineages, list) or not lineages:
        raise ValueError("ranked lineages are required")
    if not isinstance(review, dict) or set(review) != {"schema", "lineages"} or \
            review["schema"] != "solcodex.task-review.v1" or \
            not isinstance(review["lineages"], list):
        raise ValueError("invalid task review manifest")
    reviews = review["lineages"]
    validate_lineage_entries(lineages)

    reason_counts = {reason: 0 for reason in sorted(REASONS)}
    choices = {}
    assignments = {}
    members = {family: [] for family in FAMILIES}
    reviewed_lineages = {}
    reviewed_count = 0
    reviewed_tasks = 0
    excluded_exposure = 0
    for lineage in lineages:
        if all(len(members[family]) == quota for family in FAMILIES):
            break
        if lineage["selection_eligible"] is False:
            excluded_exposure += 1
            continue
        if reviewed_count >= len(reviews):
            raise ValueError("task review is not a complete rank prefix")
        block = reviews[reviewed_count]
        reviewed_count += 1
        if not isinstance(block, dict) or set(block) != {"lineage_rank_sha256", "tasks"} or \
                block["lineage_rank_sha256"] != lineage["lineage_rank_sha256"] or \
                not isinstance(block["tasks"], list):
            raise ValueError("task review is not a contiguous lineage-rank prefix")
        candidates = lineage["candidates"]
        ids = [task["instance_id"] for task in candidates]
        if len(block["tasks"]) != len(ids):
            raise ValueError("every task in a visited lineage must be classified")
        labels = {}
        for item in block["tasks"]:
            if not isinstance(item, dict) or set(item) != {"instance_id", "eligible_families", "exclusion_reason"} or \
                    not isinstance(item["instance_id"], str) or item["instance_id"] in labels or \
                    not isinstance(item["eligible_families"], list):
                raise ValueError("invalid task classification")
            families = item["eligible_families"]
            if any(not isinstance(family, str) for family in families) or \
                    len(set(families)) != len(families) or any(family not in FAMILIES for family in families):
                raise ValueError("invalid family classification")
            reason = item["exclusion_reason"]
            if (families and reason is not None) or \
                    (not families and (not isinstance(reason, str) or reason not in REASONS)):
                raise ValueError("exclusion reason is inconsistent with eligibility")
            labels[item["instance_id"]] = set(families)
            if reason is not None:
                reason_counts[reason] += 1
        if set(labels) != set(ids):
            raise ValueError("reviewed task IDs differ from ranked lineage")
        reviewed_tasks += len(ids)
        eligible_families = {family for family in FAMILIES if any(family in labels[id_] for id_ in ids)}
        index = reviewed_count - 1
        choices[index] = eligible_families
        reviewed_lineages[index] = (lineage, candidates, labels)
        augment_matching(index, choices, assignments, members, quota)

    counts = {family: len(members[family]) for family in FAMILIES}
    if reviewed_count != len(reviews) or any(counts[family] != quota for family in FAMILIES):
        raise ValueError("review must stop at the first feasible rank prefix")
    selected = []
    overlap_assignments = 0
    for index, family in sorted(assignments.items()):
        lineage, candidates, labels = reviewed_lineages[index]
        chosen = next(task for task in candidates if family in labels[task["instance_id"]])
        selected.append({"instance_id": chosen["instance_id"], "repo": chosen["repo"],
                         "family": family,
                         "lineage_rank_sha256": lineage["lineage_rank_sha256"],
                         "task_rank_sha256": chosen["task_rank_sha256"]})
        overlap_assignments += len(choices[index]) > 1
    return {"schema": "solcodex.confirmation-sample.v1", "quota_per_family": quota,
            "selected_count": len(selected), "family_counts": counts,
            "reviewed_lineages": reviewed_count, "reviewed_tasks": reviewed_tasks,
            "excluded_exposure_before_stop": excluded_exposure,
            "overlap_assignments": overlap_assignments,
            "task_exclusion_reasons": reason_counts,
            "verifiers_qualified": False, "isolation_qualified": False,
            "provider_accounting_qualified": False,
            "confirmation_run_authorized": False,
            "selected": selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lineage-ranking", required=True, type=Path)
    parser.add_argument("--task-review", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("private publication requires POSIX")
    for path in (args.lineage_ranking, args.task_review, args.out):
        outside_repository(path)
    if args.out.exists():
        parser.error("output already exists")
    lineage_bytes = args.lineage_ranking.read_bytes()
    review_bytes = args.task_review.read_bytes()
    selector_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    rules_hash = hashlib.sha256(RULES_PATH.read_bytes()).hexdigest()
    try:
        verify_approval(lineage_bytes, review_bytes, selector_hash, rules_hash,
                        json.loads(APPROVAL_PATH.read_bytes()))
        ranked = json.loads(lineage_bytes)
        exposure_hash = hashlib.sha256(EXPOSURE_PATH.read_bytes()).hexdigest()
        ranker_hash = hashlib.sha256(Path(__file__).with_name("rank_confirm_lineages.py").read_bytes()).hexdigest()
        verify_current_map_approval(ranked, json.loads(MAP_APPROVAL_PATH.read_bytes()),
                                    exposure_hash, ranker_hash)
        report = select_sample(ranked, json.loads(review_bytes))
    except ValueError as error:
        parser.error(str(error))
    report["lineage_ranking_sha256"] = hashlib.sha256(lineage_bytes).hexdigest()
    report["task_review_sha256"] = hashlib.sha256(review_bytes).hexdigest()
    report["selector_sha256"] = selector_hash
    report["rules_sha256"] = rules_hash
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".confirmation-sample-", dir=str(args.out.parent))
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
    print(json.dumps({key: report[key] for key in
                      ("selected_count", "family_counts", "reviewed_lineages",
                       "reviewed_tasks", "excluded_exposure_before_stop", "overlap_assignments",
                       "task_exclusion_reasons", "confirmation_run_authorized")}, sort_keys=True))


if __name__ == "__main__":
    main()
