"""Verify column projection and provenance checks on a synthetic Parquet file."""
import hashlib
from importlib.util import find_spec
import json
from pathlib import Path
import tempfile
import unittest

try:
    from scripts.export_swe_rebench_metadata import export
    from scripts.rank_confirm_candidates import REVISION, SOURCE_URI, verify_export_manifest
except ModuleNotFoundError:
    from export_swe_rebench_metadata import export
    from rank_confirm_candidates import REVISION, SOURCE_URI, verify_export_manifest


@unittest.skipUnless(find_spec("duckdb"), "duckdb is not installed")
class MetadataExportTests(unittest.TestCase):
    def test_projection_excludes_gold_and_manifest_detects_tampering(self):
        import duckdb
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.parquet"
            output = root / "metadata.jsonl"
            con = duckdb.connect()
            try:
                con.execute("CREATE TABLE sample (instance_id VARCHAR, repo VARCHAR, "
                            "language VARCHAR, license VARCHAR, patch VARCHAR, test_patch VARCHAR)")
                con.execute("INSERT INTO sample VALUES ('owner__repo-1', 'owner/repo', "
                            "'python', 'MIT', 'SYNTHETIC_GOLD', 'SYNTHETIC_TEST')")
                con.execute("COPY sample TO '" + str(source).replace("'", "''") + "' (FORMAT parquet)")
            finally:
                con.close()
            self.assertEqual(export(str(source), output, root / "runtime", 1), 1)
            contents = output.read_bytes()
            self.assertNotIn(b"SYNTHETIC_GOLD", contents)
            self.assertNotIn(b"SYNTHETIC_TEST", contents)
            self.assertEqual(set(json.loads(contents)[key] for key in ("instance_id", "repo")),
                             {"owner__repo-1", "owner/repo"})
            manifest = {"schema": "solcodex.metadata-export.v1",
                        "source_uri": SOURCE_URI, "source_revision": REVISION,
                        "split": "train", "projection": ["instance_id", "repo", "language", "license"],
                        "source_rows": 1, "export_rows": 1,
                        "metadata_sha256": hashlib.sha256(contents).hexdigest(),
                        "exporter_sha256": hashlib.sha256(
                            (Path(__file__).resolve().parent / "export_swe_rebench_metadata.py").read_bytes()).hexdigest(),
                        "duckdb_version": "1.3.2"}
            verify_export_manifest(manifest, contents, 1, expected_rows=1)
            with self.assertRaises(ValueError):
                verify_export_manifest(dict(manifest, metadata_sha256="0" * 64),
                                       contents, 1, expected_rows=1)
            with self.assertRaises(ValueError):
                verify_export_manifest(dict(manifest, source_rows=2),
                                       contents, 1, expected_rows=1)
            with self.assertRaises(ValueError):
                verify_export_manifest(dict(manifest, source_rows=2, export_rows=1),
                                       contents, 1, expected_rows=2)

    def test_existing_runtime_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            with self.assertRaises(FileExistsError):
                export(str(root / "unused.parquet"), root / "unused.jsonl", runtime, 1)


if __name__ == "__main__":
    unittest.main()
