#!/usr/bin/env bash
set -euo pipefail

plugin_name="sol-codex"
plugin_version="0.1.0+codex.20260923075309"
marketplace_name="sol-codex-portable"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source_root="$script_dir"
codex_bin="${CODEX_BIN:-codex}"
data_parent="${XDG_DATA_HOME:-${HOME:?HOME is required}/.local/share}"
install_root="$data_parent/sol-codex-portable"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi
if ! command -v "$codex_bin" >/dev/null 2>&1; then
  echo "Codex CLI not found: $codex_bin" >&2
  exit 1
fi

mkdir -p "$data_parent"
install_lock="$data_parent/.sol-codex-portable.install.lock"
if [ "${SOL_CODEX_PORTABLE_INSTALL_LOCK_HELD:-}" != "$install_root" ]; then
  export SOL_CODEX_PORTABLE_INSTALL_LOCK_HELD="$install_root"
  exec python3 - "$install_lock" "${BASH_SOURCE[0]}" "$@" <<'PY'
import fcntl
import os
import signal
import stat
import subprocess
import sys

lock_path, script, *arguments = sys.argv[1:]
flags = os.O_RDWR | os.O_CREAT
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = -1
try:
    descriptor = os.open(lock_path, flags, 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise OSError("lock path is not a regular file")
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "r+", closefd=True) as lock:
        descriptor = -1
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        child = None

        def forward(signum, _frame):
            if child is not None and child.poll() is None:
                child.send_signal(signum)

        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, forward)
        child = subprocess.Popen(["bash", script, *arguments], env=os.environ.copy())
        return_code = child.wait()
except OSError as error:
    print(f"could not lock portable install: {error}", file=sys.stderr)
    raise SystemExit(1)
finally:
    if descriptor >= 0:
        os.close(descriptor)
raise SystemExit(return_code)
PY
fi

if ! python3 - "$source_root" "$plugin_name" "$plugin_version" "$marketplace_name" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected_name, expected_version, expected_marketplace = sys.argv[2:]
try:
    portable = json.loads((root / "plugins/sol-codex/plugin.json").read_text(encoding="utf-8"))
    compat = json.loads((root / "plugins/sol-codex/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
    market = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    entries = market["plugins"]
    valid = (
        portable.get("name") == expected_name
        and compat.get("name") == expected_name
        and portable.get("version") == expected_version
        and compat.get("version") == expected_version
        and market.get("name") == expected_marketplace
        and isinstance(entries, list)
        and len(entries) == 1
        and entries[0].get("name") == expected_name
        and entries[0].get("source") == {"source": "local", "path": "./plugins/sol-codex"}
    )
except (OSError, ValueError, KeyError, TypeError):
    valid = False
if not valid:
    raise SystemExit(1)
PY
then
  echo "portable package identity/version validation failed" >&2
  exit 1
fi

marketplaces_json="$("$codex_bin" plugin marketplace list --json)"
marketplace_state="$(python3 - "$marketplace_name" "$install_root" "$marketplaces_json" <<'PY'
import json
import sys
from pathlib import Path

name, expected_root, raw_payload = sys.argv[1:]
payload = json.loads(raw_payload)
matches = [item for item in payload.get("marketplaces", []) if item.get("name") == name]
if len(matches) > 1:
    print("collision:duplicate marketplace entries")
elif not matches:
    print("absent")
else:
    actual = str(Path(str(matches[0].get("root", ""))).expanduser().resolve())
    expected = str(Path(expected_root).expanduser().resolve())
    print("same" if actual == expected else f"collision:{actual}")
PY
)"

case "$marketplace_state" in
  absent|same)
    ;;
  collision:*)
    echo "marketplace '$marketplace_name' already points to a different root: ${marketplace_state#collision:}" >&2
    exit 1
    ;;
  *)
    echo "could not inspect existing marketplace roots" >&2
    exit 1
    ;;
esac

directory_identity() {
  python3 - "$1" <<'PY'
import os
import stat
import sys

info = os.lstat(sys.argv[1])
if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
    raise SystemExit(1)
print(f"{info.st_dev}:{info.st_ino}")
PY
}

if [ -L "$install_root" ]; then
  echo "refusing symlink install root: $install_root" >&2
  exit 1
fi
had_existing=0
original_identity=""
if [ -e "$install_root" ]; then
  if [ ! -d "$install_root" ]; then
    echo "refusing non-directory install root: $install_root" >&2
    exit 1
  fi
  had_existing=1
  original_identity="$(directory_identity "$install_root")"
