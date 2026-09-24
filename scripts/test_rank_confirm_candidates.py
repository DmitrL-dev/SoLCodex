"""Protect the candidate ranking from outcome fields and known exposures."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
import unittest

try:
    from scripts.rank_confirm_candidates import SEED, REVISION, SOURCE, canonical_repo, outside_repository, publish_no_replace, rank
except ModuleNotFoundError:
    from rank_confirm_candidates import SEED, REVISION, SOURCE, canonical_repo, outside_repository, publish_no_replace, rank


EXPOSURE = {"schema": "solcodex.development-exposure.v1",
            "selection_seed": SEED,
            "candidate_source": SOURCE,
            "candidate_revision": REVISION,
            "lineages": [{"id": "click", "repositories": ["pallets/click"]}]}


class RankingTests(unittest.TestCase):
    def test_ranking_excludes_exposed_repository_and_matches_frozen_hash(self):
        result = rank([{"instance_id": "pallets__click-1", "repo": "https://github.com/Pallets/Click.git"},
                       {"instance_id": "owner__other-2", "repo": "owner/other", "language": "python"},
                       {"instance_id": "owner__new-3", "repo": "owner/new"}], EXPOSURE)
        self.assertEqual(result["directly_exposed_repositories_excluded"], 1)
        self.assertEqual(result["input_rows"], 3)
        for item in result["candidates"]:
            expected = hashlib.sha256((EXPOSURE["selection_seed"] + "\n" + item["instance_id"]).encode()).hexdigest()
            self.assertEqual(item["rank_sha256"], expected)
            self.assertEqual(item["lineage_review"], "required")
        self.assertFalse(result["forks_and_related_lineages_reviewed"])
        self.assertFalse(result["export_manifest_checks_passed"])
        self.assertFalse(result["source_origin_independently_attested"])

    def test_outcome_fields_and_duplicate_ids_fail_closed(self):
        with self.assertRaises(ValueError):
            rank([{"instance_id": "owner__repo-1", "repo": "owner/repo", "patch": "gold"}], EXPOSURE)
        with self.assertRaises(ValueError):
            rank([{"instance_id": "owner__repo-1", "repo": "owner/repo", "test_patch": "hidden"}], EXPOSURE)
        with self.assertRaises(ValueError):
            rank([{"instance_id": "owner__repo-1", "repo": "owner/repo"},
                  {"instance_id": "owner__repo-1", "repo": "owner/repo"}], EXPOSURE)
        with self.assertRaises(ValueError):
            rank([{"instance_id": "owner__repo-1", "repo": "owner/repo"},
                  {"instance_id": "OWNER__REPO-01", "repo": "OWNER/REPO"}], EXPOSURE)
        with self.assertRaises(ValueError):
            rank([{"instance_id": "owner__repo-1", "repo": "owner/repo",
                   "license": "MIT\n+ diff --git a/secret b/secret"}], EXPOSURE)
        with self.assertRaises(ValueError):
            rank([{"instance_id": "owner__repo-1", "repo": "owner/repo",
                   "language": "python\nsecret"}], EXPOSURE)

    def test_ambiguous_repo_and_public_output_fail_closed(self):
        with self.assertRaises(ValueError):
            canonical_repo("https://example.com/owner/repo")
        self.assertEqual(canonical_repo("https://github.com/Pallets/Click.git/"), "pallets/click")
        self.assertEqual(canonical_repo("https://github.com/pallets/Click.GIT"), "pallets/click")
        with self.assertRaises(ValueError):
            outside_repository(Path(__file__).resolve().parent / "candidate-output.json")

    @unittest.skipUnless(os.name == "posix", "private publication requires POSIX")
    def test_publish_never_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            destination = Path(directory) / "destination"
            source.write_text("new")
            destination.write_text("old")
            with self.assertRaises(FileExistsError):
                publish_no_replace(source, destination)
            self.assertEqual(destination.read_text(), "old")

    def test_missing_manifest_lineages_rejected(self):
        incomplete = {key: value for key, value in EXPOSURE.items() if key != "lineages"}
        with self.assertRaises(ValueError):
            rank([{"instance_id": "pallets__click-1", "repo": "pallets/click"}], incomplete)

    def test_public_exposure_excludes_all_documented_development_lineages(self):
        path = Path(__file__).resolve().parent.parent / "docs/research/data/2026-09-24-development-exposure.json"
        exposure = json.loads(path.read_text())
        repos = ["DmitrL-dev/SoLCodex", "pallets/click", "pypa/packaging",
                 "tobymao/sqlglot", "sympy/sympy", "elastic/synthetics",
                 "wtforms/wtforms", "webpack-contrib/copy-webpack-plugin",
                 "crawler-commons/crawler-commons", "dtolnay/cxx", "GradleUp/shadow",
                 "google-research/rrsi", "openai/codex",
                 "unreallabsai/unreal-agent", "NVlabs/SoL-Pi"]
        result = rank([{"instance_id": canonical_repo(repo).replace("/", "__") + "-" + str(i + 1), "repo": repo}
                       for i, repo in enumerate(repos)], exposure)
        self.assertEqual(result["directly_exposed_repositories_excluded"], len(repos))
        self.assertEqual(result["candidates"], [])


if __name__ == "__main__":
    unittest.main()
