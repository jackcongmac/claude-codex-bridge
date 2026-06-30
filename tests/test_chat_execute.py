import json, os, pathlib, subprocess, sys, tempfile, time, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_execute as ce

def _definitely_dead_pid():
    pid = max(os.getpid() + 10000, 50000)
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            pid += 1
        else:
            pid += 1

class HighRiskTests(unittest.TestCase):
    def test_flags_release_delete_publish(self):
        for t in ["发版 v0.9", "打 tag v1", "删掉这个文件", "git push --force",
                  "publish to npm", "release the build", "drop the table",
                  "git reset --hard", "reset --hard HEAD~1", "git clean",
                  "clean -fdx", "删库 wipe everything", "erase the disk",
                  "抹除缓存", "清空目录", "清除状态", "deploy to prod",
                  "部署到线上", "production release", "rotate the secret",
                  "token rotation"]:
            self.assertTrue(ce.is_high_risk(t), t)

    def test_allows_routine_work(self):
        for t in ["把④英文化做了", "加个键盘导航", "修一下时间戳显示", "run the tests"]:
            self.assertFalse(ce.is_high_risk(t), t)

    def test_drag_and_drop_is_not_high_risk(self):
        # regression: "drop" inside "drag-and-drop"/"dropdown" must NOT trigger the DROP gate
        for t in ["Fix the image upload/paste/drag-and-drop channel",
                  "add a dropdown menu", "支持拖拽 drag-and-drop 上传"]:
            self.assertFalse(ce.is_high_risk(t), t)

class DecideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.tmp, ".collab"))
        import json
        with open(os.path.join(self.tmp, ".collab", "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": "Claude"}, f)

    def _msgs(self, *pairs):
        return [{"speaker": s, "text": t} for s, t in pairs]

    def test_non_human_latest_is_ignored(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "hi"), ("Codex", "我去做")),
                      judge=lambda t, c: {"kind": "actionable", "task": t})
        self.assertEqual(d["action"], "ignore")

    def test_actionable_low_risk_executes(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "把④英文化做了")),
                      judge=lambda t, c: {"kind": "actionable", "task": "做 ④ 英文化"})
        self.assertEqual(d["action"], "execute")
        self.assertEqual(d["task"], "做 ④ 英文化")

    def test_actionable_high_risk_requests_greenlight(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "发版吧")),
                      judge=lambda t, c: {"kind": "actionable", "task": "发版 v0.9"})
        self.assertEqual(d["action"], "request_greenlight")

    def test_high_risk_in_original_text_requests_greenlight_even_if_task_is_benign(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "git push --force after cleanup")),
                      judge=lambda t, c: {"kind": "actionable", "task": "cleanup branch"})
        self.assertEqual(d["action"], "request_greenlight")
        self.assertEqual(d["task"], "cleanup branch")

    def test_ambiguous_asks(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "④ 怎么样")),
                      judge=lambda t, c: {"kind": "ambiguous", "question": "你是要现在做④吗?"})
        self.assertEqual(d["action"], "ask")
        self.assertIn("④", d["question"])

    def test_opinion_is_ignored(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "我觉得④挺重要")),
                      judge=lambda t, c: {"kind": "opinion"})
        self.assertEqual(d["action"], "ignore")

class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.tmp, ".collab"))
        self.events = []

    def _poster(self, text): self.events.append(("post", text))
    def _enqueue(self, task): self.events.append(("enqueue", task))

    def test_execute_acks_before_enqueue(self):
        st = ce.dispatch(self.tmp, {"action": "execute", "task": "做 ④"},
                         self._poster, self._enqueue)
        self.assertEqual(st, "acked-enqueued")
        kinds = [e[0] for e in self.events]
        self.assertEqual(kinds, ["post", "enqueue"])          # ack strictly first
        self.assertIn("开始执行", self.events[0][1])
        self.assertIn("做 ④", self.events[0][1])

    def test_request_greenlight_posts_and_does_not_enqueue(self):
        st = ce.dispatch(self.tmp, {"action": "request_greenlight", "task": "发版"},
                         self._poster, self._enqueue)
        self.assertEqual(st, "requested-greenlight")
        self.assertEqual([e[0] for e in self.events], ["post"])
        self.assertIn("需要你点头", self.events[0][1])

    def test_ask_posts_question_only(self):
        st = ce.dispatch(self.tmp, {"action": "ask", "question": "现在做④吗?"},
                         self._poster, self._enqueue)
        self.assertEqual(st, "asked")
        self.assertEqual([e[0] for e in self.events], ["post"])

    def test_ignore_is_noop(self):
        st = ce.dispatch(self.tmp, {"action": "ignore"}, self._poster, self._enqueue)
        self.assertEqual(st, "noop")
        self.assertEqual(self.events, [])