fi
if [ -d "$install_root" ] && [ -n "$(find "$install_root" -mindepth 1 -maxdepth 1 -print -quit)" ] && [ "$marketplace_state" != "same" ]; then
  owner_marker="$install_root/.sol-codex-portable-owner.json"
  if ! python3 - "$owner_marker" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    valid = not path.is_symlink() and path.is_file() and json.loads(
        path.read_text(encoding="utf-8")
    ) == {"owner": "sol-codex-portable", "schema_version": 1}
except (OSError, ValueError, TypeError):
    valid = False
raise SystemExit(0 if valid else 1)
PY
  then
    echo "refusing unowned install root: $install_root" >&2
    exit 1
  fi
fi
if [ "$had_existing" -eq 1 ]; then
  current_identity="$(directory_identity "$install_root" 2>/dev/null || true)"
  if [ "$current_identity" != "$original_identity" ]; then
    echo "install root changed during ownership validation: $install_root" >&2
    exit 1
  fi
fi

staging="$(mktemp -d "$data_parent/.sol-codex-portable.XXXXXX")"
backup=""
backup_container=""
attempt_id="${staging##*/}"
attempt_marker_name=".sol-codex-portable-install-attempt"
installation_complete=0
attempt_root_is_current() {
  marker="$install_root/$attempt_marker_name"
  [ ! -L "$marker" ] && [ -f "$marker" ] && [ "$(cat "$marker")" = "$attempt_id" ]
}
cleanup() {
  status=$?
  trap - EXIT
  if [ -n "$staging" ] && [ -d "$staging" ]; then
    rm -rf -- "$staging"
  fi
  if [ "$installation_complete" -eq 0 ]; then
    if [ -n "$backup" ] && [ -d "$backup" ]; then
      if [ ! -e "$install_root" ] && [ ! -L "$install_root" ]; then
        mv "$backup" "$install_root"
        backup=""
      elif [ ! -L "$install_root" ] && attempt_root_is_current; then
        rm -rf -- "$install_root"
        mv "$backup" "$install_root"
        backup=""
      else
        echo "rollback preserved prior installation at $backup because install root changed" >&2
      fi
    elif [ ! -L "$install_root" ] && attempt_root_is_current; then
      rm -rf -- "$install_root"
    fi
  else
    if [ ! -L "$install_root" ] && attempt_root_is_current; then
      rm -f -- "$install_root/$attempt_marker_name"
    fi
    if [ -n "$backup" ] && [ -d "$backup" ]; then
      rm -rf -- "$backup"
      backup=""
    fi
  fi
  if [ -n "$backup_container" ] && [ -d "$backup_container" ]; then
    rmdir -- "$backup_container" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT

cp -R "$source_root/.agents" "$source_root/plugins" "$staging/"
printf '%s\n' '{"owner":"sol-codex-portable","schema_version":1}' > "$staging/.sol-codex-portable-owner.json"
if [ -f "$source_root/README.md" ]; then
  cp "$source_root/README.md" "$staging/README.md"
fi
printf '%s\n' "$attempt_id" > "$staging/$attempt_marker_name"

if [ "$had_existing" -eq 1 ]; then
  current_identity="$(directory_identity "$install_root" 2>/dev/null || true)"
  if [ "$current_identity" != "$original_identity" ]; then
    echo "install root changed during staging: $install_root" >&2
    exit 1
  fi
  backup_container="$(mktemp -d "$data_parent/.sol-codex-portable.backup.XXXXXX")"
  backup="$backup_container/original"
  mv "$install_root" "$backup"
  moved_identity="$(directory_identity "$backup" 2>/dev/null || true)"
  if [ "$moved_identity" != "$original_identity" ]; then
    echo "install root identity changed while creating backup: $install_root" >&2
    exit 1
  fi
elif [ -e "$install_root" ] || [ -L "$install_root" ]; then
  echo "install root appeared during staging: $install_root" >&2
  exit 1
fi
mv "$staging" "$install_root"
staging=""

if [ "$marketplace_state" = "absent" ]; then
  "$codex_bin" plugin marketplace add "$install_root" --json >/dev/null
fi
"$codex_bin" plugin add "$plugin_name@$marketplace_name" --json >/dev/null
installation_complete=1
rm -f -- "$install_root/$attempt_marker_name"

if [ -n "$backup" ] && [ -d "$backup" ]; then
  rm -rf -- "$backup"
  backup=""
fi
if [ -n "$backup_container" ] && [ -d "$backup_container" ]; then
  rmdir -- "$backup_container"
  backup_container=""
fi

echo "Installed $plugin_name $plugin_version from $install_root"
echo "Open /hooks, review and trust the hook commands, then start a new task."
