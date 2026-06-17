import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _chat_respond as cr  # noqa: E402


class RespondOnceTests(unittest.TestCase):
    """The chat auto-responder: an agent replies when the latest message is from someone
    else and targets it — either a human group message with no @ (everyone replies) or an
    explicit @it (or @All); an agent posting with no @ compels no one; never to itself; a
    consecutive-agent-turn cap breaks ping-pong; 'PASS' means stay silent."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))

    def _set_chat(self, pairs):                       # pairs: chronological
        entries = []
        for k, (sp, tx) in enumerate(pairs):
            ts = "2026-06-16 10:00:%02d PDT" % (k + 1)
            entries.append("### %s\n\n**%s:** %s\n" % (ts, sp, tx))
        body = "# Board\n\n## Chat\n\n" + "\n".join(reversed(entries)) + "\n"
        (self.collab / "collaboration.md").write_text(body)

    def _chat_text(self):
        return (self.collab / "collaboration.md").read_text()

    def _run(self, who, reply="ok", max_turns=6):
        return cr.respond_once(self.tmp, who, max_turns=max_turns,
                               runner=lambda prompt, project: reply)

    def test_responds_when_mentioned(self):
        self._set_chat([("Jack", "@Claude hello there")])
        self.assertEqual(self._run("Claude", reply="hi back"), "responded")
        self.assertIn("**Claude:** hi back", self._chat_text())

    def test_silent_when_someone_else_is_addressed(self):
        # @Codex means "for Codex"; Claude may stay quiet.
        self._set_chat([("Jack", "@Codex run it")])
        self.assertEqual(self._run("Claude"), "not-addressed")
        self.assertNotIn("**Claude:**", self._chat_text())

    def test_human_no_at_is_a_group_message_everyone_replies(self):
        self._set_chat([("Jack", "大家都在吗")])
        self.assertEqual(self._run("Claude", reply="在"), "responded")

    def test_delayed_agent_still_answers_group_message_after_peer_reply(self):
        self._set_chat([("Jack", "大家都在吗"), ("Claude", "我在")])
        self.assertEqual(self._run("Codex", reply="我也在"), "responded")
        self.assertIn("**Codex:** 我也在", self._chat_text())

    def test_agent_does_not_answer_same_group_message_twice(self):
        self._set_chat([("Jack", "大家都在吗"), ("Codex", "我在"), ("Claude", "我也在")])
        self.assertEqual(self._run("Codex", reply="重复回复"), "not-addressed")
        self.assertNotIn("重复回复", self._chat_text())

    def test_agent_chatter_without_at_does_not_compel_a_reply(self):
        # An agent talking without @ — the human watches; the other agent isn't forced in.
        self._set_chat([("Jack", "@Codex go"), ("Codex", "我先看看代码")])
        self.assertEqual(self._run("Claude"), "not-addressed")

    def test_never_responds_to_itself(self):
        self._set_chat([("Jack", "@Claude hi"), ("Claude", "already replied")])
        self.assertEqual(self._run("Claude"), "self")

    def test_at_all_triggers_each_agent(self):
        self._set_chat([("Jack", "@All standup")])
        self.assertEqual(self._run("Claude"), "responded")

    def test_consecutive_agent_cap(self):
        convo = [("Jack", "@All go")]
        convo += [("Claude", "a @Codex"), ("Codex", "b @Claude")] * 3  # 6 agent msgs
        convo += [("Codex", "@Claude more")]
        self._set_chat(convo)
        self.assertEqual(self._run("Claude", max_turns=6), "capped")
        self.assertNotIn("**Claude:** ok", self._chat_text())

    def test_pass_means_stay_silent(self):
        self._set_chat([("Jack", "@Claude anything?")])
        self.assertEqual(self._run("Claude", reply="PASS"), "passed")
        self.assertNotIn("**Claude:**", self._chat_text())

    def test_empty_chat_is_noop(self):
        (self.collab / "collaboration.md").write_text("# Board\n")
        self.assertEqual(self._run("Claude"), "empty")

    def test_superseded_when_a_NEW_PROMPT_FOR_ME_interleaves(self):
        convo = [("Jack", "@Claude question one")]
        self._set_chat(convo)

        def runner(prompt, project):           # a fresh @Claude prompt lands mid-spawn
            convo.append(("Jack", "@Claude scrap that — question two"))
            self._set_chat(convo)
            return "answer to question one"

        self.assertEqual(cr.respond_once(self.tmp, "Claude", runner=runner), "superseded")
        self.assertNotIn("answer to question one", self._chat_text())  # stale reply dropped

    def test_posts_even_if_a_message_for_someone_else_interleaves(self):
        convo = [("Jack", "@Claude question one")]
        self._set_chat(convo)

        def runner(prompt, project):           # an unrelated message (not for me) lands
            convo.append(("Codex", "我这边在跑测试"))
            self._set_chat(convo)
            return "answer one"

        self.assertEqual(cr.respond_once(self.tmp, "Claude", runner=runner), "responded")
        self.assertIn("answer one", self._chat_text())  # my answer to the original is still valid

    def test_drops_reply_if_chat_was_cleared_during_spawn(self):
        convo = [("Jack", "@Claude question one")]
        self._set_chat(convo)

        def runner(prompt, project):           # user closed/archived the live chat mid-spawn
            (self.collab / "collaboration.md").write_text("# Board\n\n## Chat\n\n")
            return "late answer"

        self.assertEqual(cr.respond_once(self.tmp, "Claude", runner=runner), "superseded")
        self.assertNotIn("late answer", self._chat_text())

    def test_lockbusy_surfaces_for_retry(self):
        self._set_chat([("Jack", "@Claude hi")])
        orig = cr._board_post
        cr._board_post = lambda *a, **k: "lockbusy"
        try:
            self.assertEqual(self._run("Claude", reply="x"), "lockbusy")
        finally:
            cr._board_post = orig

    def test_reply_lands_in_live_chat_not_lookalike_section(self):
        self.collab.joinpath("collaboration.md").write_text(
            "# Board\n\n## Chat Archive\n\nold archived stuff\n\n"
            "## Chat\n\n### 2026-06-16 10:00:01 PDT\n\n**Jack:** @Claude hey\n")
        self.assertEqual(self._run("Claude", reply="live reply"), "responded")
        board = self._chat_text()
        self.assertIn("live reply", board)
        self.assertGreater(board.index("live reply"), board.index("## Chat\n"))  # under live Chat
        self.assertLess(board.index("old archived stuff"), board.index("## Chat\n"))  # archive intact


if __name__ == "__main__":
    unittest.main()
