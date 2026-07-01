import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, "scripts")
import _agent_cli as ac


class ArgvTests(unittest.TestCase):
    def test_codex_chat_fresh_is_exec_json(self):
        a = ac.chat_argv("Codex", "hi", ".", session_id=None)
        self.assertEqual(a[:2], ["codex", "exec"])
        self.assertIn("--json", a)
        self.assertNotIn("resume", a)

    def test_codex_chat_with_session_is_resume(self):
        a = ac.chat_argv("Codex", "hi", ".", session_id="abc-123")
        self.assertEqual(a[:3], ["codex", "exec", "resume"])
        self.assertIn("abc-123", a)

    def test_codex_resume_omits_sandbox_and_cd_flags(self):
        # `codex exec resume` rejects -s/--sandbox and -C/--cd (they belong to the original
        # session). Including them makes resume error -> silent fallback to a fresh,
        # context-less session. Guard against that regression.
        a = ac.chat_argv("Codex", "hi", ".", session_id="abc-123")
        self.assertNotIn("-s", a)
        self.assertNotIn("--sandbox", a)
        self.assertNotIn("-C", a)

    def test_codex_chat_carries_image(self):
        a = ac.chat_argv("Codex", "hi", ".", image_path="/tmp/x.png")
        self.assertIn("-i", a); self.assertIn("/tmp/x.png", a)

    def test_claude_chat_is_headless_p(self):
        a = ac.chat_argv("Claude", "hi", ".")
        self.assertEqual(a[0], "claude"); self.assertIn("-p", a)

    def test_claude_chat_without_session_omits_resume(self):
        a = ac.chat_argv("Claude", "hi", ".", session_id=None)
        self.assertNotIn("--resume", a)

    def test_claude_chat_with_session_is_resume(self):
        a = ac.chat_argv("Claude", "hi", ".", session_id="s1")
        self.assertIn("--resume", a)
        self.assertEqual(a[a.index("--resume") + 1], "s1")
        self.assertIn("-p", a)

    def test_codex_implement_is_workspace_write(self):
        a = ac.implement_argv("Codex", "do it", ".")
        self.assertIn("workspace-write", a); self.assertNotIn("danger-full-access", a)

    def test_codex_review_is_read_only(self):
        a = ac.review_argv("Codex", "review", ".")
        self.assertIn("read-only", a)

    def test_claude_review_is_headless_p(self):
        a = ac.review_argv("Claude", "review", ".")
        self.assertEqual(a[0], "claude"); self.assertIn("-p", a)


class ClaudeChatRunTests(unittest.TestCase):
    def _project(self):
        project = tempfile.mkdtemp()
        os.makedirs(os.path.join(project, ".collab"))
        return project

    def test_claude_chat_fresh_captures_session_then_resumes(self):
        project = self._project()
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append((list(cmd), kwargs))
            return type("R", (), {
                "returncode": 0,
                "stdout": json.dumps({"result": "hi", "session_id": "s1"}),
                "stderr": "",
            })()

        with mock.patch.object(ac.subprocess, "run", side_effect=fake_run):
            self.assertEqual(ac.run_claude_chat("hello", project), "hi")

            session_path = os.path.join(project, ".collab", ".claude_chat_session")
            self.assertEqual(json.loads(pathlib.Path(session_path).read_text())["session_id"], "s1")
            self.assertNotIn("--resume", calls[-1][0])
            self.assertEqual(getattr(calls[-1][1]["stdin"], "name", ""), os.devnull)
            self.assertIn("timeout", calls[-1][1])

            calls.clear()
            self.assertEqual(ac.run_claude_chat("again", project), "hi")

        self.assertIn("--resume", calls[-1][0])
        self.assertEqual(calls[-1][0][calls[-1][0].index("--resume") + 1], "s1")

    def test_claude_chat_resume_failure_drops_session_and_retries_fresh_once(self):
        project = self._project()
        session_path = os.path.join(project, ".collab", ".claude_chat_session")
        pathlib.Path(session_path).write_text(json.dumps({"session_id": "stale"}))
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            if "--resume" in cmd:
                return type("R", (), {"returncode": 42, "stdout": "", "stderr": "stale"})()
            return type("R", (), {
                "returncode": 0,
                "stdout": json.dumps({"result": "fresh", "session_id": "s2"}),
                "stderr": "",
            })()

        with mock.patch.object(ac.subprocess, "run", side_effect=fake_run):
            self.assertEqual(ac.run_claude_chat("recover", project), "fresh")

        self.assertIn("--resume", calls[0])
        self.assertEqual(calls[0][calls[0].index("--resume") + 1], "stale")
        self.assertNotIn("--resume", calls[1])
        self.assertEqual(json.loads(pathlib.Path(session_path).read_text())["session_id"], "s2")

    def test_claude_and_codex_chat_session_files_do_not_collide(self):
        project = self._project()

        def fake_run(cmd, *args, **kwargs):
            if cmd[:2] == ["codex", "exec"]:
                last = cmd[cmd.index("--output-last-message") + 1]
                pathlib.Path(last).write_text("codex reply")
                return type("R", (), {
                    "returncode": 0,
                    "stdout": json.dumps({"type": "thread.started", "thread_id": "tid-1"}) + "\n",
                    "stderr": "",
                })()
            return type("R", (), {
                "returncode": 0,
                "stdout": json.dumps({"result": "claude reply", "session_id": "sid-1"}),
                "stderr": "",
            })()

        with mock.patch.object(ac.subprocess, "run", side_effect=fake_run):
            self.assertEqual(ac.run_codex_chat("hello codex", project), "codex reply")
            self.assertEqual(ac.run_claude_chat("hello claude", project), "claude reply")

        codex_session = pathlib.Path(project) / ".collab" / ".codex_chat_session"
        claude_session = pathlib.Path(project) / ".collab" / ".claude_chat_session"
        self.assertEqual(json.loads(codex_session.read_text())["session_id"], "tid-1")
        self.assertEqual(json.loads(claude_session.read_text())["session_id"], "sid-1")


if __name__ == "__main__":
    unittest.main()
