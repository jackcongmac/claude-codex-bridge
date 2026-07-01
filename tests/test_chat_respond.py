import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _chat_respond as cr  # noqa: E402
import _agent_cli  # noqa: E402
from bridge_common import read_section  # noqa: E402


class ImagePathResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.uploads = self.collab / "chat_uploads"
        self.uploads.mkdir(parents=True)

    def test_returns_existing_chat_image_path_for_safe_ref(self):
        ref = "a1b2c3d4.png"
        image = self.uploads / ref
        image.write_bytes(b"png")

        self.assertEqual(cr._image_path_for(self.tmp, {"img": ref}), str(image))

    def test_returns_none_when_message_has_no_image(self):
        self.assertIsNone(cr._image_path_for(self.tmp, {"text": "hello"}))

    def test_returns_none_for_invalid_or_traversal_refs(self):
        for ref in ("../x", "nope"):
            with self.subTest(ref=ref):
                self.assertIsNone(cr._image_path_for(self.tmp, {"img": ref}))

    def test_returns_none_when_safe_ref_file_is_missing(self):
        self.assertIsNone(cr._image_path_for(self.tmp, {"img": "a1b2c3d4.png"}))


class SpawnImageTests(unittest.TestCase):
    def _patch_agent_cli_run(self, fake_run):
        if not hasattr(_agent_cli, "subprocess"):
            self.addCleanup(lambda: delattr(_agent_cli, "subprocess"))
            _agent_cli.subprocess = type("SubprocessStub", (), {
                "run": fake_run,
                "SubprocessError": Exception,
            })()
            return
        old_run = _agent_cli.subprocess.run
        _agent_cli.subprocess.run = fake_run
        self.addCleanup(lambda: setattr(_agent_cli.subprocess, "run", old_run))

    def test_run_codex_chat_includes_image_flag_when_image_path_is_provided(self):
        image_path = "/tmp/a1b2c3d4.png"
        proj = tempfile.mkdtemp()
        os.makedirs(os.path.join(proj, ".collab"))
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            last = cmd[cmd.index("--output-last-message") + 1]
            pathlib.Path(last).write_text("ok")
            return mock.Mock(returncode=0, stdout=json.dumps({
                "type": "thread.started", "thread_id": "tid-img"}))

        self._patch_agent_cli_run(fake_run)
        self.assertEqual(_agent_cli.run_codex_chat("prompt", proj, image_path=image_path), "ok")

        self.assertIn("-i", captured["cmd"])
        self.assertEqual(captured["cmd"][captured["cmd"].index("-i") + 1], image_path)

    def test_run_codex_chat_cleans_its_temp_dir(self):
        # run_codex_chat writes the model's last message to a temp file; the temp
        # directory it creates must be removed after the call (no per-pass leak),
        # even though the reply is still returned.
        proj = tempfile.mkdtemp()
        os.makedirs(os.path.join(proj, ".collab"))
        captured = {}

        def fake_run(cmd, **kwargs):
            last = cmd[cmd.index("--output-last-message") + 1]
            captured["last"] = last
            pathlib.Path(last).write_text("known reply")
            return mock.Mock(returncode=0, stdout=json.dumps({
                "type": "thread.started", "thread_id": "tid-clean"}))

        self._patch_agent_cli_run(fake_run)
        reply = _agent_cli.run_codex_chat("do x", proj)

        self.assertEqual(reply, "known reply")
        self.assertFalse(os.path.exists(os.path.dirname(captured["last"])),
                         "temp dir must be cleaned up (no resource leak)")

    def test_spawn_codex_delegates_to_agent_cli_preserving_signature(self):
        old_run = cr._agent_cli.run_codex_chat
        seen = {}
        try:
            def fake_run(prompt, project, image_path=None):
                seen["args"] = (prompt, project, image_path)
                return "delegated"

            cr._agent_cli.run_codex_chat = fake_run
            self.assertEqual(cr._spawn_codex("prompt", "/tmp/project", image_path="/tmp/x.png"),
                             "delegated")
        finally:
            cr._agent_cli.run_codex_chat = old_run

        self.assertEqual(seen["args"], ("prompt", "/tmp/project", "/tmp/x.png"))

    def test_codex_chat_fresh_captures_thread_id_then_resumes(self):
        proj = tempfile.mkdtemp(); os.makedirs(os.path.join(proj, ".collab"))
        calls = []
        def fake_run(cmd, *a, **k):
            calls.append((list(cmd), k))
            # write the reply file (--output-last-message)
            if "--output-last-message" in cmd:
                pathlib.Path(cmd[cmd.index("--output-last-message")+1]).write_text("hi from codex")
            # fresh run emits --json thread.started on stdout
            out = ""
            if "--json" in cmd:
                out = json.dumps({"type":"thread.started","thread_id":"tid-1"}) + "\n"
            return type("R",(),{"returncode":0,"stdout":out,"stderr":""})()
        self._patch_agent_cli_run(fake_run)
        r1 = _agent_cli.run_codex_chat("hello", proj)
        self.assertEqual(r1, "hi from codex")
        self.assertIn("--json", calls[-1][0])
        with open(os.path.join(proj, ".collab", ".codex_chat_session")) as f:
            self.assertEqual(json.load(f)["session_id"], "tid-1")
        self.assertEqual(getattr(calls[-1][1]["stdin"], "name", ""), os.devnull)
        self.assertIn("timeout", calls[-1][1])
        # second call resumes tid-1
        calls.clear()
        _agent_cli.run_codex_chat("again", proj)
        self.assertIn("resume", calls[-1][0]); self.assertIn("tid-1", calls[-1][0])

    def test_codex_chat_resume_failure_drops_session_and_retries_fresh_once(self):
        proj = tempfile.mkdtemp(); os.makedirs(os.path.join(proj, ".collab"))
        session = os.path.join(proj, ".collab", ".codex_chat_session")
        pathlib.Path(session).write_text(json.dumps({"session_id": "stale-tid"}))
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            if "resume" in cmd:
                return type("R", (), {"returncode": 42, "stdout": "", "stderr": "stale"})()
            if "--output-last-message" in cmd:
                pathlib.Path(cmd[cmd.index("--output-last-message") + 1]).write_text("fresh reply")
            return type("R", (), {
                "returncode": 0,
                "stdout": json.dumps({"type": "thread.started", "thread_id": "tid-2"}) + "\n",
                "stderr": "",
            })()

        self._patch_agent_cli_run(fake_run)

        self.assertEqual(_agent_cli.run_codex_chat("recover", proj), "fresh reply")
        self.assertEqual(calls[0][:4], ["codex", "exec", "resume", "stale-tid"])
        self.assertIn("--json", calls[1])
        self.assertNotIn("resume", calls[1])
        self.assertEqual(json.loads(pathlib.Path(session).read_text())["session_id"], "tid-2")

    def test_spawn_claude_includes_image_path_and_read_instruction_in_prompt(self):
        image_path = "/tmp/a1b2c3d4.png"
        old_run = cr._agent_cli.run_claude_chat
        seen = {}

        try:
            def fake_run(prompt, project, image_path=None):
                seen["args"] = (prompt, project, image_path)
                return "ok"

            cr._agent_cli.run_claude_chat = fake_run
            self.assertEqual(cr._spawn_claude("prompt", str(ROOT), image_path=image_path), "ok")
        finally:
            cr._agent_cli.run_claude_chat = old_run

        sent_prompt = seen["args"][0]
        self.assertIn(image_path, sent_prompt)
        self.assertIn("Read", sent_prompt)
        self.assertEqual(seen["args"][1:], (str(ROOT), image_path))


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
        # These tests exercise the auto-responder's reply behavior, so opt it in
        # explicitly (it is OFF by default — see AutoRespondGateTests).
        self._prev_autorespond = os.environ.get("BRIDGE_CHAT_AUTORESPOND")
        os.environ["BRIDGE_CHAT_AUTORESPOND"] = "1"

    def tearDown(self):
        if self._prev_autorespond is None:
            os.environ.pop("BRIDGE_CHAT_AUTORESPOND", None)
        else:
            os.environ["BRIDGE_CHAT_AUTORESPOND"] = self._prev_autorespond

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
                               runner=lambda prompt, project, image_path=None: reply)

    def test_responds_when_mentioned(self):
        self._set_chat([("Jack", "@Claude hello there")])
        self.assertEqual(self._run("Claude", reply="hi back"), "responded")
        self.assertIn("**Claude:** hi back", self._chat_text())

    def test_passes_prompt_image_path_to_runner(self):
        ref = "a1b2c3d4.png"
        uploads = self.collab / "chat_uploads"
        uploads.mkdir()
        image = uploads / ref
        image.write_bytes(b"png")
        self.collab.joinpath("collaboration.md").write_text(
            "# Board\n\n## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n"
            + cr.format_chat_message("Jack", "@Claude look at this", msg_id="m1", img=ref))
        seen = {}

        def runner(prompt, project, image_path=None):
            seen["image_path"] = image_path
            return "image received"

        self.assertEqual(cr.respond_once(self.tmp, "Claude", runner=runner), "responded")
        self.assertEqual(seen["image_path"], str(image))

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

    def test_configured_agent_no_at_chatter_does_not_compel_reply(self):
        (self.collab / "roles.json").write_text(json.dumps({
            "human": "Jack",
            "agents": ["Alice", "Bob"],
        }))
        self._set_chat([("Jack", "@Alice go"), ("Alice", "checking")])

        self.assertEqual(self._run("Bob"), "not-addressed")

    def test_configured_agents_are_targets_for_human_group_messages(self):
        (self.collab / "roles.json").write_text(json.dumps({
            "human": "Jack",
            "agents": ["Alice", "Bob"],
        }))
        self._set_chat([("Jack", "team check")])

        self.assertEqual(self._run("Alice", reply="here"), "responded")
        self.assertIn("**Alice:** here", self._chat_text())

    def test_worker_agent_chatter_without_at_does_not_compel_a_reply(self):
        self._set_chat([("Jack", "@Codex go"), ("Codex (worker a1b2c3)", "running tests")])

        self.assertEqual(self._run("Claude"), "not-addressed")
        self.assertEqual(self._run("Codex"), "self")

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

    def test_passed_prompt_is_not_reselected_after_restart(self):
        self._set_chat([("Jack", "@Claude anything?")])

        self.assertEqual(self._run("Claude", reply="PASS"), "passed")
        self.assertEqual(self._run("Claude", reply="duplicate"), "not-addressed")

        self.assertNotIn("duplicate", self._chat_text())

    def test_same_second_duplicate_prompt_is_not_collapsed_by_delivery_id(self):
        self.collab.joinpath("collaboration.md").write_text(
            "# Board\n\n## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n**Jack:** @Claude repeat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n**Jack:** @Claude repeat\n")

        self.assertEqual(self._run("Claude", reply="PASS"), "passed")
        self.assertEqual(self._run("Claude", reply="PASS"), "passed")

        delivery = json.loads((self.collab / "chat_delivery.json").read_text())
        handled = delivery["agents"]["Claude"]["handled"]
        self.assertEqual(len(handled), 2)
        self.assertEqual(sorted(m.get("duplicate") for m in delivery["messages"].values()), [0, 1])

    def test_chat_ids_keep_processed_message_stable_when_duplicate_arrives_later(self):
        self.collab.joinpath("collaboration.md").write_text(
            "# Board\n\n## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n<!-- chat-id:m1 -->\n**Jack:** @Claude repeat\n")

        self.assertEqual(self._run("Claude", reply="PASS"), "passed")
        delivery = json.loads((self.collab / "chat_delivery.json").read_text())
        first_handled = set(delivery["agents"]["Claude"]["handled"])

        self.collab.joinpath("collaboration.md").write_text(
            "# Board\n\n## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n<!-- chat-id:m2 -->\n**Jack:** @Claude repeat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n<!-- chat-id:m1 -->\n**Jack:** @Claude repeat\n")

        self.assertEqual(self._run("Claude", reply="PASS"), "passed")
        delivery = json.loads((self.collab / "chat_delivery.json").read_text())
        handled = set(delivery["agents"]["Claude"]["handled"])

        self.assertEqual(len(handled), 2)
        self.assertTrue(first_handled.issubset(handled))
        self.assertEqual(sorted(m.get("id") for m in delivery["messages"].values()), ["m1", "m2"])

    def test_empty_chat_is_noop(self):
        (self.collab / "collaboration.md").write_text("# Board\n")
        self.assertEqual(self._run("Claude"), "empty")

    def test_superseded_when_a_NEW_PROMPT_FOR_ME_interleaves(self):
        convo = [("Jack", "@Claude question one")]
        self._set_chat(convo)

        def runner(prompt, project, image_path=None):  # a fresh @Claude prompt lands mid-spawn
            convo.append(("Jack", "@Claude scrap that — question two"))
            self._set_chat(convo)
            return "answer to question one"

        self.assertEqual(cr.respond_once(self.tmp, "Claude", runner=runner), "superseded")
        self.assertNotIn("answer to question one", self._chat_text())  # stale reply dropped

    def test_posts_even_if_a_message_for_someone_else_interleaves(self):
        convo = [("Jack", "@Claude question one")]
        self._set_chat(convo)

        def runner(prompt, project, image_path=None):  # an unrelated message (not for me) lands
            convo.append(("Codex", "我这边在跑测试"))
            self._set_chat(convo)
            return "answer one"

        self.assertEqual(cr.respond_once(self.tmp, "Claude", runner=runner), "responded")
        self.assertIn("answer one", self._chat_text())  # my answer to the original is still valid

    def test_drops_reply_if_chat_was_cleared_during_spawn(self):
        convo = [("Jack", "@Claude question one")]
        self._set_chat(convo)

        def runner(prompt, project, image_path=None):  # user closed/archived the live chat mid-spawn
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

    def test_reply_escapes_board_section_header_lines(self):
        self._set_chat([("Jack", "@Claude can this break the board?")])

        self.assertEqual(self._run("Claude", reply="sure\n## Chat Archive\nstill reply"),
                         "responded")
        chat = read_section(self.collab / "collaboration.md", "Chat")
        self.assertIn("\\## Chat Archive", chat)
        self.assertIn("still reply", chat)

    def test_resync_answers_missed_older_prompt_when_latest_targets_someone_else(self):
        # Claude was offline while two human prompts landed. On restart, latest is
        # for Codex, but Claude's earlier @ message must not sink forever.
        self._set_chat([("Jack", "@Claude first"), ("Jack", "@Codex second")])

        self.assertEqual(self._run("Claude", reply="answer first"), "responded")

        board = self._chat_text()
        self.assertIn("**Claude:** answer first", board)
        delivery = json.loads((self.collab / "chat_delivery.json").read_text())
        self.assertEqual(len(delivery["agents"]["Claude"]["handled"]), 1)

    def test_resync_does_not_repeat_a_delivered_missed_prompt_after_restart(self):
        self._set_chat([("Jack", "@Claude first"), ("Jack", "@Codex second")])

        self.assertEqual(self._run("Claude", reply="answer first"), "responded")
        self._run("Claude", reply="duplicate")

        self.assertNotIn("duplicate", self._chat_text())

    def test_corrupt_delivery_state_does_not_block_reply(self):
        self._set_chat([("Jack", "@Claude recover delivery")])
        delivery_path = self.collab / "chat_delivery.json"
        delivery_path.write_text("{not json")

        self.assertEqual(self._run("Claude", reply="delivery recovered"), "responded")

        self.assertIn("**Claude:** delivery recovered", self._chat_text())
        delivery = json.loads(delivery_path.read_text())
        self.assertIn("messages", delivery)
        self.assertIn("agents", delivery)
        self.assertEqual(len(delivery["agents"]["Claude"]["handled"]), 1)

    def test_malformed_delivery_state_does_not_block_reply(self):
        self._set_chat([("Jack", "@Claude repair delivery schema")])
        delivery_path = self.collab / "chat_delivery.json"
        delivery_path.write_text(json.dumps({"agents": ["bad"], "messages": ["bad"]}))

        self.assertEqual(self._run("Claude", reply="schema recovered"), "responded")

        self.assertIn("**Claude:** schema recovered", self._chat_text())
        delivery = json.loads(delivery_path.read_text())
        self.assertIsInstance(delivery.get("messages"), dict)
        self.assertIsInstance(delivery.get("agents"), dict)
        self.assertEqual(len(delivery["agents"]["Claude"]["handled"]), 1)

    def test_malformed_delivery_agent_record_does_not_block_reply(self):
        self._set_chat([("Jack", "@Claude repair agent record")])
        delivery_path = self.collab / "chat_delivery.json"
        delivery_path.write_text(json.dumps({"agents": {"Claude": ["bad"]}, "messages": {}}))

        self.assertEqual(self._run("Claude", reply="agent record recovered"), "responded")

        self.assertIn("**Claude:** agent record recovered", self._chat_text())
        delivery = json.loads(delivery_path.read_text())
        self.assertEqual(len(delivery["agents"]["Claude"]["handled"]), 1)

    def test_malformed_delivery_handled_record_does_not_block_reply(self):
        self._set_chat([("Jack", "@Claude repair handled record")])
        delivery_path = self.collab / "chat_delivery.json"
        delivery_path.write_text(json.dumps({"agents": {"Claude": {"handled": ["bad"]}}, "messages": {}}))

        self.assertEqual(self._run("Claude", reply="handled record recovered"), "responded")

        self.assertIn("**Claude:** handled record recovered", self._chat_text())
        delivery = json.loads(delivery_path.read_text())
        self.assertEqual(len(delivery["agents"]["Claude"]["handled"]), 1)

    def test_resync_continues_to_later_unhandled_group_prompt_after_missed_reply(self):
        self._set_chat([("Jack", "@Claude first"), ("Jack", "team standup")])

        self.assertEqual(self._run("Claude", reply="answer first"), "responded")
        self.assertEqual(self._run("Claude", reply="answer standup"), "responded")

        board = self._chat_text()
        self.assertIn("answer first", board)
        self.assertIn("answer standup", board)

    def test_typing_state_is_visible_only_while_runner_is_active(self):
        self._set_chat([("Jack", "@Claude think")])
        typing_path = self.collab / "chat_typing.json"

        def runner(prompt, project, image_path=None):
            state = json.loads(typing_path.read_text())
            self.assertEqual(state["agents"]["Claude"]["status"], "thinking")
            self.assertIn("message_id", state["agents"]["Claude"])
            return "done"

        self.assertEqual(cr.respond_once(self.tmp, "Claude", runner=runner), "responded")
        state = json.loads(typing_path.read_text())
        self.assertNotIn("Claude", state.get("agents", {}))

    def test_corrupt_typing_state_does_not_block_reply(self):
        self._set_chat([("Jack", "@Claude recover typing")])
        typing_path = self.collab / "chat_typing.json"
        typing_path.write_text("{not json")

        self.assertEqual(self._run("Claude", reply="typing recovered"), "responded")

        self.assertIn("**Claude:** typing recovered", self._chat_text())
        state = json.loads(typing_path.read_text())
        self.assertIsInstance(state.get("agents"), dict)
        self.assertNotIn("Claude", state.get("agents", {}))

    def test_malformed_typing_agents_state_does_not_block_reply(self):
        self._set_chat([("Jack", "@Claude recover agents")])
        typing_path = self.collab / "chat_typing.json"
        typing_path.write_text(json.dumps({"agents": ["bad"]}))

        self.assertEqual(self._run("Claude", reply="agents recovered"), "responded")

        self.assertIn("**Claude:** agents recovered", self._chat_text())
        state = json.loads(typing_path.read_text())
        self.assertIsInstance(state.get("agents"), dict)
        self.assertNotIn("Claude", state.get("agents", {}))


