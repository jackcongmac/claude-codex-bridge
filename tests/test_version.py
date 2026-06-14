import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _version  # noqa: E402


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True)


class VersionStatusTests(unittest.TestCase):
    """_version.status(repo) tells whether THIS clone is behind its upstream, so a
    user on a stale clone can be told to update. Offline-safe: failures -> unknown,
    never raise."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)],
                       check=True, capture_output=True, text=True)
        # author clone A: make the first commit, push
        self.a = self.tmp / "a"
        subprocess.run(["git", "clone", str(self.remote), str(self.a)],
                       check=True, capture_output=True, text=True)
        (self.a / "f.txt").write_text("1")
        _git(self.a, "add", "."); _git(self.a, "commit", "-m", "c1"); _git(self.a, "push", "origin", "main")
        # consumer clone B: up to date at c1
        self.b = self.tmp / "b"
        subprocess.run(["git", "clone", str(self.remote), str(self.b)],
                       check=True, capture_output=True, text=True)

    def test_up_to_date_clone_is_not_behind(self):
        s = _version.status(str(self.b), fetch=True)
        self.assertTrue(s["is_git"])
        self.assertEqual(s["behind"], 0)

    def test_stale_clone_is_reported_behind(self):
        # advance the remote past B
        (self.a / "f.txt").write_text("2")
        _git(self.a, "commit", "-am", "c2"); _git(self.a, "push", "origin", "main")
        s = _version.status(str(self.b), fetch=True)
        self.assertEqual(s["behind"], 1)
        self.assertEqual(s["ahead"], 0)

    def test_fetch_failure_reports_unknown_not_uptodate(self):
        # offline / broken remote must NOT print "up to date" off stale tracking refs
        _git(self.b, "remote", "set-url", "origin", str(self.tmp / "nonexistent.git"))
        s = _version.status(str(self.b), fetch=True)
        self.assertIsNone(s["behind"])
        self.assertEqual(s["error"], "fetch-failed")

    def test_non_git_dir_is_not_git(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        s = _version.status(str(plain))
        self.assertFalse(s["is_git"])

    def test_check_cli_json(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "_version.py"), "check",
             "--repo", str(self.b), "--json"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(r.stdout)["is_git"])

    def test_check_cli_behind_message_points_to_update(self):
        (self.a / "f.txt").write_text("3")
        _git(self.a, "commit", "-am", "c3"); _git(self.a, "push", "origin", "main")
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "_version.py"), "check",
             "--repo", str(self.b), "--fetch"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("behind", r.stdout.lower())


class BridgeUpdateScriptTests(unittest.TestCase):
    def test_update_on_non_git_dir_fails_gracefully(self):
        plain = pathlib.Path(tempfile.mkdtemp())
        r = subprocess.run(
            [str(SCRIPTS / "bridge-update.sh"), "--repo", str(plain)],
            capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a git", (r.stdout + r.stderr).lower())


if __name__ == "__main__":
    unittest.main()
