#!/usr/bin/env python3
"""Validate public SoL Codex repository and release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence
from urllib.parse import unquote


PLUGIN_NAME = "sol-codex"
PLUGIN_PATH = "./plugins/sol-codex"
IGNORED_PREFIXES = (".git/", ".superpowers/", "docs/superpowers/", "dist/")
PROHIBITED_PARTS = {
    "__pycache__", "__MACOSX", "plugin-data", "observations", "state", "transcripts",
}
PROHIBITED_SUFFIXES = (".pyc", ".pyo")
ARCHIVE_ROOT_RE = re.compile(r"^sol-codex-portable-[A-Za-z0-9][A-Za-z0-9._+-]*$")
ARCHIVE_ALLOWED_FILES = frozenset(
    {
        ".agents/plugins/marketplace.json",
        "LICENSE",
        "README.md",
        "install.sh",
        "plugins/sol-codex/.codex-plugin/plugin.json",
        "plugins/sol-codex/LICENSE",
        "plugins/sol-codex/hooks/hooks.json",
        "plugins/sol-codex/scripts/sol_hook.py",
        "plugins/sol-codex/scripts/sol_bootstrap.py",
        "plugins/sol-codex/scripts/sol_hook.cmd",
        "plugins/sol-codex/skills/efficient-agent-loop/SKILL.md",
        "plugins/sol-codex/skills/efficient-agent-loop/agents/openai.yaml",
    }
)
ARCHIVE_ALLOWED_DIRECTORIES = frozenset(
    parent.as_posix()
    for filename in ARCHIVE_ALLOWED_FILES
    for parent in PurePosixPath(filename).parents
    if parent.as_posix() != "."
)
LOCAL_PATH_RE = re.compile(r"/(?:Users|home)/[^/\s]+/")
# Recorded measurement evidence that already contains local paths. Most files
# are pinned by SHA-256 from other tracked results and rechecked by reducers,
# so rewriting them would break the evidence chain. The exemption covers only
# the local-path check and lapses as soon as a file's bytes change.
FROZEN_LOCAL_PATH_EVIDENCE = {
    "docs/measurements/data/2026-09-25-click-source-only-v3-normal-cli-exploratory-trace.jsonl":
        "fd75a0132616931730be0ba573bf1adf9d67ad8634759696418df353ce98dc4e",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-negative-trace.jsonl":
        "108c164043a3467aa80ec4139054e68ad60a6bb58400ddb79b27c4f03935f8a0",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-positive-path-trace.jsonl":
        "3a69e86077450b1e24396d599d205a166188e31e73b45b41f1afed8995156ff1",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-integrated-wrong_cleanup_uncertain-trace.jsonl":
        "ff5d25c140b5300389ae0d7c5385ad1491c8e2696b54fe3d428d2a08c3bc8fe6",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-integrated-wrong_complete-trace.jsonl":
        "fcf7a11064aaa2e2bc0ea8ebd61864377b1c6cbac21ae6c8fea59b464ff03630",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-negative-trace.jsonl":
        "9592f468e648ef5c1debedb8ffd0ead8965a1fe916dfd40e7142d27b1bc3f639",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-01-b1-packaging-verbose-trace.jsonl":
        "d2624a3c2164b392207a00819ae46bf1caa8bf49468ca962917488c108ed7191",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-02-b1-packaging-quiet-trace.jsonl":
        "031771f6c5c6f7dd8bd2aa393161309ac96ce50ca195cf079667bad9ebaad9bd",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-03-b1-click-quiet-trace.jsonl":
        "39558704ea89bf61ebca93586d05486030788bd620e43b26dcf72ba064d5eb84",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-04-b1-click-verbose-trace.jsonl":
        "da03be3444bc4bee62cb9848d6e4e59d308833c40e72bbde865cf20729eca60b",
    "docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-positive-trace.jsonl":
        "cfec1cf9a0394c2a656e75676e9862602601a795a8457820fcaa6c74ced5009d",
    "docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v4-candidate.json":
        "e3f3203c07a199e47a3ccc261d4317352bacb703dd2a3288fddeab7826291f68",
    "docs/research/data/2026-09-25-quiet-variance-bundle-v3-integrated-expectations.json":
        "878f41c2521aa4551593cfc8797b8b78c1fb0c8ef7d1af31fbfcb329a1bb481e",
}
CREDENTIAL_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
KNOWN_SYNTHETIC_TOKENS = {"sk-abcdefghijklmnopqrstuvwxyz123456"}
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----\r?\n"
    r"(?:[A-Za-z0-9+/=]{16,}\r?\n){2,}"
    r"-----END (?P=label)-----"
)
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
UNSUPPORTED_PUBLIC_CLAIM_PATTERNS = (
    re.compile(r"\b(?:reduce|save|cut)[sd]?\s+(?:tokens?|costs?|money|quota)\b", re.I),
    re.compile(r"\bautomat(?:ic|ically)\w*\s+(?:select|switch)\w*\s+(?:the\s+)?(?:best\s+)?models?\b", re.I),
    re.compile(r"\bis\s+(?:an\s+)?official\s+(?:NVIDIA|OpenAI)\b", re.I),
    re.compile(r"\bis\s+(?:a\s+)?fork\s+of\s+(?:NVIDIA(?:'s)?\s+)?SoL-Pi\b", re.I),
)


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def public_relative_paths(repo: Path) -> Iterable[Path]:
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(repo)
        rendered = relative.as_posix()
        # In a linked worktree `.git` is a file that names the main checkout.
        if rendered in {".DS_Store", ".git"} or rendered.startswith(IGNORED_PREFIXES):
            continue
        yield relative


def validate_manifest_identity(repo: Path) -> list[str]:
    compat = load_json(repo / "plugins/sol-codex/.codex-plugin/plugin.json")
    marketplace = load_json(repo / ".agents/plugins/marketplace.json")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
        return ["marketplace must contain exactly one plugin object"]
    entry = plugins[0]
    errors: list[str] = []
    if compat.get("name") != PLUGIN_NAME or entry.get("name") != compat.get("name"):
        errors.append("manifest name mismatch")
    if not isinstance(compat.get("version"), str) or not compat["version"]:
        errors.append("plugin manifest version missing")
    if (repo / "plugins/sol-codex/plugin.json").exists():
        errors.append("root plugin manifest hides hooks in Codex; use .codex-plugin/plugin.json only")
    return errors


def validate_marketplace(repo: Path) -> list[str]:
    marketplace = load_json(repo / ".agents/plugins/marketplace.json")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
        return ["marketplace must contain exactly one plugin object"]
    entry = plugins[0]
    errors: list[str] = []
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "local" or source.get("path") != PLUGIN_PATH:
        errors.append(f"marketplace source path must be {PLUGIN_PATH!r} with source 'local'")
    policy = entry.get("policy")
    if not isinstance(policy, dict) or policy.get("installation") != "AVAILABLE":
        errors.append("marketplace installation policy must be AVAILABLE")
    if not isinstance(policy, dict) or policy.get("authentication") != "ON_INSTALL":
        errors.append("marketplace authentication policy must be ON_INSTALL")
    if entry.get("category") != "Productivity":
        errors.append("marketplace category must be Productivity")
    return errors


def plugin_relative_path(plugin_root: Path, raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = (plugin_root / raw).resolve()
    try:
        candidate.relative_to(plugin_root.resolve())
    except ValueError:
        return None
    return candidate


def skill_frontmatter(path: Path) -> dict[str, str] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return None
    header = text[4:].split("\n---\n", 1)[0]
    values: dict[str, str] = {}
    for line in header.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip()
    return values


def validate_plugin_structure(repo: Path) -> list[str]:
    errors: list[str] = []
    plugin_root = repo / "plugins/sol-codex"
    compat = load_json(plugin_root / ".codex-plugin/plugin.json")

    root_license = repo / "LICENSE"
    plugin_license = plugin_root / "LICENSE"
    if not root_license.is_file() or not plugin_license.is_file():
        errors.append("plugin license missing")
    elif root_license.read_bytes() != plugin_license.read_bytes():
        errors.append("plugin license must match repository LICENSE")

    hook_path = plugin_relative_path(plugin_root, compat.get("hooks"))
    if hook_path is None or not hook_path.is_file():
        errors.append("hook configuration missing or outside plugin root")
    else:
        hooks = load_json(hook_path)
        if not isinstance(hooks.get("hooks"), dict):
            errors.append("hook configuration must contain a hooks object")

    skills_path = plugin_relative_path(plugin_root, compat.get("skills"))
    if skills_path is None or not skills_path.is_dir():
        errors.append("skills directory missing or outside plugin root")
        return errors
    skill_files = sorted(skills_path.glob("*/SKILL.md"))
    if not skill_files:
        errors.append("skills directory must contain at least one SKILL.md")
    for path in skill_files:
        values = skill_frontmatter(path)
        if not values or values.get("name") != path.parent.name or not values.get("description"):
            errors.append(f"{path.relative_to(repo).as_posix()}: invalid skill frontmatter")
    return errors


def prohibited_path_reason(relative: Path) -> str | None:
    parts = set(relative.parts)
    if parts.intersection(PROHIBITED_PARTS):
        return "prohibited repository path"
    if relative.name.startswith("._") or relative.name == ".DS_Store":
        return "prohibited repository path"
    if relative.suffix.lower() in PROHIBITED_SUFFIXES:
        return "prohibited repository path"
    if relative.name.endswith((".lock",)) and "state" in parts:
        return "prohibited repository path"
    return None


def scan_text(label: str, text: str, allow_local_paths: bool = False) -> list[str]:
    errors: list[str] = []
    if not allow_local_paths and LOCAL_PATH_RE.search(text):
        errors.append(f"{label}: local absolute path")
    if any(match.group(0) not in KNOWN_SYNTHETIC_TOKENS for match in CREDENTIAL_RE.finditer(text)):
        errors.append(f"{label}: credential marker")
    if PRIVATE_KEY_RE.search(text):
        errors.append(f"{label}: private-key marker")
    return errors


def validate_tracked_files(repo: Path) -> list[str]:
    errors: list[str] = []
    for relative in public_relative_paths(repo):
        reason = prohibited_path_reason(relative)
        if reason:
            errors.append(f"{relative.as_posix()}: {reason}")
            continue
        path = repo / relative
        try:
            content = path.read_bytes()
        except OSError as error:
            errors.append(f"{relative.as_posix()}: unreadable file ({type(error).__name__})")
            continue
        if len(content) > 5_000_000 or b"\x00" in content:
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        frozen = FROZEN_LOCAL_PATH_EVIDENCE.get(relative.as_posix()) == hashlib.sha256(content).hexdigest()
        errors.extend(scan_text(relative.as_posix(), text, allow_local_paths=frozen))
    return errors


def validate_markdown_links(repo: Path) -> list[str]:
    errors: list[str] = []
    for relative in public_relative_paths(repo):
        if relative.suffix.lower() not in {".md", ".markdown"}:
            continue
        text = (repo / relative).read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_RE.findall(text):
            target = unquote(raw_target.split("#", 1)[0])
            if not target or target.startswith(("http://", "https://", "mailto:", "codex://")):
                continue
            candidate = (repo / relative.parent / target).resolve()
            try:
                candidate.relative_to(repo.resolve())
            except ValueError:
                errors.append(f"{relative.as_posix()}: broken relative link {raw_target!r}")
                continue
            if not candidate.exists():
                errors.append(f"{relative.as_posix()}: broken relative link {raw_target!r}")
    return errors


def validate_public_claims(repo: Path) -> list[str]:
    errors: list[str] = []
    for relative in public_relative_paths(repo):
        if relative.suffix.lower() not in {".md", ".markdown"}:
            continue
        text = (repo / relative).read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in UNSUPPORTED_PUBLIC_CLAIM_PATTERNS):
            errors.append(f"{relative.as_posix()}: unsupported public claim")
    return errors


def prohibited_archive_reason(name: str, is_directory: bool = False) -> str | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return "prohibited archive member"
    if set(path.parts).intersection(PROHIBITED_PARTS):
        return "prohibited archive member"
    if path.name.startswith("._") or path.name == ".DS_Store":
        return "prohibited archive member"
    if path.suffix.lower() in PROHIBITED_SUFFIXES:
        return "prohibited archive member"
    if not path.parts or not ARCHIVE_ROOT_RE.fullmatch(path.parts[0]):
        return "prohibited archive member"
    relative = "/".join(path.parts[1:])
    allowed = ARCHIVE_ALLOWED_DIRECTORIES if is_directory else ARCHIVE_ALLOWED_FILES
    if (not relative and not is_directory) or (relative and relative not in allowed):
        return "prohibited archive member"
    return None


def validate_archive(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            roots: set[str] = set()
            seen_files: set[str] = set()
            seen_members: set[str] = set()
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                member = PurePosixPath(info.filename)
                if member.parts and ARCHIVE_ROOT_RE.fullmatch(member.parts[0]):
                    roots.add(member.parts[0])
                if info.filename in seen_members:
                    errors.append(f"{info.filename}: duplicate archive member")
                seen_members.add(info.filename)
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                member_type = stat.S_IFMT(unix_mode) if info.create_system == 3 else 0
                expected_types = {0, stat.S_IFDIR} if info.is_dir() else {0, stat.S_IFREG}
                if member_type not in expected_types:
                    errors.append(f"{info.filename}: prohibited archive member type")
                reason = prohibited_archive_reason(info.filename, info.is_dir())
                if reason:
                    errors.append(f"{info.filename}: {reason}")
                elif not info.is_dir():
                    seen_files.add("/".join(member.parts[1:]))
                if info.is_dir() or info.file_size > 5_000_000:
                    continue
                content = archive.read(info)
                if b"\x00" in content:
                    continue
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                for error in scan_text(info.filename, text):
                    errors.append(error.replace("local absolute path", "local absolute path in archive"))
            if len(roots) != 1:
                errors.append("archive must contain exactly one versioned package root")
            else:
                root = next(iter(roots))
                manifest_path = f"{root}/plugins/sol-codex/.codex-plugin/plugin.json"
                installer_path = f"{root}/install.sh"
                if manifest_path in archive.namelist() and installer_path in archive.namelist():
                    try:
                        manifest = json.loads(archive.read(manifest_path))
                        installer = archive.read(installer_path).decode("utf-8")
                        version = manifest["version"]
                        installer_match = re.search(r'^plugin_version="([^"]+)"$', installer, re.M)
                        if not isinstance(version, str) or not version:
                            raise ValueError("invalid version")
                        if root != f"sol-codex-portable-{version.replace('+', '-')}":
                            errors.append("archive package root does not match embedded plugin version")
                        if installer_match is None or installer_match.group(1) != version:
                            errors.append("archive installer version does not match embedded plugin version")
                    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                        errors.append("archive has invalid version metadata")
            for missing in sorted(ARCHIVE_ALLOWED_FILES - seen_files):
                errors.append(f"{missing}: missing required archive member")
    except (OSError, zipfile.BadZipFile) as error:
        errors.append(f"{path}: invalid ZIP archive ({type(error).__name__})")
    return errors


def validate(repo: Path, archive: Path | None = None) -> list[str]:
    errors: list[str] = []
    try:
        errors.extend(validate_manifest_identity(repo))
        errors.extend(validate_marketplace(repo))
        errors.extend(validate_plugin_structure(repo))
        errors.extend(validate_tracked_files(repo))
        errors.extend(validate_markdown_links(repo))
        errors.extend(validate_public_claims(repo))
        if archive is not None:
            errors.extend(validate_archive(archive))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        errors.append(f"validation input error: {type(error).__name__}: {error}")
    return sorted(set(errors))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args(argv)
    errors = validate(args.root.resolve(), args.archive.resolve() if args.archive else None)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print("Repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