class AutoRespondGateTests(unittest.TestCase):
    """First cut of the platform remediation (docs/collaboration-platform-diagnosis-2026-06-17.md):
    the disposable chat auto-responder is OFF by default. It must not post AS the agent
    unless explicitly opted in via BRIDGE_CHAT_AUTORESPOND=1. Real interactive panes post
    via bridge-chat/bridge-post and are unaffected — this removes the 'a read-only responder
    speaks with the agent's identity' confusion that cost a full day."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        self._prev = os.environ.pop("BRIDGE_CHAT_AUTORESPOND", None)

    def tearDown(self):
        if self._prev is not None:
            os.environ["BRIDGE_CHAT_AUTORESPOND"] = self._prev

    def test_autorespond_is_disabled_by_default(self):
        body = "# Board\n\n## Chat\n\n### 2026-06-16 10:00:01 PDT\n\n**Jack:** @Claude hi\n"
        (self.collab / "collaboration.md").write_text(body)
        calls = {"n": 0}

        def runner(prompt, project, image_path=None):
            calls["n"] += 1
            return "I should never be posted"

        status = cr.respond_once(self.tmp, "Claude", runner=runner)

        self.assertEqual(status, "disabled")
        self.assertEqual(calls["n"], 0, "the model runner must not run when responder is off")
        self.assertNotIn("**Claude:**", (self.collab / "collaboration.md").read_text())

    def test_autorespond_runs_when_explicitly_enabled(self):
        os.environ["BRIDGE_CHAT_AUTORESPOND"] = "1"
        body = "# Board\n\n## Chat\n\n### 2026-06-16 10:00:01 PDT\n\n**Jack:** @Claude hi\n"
        (self.collab / "collaboration.md").write_text(body)

        status = cr.respond_once(self.tmp, "Claude", runner=lambda p, proj, image_path=None: "hello back")

        self.assertEqual(status, "responded")
        self.assertIn("**Claude:** hello back", (self.collab / "collaboration.md").read_text())


class PromptIdentityTests(unittest.TestCase):
    """The chat responder spawns a disposable, read-only subprocess per reply. Its prompt
    must tell that instance it IS the read-only group-chat responder (not the human's
    interactive coding pane) so it stops giving the misleading 'reopen me with
    workspace-write' advice that conflated the two and burned a day of debugging."""

    def _prompt(self, who):
        msgs = [{"speaker": "Jack", "text": "are you full access now?"}]
        return cr._prompt(who, msgs, msgs[-1])

    def test_codex_prompt_declares_read_only_responder_identity(self):
        p = self._prompt("Codex").lower()
        self.assertIn("read-only", p)
        self.assertIn("responder", p)

    def test_claude_prompt_declares_read_only_responder_identity(self):
        p = self._prompt("Claude").lower()
        self.assertIn("read-only", p)
        self.assertIn("responder", p)

    def test_prompt_forbids_advising_reopen_for_write_access(self):
        # The instruction must steer the responder away from telling the human to
        # relaunch/reopen this instance to gain write access, and point at the
        # interactive pane as where code changes actually happen.
        p = self._prompt("Codex").lower()
        self.assertIn("reopen", p)
        self.assertIn("interactive", p)

    def test_prompt_discourages_at_mentioning_the_other_agent(self):
        # Both agents still reply to plain human messages (that's _targets); but the
        # prompt must NOT tell them to @ the other agent — an @ forces a reply and
        # creates agent-to-agent ping-pong. PASS stays the quiet path.
        p = self._prompt("Claude").lower()
        self.assertIn("do not @-mention the other agent", p)
        self.assertIn("ping-pong", p)
        self.assertNotIn("to pass the turn, @-mention", p)


if __name__ == "__main__":
    unittest.main()