class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.tmp, ".collab"))
        self.posts = []

    def _issues(self):
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            return f.read()

    def test_success_posts_and_logs_commit(self):
        ce.report(self.tmp, "做 ④ 英文化",
                  {"ok": True, "summary": "英文化完成", "commit": "abc1234"},
                  self.posts.append)
        self.assertTrue(any("✅" in p and "abc1234" in p for p in self.posts))
        log = self._issues()
        self.assertIn("## 执行记录", log)
        self.assertIn("做 ④ 英文化", log)
        self.assertIn("abc1234", log)

    def test_failure_posts_and_logs_reason(self):
        ce.report(self.tmp, "做 X", {"ok": False, "summary": "测试没过"}, self.posts.append)
        self.assertTrue(any("❌" in p and "测试没过" in p for p in self.posts))
        self.assertIn("测试没过", self._issues())

class ExecuteOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        collab = os.path.join(self.tmp, ".collab"); os.mkdir(collab)
        import json
        with open(os.path.join(collab, "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": "Claude"}, f)
        with open(os.path.join(collab, "collaboration_signal.json"), "w") as f:
            json.dump({"update_id": 0}, f)
        with open(os.path.join(collab, "collaboration.md"), "w") as f:
            f.write("# Board\n\n## Chat\n\n### 2026-06-29 10:00:00 PDT\n\n**Jack:** 把④英文化做了\n")
        self.posts = []

    def test_disabled_by_default(self):
        os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
        self.assertEqual(ce.execute_once(self.tmp), "disabled")

    def test_runs_full_path_with_injected_judge_and_executor(self):
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "做 ④ 英文化"},
                executor=lambda task, project: {"ok": True, "summary": "done", "commit": "abc1234"},
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
        self.assertEqual(st, "done")
        joined = "\n".join(self.posts)
        self.assertIn("开始执行", joined)     # ack
        self.assertIn("✅", joined)            # report
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            self.assertIn("abc1234", f.read())

    def test_default_poster_formats_executor_messages_as_lead(self):
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "做 ④ 英文化"},
                executor=lambda task, project: {"ok": True, "summary": "done"},
                poster=None)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        from bridge_common import collab_paths, find_project_root, read_section
        board = collab_paths(find_project_root(self.tmp))["board"]
        msgs = ce._parse_chat_fn()(read_section(board, "Chat"))
        ack = next(msg for msg in msgs if msg["text"].startswith("开始执行:"))
        self.assertEqual(ack["speaker"], "Claude")
        self.assertNotIn("?", [msg["speaker"] for msg in msgs])

    def test_skips_latest_message_already_marked_handled(self):
        import json
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "<!-- chat-id:greenlight-1 -->\n"
                "**Jack:** yes, do it\n"
                "### 2026-06-29 09:59:00 PDT\n\n"
                "**Claude:** waiting for your greenlight\n")
        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json"), "w") as f:
            json.dump({"handled": ["greenlight-1"]}, f)

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "do it"},
                executor=lambda task, project: self.fail("executor must not re-run"),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "none")
        self.assertEqual(self.posts, [])

    def test_runs_oldest_unhandled_human_message_when_agent_report_is_latest(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:05:00 PDT\n\n"
                "<!-- chat-id:agent-report-newer -->\n"
                "**Claude:** ✅ 完成:previous task\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "<!-- chat-id:human-greenlight-older -->\n"
                "**Jack:** yes, execute the next task\n")

        seen = {}
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            def judge(t, c):
                seen["text"] = t
                return {"kind": "actionable", "task": "execute the next task"}

            st = ce.execute_once(
                self.tmp,
                judge=judge,
                executor=lambda task, project: {"ok": True, "summary": "done"},
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertEqual(seen["text"], "yes, execute the next task")
        self.assertIn("human-greenlight-older", ce._load_handled(self.tmp))
        self.assertNotIn("agent-report-newer", ce._load_handled(self.tmp))

    def test_skips_handled_human_message_and_runs_next_oldest_unhandled_human_message(self):
        import json
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:10:00 PDT\n\n"
                "<!-- chat-id:agent-report-newer -->\n"
                "**Claude:** ✅ 完成:previous task\n"
                "### 2026-06-29 10:05:00 PDT\n\n"
                "<!-- chat-id:human-second -->\n"
                "**Jack:** do the second task\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "<!-- chat-id:human-first -->\n"
                "**Jack:** do the first task\n")
        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json"), "w") as f:
            json.dump({"handled": ["human-first"]}, f)

        seen = {}
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            def judge(t, c):
                seen["text"] = t
                return {"kind": "actionable", "task": "second task"}

            st = ce.execute_once(
                self.tmp,
                judge=judge,
                executor=lambda task, project: {"ok": True, "summary": "done"},
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertEqual(seen["text"], "do the second task")
        self.assertIn("human-second", ce._load_handled(self.tmp))

    def test_actionable_run_marks_latest_message_handled(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "<!-- chat-id:greenlight-2 -->\n"
                "**Jack:** yes, execute the task\n"
                "### 2026-06-29 09:59:00 PDT\n\n"
                "**Claude:** waiting for your greenlight\n")

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "execute the task"},
                executor=lambda task, project: {"ok": True, "summary": "done"},
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertIn("greenlight-2", ce._load_handled(self.tmp))

    def test_executor_crash_after_running_is_not_rerun_and_surfaces_once(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "<!-- chat-id:crashy-task -->\n"
                "**Jack:** ship the small safe fix\n")

        calls = []
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            def boom(task, project):
                calls.append(task)
                raise RuntimeError("executor crashed after start")

            with self.assertRaises(RuntimeError):
                ce.execute_once(
                    self.tmp,
                    judge=lambda t, c: {"kind": "actionable", "task": "safe fix"},
                    executor=boom,
                    poster=self.posts.append)

            self.assertEqual(calls, ["safe fix"])

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "must not rerun"},
                executor=lambda task, project: self.fail("running task must not be rerun"),
                poster=self.posts.append)
            self.assertEqual(st, "none")
            warnings = [p for p in self.posts if "上一个任务执行中断" in p]
            self.assertEqual(len(warnings), 1)
            self.assertIn("ship the small safe fix", warnings[0])

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "must not rerun"},
                executor=lambda task, project: self.fail("running task must not be rerun"),
                poster=self.posts.append)
            self.assertEqual(st, "none")
            warnings = [p for p in self.posts if "上一个任务执行中断" in p]
            self.assertEqual(len(warnings), 1)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json")) as f:
            state = json.load(f)
        self.assertEqual(state["messages"]["crashy-task"]["state"], "done")

    def test_claimed_message_is_repicked_after_decision_crash_before_executor(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "<!-- chat-id:claimed-before-run -->\n"
                "**Jack:** do a retryable task\n")
        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json"), "w") as f:
            json.dump({"version": 2, "messages": {
                "claimed-before-run": {
                    "state": "claimed",
                    "at": "2026-06-29T10:00:00Z",
                    "pid": _definitely_dead_pid(),
                    "epoch": time.time() - 4000,
                },
            }}, f)

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            calls = []
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "retryable task"},
                executor=lambda task, project: calls.append(task) or {"ok": True, "summary": "done"},
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertEqual(calls, ["retryable task"])
        self.assertIn("claimed-before-run", ce._load_handled(self.tmp))

    def test_fresh_claim_by_live_owner_is_not_re_executed(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "<!-- chat-id:fresh-live-claim -->\n"
                "**Jack:** do not double execute this\n")
        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json"), "w") as f:
            json.dump({"version": 2, "messages": {
                "fresh-live-claim": {
                    "state": "claimed",
                    "at": "2026-06-29T10:00:00Z",
                    "pid": os.getpid(),
                    "epoch": time.time(),
                },
            }}, f)

        calls = []
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "must not run"},
                executor=lambda task, project: calls.append(task) or {"ok": True, "summary": "wrong"},
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "none")
        self.assertEqual(calls, [])
        self.assertFalse(any("上一个任务执行中断" in p for p in self.posts))
        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json")) as f:
            state = json.load(f)
        self.assertEqual(state["messages"]["fresh-live-claim"]["state"], "claimed")
        self.assertEqual(state["messages"]["fresh-live-claim"]["pid"], os.getpid())

    def test_claim_with_stale_epoch_but_live_pid_is_repicked(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "<!-- chat-id:stale-live-claim -->\n"
                "**Jack:** recover old live-pid claim\n")
        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json"), "w") as f:
            json.dump({"version": 2, "messages": {
                "stale-live-claim": {
                    "state": "claimed",
                    "at": "2026-06-29T10:00:00Z",
                    "pid": os.getpid(),
                    "epoch": time.time() - ce._CLAIM_STALE_SECONDS - 10,
                },
            }}, f)

        calls = []
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "recover stale claim"},
                executor=lambda task, project: calls.append(task) or {"ok": True, "summary": "done"},
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertEqual(calls, ["recover stale claim"])
        self.assertIn("stale-live-claim", ce._load_handled(self.tmp))

    def test_claim_lock_contention_returns_busy_without_executing(self):
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        original_acquire = getattr(ce, "acquire_lock", None)
        original_release = getattr(ce, "release_lock", None)
        ce.acquire_lock = lambda *args, **kwargs: False
        ce.release_lock = lambda *args, **kwargs: self.fail("must not release an unheld lock")
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "should not execute"},
                executor=lambda task, project: self.fail("executor must not run when busy"),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
            if original_acquire is None:
                del ce.acquire_lock
            else:
                ce.acquire_lock = original_acquire
            if original_release is None:
                del ce.release_lock
            else:
                ce.release_lock = original_release

        self.assertEqual(st, "busy")
        self.assertEqual(self.posts, [])

    def test_second_pass_after_done_does_not_double_execute(self):
        calls = []
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "single run"},
                executor=lambda task, project: calls.append(task) or {"ok": True, "summary": "done"},
                poster=self.posts.append)
            self.assertEqual(st, "done")

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "single run"},
                executor=lambda task, project: self.fail("done task must not run twice"),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "none")
        self.assertEqual(calls, ["single run"])

    def test_mark_handled_is_idempotent_and_bounded(self):
        ce._mark_handled(self.tmp, "same-id")
        ce._mark_handled(self.tmp, "same-id")
        self.assertEqual(ce._load_handled(self.tmp), ["same-id"])
        for i in range(600):
            ce._mark_handled(self.tmp, "id-%03d" % i)

        handled = ce._load_handled(self.tmp)
        self.assertLessEqual(len(handled), 500)
        self.assertEqual(handled[-1], "id-599")

    def test_state_messages_map_is_bounded_to_recent_keys(self):
        for i in range(600):
            ce._mark_handled(self.tmp, "id-%03d" % i)

        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json")) as f:
            state = json.load(f)
        self.assertEqual(state["version"], 2)
        self.assertLessEqual(len(state["messages"]), 500)
        self.assertNotIn("id-000", state["messages"])
        self.assertIn("id-599", state["messages"])

