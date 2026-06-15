import json
import os
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "package.json"
BIN = ROOT / "bin" / "claude-codex-bridge"


class PackageJsonTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PKG.exists(), "package.json must exist")
        self.pkg = json.loads(PKG.read_text())

    def test_required_fields(self):
        self.assertEqual(self.pkg["name"], "claude-codex-bridge")
        self.assertRegex(self.pkg["version"], r"^\d+\.\d+\.\d+")
        self.assertIn("claude-codex-bridge", self.pkg["bin"])
        self.assertIsInstance(self.pkg["files"], list)
        self.assertEqual(self.pkg.get("license"), "MIT")

    def test_bin_target_exists_and_executable(self):
        target = ROOT / self.pkg["bin"]["claude-codex-bridge"]
        self.assertTrue(target.exists(), "bin target must exist")
        self.assertTrue(os.access(target, os.X_OK), "bin must be executable")


class BinDispatchTests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([str(BIN), *args], capture_output=True, text=True, timeout=20)

    def test_version_prints_package_version(self):
        ver = json.loads(PKG.read_text())["version"]
        r = self._run("version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(ver, r.stdout)

    def test_help_lists_install_and_update(self):
        r = self._run("help")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.lower()
        self.assertIn("install", out)
        self.assertIn("update", out)


class BinSymlinkTests(unittest.TestCase):
    def test_bin_resolves_root_through_a_symlink(self):
        # npm installs the bin as a symlink in its prefix; ROOT must still resolve to
        # the package dir, not the symlink's location.
        import os
        import tempfile
        link = pathlib.Path(tempfile.mkdtemp()) / "ccb"
        os.symlink(BIN, link)
        ver = json.loads(PKG.read_text())["version"]
        r = subprocess.run([str(link), "version"], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(ver, r.stdout)


class PackManifestTests(unittest.TestCase):
    def test_pack_includes_runtime_and_excludes_tests_and_collab(self):
        r = subprocess.run(["npm", "pack", "--dry-run", "--json"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        paths = {f["path"] for f in data[0]["files"]}
        for need in ("install.sh", "scripts/bridge-live.sh", "claude_chat_mcp.py",
                     "package.json", "templates/collaboration.md", "AGENTS.md",
                     "CLAUDE.md", "bin/claude-codex-bridge"):
            self.assertIn(need, paths, "package must ship %s (init-collaboration needs it)" % need)
        for p in paths:
            self.assertFalse(p.startswith("tests/"), "tests must not be published: %s" % p)
            self.assertNotIn(".collab", p, "runtime board must not be published: %s" % p)


if __name__ == "__main__":
    unittest.main()
