"""Exercise cache retention across a simulated Codex plugin reinstall."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("upgrade_preserve_cache.py")


class UpgradeCacheTests(unittest.TestCase):
    def test_reinstall_restores_old_cache_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            market = root / "market"
            old = cache / "0.1.6"
            (old / ".codex-plugin").mkdir(parents=True)
            (old / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "sol-codex", "version": "0.1.6"}))
            (old / "hook.py").write_text("old trusted hook\n")
            (market / ".codex-plugin").mkdir(parents=True)
            (market / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "sol-codex", "version": "0.1.7"}))
            (cache / "0.1.5").symlink_to(old)
            fake = root / "codex"
            fake.write_text("""#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
cache = Path(os.environ['FAKE_CACHE'])
market = Path(os.environ['FAKE_MARKET'])
installed = Path(os.environ['FAKE_INSTALLED'])
if args == ['plugin', 'list', '--json']:
    print(json.dumps({'installed': [{'pluginId': 'sol-codex@sol-codex', 'version': installed.read_text(), 'source': {'path': str(market)}}]}))
elif args == ['plugin', 'marketplace', 'upgrade', 'sol-codex']:
    pass
elif args == ['plugin', 'remove', 'sol-codex@sol-codex']:
    shutil.rmtree(cache)
    installed.write_text('none')
elif args == ['plugin', 'add', 'sol-codex@sol-codex']:
    shutil.copytree(market, cache / '0.1.7')
    installed.write_text('0.1.7')
else:
    raise SystemExit(2)
""")
            fake.chmod(0o755)
            installed = root / "installed"
            installed.write_text("0.1.6")
            environment = dict(os.environ, FAKE_CACHE=str(cache), FAKE_MARKET=str(market), FAKE_INSTALLED=str(installed))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--codex", str(fake), "--cache-root", str(cache)],
                capture_output=True, text=True, env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((cache / "0.1.6/hook.py").read_text(), "old trusted hook\n")
            self.assertEqual((cache / "0.1.7/.codex-plugin/plugin.json").is_file(), True)
            self.assertTrue((cache / "0.1.5").is_symlink())


if __name__ == "__main__":
    unittest.main()
