import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PUSH = ROOT / "scripts" / "bridge-push.sh"
REVIEW = ROOT / "scripts" / "bridge-review.sh"


class PushGateRebaseTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.origin = os.path.join(self.tmpdir, "origin.git")
        self.a = os.path.join(self.tmpdir, "a")
        self.b = os.path.join(self.tmpdir, "b")
        self._run(["git", "init", "--bare", "--initial-branch=main", self.origin])
        self._run(["git", "clone", self.origin, self.a])
        self._run(["git", "clone", self.origin, self.b])
        for repo in (self.a, self.b):
            self._git(repo, "config", "user.email", "test@example.test")
            self._git(repo, "config", "user.name", "Test User")
        self._git(self.a, "checkout", "-q", "-B", "main")
        self._write(self.a, "base.txt", "base\n")
        self._git(self.a, "add", "base.txt")
        self._git(self.a, "commit", "-q", "-m", "base")
        self._git(self.a, "push", "-q", "-u", "origin", "main")
        self._git(self.b, "fetch", "-q", "origin", "main")
        self._git(self.b, "checkout", "-q", "-B", "main", "origin/main")
        self._git(self.a, "checkout", "-q", "-B", "main", "origin/main")
        pathlib.Path(self.a, ".collab").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _env(self):
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("CODEX_") or key.startswith("CLAUDE"):
                env.pop(key, None)
        env["BRIDGE_REQUIRE_SIGNATURES"] = "0"
        env["BRIDGE_PUSH_WAIT"] = "0"
        env["BRIDGE_PUSH_TTL"] = "1"
        return env

    def _run(self, args, cwd=None, env=None, check=True):
        r = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check and r.returncode != 0:
            self.fail("%r failed:\nstdout:\n%s\nstderr:\n%s" % (args, r.stdout, r.stderr))
        return r

    def _git(self, repo, *args, check=True, env=None):
        return self._run(["git", "-C", repo, *args], env=env, check=check)

    def _write(self, repo, relpath, text):
        path = pathlib.Path(repo, relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def _commit(self, repo, relpath, text, message):
        self._write(repo, relpath, text)
        self._git(repo, "add", relpath)
        self._git(repo, "commit", "-q", "-m", message)
        return self._git(repo, "rev-parse", "HEAD").stdout.strip()

    def _record_go(self, sha):
        return self._run(
            [
                "bash", str(REVIEW),
                "--self", "Reviewer",
                "--sha", sha,
                "--verdict", "GO",
                "--project", self.a,
            ],
            env=self._env(),
        )

    def _bridge_push(self, check=False):
        return self._run(
            ["bash", str(PUSH), "Author"],
            cwd=self.a,
            env=self._env(),
            check=check,
        )

    def _origin_file(self, relpath):
        return self._run(
            ["git", "--git-dir", self.origin, "show", "main:%s" % relpath],
            check=False,
        )

    def test_clean_rebase_preserves_reviewed_patch_id_and_pushes(self):
        a_sha = self._commit(self.a, "a.txt", "reviewed a\n", "a change")
        self._record_go(a_sha)
        self._commit(self.b, "b.txt", "upstream b\n", "b change")
        self._git(self.b, "push", "-q", "origin", "main")

        r = self._bridge_push()

        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self._origin_file("a.txt").stdout, "reviewed a\n")

    def test_content_modifying_rerere_rebase_blocks_unreviewed_patch_id(self):
        self._write(self.a, "same.txt", "base\n")
        self._git(self.a, "add", "same.txt")
        self._git(self.a, "commit", "-q", "-m", "same base")
        self._git(self.a, "push", "-q", "origin", "main")
        self._git(self.b, "fetch", "-q", "origin", "main")
        self._git(self.b, "checkout", "-q", "-B", "main", "origin/main")
        self._git(self.a, "checkout", "-q", "-B", "main", "origin/main")

        a_sha = self._commit(self.a, "same.txt", "from-a\n", "a same-file change")
        self._record_go(a_sha)
        self._git(self.a, "config", "rerere.enabled", "true")
        self._git(self.a, "config", "rerere.autoupdate", "true")
        self._commit(self.b, "same.txt", "from-b\n", "b same-file change")
        self._git(self.b, "push", "-q", "origin", "main")

        self._git(self.a, "fetch", "-q", "origin", "main")
        self._git(self.a, "rebase", "origin/main", check=False)
        self._write(self.a, "same.txt", "from-b\nfrom-a\n")
        self._git(self.a, "add", "same.txt")
        self._git(self.a, "rebase", "--continue", env={**self._env(), "GIT_EDITOR": "true"})
        self._git(self.a, "reset", "--hard", "-q", a_sha)

        r = self._bridge_push()

        self.assertEqual(r.returncode, 4, r.stderr + r.stdout)
        self.assertIn("rebase changed pushed content", r.stderr)
        self.assertEqual(self._origin_file("same.txt").stdout, "from-b\n")

    def test_dropped_duplicate_rebase_is_not_a_false_block(self):
        a_sha = self._commit(self.a, "dup.txt", "same change\n", "duplicate change")
        self._record_go(a_sha)
        self._commit(self.b, "dup.txt", "same change\n", "duplicate change elsewhere")
        self._git(self.b, "push", "-q", "origin", "main")

        r = self._bridge_push()

        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self._origin_file("dup.txt").stdout, "same change\n")


if __name__ == "__main__":
    unittest.main()
