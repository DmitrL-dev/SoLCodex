"""Exercise complete rank-prefix review and one-task-per-lineage selection."""
import contextlib
import copy
import hashlib
import itertools
import io
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest import mock

try:
    from scripts import select_confirm_sample as selector
except ModuleNotFoundError:
    import select_confirm_sample as selector

FAMILIES = selector.FAMILIES
augment_matching = selector.augment_matching
select_sample = selector.select_sample
verify_approval = selector.verify_approval


def task(name, rank="0"):
    return {"instance_id": name, "repo": name.rsplit("-", 1)[0].replace("__", "/"),
            "language": "python", "license": "MIT", "task_rank_sha256": rank * 64}


def lineage(rank, *tasks, eligible=True):
    return {"repositories": [tasks[0]["repo"]],
            "exposure_witnesses": [] if eligible else ["known/exposed"],
            "selection_eligible": eligible, "lineage_rank_sha256": rank * 64,
            "candidates": list(tasks)}


def classify(name, families=(), reason=None):
    return {"instance_id": name, "eligible_families": list(families),
            "exclusion_reason": reason}


def block(rank, *items):
    return {"lineage_rank_sha256": rank * 64, "tasks": list(items)}


RANKED = {"schema": "solcodex.lineage-rank.v1", "public_map_approval_checked": True,
          "lineages": [lineage("0", task("x__fork-1"), eligible=False),
                       lineage("1", task("a__repo-1"), task("a__repo-2", "1")),
                       lineage("2", task("b__repo-1")),
                       lineage("3", task("c__repo-1")),
                       lineage("4", task("d__repo-1"))]}
REVIEW = {"schema": "solcodex.task-review.v1", "lineages": [
    block("1", classify("a__repo-1", ("state", "build")),
          classify("a__repo-2", ("build",))),
    block("2", classify("b__repo-1", ("build",))),
    block("3", classify("c__repo-1", ("api",)))]}