class MsgKeyTests(unittest.TestCase):
    def test_id_bearing_message_key_is_unchanged(self):
        self.assertEqual(
            ce._msg_key({"_id": "abc123def456", "ts": "t", "speaker": "Jack", "text": "ignored"}),
            "abc123def456")

    def test_no_id_long_messages_with_same_prefix_have_distinct_keys(self):
        common = "x" * 80
        a = ce._msg_key({"ts": "2026", "speaker": "Jack", "text": common + "A"})
        b = ce._msg_key({"ts": "2026", "speaker": "Jack", "text": common + "B"})
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("h:"))
        self.assertTrue(b.startswith("h:"))

    def test_no_id_duplicate_messages_use_dup_disambiguator(self):
        base = {"ts": "2026", "speaker": "Jack", "text": "same text"}
        self.assertNotEqual(
            ce._msg_key({**base, "_dup": 0}),
            ce._msg_key({**base, "_dup": 1}))

class DefaultPosterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.mkdir(os.path.join(self.tmp, ".collab"))

    def test_default_poster_speaker_is_never_empty(self):
        import json
        with open(os.path.join(self.tmp, ".collab", "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": ""}, f)   # lead unconfigured
        self.assertNotEqual(ce._poster_speaker(self.tmp), "")   # falls back, never ""

