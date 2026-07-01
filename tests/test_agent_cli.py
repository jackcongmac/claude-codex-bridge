import os, sys, unittest
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

    def test_codex_chat_carries_image(self):
        a = ac.chat_argv("Codex", "hi", ".", image_path="/tmp/x.png")
        self.assertIn("-i", a); self.assertIn("/tmp/x.png", a)

    def test_claude_chat_is_headless_p(self):
        a = ac.chat_argv("Claude", "hi", ".")
        self.assertEqual(a[0], "claude"); self.assertIn("-p", a)

    def test_codex_implement_is_workspace_write(self):
        a = ac.implement_argv("Codex", "do it", ".")
        self.assertIn("workspace-write", a); self.assertNotIn("danger-full-access", a)

    def test_codex_review_is_read_only(self):
        a = ac.review_argv("Codex", "review", ".")
        self.assertIn("read-only", a)

    def test_claude_review_is_headless_p(self):
        a = ac.review_argv("Claude", "review", ".")
        self.assertEqual(a[0], "claude"); self.assertIn("-p", a)


if __name__ == "__main__":
    unittest.main()
