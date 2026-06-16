import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
_spec = importlib.util.spec_from_file_location("chatweb", SCRIPTS / "bridge-chat-web.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class ChatArchiveTests(unittest.TestCase):
    """Closing a chat session archives the full ## Chat thread to disk and clears the
    live thread, so past conversations aren't lost and the next session starts fresh."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (self.collab / "collaboration.md").write_text(
            "# Board\n\n## Decision Log\n\nkeep me\n\n"
            "## Chat\n\n### 2026-06-16 10:00:01 PDT\n\n**Jack:** hi\n\n"
            "### 2026-06-16 10:00:02 PDT\n\n**Claude:** hello\n")

    def _board(self):
        return (self.collab / "collaboration.md").read_text()

    def test_archive_writes_a_file_with_the_thread(self):
        path = cw.archive_and_clear_chat(self.tmp)
        self.assertIsNotNone(path)
        self.assertTrue(pathlib.Path(path).exists())
        text = pathlib.Path(path).read_text()
        self.assertIn("Jack", text)
        self.assertIn("hello", text)

    def test_archive_clears_the_chat_section_but_keeps_others(self):
        cw.archive_and_clear_chat(self.tmp)
        board = self._board()
        self.assertNotIn("**Jack:** hi", board)
        self.assertNotIn("## Chat", board)
        self.assertIn("## Decision Log", board)   # other sections untouched
        self.assertIn("keep me", board)

    def test_archive_is_chronological(self):
        path = cw.archive_and_clear_chat(self.tmp)
        text = pathlib.Path(path).read_text()
        self.assertLess(text.index("hi"), text.index("hello"))

    def test_does_not_match_a_similarly_named_section(self):
        (self.collab / "collaboration.md").write_text(
            "# Board\n\n## Chat Archive\n\nold stuff\n\n"
            "## Chat\n\n### 2026-06-16 10:00:01 PDT\n\n**Jack:** hi\n")
        cw.archive_and_clear_chat(self.tmp)
        board = self._board()
        self.assertIn("## Chat Archive", board)   # the look-alike section survives
        self.assertNotIn("**Jack:** hi", board)

    def test_empty_chat_archives_nothing(self):
        (self.collab / "collaboration.md").write_text("# Board\n")
        self.assertIsNone(cw.archive_and_clear_chat(self.tmp))


class ResponderLaunchTests(unittest.TestCase):
    """Launching the group chat auto-starts the Claude/Codex responders, so the room is
    live the moment it opens; closing it stops them."""

    def test_responder_cmds_target_both_agents(self):
        cmds = cw.responder_cmds("/scripts", "/proj")
        selves = sorted(c[c.index("--self") + 1] for c in cmds)
        self.assertEqual(selves, ["Claude", "Codex"])
        for c in cmds:
            self.assertTrue(c[0].endswith("bridge-chat-respond.sh"))
            self.assertIn("/proj", c)

    def test_start_responders_spawns_each(self):
        seen = []
        handles = cw.start_responders("/proj", scripts_dir="/s",
                                      spawn=lambda cmd: seen.append(cmd) or object())
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(handles), 2)

    def test_default_spawn_isolates_process_group(self):
        captured = {}

        class Fake:
            def __init__(self, *a, **k):
                captured.update(k)
                self.pid = 4242

        orig = cw.subprocess.Popen
        cw.subprocess.Popen = Fake
        try:
            cw.start_responders("/proj", scripts_dir="/s")
        finally:
            cw.subprocess.Popen = orig
        self.assertTrue(captured.get("start_new_session"))   # own session => killable tree

    def test_kill_tree_kills_the_whole_process_group(self):
        import os
        import signal
        import subprocess
        import time
        # session leader (sh) that spawns a child sleep; killing the GROUP must reap both.
        p = subprocess.Popen(["sh", "-c", "sleep 30 & echo $! && wait"],
                             stdout=subprocess.PIPE, start_new_session=True)
        child = int(p.stdout.readline().strip())
        self.assertEqual(os.kill(child, 0), None)            # child alive
        cw.stop_responders([p])
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):          # child reaped via the group
            os.kill(child, 0)
        try:
            p.wait(timeout=2)                                # reap the sh leader (no warning)
        except Exception:
            pass
        if p.stdout:
            p.stdout.close()

    def test_stop_responders_terminates_all_even_if_one_raises(self):
        class H:
            def __init__(self, boom=False):
                self.killed = False
                self.boom = boom

            def terminate(self):
                if self.boom:
                    raise RuntimeError("already dead")
                self.killed = True

        ok, boom = H(), H(boom=True)
        cw.stop_responders([boom, ok])      # must not raise; must keep going
        self.assertTrue(ok.killed)


class SkillTriggerTests(unittest.TestCase):
    def test_skill_documents_the_group_chat_trigger(self):
        text = (ROOT / "skill" / "SKILL.md").read_text()
        self.assertIn("bridge-chat-web", text)
        self.assertIn("群聊", text)
        self.assertIn("group chat", text.lower())


if __name__ == "__main__":
    unittest.main()