class DefaultExecutorWiringTests(unittest.TestCase):
    def test_default_executor_is_the_pipeline_runner(self):
        self.assertIs(ce._default_executor, __import__("_chat_executor").run_task_executor)

class ChatExecuteSupervisorScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "roles.json").write_text(json.dumps({"human": "Jack", "lead": "Claude"}))
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (self.collab / "collaboration.md").write_text("# Board\n\n## Chat\n\n")
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
        pidfile = self.collab / ".chatexecute.pid"
        if pidfile.exists():
            try:
                pidfile.unlink()
            except OSError:
                pass

    def test_supervisor_loop_stays_running_and_claims_pidfile(self):
        env = {**os.environ, "BRIDGE_CHAT_EXECUTE_INTERVAL": "0.1"}
        env.pop("BRIDGE_CHAT_EXECUTE", None)
        proc = subprocess.Popen(
            [str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "bridge-chat-execute.sh"),
             self.tmp],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.procs.append(proc)

        pidfile = self.collab / ".chatexecute.pid"
        deadline = time.time() + 4
        while time.time() < deadline and not pidfile.exists() and proc.poll() is None:
            time.sleep(0.05)

        self.assertIsNone(proc.poll(), "chat execute supervisor must stay running")
        self.assertEqual(pidfile.read_text().strip(), str(proc.pid))
        proc.terminate()
        self.assertEqual(proc.wait(timeout=2), 0)
        self.assertFalse(pidfile.exists())

if __name__ == "__main__":
    unittest.main()