class SampleSelectionTests(unittest.TestCase):
    def test_one_task_per_lineage_and_priority_with_complete_prefix(self):
        result = select_sample(RANKED, REVIEW, quota=1)
        self.assertEqual(result["family_counts"], {"state": 1, "build": 1, "api": 1})
        self.assertEqual([item["instance_id"] for item in result["selected"]],
                         ["a__repo-1", "b__repo-1", "c__repo-1"])
        self.assertEqual(result["reviewed_lineages"], 3)
        self.assertEqual(result["reviewed_tasks"], 4)
        self.assertEqual(result["excluded_exposure_before_stop"], 1)
        self.assertEqual(result["overlap_assignments"], 1)
        self.assertFalse(result["confirmation_run_authorized"])

    def test_missing_middle_lineage_or_task_fails_closed(self):
        for reviewed in (REVIEW["lineages"][:1] + REVIEW["lineages"][2:],
                         [dict(REVIEW["lineages"][0], tasks=REVIEW["lineages"][0]["tasks"][:1])]
                         + REVIEW["lineages"][1:]):
            with self.subTest(reviewed=reviewed), self.assertRaises(ValueError):
                select_sample(RANKED, {"schema": "solcodex.task-review.v1",
                                       "lineages": reviewed}, quota=1)

    def test_extra_review_after_quota_and_unknown_family_fail_closed(self):
        extra = REVIEW["lineages"] + [block("4", classify("d__repo-1", ("api",)))]
        with self.assertRaises(ValueError):
            select_sample(RANKED, {"schema": "solcodex.task-review.v1", "lineages": extra}, quota=1)
        bad = [dict(REVIEW["lineages"][0], tasks=[classify("a__repo-1", ("state", "unknown")),
                                                    classify("a__repo-2", ("build",))])]
        with self.assertRaises(ValueError):
            select_sample(RANKED, {"schema": "solcodex.task-review.v1",
                                   "lineages": bad + REVIEW["lineages"][1:]}, quota=1)

    def test_reassignment_fills_feasible_quotas_that_greedy_misses(self):
        ranked = {"schema": "solcodex.lineage-rank.v1", "public_map_approval_checked": True,
                  "lineages": [lineage("1", task("a__repo-1")),
                               lineage("2", task("b__repo-1")),
                               lineage("3", task("c__repo-1"))]}
        reviewed = {"schema": "solcodex.task-review.v1", "lineages": [
            block("1", classify("a__repo-1", ("state", "api"))),
            block("2", classify("b__repo-1", ("state",))),
            block("3", classify("c__repo-1", ("build",)))]}
        result = select_sample(ranked, reviewed, quota=1)
        self.assertEqual([(item["instance_id"], item["family"]) for item in result["selected"]],
                         [("a__repo-1", "api"), ("b__repo-1", "state"),
                          ("c__repo-1", "build")])

    def test_exact_latest_incumbent_replay_and_first_feasible_stop(self):
        ranked = {"schema": "solcodex.lineage-rank.v1", "public_map_approval_checked": True,
                  "lineages": [lineage("1", task("a__repo-1"), task("a__repo-2", "1")),
                               lineage("2", task("b__repo-1"), task("b__repo-2", "1")),
                               lineage("3", task("c__repo-1")),
                               lineage("4", task("d__repo-1")),
                               lineage("5", task("e__repo-1")),
                               lineage("6", task("f__repo-1")),
                               lineage("7", task("g__repo-1"))]}
        reviewed = {"schema": "solcodex.task-review.v1", "lineages": [
            block("1", classify("a__repo-1", ("state",)), classify("a__repo-2", ("api",))),
            block("2", classify("b__repo-1", ("state",)), classify("b__repo-2", ("api",))),
            block("3", classify("c__repo-1", ("state",))),
            block("4", classify("d__repo-1", ("api",))),
            block("5", classify("e__repo-1", ("build",))),
            block("6", classify("f__repo-1", ("build",)))]}
        result = select_sample(ranked, reviewed, quota=2)
        self.assertEqual(result["reviewed_lineages"], 6)
        self.assertEqual([(item["instance_id"], item["family"]) for item in result["selected"]],
                         [("a__repo-1", "state"), ("b__repo-2", "api"),
                          ("c__repo-1", "state"), ("d__repo-1", "api"),
                          ("e__repo-1", "build"), ("f__repo-1", "build")])

    def test_duplicate_or_outcome_payload_in_ranked_pool_fails_closed(self):
        for edit in (lambda pool: pool["lineages"][2]["candidates"][0].update(
                        {"instance_id": "a__repo-1", "repo": "a/repo"}),
                     lambda pool: pool["lineages"][1]["candidates"][0].update(
                        {"repo": {"patch": "SYNTHETIC_PAYLOAD"}}),
                     lambda pool: pool["lineages"][1].update(
                        {"exposure_witnesses": ["known/exposed"]})):
            pool = copy.deepcopy(RANKED)
            edit(pool)
            with self.subTest(pool=pool), self.assertRaises(ValueError):
                select_sample(pool, REVIEW, quota=1)

    def test_cli_blocks_stale_or_pending_current_map_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ranking = root / "ranking.json"
            review = root / "review.json"
            output = root / "sample.json"
            exposure_hash = hashlib.sha256(selector.EXPOSURE_PATH.read_bytes()).hexdigest()
            ranker_hash = hashlib.sha256(
                Path(selector.__file__).with_name("rank_confirm_lineages.py").read_bytes()).hexdigest()
            ranking.write_text(json.dumps({"schema": "solcodex.lineage-rank.v1",
                                           "public_map_approval_checked": True,
                                           "candidate_ranking_sha256": "a" * 64,
                                           "lineage_map_sha256": "b" * 64,
                                           "exposure_manifest_sha256": exposure_hash,
                                           "lineage_ranker_sha256": ranker_hash,
                                           "lineages": []}))
            review.write_text('{"schema":"solcodex.task-review.v1","lineages":[]}')
            sample_approval = root / "sample-approval.json"
            sample_approval.write_text(json.dumps({
                "schema": "solcodex.sample-approval.v1", "status": "approved",
                "lineage_ranking_sha256": hashlib.sha256(ranking.read_bytes()).hexdigest(),
                "task_review_sha256": hashlib.sha256(review.read_bytes()).hexdigest(),
                "selector_sha256": hashlib.sha256(Path(selector.__file__).read_bytes()).hexdigest(),
                "rules_sha256": hashlib.sha256(selector.RULES_PATH.read_bytes()).hexdigest(),
                "independent_curators": ["one", "two"]}))
            map_approval = root / "map-approval.json"
            for status, map_hash in (("pending", "b" * 64), ("approved", "c" * 64)):
                map_approval.write_text(json.dumps({
                    "schema": "solcodex.lineage-map-approval.v1", "status": status,
                    "candidate_ranking_sha256": "a" * 64,
                    "lineage_map_sha256": map_hash,
                    "exposure_manifest_sha256": exposure_hash,
                    "lineage_ranker_sha256": ranker_hash,
                    "independent_reviewers": ["one", "two"]}))
                argv = ["select_confirm_sample.py", "--lineage-ranking", str(ranking),
                        "--task-review", str(review), "--out", str(output)]
                errors = io.StringIO()
                with mock.patch.object(selector, "APPROVAL_PATH", sample_approval), \
                        mock.patch.object(selector, "MAP_APPROVAL_PATH", map_approval), \
                        mock.patch.object(sys, "argv", argv), \
                        contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
                    selector.main()
                self.assertIn("lineage ranking no longer matches current map approval",
                              errors.getvalue())
                self.assertFalse(output.exists())

    @unittest.skipUnless(os.name == "posix", "private publication requires POSIX")
    def test_approved_synthetic_360_task_cli_keeps_ids_private_and_run_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exposure_hash = hashlib.sha256(selector.EXPOSURE_PATH.read_bytes()).hexdigest()
            ranker_hash = hashlib.sha256(
                Path(selector.__file__).with_name("rank_confirm_lineages.py").read_bytes()).hexdigest()
            families = ("state", "build", "api")
            lineages = []
            reviewed = []
            for index in range(360):
                family = families[index // 120]
                repo = "owner%03d/repo" % index
                instance_id = repo.replace("/", "__") + "-1"
                rank_hash = "%064x" % index
                lineages.append({"repositories": [repo], "exposure_witnesses": [],
                                 "selection_eligible": True,
                                 "lineage_rank_sha256": rank_hash,
                                 "candidates": [{"instance_id": instance_id, "repo": repo,
                                                 "language": "python", "license": "MIT",
                                                 "task_rank_sha256": "f" * 64}]})
                reviewed.append({"lineage_rank_sha256": rank_hash,
                                 "tasks": [classify(instance_id, (family,))]})
            lineage_file = root / "ranking.json"
            review_file = root / "review.json"
            output = root / "sample.json"
            lineage_file.write_text(json.dumps({
                "schema": "solcodex.lineage-rank.v1", "public_map_approval_checked": True,
                "candidate_ranking_sha256": "a" * 64, "lineage_map_sha256": "b" * 64,
                "exposure_manifest_sha256": exposure_hash,
                "lineage_ranker_sha256": ranker_hash, "lineages": lineages}))
            review_file.write_text(json.dumps({"schema": "solcodex.task-review.v1",
                                               "lineages": reviewed}))
            sample_approval = root / "sample-approval.json"
            sample_approval.write_text(json.dumps({
                "schema": "solcodex.sample-approval.v1", "status": "approved",
                "lineage_ranking_sha256": hashlib.sha256(lineage_file.read_bytes()).hexdigest(),
                "task_review_sha256": hashlib.sha256(review_file.read_bytes()).hexdigest(),
                "selector_sha256": hashlib.sha256(Path(selector.__file__).read_bytes()).hexdigest(),
                "rules_sha256": hashlib.sha256(selector.RULES_PATH.read_bytes()).hexdigest(),
                "independent_curators": ["one", "two"]}))
            map_approval = root / "map-approval.json"
            map_approval.write_text(json.dumps({
                "schema": "solcodex.lineage-map-approval.v1", "status": "approved",
                "candidate_ranking_sha256": "a" * 64, "lineage_map_sha256": "b" * 64,
                "exposure_manifest_sha256": exposure_hash,
                "lineage_ranker_sha256": ranker_hash,
                "independent_reviewers": ["three", "four"]}))
            argv = ["select_confirm_sample.py", "--lineage-ranking", str(lineage_file),
                    "--task-review", str(review_file), "--out", str(output)]
            public_stdout = io.StringIO()
            with mock.patch.object(selector, "APPROVAL_PATH", sample_approval), \
                    mock.patch.object(selector, "MAP_APPROVAL_PATH", map_approval), \
                    mock.patch.object(sys, "argv", argv), \
                    contextlib.redirect_stdout(public_stdout):
                selector.main()
            aggregate = json.loads(public_stdout.getvalue())
            private = json.loads(output.read_text())
            self.assertEqual(aggregate["selected_count"], 360)
            self.assertEqual(private["family_counts"], {family: 120 for family in families})
            self.assertFalse(private["confirmation_run_authorized"])
            self.assertNotIn("owner000", public_stdout.getvalue())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_matching_cardinality_matches_independent_exhaustive_search(self):
        rng = random.Random(1701)
        for case in range(24):
            quota = 1 + case % 2
            choices = {}
            assignments = {}
            members = {family: [] for family in FAMILIES}
            for index in range(6):
                bits = rng.randrange(8)
                choices[index] = {family for bit, family in enumerate(FAMILIES) if bits & (1 << bit)}
                augment_matching(index, choices, assignments, members, quota)
                maximum = 0
                for proposal in itertools.product((None,) + FAMILIES, repeat=index + 1):
                    if any(proposal.count(family) > quota for family in FAMILIES):
                        continue
                    if any(family is not None and family not in choices[i]
                           for i, family in enumerate(proposal)):
                        continue
                    maximum = max(maximum, sum(family is not None for family in proposal))
                self.assertEqual(len(assignments), maximum)
                self.assertEqual(sum(map(len, members.values())), maximum)

    def test_public_approval_binds_both_private_inputs(self):
        lineage_bytes, review_bytes = b"lineages", b"reviews"
        selector_hash, rules_hash = "c" * 64, "d" * 64
        approval = {"schema": "solcodex.sample-approval.v1", "status": "approved",
                    "lineage_ranking_sha256": hashlib.sha256(lineage_bytes).hexdigest(),
                    "task_review_sha256": hashlib.sha256(review_bytes).hexdigest(),
                    "selector_sha256": selector_hash, "rules_sha256": rules_hash,
                    "independent_curators": ["curator-one", "curator-two"]}
        verify_approval(lineage_bytes, review_bytes, selector_hash, rules_hash, approval)
        with self.assertRaises(ValueError):
            verify_approval(lineage_bytes, b"changed", selector_hash, rules_hash, approval)
        with self.assertRaises(ValueError):
            verify_approval(lineage_bytes, review_bytes, "e" * 64, rules_hash, approval)
        approval["status"] = "pending"
        with self.assertRaises(ValueError):
            verify_approval(lineage_bytes, review_bytes, selector_hash, rules_hash, approval)


if __name__ == "__main__":
    unittest.main()
