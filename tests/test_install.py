import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import install  # noqa: E402


class InstallTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="install_test_")
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name).resolve()
        config_roots = patch.dict(os.environ, {
            "APPDATA": str(self.home / "AppData" / "Roaming"),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
        })
        config_roots.start()
        self.addCleanup(config_roots.stop)

        # Fail before any install/uninstall if a destination escapes the fixture.
        for target in install.get_targets(self.home):
            dest = target["dest"].resolve()
            self.assertIn(self.home, dest.parents, f"Unsafe test destination: {dest}")

    def test_auto_detect_only_installs_present_harnesses(self):
        (self.home / ".codex").mkdir()
        (self.home / ".gemini" / "antigravity").mkdir(parents=True)
        (self.home / ".dsh").mkdir()
        res = install.run_install(ROOT, self.home)
        by_key = {r["key"]: r["action"] for r in res}
        self.assertEqual(by_key["agents"], "installed")
        self.assertEqual(by_key["antigravity"], "installed")
        self.assertEqual(by_key["dsh"], "installed")
        self.assertEqual(by_key["claude"], "skip")
        self.assertEqual(by_key["windsurf"], "skip")
        self.assertEqual(by_key["devin"], "skip")
        self.assertTrue((self.home / ".dsh" / "skills" / install.SKILL_NAME / "SKILL.md").exists())
        self.assertTrue((self.home / ".agents" / "skills" / install.SKILL_NAME / "SKILL.md").exists())
        self.assertTrue((self.home / ".gemini" / "config" / "skills" / install.SKILL_NAME / "scripts" / "takeover.py").exists())
        self.assertTrue((self.home / ".gemini" / "config" / "skills" / install.SKILL_NAME / "scripts" / "install.py").exists())

    def test_install_all_and_uninstall(self):
        res = install.run_install(ROOT, self.home, install_all=True)
        self.assertTrue(all(r["action"] == "installed" for r in res))
        for t in install.get_targets(self.home):
            self.assertTrue((t["dest"] / "SKILL.md").exists())

        un = install.run_install(ROOT, self.home, uninstall=True)
        self.assertTrue(all(r["action"] == "removed" for r in un))
        for t in install.get_targets(self.home):
            self.assertFalse(t["dest"].exists())

    def test_source_dir_is_never_overwritten_or_deleted(self):
        src = self.home / ".agents" / "skills" / install.SKILL_NAME
        install.copy_skill(ROOT, src)
        (self.home / ".claude").mkdir()

        res = install.run_install(src, self.home)
        by_key = {r["key"]: r["action"] for r in res}
        self.assertEqual(by_key["agents"], "source")
        self.assertEqual(by_key["claude"], "installed")

        un = install.run_install(src, self.home, uninstall=True)
        by_key_un = {r["key"]: r["action"] for r in un}
        self.assertEqual(by_key_un["agents"], "source")
        self.assertEqual(by_key_un["claude"], "removed")
        self.assertTrue((src / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
