#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/.." && pwd -P)"
dist_dir="${DIST_DIR:-$repo_root/dist}"
source_date_epoch="${SOURCE_DATE_EPOCH:-315532800}"

if [ -n "$(git -C "$repo_root" status --porcelain --untracked-files=all -- \
  .agents/plugins/marketplace.json plugins/sol-codex scripts/install.sh LICENSE)" ]; then
  echo "release inputs differ from HEAD; commit them before building" >&2
  exit 1
fi

temporary="$(mktemp -d)"
cleanup() {
  rm -rf -- "$temporary"
}
trap cleanup EXIT
source_root="$temporary/source"
mkdir -p "$source_root"
git -C "$repo_root" archive --format=tar HEAD \
  .agents/plugins/marketplace.json plugins/sol-codex scripts/install.sh LICENSE \
  | tar -xf - -C "$source_root"

version="$(python3 - "$source_root/plugins/sol-codex/.codex-plugin/plugin.json" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["version"])
PY
)"
file_version="${version/+/-}"
package_name="sol-codex-portable-$file_version"
archive_name="$package_name.zip"
archive_path="$dist_dir/$archive_name"
checksum_path="$archive_path.sha256"

package_root="$temporary/$package_name"
mv "$source_root" "$package_root"
mkdir -p "$dist_dir"
find "$package_root/plugins" -type f -name 'test_*.py' -delete
mv "$package_root/scripts/install.sh" "$package_root/install.sh"
rmdir "$package_root/scripts"
cp "$package_root/LICENSE" "$package_root/plugins/sol-codex/LICENSE"
chmod 0755 "$package_root/install.sh"

python3 - "$package_root/.agents/plugins/marketplace.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["name"] = "sol-codex-portable"
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

{
  printf '# SoL Codex portable package\n\nVersion: `%s`\n\n' "$version"
  cat <<'EOF'
From this extracted directory, run:

```bash
bash install.sh
```

The installer validates package identity, copies this local marketplace to a user data directory, installs `sol-codex@sol-codex-portable`, and prints the required hook-trust step. Exact tool-output artifacts remain local and may contain sensitive data.

Project documentation: https://github.com/DmitrL-dev/SoLCodex
EOF
} > "$package_root/README.md"

python3 - "$package_root" "$archive_path" "$source_date_epoch" <<'PY'
import os
import sys
import time
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
destination = Path(sys.argv[2])
epoch = max(315532800, int(sys.argv[3]))
timestamp = time.gmtime(epoch)[:6]
temporary = destination.with_suffix(destination.suffix + ".tmp")
paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root.parent).as_posix())
with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in paths:
        relative = path.relative_to(root.parent).as_posix()
        is_directory = path.is_dir()
        name = relative + ("/" if is_directory else "")
        info = zipfile.ZipInfo(name, date_time=timestamp)
        info.create_system = 3
        mode = 0o40755 if is_directory else (0o100755 if path.name == "install.sh" else 0o100644)
        info.external_attr = mode << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, b"" if is_directory else path.read_bytes())
os.replace(temporary, destination)
PY

PYTHONDONTWRITEBYTECODE=1 python3 "$script_dir/validate_repository.py" \
  --root "$repo_root" --archive "$archive_path"
unzip -tqq "$archive_path"

(
  cd "$dist_dir"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$archive_name"
  else
    shasum -a 256 "$archive_name"
  fi
) > "$checksum_path"

echo "$archive_path"
echo "$checksum_path"
