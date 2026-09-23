#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import hashlib
import os
import stat
import subprocess
import tempfile
import time
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_repository.py"
BENCHMARK = ROOT / "scripts" / "benchmark_receipts.py"
BUILD_RELEASE = ROOT / "scripts" / "build_release.sh"


class ReleaseToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def fixture_repo(self) -> Path:
        repo = self.temp / "repo"
        self.write_json(
            repo / "plugins/sol-codex/.codex-plugin/plugin.json",
            {"name": "sol-codex", "version": "0.1.0", "skills": "./skills/", "hooks": "./hooks/hooks.json"},
        )
        self.write_json(repo / "plugins/sol-codex/hooks/hooks.json", {"hooks": {}})
        license_text = "MIT License\n\nCopyright (c) 2026 Spectorn\n"
        (repo / "LICENSE").write_text(license_text, encoding="utf-8")
        (repo / "plugins/sol-codex/LICENSE").write_text(license_text, encoding="utf-8")
        skill = repo / "plugins/sol-codex/skills/efficient-agent-loop/SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(
            "---\nname: efficient-agent-loop\ndescription: Focus verification evidence.\n---\n",
            encoding="utf-8",
        )
        self.write_json(
            repo / ".agents/plugins/marketplace.json",
            {
                "name": "sol-codex",
                "plugins": [
                    {
                        "name": "sol-codex",
                        "source": {"source": "local", "path": "./plugins/sol-codex"},
                        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                        "category": "Productivity",
                    }
                ],
            },
        )
        (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
        return repo

    def run_validator(self, repo: Path, archive: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = ["python3", str(VALIDATOR), "--root", str(repo)]
        if archive is not None:
            command.extend(["--archive", str(archive)])
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def zip_fixture(self, members: Dict[str, bytes]) -> Path:
        archive = self.temp / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            for name, content in members.items():
                handle.writestr(name, content)
        return archive

    def build_release(self) -> tuple[Path, subprocess.CompletedProcess[str]]:
        dist = self.temp / "dist"
        environment = os.environ.copy()
        environment.update({"DIST_DIR": str(dist), "PYTHONDONTWRITEBYTECODE": "1"})
        result = subprocess.run(
            ["bash", str(BUILD_RELEASE)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        archives = sorted(dist.glob("*.zip"))
        self.assertEqual(len(archives), 1, result.stderr)
        return archives[0], result

    def fake_codex(self, marketplaces: object) -> tuple[Path, Path]:
        executable = self.temp / "fake-codex"
        log = self.temp / "fake-codex.log"
        executable.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_CODEX_LOG\"\n"
            "if [ \"${1-} ${2-} ${3-}\" = 'plugin marketplace list' ]; then\n"
            "  printf '%s\\n' \"$FAKE_MARKETPLACES\"\n"
            "elif [ \"${1-} ${2-}\" = 'plugin add' ] && [ \"${FAKE_CODEX_FAIL_ADD-0}\" = '1' ]; then\n"
            "  exit 7\n"
            "else\n"
            "  printf '%s\\n' '{\"ok\":true}'\n"
            "fi\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        os.environ.pop("FAKE_CODEX_LOG", None)
        log.write_text("", encoding="utf-8")
        return executable, log

    def test_valid_minimal_repository_passes(self) -> None:
        result = self.run_validator(self.fixture_repo())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_identity_mismatch_fails(self) -> None:
        repo = self.fixture_repo()
        self.write_json(
            repo / "plugins/sol-codex/.codex-plugin/plugin.json",
            {"name": "wrong", "version": "0.1.0", "skills": "./skills/", "hooks": "./hooks/hooks.json"},
        )
        result = self.run_validator(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest name mismatch", result.stderr)

    def test_root_manifest_that_hides_hooks_is_rejected(self) -> None:
        repo = self.fixture_repo()
        self.write_json(repo / "plugins/sol-codex/plugin.json", {"name": "sol-codex", "version": "0.1.0"})
        result = self.run_validator(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root plugin manifest", result.stderr)

    def test_marketplace_path_and_policy_are_enforced(self) -> None:
        repo = self.fixture_repo()
        marketplace = repo / ".agents/plugins/marketplace.json"
        payload = json.loads(marketplace.read_text(encoding="utf-8"))
        payload["plugins"][0]["source"]["path"] = "../outside"
        payload["plugins"][0]["policy"].pop("authentication")
        self.write_json(marketplace, payload)
        result = self.run_validator(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("marketplace source path", result.stderr)
        self.assertIn("authentication policy", result.stderr)

    def test_broken_relative_markdown_link_fails(self) -> None:
        repo = self.fixture_repo()
        (repo / "README.md").write_text("[missing](docs/missing.md)\n", encoding="utf-8")
        result = self.run_validator(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broken relative link", result.stderr)

    def test_sensitive_markers_are_reported_without_echoing_values(self) -> None:
        repo = self.fixture_repo()
        secret_value = "sk-" + ("a" * 32)
        local_path = "/" + "Users/example/private"
        key_marker = (
            "-----BEGIN " + "PRIVATE KEY-----\n"
            + ("A" * 64)
            + "\n"
            + ("B" * 64)
            + "\n-----END "
            + "PRIVATE KEY-----"
        )
        (repo / "public.txt").write_text(
            f"{local_path}\n{secret_value}\n{key_marker}\n",
            encoding="utf-8",
        )
        result = self.run_validator(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local absolute path", result.stderr)
        self.assertIn("credential marker", result.stderr)
        self.assertIn("private-key marker", result.stderr)
        self.assertNotIn(secret_value, result.stderr)

    def test_prohibited_state_and_observation_paths_fail(self) -> None:
        repo = self.fixture_repo()
        path = repo / "plugin-data/observations/session/obs_deadbeef.txt"
        path.parent.mkdir(parents=True)
        path.write_text("local output", encoding="utf-8")
        result = self.run_validator(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prohibited repository path", result.stderr)

    def test_plugin_runtime_state_directory_fails(self) -> None:
        repo = self.fixture_repo()
        path = repo / "plugins/sol-codex/state/session.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}\n", encoding="utf-8")
        result = self.run_validator(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plugins/sol-codex/state/session.json", result.stderr)
        self.assertIn("prohibited repository path", result.stderr)

    def test_known_synthetic_secret_fixtures_do_not_block_public_tests(self) -> None:
        repo = self.fixture_repo()
        fake_token = "sk-abcdefghijklmnopqrstuvwxyz123456"
        source = (
            f'fake_token = "{fake_token}"\n'
            'begin = "-----BEGIN PRIVATE KEY-----"\n'
            'end = "-----END PRIVATE KEY-----"\n'
        )
        (repo / "test_fixture.py").write_text(source, encoding="utf-8")
        result = self.run_validator(repo)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_archive_rejects_python_cache_and_local_paths(self) -> None:
        archive = self.zip_fixture(
            {
                "plugins/sol-codex/__pycache__/hook.pyc": b"x",
                "plugins/sol-codex/README.txt": b"/" + b"Users/example/private",
            }
        )
        result = self.run_validator(self.fixture_repo(), archive)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prohibited archive member", result.stderr)
        self.assertIn("local absolute path in archive", result.stderr)

    def test_archive_rejects_macos_metadata(self) -> None:
        archive = self.zip_fixture({"__MACOSX/._plugin.json": b"x"})
        result = self.run_validator(self.fixture_repo(), archive)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prohibited archive member", result.stderr)

    def test_archive_rejects_unexpected_process_and_runtime_state(self) -> None:
        archive = self.zip_fixture(
            {
                "sol-codex-portable-test/.superpowers/session.json": b"{}",
                "sol-codex-portable-test/docs/superpowers/review.md": b"review",
                "sol-codex-portable-test/plugins/sol-codex/state/session.json": b"{}",
            }
        )
        result = self.run_validator(self.fixture_repo(), archive)
        self.assertNotEqual(result.returncode, 0)
        for member in (
            ".superpowers/session.json",
            "docs/superpowers/review.md",
            "plugins/sol-codex/state/session.json",
        ):
            self.assertIn(member, result.stderr)
        self.assertIn("prohibited archive member", result.stderr)

    def test_archive_rejects_symlink_member_type(self) -> None:
        archive, built = self.build_release()
        self.assertEqual(built.returncode, 0, built.stderr)
        rewritten = self.temp / "symlink-member.zip"
        target = "plugins/sol-codex/scripts/sol_hook.py"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(rewritten, "w") as destination:
            for info in source.infolist():
                cloned = copy.copy(info)
                content = source.read(info)
                if info.filename.endswith(target):
                    cloned.create_system = 3
                    cloned.external_attr = (stat.S_IFLNK | 0o777) << 16
                    content = b"../target"
                destination.writestr(cloned, content)
        result = self.run_validator(ROOT, rewritten)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prohibited archive member type", result.stderr)

    def test_benchmark_exercises_thresholds_and_safe_fallbacks(self) -> None:
        result = subprocess.run(
            ["python3", str(BENCHMARK)], text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 1)
        cases = {case["name"]: case for case in payload["cases"]}
        self.assertTrue(cases["astra-5000"]["packed"])
        self.assertTrue(cases["default-13000"]["packed"])
        self.assertFalse(cases["unknown-exit-status"]["packed"])
        self.assertTrue(cases["plain-string-unknown"]["packed"])
        self.assertTrue(cases["plain-string-unknown"]["status_unknown"])
        self.assertTrue(cases["credential-redaction"]["redacted"])
        for name in ("astra-5000", "default-13000", "plain-string-unknown", "credential-redaction"):
            self.assertGreater(cases[name]["saved_bytes"], 0)
            self.assertLess(cases[name]["receipt_bytes"], cases[name]["source_bytes"])

    def test_measurement_snapshot_is_aggregate_only_and_balanced(self) -> None:
        snapshot_path = ROOT / "docs/measurements/2026-09-22-packing-report.json"
        self.assertTrue(snapshot_path.exists(), "measurement snapshot missing")
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "snapshot_date",
                "unit",
                "totals",
                "gpt-6-astra",
                "gpt-5.6-sol",
                "other_models",
                "legacy_unattributed",
                "formulas",
            },
        )
        metric_keys = {"packed_observations", "source_bytes", "receipt_bytes", "saved_bytes"}
        groups = ("totals", "gpt-6-astra", "gpt-5.6-sol", "other_models", "legacy_unattributed")
        for group in groups:
            metrics = payload[group]
            self.assertEqual(set(metrics), metric_keys)
            self.assertEqual(metrics["saved_bytes"], metrics["source_bytes"] - metrics["receipt_bytes"])
        for key in metric_keys:
            attributed = (
                payload["gpt-6-astra"][key]
                + payload["gpt-5.6-sol"][key]
                + payload["other_models"][key]
                + payload["legacy_unattributed"][key]
            )
            self.assertEqual(payload["totals"][key], attributed)
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("session_id", serialized)
        self.assertNotIn("transcript", serialized.lower())

    def test_architecture_assets_are_accessible_and_synchronized(self) -> None:
        svg_path = ROOT / "assets/architecture.svg"
        mermaid_path = ROOT / "assets/architecture.mmd"
        self.assertTrue(svg_path.exists(), "architecture SVG missing")
        self.assertTrue(mermaid_path.exists(), "architecture Mermaid source missing")
        tree = ET.parse(svg_path)
        root = tree.getroot()
        namespace = "{http://www.w3.org/2000/svg}"
        self.assertIsNotNone(root.find(f"{namespace}title"))
        self.assertIsNotNone(root.find(f"{namespace}desc"))
        svg_text = svg_path.read_text(encoding="utf-8")
        for label in (
            "PostToolUse",
            "4 KiB Astra",
            "6 KiB default",
            "exact local artifact",
            "sanitized bounded receipt",
            "per-model report",
        ):
            self.assertIn(label, svg_text)
        self.assertNotIn("data:image", svg_text)
        self.assertNotIn("base64", svg_text)
        self.assertNotIn('d="M1199 208 V267 H1288"', svg_text)
        self.assertIn('d="M1284 188 H1308 V238"', svg_text)
        mermaid = mermaid_path.read_text(encoding="utf-8")
        self.assertIn("subgraph runtime", mermaid)
        self.assertIn("subgraph verification_debt", mermaid)
        self.assertIn("PreCompact", mermaid)
        self.assertIn("PostCompact", mermaid)

    def test_public_readmes_cover_installation_limits_and_attribution(self) -> None:
        required_literals = (
            "codex plugin marketplace add DmitrL-dev/SoLCodex",
            "codex plugin add sol-codex@sol-codex",
            "codex plugin remove sol-codex@sol-codex",
            "/hooks",
            "--report",
            "SOL_CODEX_ASTRA_PACK_THRESHOLD_BYTES",
            "SOL_CODEX_PACK_THRESHOLD_BYTES",
            "Windows",
            "docs/security.md",
            "docs/troubleshooting.md",
            "assets/architecture.svg",
            "NVlabs/SoL-Pi",
            "code-mode",
        )
        readmes = {
            "README.md": "does not select a model",
            "README.ru.md": "не выбирает модель",
        }
        for relative, model_disclaimer in readmes.items():
            path = ROOT / relative
            self.assertTrue(path.exists(), f"{relative} missing")
            text = path.read_text(encoding="utf-8")
            for literal in required_literals:
                self.assertIn(literal, text, f"{relative} missing {literal!r}")
            self.assertIn(model_disclaimer, text)

    def test_validator_rejects_unsupported_public_claims(self) -> None:
        repo = self.fixture_repo()
        (repo / "README.md").write_text(
            "This plugin automatically selects the best model and reduces tokens.\n",
            encoding="utf-8",
        )
        result = self.run_validator(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported public claim", result.stderr)

    def test_release_archive_is_reproducible_minimal_and_checksummed(self) -> None:
        archive, first = self.build_release()
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = archive.read_bytes()
        rebuilt, second = self.build_release()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first_bytes, rebuilt.read_bytes())

        version = json.loads(
            (ROOT / "plugins/sol-codex/.codex-plugin/plugin.json").read_text(encoding="utf-8")
        )["version"]
        expected_prefix = f"sol-codex-portable-{version.replace('+', '-')}/"
        with zipfile.ZipFile(archive) as handle:
            infos = handle.infolist()
            names = [info.filename for info in infos]
            self.assertEqual(names, sorted(names))
            self.assertIn(expected_prefix + ".agents/plugins/marketplace.json", names)
            self.assertNotIn(expected_prefix + "plugins/sol-codex/plugin.json", names)
            self.assertIn(expected_prefix + "plugins/sol-codex/.codex-plugin/plugin.json", names)
            self.assertIn(expected_prefix + "install.sh", names)
            self.assertIn(expected_prefix + "README.md", names)
            self.assertIn(expected_prefix + "LICENSE", names)
            self.assertIn(expected_prefix + "plugins/sol-codex/LICENSE", names)
            self.assertIn(expected_prefix + "plugins/sol-codex/scripts/sol_hook.cmd", names)
            portable_readme = handle.read(expected_prefix + "README.md").decode("utf-8")
            self.assertIn(version, portable_readme)
            self.assertIn("bash install.sh", portable_readme)
            self.assertIn("sol-codex@sol-codex-portable", portable_readme)
            root_license = handle.read(expected_prefix + "LICENSE")
            plugin_license = handle.read(expected_prefix + "plugins/sol-codex/LICENSE")
            self.assertEqual(root_license, plugin_license)
            self.assertIn(b"MIT License", root_license)
            self.assertIn(b"Copyright (c) 2026 Spectorn", root_license)
            self.assertFalse(any("__pycache__" in name or "/._" in name for name in names))
            self.assertFalse(any("docs/superpowers" in name or "/scripts/test_" in name for name in names))
            for info in infos:
                mode = (info.external_attr >> 16) & 0o777
                expected_mode = 0o755 if info.is_dir() or info.filename.endswith("/install.sh") else 0o644
                self.assertEqual(mode, expected_mode, info.filename)

        checksum = archive.with_suffix(".zip.sha256")
        fields = checksum.read_text(encoding="utf-8").strip().split()
        self.assertEqual(fields, [hashlib.sha256(first_bytes).hexdigest(), archive.name])

    def test_portable_installer_validates_identity_and_refuses_root_collision(self) -> None:
        archive, built = self.build_release()
        self.assertEqual(built.returncode, 0, built.stderr)
        extracted = self.temp / "extracted"
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(extracted)
        package = next(extracted.iterdir())
        fake_codex, log = self.fake_codex({"marketplaces": []})
        environment = os.environ.copy()
        environment.update(
            {
                "CODEX_BIN": str(fake_codex),
                "FAKE_CODEX_LOG": str(log),
                "FAKE_MARKETPLACES": json.dumps({"marketplaces": []}),
                "HOME": str(self.temp / "home"),
                "XDG_DATA_HOME": str(self.temp / "data"),
            }
        )
        installed = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        install_root = self.temp / "data/sol-codex-portable"
        self.assertFalse((install_root / "plugins/sol-codex/plugin.json").exists())
        self.assertTrue((install_root / "plugins/sol-codex/.codex-plugin/plugin.json").is_file())
        calls = log.read_text(encoding="utf-8")
        self.assertIn(f"plugin marketplace add {install_root}", calls)
        self.assertIn("plugin add sol-codex@sol-codex-portable", calls)

        collision_data = self.temp / "collision-data"
        environment.update(
            {
                "XDG_DATA_HOME": str(collision_data),
                "FAKE_MARKETPLACES": json.dumps(
                    {"marketplaces": [{"name": "sol-codex-portable", "root": "/different/root"}]}
                ),
            }
        )
        collision = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(collision.returncode, 0)
        self.assertIn("different root", collision.stderr)
        self.assertFalse((collision_data / "sol-codex-portable").exists())

        foreign_data = self.temp / "foreign-data"
        foreign_root = foreign_data / "sol-codex-portable"
        foreign_root.mkdir(parents=True)
        foreign_marker = foreign_root / "unrelated.txt"
        foreign_marker.write_text("must survive", encoding="utf-8")
        foreign = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env={
                **environment,
                "XDG_DATA_HOME": str(foreign_data),
                "FAKE_MARKETPLACES": json.dumps({"marketplaces": []}),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(foreign.returncode, 0)
        self.assertIn("unowned install root", foreign.stderr)
        self.assertEqual(foreign_marker.read_text(encoding="utf-8"), "must survive")

        rollback_data = self.temp / "rollback-data"
        prior_root = rollback_data / "sol-codex-portable"
        prior_root.mkdir(parents=True)
        (prior_root / "prior-installation").write_text("keep", encoding="utf-8")
        rollback_environment = {
            **environment,
            "XDG_DATA_HOME": str(rollback_data),
            "FAKE_CODEX_FAIL_ADD": "1",
            "FAKE_MARKETPLACES": json.dumps(
                {"marketplaces": [{"name": "sol-codex-portable", "root": str(prior_root)}]}
            ),
        }
        rollback = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env=rollback_environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(rollback.returncode, 7)
        self.assertEqual((prior_root / "prior-installation").read_text(encoding="utf-8"), "keep")

        compat = package / "plugins/sol-codex/.codex-plugin/plugin.json"
        payload = json.loads(compat.read_text(encoding="utf-8"))
        payload["version"] = "9.9.9"
        compat.write_text(json.dumps(payload), encoding="utf-8")
        invalid = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env={**environment, "XDG_DATA_HOME": str(self.temp / "invalid-data"), "FAKE_MARKETPLACES": json.dumps({"marketplaces": []})},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("identity/version validation failed", invalid.stderr)

    def test_portable_installer_restores_existing_install_when_backup_move_is_interrupted(self) -> None:
        archive, built = self.build_release()
        self.assertEqual(built.returncode, 0, built.stderr)
        extracted = self.temp / "interrupted-extracted"
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(extracted)
        package = next(extracted.iterdir())

        fake_codex, log = self.fake_codex({"marketplaces": []})
        data = self.temp / "interrupted-data"
        install_root = data / "sol-codex-portable"
        install_root.mkdir(parents=True)
        prior_marker = install_root / "prior-installation"
        prior_marker.write_text("keep", encoding="utf-8")

        fake_bin = self.temp / "interrupt-bin"
        fake_bin.mkdir()
        interrupt_flag = self.temp / "mv-interrupted"
        fake_mv = fake_bin / "mv"
        fake_mv.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "/bin/mv \"$@\"\n"
            "if [ ! -e \"$MV_INTERRUPT_FLAG\" ]; then\n"
            "  : > \"$MV_INTERRUPT_FLAG\"\n"
            "  kill -TERM \"$PPID\"\n"
            "fi\n",
            encoding="utf-8",
        )
        fake_mv.chmod(0o755)

        environment = os.environ.copy()
        environment.update(
            {
                "CODEX_BIN": str(fake_codex),
                "FAKE_CODEX_LOG": str(log),
                "FAKE_MARKETPLACES": json.dumps(
                    {"marketplaces": [{"name": "sol-codex-portable", "root": str(install_root)}]}
                ),
                "HOME": str(self.temp / "home"),
                "MV_INTERRUPT_FLAG": str(interrupt_flag),
                "PATH": f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}",
                "XDG_DATA_HOME": str(data),
            }
        )
        interrupted = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(interrupted.returncode, 0)
        self.assertTrue(prior_marker.is_file(), interrupted.stderr)
        self.assertEqual(prior_marker.read_text(encoding="utf-8"), "keep")

    def test_portable_installer_preserves_preexisting_target_before_replacement(self) -> None:
        archive, built = self.build_release()
        self.assertEqual(built.returncode, 0, built.stderr)
        extracted = self.temp / "pre-replacement-extracted"
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(extracted)
        package = next(extracted.iterdir())
        fake_codex, log = self.fake_codex({"marketplaces": []})

        def environment(data: Path, install_root: Path, fake_bin: Path | None = None) -> dict[str, str]:
            result = os.environ.copy()
            result.update(
                {
                    "CODEX_BIN": str(fake_codex),
                    "FAKE_CODEX_LOG": str(log),
                    "FAKE_MARKETPLACES": json.dumps(
                        {"marketplaces": [{"name": "sol-codex-portable", "root": str(install_root)}]}
                    ),
                    "HOME": str(self.temp / "home"),
                    "XDG_DATA_HOME": str(data),
                }
            )
            if fake_bin is not None:
                result["PATH"] = f"{fake_bin}{os.pathsep}{result.get('PATH', '')}"
            return result

        regular_data = self.temp / "regular-file-data"
        regular_data.mkdir()
        regular_root = regular_data / "sol-codex-portable"
        regular_root.write_text("must survive", encoding="utf-8")
        regular = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env=environment(regular_data, regular_root),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(regular.returncode, 0)
        self.assertIn("non-directory install root", regular.stderr)
        self.assertTrue(regular_root.is_file())
        self.assertEqual(regular_root.read_text(encoding="utf-8"), "must survive")

        fail_data = self.temp / "copy-failure-data"
        fail_root = fail_data / "sol-codex-portable"
        fail_root.mkdir(parents=True)
        fail_marker = fail_root / "prior-installation"
        fail_marker.write_text("keep", encoding="utf-8")
        fail_bin = self.temp / "copy-failure-bin"
        fail_bin.mkdir()
        fail_cp = fail_bin / "cp"
        fail_cp.write_text("#!/usr/bin/env bash\nexit 23\n", encoding="utf-8")
        fail_cp.chmod(0o755)
        failed_copy = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env=environment(fail_data, fail_root, fail_bin),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(failed_copy.returncode, 23)
        self.assertTrue(fail_marker.is_file(), failed_copy.stderr)
        self.assertEqual(fail_marker.read_text(encoding="utf-8"), "keep")

        interrupt_data = self.temp / "copy-interrupt-data"
        interrupt_root = interrupt_data / "sol-codex-portable"
        interrupt_root.mkdir(parents=True)
        interrupt_marker = interrupt_root / "prior-installation"
        interrupt_marker.write_text("keep", encoding="utf-8")
        interrupt_bin = self.temp / "copy-interrupt-bin"
        interrupt_bin.mkdir()
        interrupt_cp = interrupt_bin / "cp"
        interrupt_cp.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "/bin/cp \"$@\"\n"
            "kill -TERM \"$PPID\"\n",
            encoding="utf-8",
        )
        interrupt_cp.chmod(0o755)
        interrupted_copy = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env=environment(interrupt_data, interrupt_root, interrupt_bin),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(interrupted_copy.returncode, 0)
        self.assertTrue(interrupt_marker.is_file(), interrupted_copy.stderr)
        self.assertEqual(interrupt_marker.read_text(encoding="utf-8"), "keep")

    def test_portable_installer_rejects_target_identity_swap_during_staging(self) -> None:
        archive, built = self.build_release()
        self.assertEqual(built.returncode, 0, built.stderr)
        extracted = self.temp / "identity-swap-extracted"
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(extracted)
        package = next(extracted.iterdir())
        fake_codex, log = self.fake_codex({"marketplaces": []})

        data = self.temp / "identity-swap-data"
        install_root = data / "sol-codex-portable"
        install_root.mkdir(parents=True)
        (install_root / "prior-installation").write_text("keep", encoding="utf-8")
        swapped_root = data / "swapped-original"

        fake_bin = self.temp / "identity-swap-bin"
        fake_bin.mkdir()
        swap_flag = self.temp / "identity-swapped"
        fake_cp = fake_bin / "cp"
        fake_cp.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "/bin/cp \"$@\"\n"
            "if [ ! -e \"$SWAP_FLAG\" ]; then\n"
            "  /bin/mv \"$INSTALL_ROOT\" \"$SWAPPED_ROOT\"\n"
            "  /bin/mkdir \"$INSTALL_ROOT\"\n"
            "  printf '%s\\n' foreign > \"$INSTALL_ROOT/foreign-marker\"\n"
            "  : > \"$SWAP_FLAG\"\n"
            "fi\n",
            encoding="utf-8",
        )
        fake_cp.chmod(0o755)

        environment = os.environ.copy()
        environment.update(
            {
                "CODEX_BIN": str(fake_codex),
                "FAKE_CODEX_LOG": str(log),
                "FAKE_MARKETPLACES": json.dumps(
                    {"marketplaces": [{"name": "sol-codex-portable", "root": str(install_root)}]}
                ),
                "HOME": str(self.temp / "home"),
                "INSTALL_ROOT": str(install_root),
                "PATH": f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}",
                "SWAPPED_ROOT": str(swapped_root),
                "SWAP_FLAG": str(swap_flag),
                "XDG_DATA_HOME": str(data),
            }
        )
        result = subprocess.run(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed during staging", result.stderr)
        self.assertEqual((install_root / "foreign-marker").read_text(encoding="utf-8"), "foreign\n")
        self.assertEqual((swapped_root / "prior-installation").read_text(encoding="utf-8"), "keep")

    def test_portable_installer_serializes_concurrent_attempts(self) -> None:
        archive, built = self.build_release()
        self.assertEqual(built.returncode, 0, built.stderr)
        extracted = self.temp / "concurrent-extracted"
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(extracted)
        package = next(extracted.iterdir())
        fake_codex, log = self.fake_codex({"marketplaces": []})

        data = self.temp / "concurrent-data"
        install_root = data / "sol-codex-portable"
        fake_bin = self.temp / "concurrent-bin"
        fake_bin.mkdir()
        hold_dir = self.temp / "copy-holder"
        started = self.temp / "copy-started"
        release = self.temp / "copy-release"
        contender = self.temp / "concurrent-copy-reached"
        fake_cp = fake_bin / "cp"
        fake_cp.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "if /bin/mkdir \"$HOLD_DIR\" 2>/dev/null; then\n"
            "  : > \"$COPY_STARTED\"\n"
            "  while [ ! -e \"$COPY_RELEASE\" ]; do sleep 0.02; done\n"
            "elif [ ! -e \"$COPY_RELEASE\" ]; then\n"
            "  : > \"$CONTENDER_REACHED\"\n"
            "fi\n"
            "/bin/cp \"$@\"\n",
            encoding="utf-8",
        )
        fake_cp.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "CODEX_BIN": str(fake_codex),
                "COPY_RELEASE": str(release),
                "COPY_STARTED": str(started),
                "CONTENDER_REACHED": str(contender),
                "FAKE_CODEX_LOG": str(log),
                "FAKE_MARKETPLACES": json.dumps(
                    {"marketplaces": [{"name": "sol-codex-portable", "root": str(install_root)}]}
                ),
                "HOLD_DIR": str(hold_dir),
                "HOME": str(self.temp / "home"),
                "PATH": f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}",
                "XDG_DATA_HOME": str(data),
            }
        )
        first = subprocess.Popen(
            ["bash", str(package / "install.sh")],
            cwd=package,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        second: subprocess.Popen[str] | None = None
        try:
            deadline = time.monotonic() + 5
            while not started.exists() and time.monotonic() < deadline:
                if first.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertTrue(started.exists(), "first installer did not reach the controlled copy")
            second = subprocess.Popen(
                ["bash", str(package / "install.sh")],
                cwd=package,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.25)
            self.assertFalse(contender.exists(), "second installer reached staging before the lock released")
        finally:
            release.touch()
        first_stdout, first_stderr = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, first_stderr or first_stdout)
        self.assertIsNotNone(second)
        second_stdout, second_stderr = second.communicate(timeout=10)
        self.assertEqual(second.returncode, 0, second_stderr or second_stdout)

    def test_plugin_structure_and_skill_frontmatter_are_enforced(self) -> None:
        repo = self.fixture_repo()
        (repo / "plugins/sol-codex/hooks/hooks.json").unlink()
        (repo / "plugins/sol-codex/LICENSE").unlink()
        skill = repo / "plugins/sol-codex/skills/efficient-agent-loop/SKILL.md"
        skill.write_text("---\nname: efficient-agent-loop\n---\n", encoding="utf-8")
        result = self.run_validator(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hook configuration missing", result.stderr)
        self.assertIn("plugin license", result.stderr)
        self.assertIn("skill frontmatter", result.stderr)


if __name__ == "__main__":
    unittest.main()
