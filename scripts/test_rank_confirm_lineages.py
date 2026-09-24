"""Lineage-first ranking must resist exposure and post-review map changes."""
import hashlib
import unittest

try:
    from scripts.rank_confirm_candidates import SEED as TASK_SEED
    from scripts.rank_confirm_lineages import SEED, digest, rank_lineages, verify_map_approval
except ModuleNotFoundError:
    from rank_confirm_candidates import SEED as TASK_SEED
    from rank_confirm_lineages import SEED, digest, rank_lineages, verify_map_approval


def candidate(repo, issue):
    instance_id = repo.replace("/", "__") + "-" + str(issue)
    return {"instance_id": instance_id,
            "repo": repo, "language": "python", "license": "MIT",
            "rank_sha256": digest(TASK_SEED + "\n" + instance_id),
            "lineage_review": "required"}


def ranking(*items):
    return {"schema": "solcodex.candidate-rank.v1", "export_manifest_checks_passed": True,
            "candidates": list(items)}


def group(*repos, witnesses=()):
    return {"repositories": list(repos), "exposure_witnesses": list(witnesses)}


def mapping(*groups):
    return {"schema": "solcodex.lineage-map.v1", "lineages": list(groups)}


MAP = mapping(group("a/repo", "b/fork"), group("c/other"))


class LineageRankingTests(unittest.TestCase):
    def test_lineage_rank_ignores_task_multiplicity(self):
        small = rank_lineages(ranking(candidate("a/repo", 1), candidate("b/fork", 1),
                                      candidate("c/other", 1)), MAP, set())
        large = rank_lineages(ranking(candidate("a/repo", 1), candidate("a/repo", 2),
                                      candidate("b/fork", 1), candidate("c/other", 1)), MAP, set())
        self.assertEqual([(item["repositories"], item["lineage_rank_sha256"])
                          for item in small["lineages"]],
                         [(item["repositories"], item["lineage_rank_sha256"])
                          for item in large["lineages"]])
        grouped = next(item for item in large["lineages"] if "a/repo" in item["repositories"])
        self.assertEqual(len(grouped["candidates"]), 3)
        self.assertEqual(grouped["lineage_rank_sha256"],
                         digest(SEED + "\nlineage\na/repo\nb/fork"))
        self.assertFalse(large["sample_selected"])

    def test_exposed_upstream_excludes_remaining_fork_without_dropping_coverage(self):
        source = ranking(candidate("c/other", 1), candidate("b/fork", 1))
        exposed = {"a/upstream"}
        result = rank_lineages(source, mapping(group("b/fork", witnesses=("a/upstream",)),
                                               group("c/other")), exposed)
        self.assertEqual(result["excluded_exposure_lineages"], 1)
        self.assertEqual(result["eligible_lineage_count"], 1)
        fork = next(item for item in result["lineages"] if "b/fork" in item["repositories"])
        self.assertFalse(fork["selection_eligible"])
        with self.assertRaises(ValueError):
            rank_lineages(source, mapping(group("b/fork", witnesses=("x/unknown",)),
                                          group("c/other")), exposed)

    def test_incomplete_duplicate_and_extra_mapping_fail_closed(self):
        source = ranking(candidate("a/repo", 1), candidate("c/other", 1))
        maps = [mapping(group("a/repo")),
                mapping(group("a/repo", "a/repo"), group("c/other")),
                mapping(group("a/repo", "x/extra"), group("c/other"))]
        for item in maps:
            with self.subTest(mapping=item), self.assertRaises(ValueError):
                rank_lineages(source, item, set())

    def test_nested_outcome_payload_and_wrong_task_hash_are_rejected(self):
        item = candidate("a/repo", 1)
        item["rank_sha256"] = {"patch": "SYNTHETIC_PAYLOAD"}
        with self.assertRaises(ValueError):
            rank_lineages(ranking(item), mapping(group("a/repo")), set())
        item["rank_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            rank_lineages(ranking(item), mapping(group("a/repo")), set())
        item = candidate("a/repo", 1)
        item["patch"] = "gold"
        with self.assertRaises(ValueError):
            rank_lineages(ranking(item), mapping(group("a/repo")), set())

    def test_changed_or_unapproved_map_is_rejected_even_with_new_output(self):
        original = b'{"schema":"solcodex.lineage-map.v1","lineages":[]}'
        changed = b'{"schema":"solcodex.lineage-map.v1","lineages":[1]}'
        candidate_hash = "a" * 64
        exposure_hash = "b" * 64
        ranker_hash = "c" * 64
        approval = {"schema": "solcodex.lineage-map-approval.v1", "status": "approved",
                    "candidate_ranking_sha256": candidate_hash,
                    "exposure_manifest_sha256": exposure_hash,
                    "lineage_ranker_sha256": ranker_hash,
                    "lineage_map_sha256": hashlib.sha256(original).hexdigest(),
                    "independent_reviewers": ["reviewer-one", "reviewer-two"]}
        verify_map_approval(original, candidate_hash, exposure_hash, ranker_hash, approval)
        with self.assertRaises(ValueError):
            verify_map_approval(changed, candidate_hash, exposure_hash, ranker_hash, approval)
        with self.assertRaises(ValueError):
            verify_map_approval(original, candidate_hash, "d" * 64, ranker_hash, approval)
        with self.assertRaises(ValueError):
            verify_map_approval(original, candidate_hash, exposure_hash, "d" * 64, approval)
        approval["status"] = "pending"
        with self.assertRaises(ValueError):
            verify_map_approval(original, candidate_hash, exposure_hash, ranker_hash, approval)


if __name__ == "__main__":
    unittest.main()
