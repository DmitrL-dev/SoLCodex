"""Exercise the clean-parent evaluator, including the missing-.git regression."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

try:
    from scripts.evaluate_development_patch import evaluate
except ModuleNotFoundError:
    from evaluate_development_patch import evaluate


class DevelopmentPatchEvaluationTests(unittest.TestCase):
    def test_patch_is_applied_to_initialized_clean_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent"
            parent.mkdir()
            (parent / "value.txt").write_text("old\n")
            working = root / "working"
            shutil.copytree(parent, working)
            subprocess.run(["git", "init", "-q"], cwd=working, check=True)
            subprocess.run(["git", "add", "-A"], cwd=working, check=True)
            subprocess.run(["git", "-c", "user.name=Experiment",
                            "-c", "user.email=experiment@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "-qm", "parent"],
                           cwd=working, check=True)
            (working / "value.txt").write_text("new\n")
            patch = subprocess.check_output(["git", "diff", "--binary"], cwd=working)
            verifier = root / "verify.py"
            verifier.write_text("import pathlib,sys\n"
                                "p=pathlib.Path(sys.argv[1])/'value.txt'\n"
                                "print('PASS' if p.read_text()=='new\\n' else 'FAIL')\n"
                                "sys.exit(0 if p.read_text()=='new\\n' else 1)\n")
            import sys
            result = evaluate(parent, patch, verifier, sys.executable)
            self.assertTrue(result["applies"])
            self.assertEqual(result["diff_check_exit"], 0)
            self.assertEqual(result["verifier_exit"], 0)
            self.assertIsNone(result["verifier_summary"])

    def test_rejects_empty_patch_and_parent_with_git_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent"
            parent.mkdir()
            verifier = root / "verify.py"
            verifier.write_text("pass\n")
            import sys
            with self.assertRaises(ValueError):
                evaluate(parent, b"", verifier, sys.executable)
            (parent / ".git").mkdir()
            with self.assertRaises(ValueError):
                evaluate(parent, b"patch", verifier, sys.executable)


if __name__ == "__main__":
    unittest.main()
