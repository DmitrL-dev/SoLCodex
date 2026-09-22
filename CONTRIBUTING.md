# Contributing

Contributions should preserve local-only operation, fail-open host behavior, exact-artifact integrity, bounded receipts, and verification-debt semantics.

## Development

Run the plugin unit tests. The file path form is required because the plugin directory contains a hyphen:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 plugins/sol-codex/scripts/test_sol_hook.py -v
```

Run repository checks:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts.test_release_tools -v
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_repository.py
```

Keep Python standard-library-only unless a dependency is justified for every supported installation. Add focused tests before behavior changes. Public code, documentation, commit messages, and release notes must remain professional and free of private paths, secrets, local state, observations, transcripts, or generated cache files.

Security-sensitive changes must test private modes, symlink rejection, redaction boundaries, structured exit-status handling, and fail-open behavior. Do not add network transmission of artifacts.

## Pull requests

Describe the user-visible behavior, risk, and exact verification commands. Keep unrelated refactors out of the patch. For security reports, use [SECURITY.md](SECURITY.md) instead of a public issue.

## Releases

Before tagging, run all tests, validate the repository and release archive, perform a clean installation smoke test, verify the version in both manifests, and review the generated ZIP and SHA-256 checksum. Release assets must not contain local plugin data or platform metadata.
