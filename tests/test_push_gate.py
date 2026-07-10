import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PUSH = ROOT / "scripts" / "bridge-push.sh"


class PushTestGateTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.repo = pathlib.Path(self.tmpdir, "repo")
        self.bin = pathlib.Path(self.tmpdir, "bin")
        self.repo.mkdir()
        self.bin.mkdir()
        self.git_log = self.repo / "git.log"
        self._write_fake_git()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_fake_git(self):
        git = self.bin / "git"
        git.write_text(
            """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$BRIDGE_FAKE_GIT_LOG"
if [ "$1" = "rev-parse" ] && [ "$2" = "--show-toplevel" ]; then
  printf '%s\\n' "$BRIDGE_PUSH_REPO"
elif [ "$1" = "rev-parse" ] && [ "$2" = "--abbrev-ref" ]; then
  printf '%s\\n' "${BRIDGE_PUSH_BRANCH:-main}"
elif [ "$1" = "rev-parse" ] && [ "$2" = "--git-path" ]; then
  printf '%s\\n' "$BRIDGE_PUSH_REPO/.git/$3"
elif [ "$1" = "rev-parse" ] && [ "$2" = "HEAD" ]; then
  printf '%s\\n' "abc123testhead"
elif [ "$1" = "fetch" ]; then
  exit 0
elif [ "$1" = "-c" ] && [ "$3" = "rebase" ]; then
  exit 0
elif [ "$1" = "push" ]; then
  printf 'push\\n' >> "$BRIDGE_FAKE_PUSH_LOG"
  exit 0
elif [ "$1" = "diff" ]; then
  exit 0
elif [ "$1" = "rebase" ]; then
  exit 0
else
  exit 0
fi
""",
            encoding="utf-8",
        )
        git.chmod(git.stat().st_mode | stat.S_IXUSR)

    def _env(self, extra=None):
        env = os.environ.copy()
        env["PATH"] = "%s%s%s" % (self.bin, os.pathsep, env.get("PATH", ""))
        env["BRIDGE_PUSH_REPO"] = str(self.repo)
        env["BRIDGE_PUSH_BRANCH"] = "main"
        env["BRIDGE_PUSH_WAIT"] = "0"
        env["BRIDGE_PUSH_TTL"] = "1"
        env["BRIDGE_FAKE_GIT_LOG"] = str(self.git_log)
        env["BRIDGE_FAKE_PUSH_LOG"] = str(self.repo / "push.log")
        env.pop("BRIDGE_TEST_CMD", None)
        if extra:
            env.update(extra)
        return env

    def _bridge_push(self, args=None, env=None):
        return subprocess.run(
            ["bash", str(PUSH), "Tester"] + (args or []),
            cwd=self.repo,
            env=env or self._env(),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _git_commands(self):
        if not self.git_log.exists():
            return []
        return self.git_log.read_text(encoding="utf-8").splitlines()

    def _push_was_invoked(self):
        return any(line.startswith("push ") for line in self._git_commands())

    def test_env_test_command_wins_over_file(self):
        (self.repo / ".bridge-test-cmd").write_text(
            "printf file > gate.txt\n",
            encoding="utf-8",
        )

        r = self._bridge_push(env=self._env({"BRIDGE_TEST_CMD": "printf env > gate.txt"}))

        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual((self.repo / "gate.txt").read_text(encoding="utf-8"), "env")
        self.assertTrue(self._push_was_invoked())

    def test_file_test_command_used_when_env_unset_and_comments_blanks_skipped(self):
        (self.repo / ".bridge-test-cmd").write_text(
            "\n"
            "# ignore this\n"
            "   # also ignore this\n"
            "printf file > gate.txt\n",
            encoding="utf-8",
        )

        r = self._bridge_push()

        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual((self.repo / "gate.txt").read_text(encoding="utf-8"), "file")
        self.assertTrue(self._push_was_invoked())

    def test_no_test_config_warns_and_proceeds(self):
        r = self._bridge_push()

        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("no test command configured", r.stderr)
        self.assertTrue(self._push_was_invoked())

    def test_failing_test_command_refuses_to_push_and_releases_lock(self):
        r = self._bridge_push(env=self._env({"BRIDGE_TEST_CMD": "false"}))

        self.assertNotEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("refusing to push", r.stderr)
        self.assertFalse(self._push_was_invoked())
        self.assertFalse((self.repo / ".bridge_push.lock").exists())

    def test_no_test_flag_skips_test_gate_and_proceeds(self):
        r = self._bridge_push(
            args=["--no-test"],
            env=self._env({"BRIDGE_TEST_CMD": "false"}),
        )

        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("--no-test: skipping the test gate for abc123testhead (audited)", r.stderr)
        self.assertTrue(self._push_was_invoked())


if __name__ == "__main__":
    unittest.main()
