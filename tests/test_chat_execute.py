import base64, json, os, pathlib, shutil, subprocess, sys, tempfile, time, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_execute as ce
import _sig

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

def _have_sshkeygen():
    return shutil.which("ssh-keygen") is not None

def _gen_key(path):
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "test",
                    "-f", path], check=True, capture_output=True)

def _pub_line(actor, keypath):
    with open(keypath + ".pub") as f:
        pub = f.read().split()
    return "%s %s %s\n" % (actor, pub[0], pub[1])

def _enroll_jack(project, keys_dir):
    if not _have_sshkeygen():
        raise unittest.SkipTest("ssh-keygen not available")
    os.environ["BRIDGE_KEYS_DIR"] = keys_dir
    os.makedirs(keys_dir)
    key = os.path.join(keys_dir, "Jack.key")
    _gen_key(key)
    reg = os.path.join(project, ".collab", "keys")
    os.makedirs(reg)
    with open(os.path.join(reg, "allowed_signers"), "w") as f:
        f.write(_pub_line("Jack", key))

def _signed_msg(project, speaker, text, msg_id="id1", sent_at="t", img=None):
    msg = {"speaker": speaker, "text": text, "sent_at": sent_at, "_id": msg_id}
    if img:
        msg["img"] = img
    raw = _sig.sign(speaker, _sig.chat_payload(msg), project=project)
    if not raw:
        raise AssertionError("test signing failed for %s" % speaker)
    msg["sig"] = base64.b64encode(raw.encode()).decode()
    return msg

def _signed_chat_line(project, speaker, text, msg_id, sent_at=None, img=None):
    msg = _signed_msg(project, speaker, text, msg_id=msg_id, sent_at=sent_at, img=img)
    return ce._format_chat_fn()(speaker, text, msg_id=msg_id, sent_at=sent_at,
                                img=img, sig=msg["sig"])

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
        self.proj = self.tmp
        self._old_keys_dir = os.environ.get("BRIDGE_KEYS_DIR")
        self._old_require = os.environ.get("BRIDGE_REQUIRE_SIGNATURES")
        os.environ.pop("BRIDGE_REQUIRE_SIGNATURES", None)
        os.mkdir(os.path.join(self.tmp, ".collab"))
        with open(os.path.join(self.tmp, ".collab", "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": "Claude"}, f)
        _enroll_jack(self.tmp, os.path.join(self.tmp, "keys"))

    def tearDown(self):
        if self._old_keys_dir is None:
            os.environ.pop("BRIDGE_KEYS_DIR", None)
        else:
            os.environ["BRIDGE_KEYS_DIR"] = self._old_keys_dir
        if self._old_require is None:
            os.environ.pop("BRIDGE_REQUIRE_SIGNATURES", None)
        else:
            os.environ["BRIDGE_REQUIRE_SIGNATURES"] = self._old_require
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _msgs(self, *pairs):
        msgs = []
        for i, (speaker, text) in enumerate(pairs):
            if speaker == "Jack":
                msgs.append(_signed_msg(
                    self.tmp, speaker, text, msg_id="msg%d" % i, sent_at="t%d" % i))
            else:
                msgs.append({"speaker": speaker, "text": text})
        return msgs

    def test_non_human_latest_is_ignored(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "hi"), ("Codex", "我去做")),
                      judge=lambda t, c, image_path=None: {"kind": "actionable", "task": t})
        self.assertEqual(d["action"], "ignore")

    def test_unsigned_human_message_does_not_trigger(self):
        msgs = [{"speaker": "Jack", "text": "改README", "sent_at": "t", "_id": "id1"}]
        d = ce.decide(self.proj, msgs,
                      judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "改README"})
        self.assertEqual(d["action"], "ignore")
        self.assertEqual(d.get("reason"), "unsigned-human")

    def test_validly_signed_human_message_triggers(self):
        msg = _signed_msg(self.proj, "Jack", "改README", msg_id="id1", sent_at="t")
        d = ce.decide(self.proj, [msg],
                      judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "改README"})
        self.assertEqual(d["action"], "execute")

    def test_signatures_disabled_allows_unsigned(self):
        os.environ["BRIDGE_REQUIRE_SIGNATURES"] = "0"
        msgs = [{"speaker": "Jack", "text": "改README", "sent_at": "t", "_id": "id1"}]
        d = ce.decide(self.proj, msgs,
                      judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "改README"})
        self.assertEqual(d["action"], "execute")

    def test_actionable_low_risk_executes(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "把④英文化做了")),
                      judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"})
        self.assertEqual(d["action"], "execute")
        self.assertEqual(d["task"], "做 ④ 英文化")

    def test_record_requirement_records_without_execution(self):
        d = ce.decide(
            self.tmp,
            self._msgs(("Jack", "记下来：后面要支持导出聊天记录")),
            judge=lambda t, c, image_path=None: {
                "kind": "record_requirement",
                "task": "支持导出聊天记录",
            })
        self.assertEqual(d["action"], "record")
        self.assertEqual(d["task"], "支持导出聊天记录")

    def test_decide_forwards_resolved_image_path_to_judge_and_decision(self):
        uploads = os.path.join(self.tmp, ".collab", "chat_uploads")
        os.makedirs(uploads)
        ref = "a1b2c3d4e5f6.png"
        image = os.path.join(uploads, ref)
        with open(image, "wb") as f:
            f.write(b"png")
        seen = {}
        msgs = [_signed_msg(self.tmp, "Jack", "fix this", msg_id="image-msg",
                            sent_at="t", img=ref)]

        def judge_fn(text, context, image_path=None):
            seen["text"] = text
            seen["context"] = context
            seen["image_path"] = image_path
            return {"kind": "actionable", "task": "fix the screenshot bug"}

        d = ce.decide(self.tmp, msgs, judge=judge_fn)

        self.assertEqual(d["action"], "execute")
        self.assertEqual(d["task"], "fix the screenshot bug")
        self.assertEqual(d["image_path"], image)
        self.assertEqual(seen["image_path"], image)
        self.assertEqual(seen["text"], "fix this")

    def test_actionable_high_risk_requests_greenlight(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "发版吧")),
                      judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "发版 v0.9"})
        self.assertEqual(d["action"], "request_greenlight")

    def test_high_risk_in_original_text_requests_greenlight_even_if_task_is_benign(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "git push --force after cleanup")),
                      judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "cleanup branch"})
        self.assertEqual(d["action"], "request_greenlight")
        self.assertEqual(d["task"], "cleanup branch")

    def test_ambiguous_asks(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "④ 怎么样")),
                      judge=lambda t, c, image_path=None: {"kind": "ambiguous", "question": "你是要现在做④吗?"})
        self.assertEqual(d["action"], "ask")
        self.assertIn("④", d["question"])

    def test_opinion_is_ignored(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "我觉得④挺重要")),
                      judge=lambda t, c, image_path=None: {"kind": "opinion"})
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
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("## Chat-Driven Tasks", issues)
        self.assertIn("- [ ]", issues)
        self.assertIn("等待确认 — 发版", issues)

    def test_record_posts_issue_item_and_does_not_enqueue(self):
        st = ce.dispatch(self.tmp, {"action": "record", "task": "支持导出聊天记录"},
                         self._poster, self._enqueue)
        self.assertEqual(st, "recorded")
        self.assertEqual([e[0] for e in self.events], ["post"])
        self.assertIn("已记录", self.events[0][1])
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("## Chat-Driven Tasks", issues)
        self.assertIn("- [ ]", issues)
        self.assertIn("待办 — 支持导出聊天记录", issues)

    def test_record_issue_write_uses_issues_lock(self):
        calls = []
        orig_acquire = ce.acquire_lock
        orig_release = ce.release_lock

        def fake_acquire(lock_path, run_id, ttl, wait=0):
            calls.append(("acquire", os.path.basename(lock_path), bool(run_id), ttl, wait))
            return True

        def fake_release(lock_path, run_id=None):
            calls.append(("release", os.path.basename(lock_path), bool(run_id)))

        try:
            ce.acquire_lock = fake_acquire
            ce.release_lock = fake_release
            ce.dispatch(self.tmp, {"action": "record", "task": "支持导出聊天记录"},
                        self._poster, self._enqueue)
        finally:
            ce.acquire_lock = orig_acquire
            ce.release_lock = orig_release

        self.assertEqual(calls[0], ("acquire", "ISSUES.lock", True, 30, 3))
        self.assertEqual(calls[-1], ("release", "ISSUES.lock", True))

    def test_backslash_task_text_is_upserted_literally_and_deduped(self):
        task = "修复 C:\\1 路径和结尾 \\"

        ce.dispatch(self.tmp, {"action": "record", "task": task}, self._poster, self._enqueue)
        ce.dispatch(self.tmp, {"action": "record", "task": task}, self._poster, self._enqueue)

        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        marker = "chat-task:%s" % ce._task_id(task)
        self.assertEqual(issues.count(marker), 1)
        self.assertIn("待办 — " + task, issues)

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
        self.assertIn("## Chat-Driven Tasks", log)
        self.assertIn("<!-- chat-task:", log)
        self.assertIn("- [x]", log)
        self.assertIn("完成 — 做 ④ 英文化", log)
        self.assertIn("## 执行记录", log)
        self.assertIn("做 ④ 英文化", log)
        self.assertIn("abc1234", log)

    def test_failure_posts_and_logs_reason(self):
        ce.report(self.tmp, "做 X", {"ok": False, "summary": "测试没过"}, self.posts.append)
        self.assertTrue(any("❌" in p and "测试没过" in p for p in self.posts))
        log = self._issues()
        self.assertIn("- [ ]", log)
        self.assertIn("失败 — 做 X", log)
        self.assertIn("测试没过", log)

    def test_report_updates_task_and_execution_record_under_one_issues_lock(self):
        calls = []
        orig_acquire = ce.acquire_lock
        orig_release = ce.release_lock

        def fake_acquire(lock_path, run_id, ttl, wait=0):
            if os.path.basename(lock_path) == "ISSUES.lock":
                calls.append(("acquire", run_id))
            return True

        def fake_release(lock_path, run_id=None):
            if os.path.basename(lock_path) == "ISSUES.lock":
                calls.append(("release", run_id))

        try:
            ce.acquire_lock = fake_acquire
            ce.release_lock = fake_release
            ce.report(self.tmp, "做 ④ 英文化",
                      {"ok": True, "summary": "done", "commit": "abc1234"},
                      self.posts.append)
        finally:
            ce.acquire_lock = orig_acquire
            ce.release_lock = orig_release

        self.assertEqual([kind for kind, _run_id in calls], ["acquire", "release"])
        log = self._issues()
        self.assertIn("完成 — 做 ④ 英文化", log)
        self.assertIn("## 执行记录", log)
        self.assertIn("abc1234", log)


class TaskIssueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.tmp, ".collab"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _issues(self):
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            return f.read()

    def test_duplicate_requirement_upsert_keeps_single_marker_line(self):
        task = "支持导出聊天记录"

        ce._upsert_task_item(self.tmp, task, "open")
        ce._upsert_task_item(self.tmp, task, "open")

        marker = "chat-task:%s" % ce._task_id(task)
        issues = self._issues()
        self.assertEqual(issues.count(marker), 1)
        self.assertEqual(len([line for line in issues.splitlines() if marker in line]), 1)

    def test_task_status_transition_replaces_single_marker_line(self):
        task = "支持导出聊天记录"

        ce._upsert_task_item(self.tmp, task, "open")
        ce._upsert_task_item(self.tmp, task, "running")
        ce._upsert_task_item(self.tmp, task, "done", {"summary": "done", "commit": "abc1234"})

        marker = "chat-task:%s" % ce._task_id(task)
        lines = [line for line in self._issues().splitlines() if marker in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("- [x]", lines[0])
        self.assertIn("完成 — " + task, lines[0])
        self.assertIn("commit abc1234", lines[0])

    def test_chat_driven_section_insert_preserves_following_section(self):
        path = os.path.join(self.tmp, ".collab", "ISSUES.md")
        with open(path, "w") as f:
            f.write(
                "# Issues\n\n"
                "## Chat-Driven Tasks\n\n"
                "Existing note\n\n"
                "## Manual Tasks\n\n"
                "- keep this line\n")

        ce._upsert_task_item(self.tmp, "支持导出聊天记录", "open")

        issues = self._issues()
        self.assertLess(issues.index("待办 — 支持导出聊天记录"),
                        issues.index("## Manual Tasks"))
        self.assertIn("Existing note", issues)
        self.assertIn("## Manual Tasks\n\n- keep this line", issues)


class ExecuteOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        collab = os.path.join(self.tmp, ".collab"); os.mkdir(collab)
        self._old_keys_dir = os.environ.get("BRIDGE_KEYS_DIR")
        self._old_require = os.environ.get("BRIDGE_REQUIRE_SIGNATURES")
        self._old_execute = os.environ.get("BRIDGE_CHAT_EXECUTE")
        os.environ.pop("BRIDGE_REQUIRE_SIGNATURES", None)
        os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
        _enroll_jack(self.tmp, os.path.join(self.tmp, "keys"))
        with open(os.path.join(collab, "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": "Claude"}, f)
        with open(os.path.join(collab, "collaboration_signal.json"), "w") as f:
            json.dump({"update_id": 0}, f)
        with open(os.path.join(collab, "collaboration.md"), "w") as f:
            f.write("# Board\n\n## Chat\n\n### 2026-06-29 10:00:00 PDT\n\n%s\n"
                    % self._signed_line("把④英文化做了", "default-task"))
        self.posts = []
        # These tests exercise the processing path; arm with an empty watermark ("" skips
        # nothing) so the first execute_once processes the setup message rather than just
        # baselining it. Watermark-behavior itself is covered by its own tests below.
        ce._set_watermark(self.tmp, "")

    def tearDown(self):
        if self._old_keys_dir is None:
            os.environ.pop("BRIDGE_KEYS_DIR", None)
        else:
            os.environ["BRIDGE_KEYS_DIR"] = self._old_keys_dir
        if self._old_require is None:
            os.environ.pop("BRIDGE_REQUIRE_SIGNATURES", None)
        else:
            os.environ["BRIDGE_REQUIRE_SIGNATURES"] = self._old_require
        if self._old_execute is None:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
        else:
            os.environ["BRIDGE_CHAT_EXECUTE"] = self._old_execute
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _signed_line(self, text, msg_id, sent_at=None, img=None):
        return _signed_chat_line(self.tmp, "Jack", text, msg_id, sent_at=sent_at, img=img)

    def test_without_judge_returns_none_when_execute_flag_off(self):
        self.assertEqual(ce.execute_once(self.tmp), "none")

    def test_capture_only_records_requirement_without_executor(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % self._signed_line(
                    "记下来：后面要支持导出聊天记录", "capture-record-task"))

        st = ce.execute_once(
            self.tmp,
            judge=lambda t, c, image_path=None: {
                "kind": "record_requirement",
                "task": "支持导出聊天记录",
            },
            executor=lambda task, project, image_path=None: self.fail("capture-only must not execute"),
            poster=self.posts.append)

        self.assertEqual(st, "recorded")
        self.assertIn("capture-record-task", ce._load_handled(self.tmp))
        self.assertTrue(any("已记录:支持导出聊天记录" in p for p in self.posts))
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("待办 — 支持导出聊天记录", issues)

    def test_capture_only_actionable_records_open_task_without_executor(self):
        st = ce.execute_once(
            self.tmp,
            judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"},
            executor=lambda task, project, image_path=None: self.fail("capture-only must not execute"),
            poster=self.posts.append)

        self.assertEqual(st, "captured")
        self.assertIn("default-task", ce._load_handled(self.tmp))
        self.assertTrue(any("已记入清单(执行器未开):做 ④ 英文化" in p for p in self.posts))
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("待办 — 做 ④ 英文化", issues)
        self.assertNotIn("进行中 — 做 ④ 英文化", issues)
        self.assertNotIn("完成 — 做 ④ 英文化", issues)

    def test_capture_only_high_risk_records_open_task_without_pending_greenlight(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % self._signed_line("发版吧", "capture-release-task"))

        st = ce.execute_once(
            self.tmp,
            judge=lambda t, c, image_path=None: {
                "kind": "actionable",
                "task": "发版 v0.9",
            },
            executor=lambda task, project, image_path=None: self.fail("capture-only must not execute"),
            poster=self.posts.append)

        self.assertEqual(st, "captured")
        self.assertIn("capture-release-task", ce._load_handled(self.tmp))
        self.assertFalse(ce._pending_greenlight(self.tmp))
        self.assertTrue(any("已记入清单(执行器未开):发版 v0.9" in p for p in self.posts))
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("待办 — 发版 v0.9", issues)
        self.assertNotIn("等待确认 — 发版 v0.9", issues)

    def test_capture_only_greenlight_reply_clears_pending_greenlight_without_executor(self):
        ce._set_pending_greenlight(self.tmp, "发版 v0.9")
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:01:00 PDT\n\n"
                "%s\n" % self._signed_line("确认，执行发版", "capture-greenlight-reply"))

        st = ce.execute_once(
            self.tmp,
            judge=lambda t, c, image_path=None: self.fail("pending greenlight should bypass judge"),
            executor=lambda task, project, image_path=None: self.fail("capture-only must not execute"),
            poster=self.posts.append)

        self.assertEqual(st, "captured")
        self.assertIn("capture-greenlight-reply", ce._load_handled(self.tmp))
        self.assertFalse(ce._pending_greenlight(self.tmp))
        self.assertTrue(any("已记入清单(执行器未开):发版 v0.9" in p for p in self.posts))
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("待办 — 发版 v0.9", issues)
        self.assertNotIn("进行中 — 发版 v0.9", issues)

    def test_capture_only_second_pass_is_handled_noop(self):
        st = ce.execute_once(
            self.tmp,
            judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"},
            executor=lambda task, project, image_path=None: self.fail("capture-only must not execute"),
            poster=self.posts.append)
        self.assertEqual(st, "captured")

        st = ce.execute_once(
            self.tmp,
            judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"},
            executor=lambda task, project, image_path=None: self.fail("handled task must not execute"),
            poster=self.posts.append)

        self.assertEqual(st, "none")
        self.assertEqual(len([p for p in self.posts if "已记入清单(执行器未开)" in p]), 1)
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        marker = "chat-task:%s" % ce._task_id("做 ④ 英文化")
        self.assertEqual(issues.count(marker), 1)

    def test_capture_only_default_poster_does_not_reprocess_own_ack(self):
        st = ce.execute_once(
            self.tmp,
            judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"},
            executor=lambda task, project, image_path=None: self.fail("capture-only must not execute"))
        self.assertEqual(st, "captured")

        board_path = os.path.join(self.tmp, ".collab", "collaboration.md")
        with open(board_path) as f:
            board_after_ack = f.read()
        self.assertIn("**Claude:** 已记入清单(执行器未开):做 ④ 英文化", board_after_ack)

        st = ce.execute_once(
            self.tmp,
            judge=lambda t, c, image_path=None: self.fail("lead ack must not be judged"),
            executor=lambda task, project, image_path=None: self.fail("lead ack must not execute"))

        self.assertEqual(st, "none")
        with open(board_path) as f:
            self.assertEqual(f.read(), board_after_ack)

    def test_capture_only_actionable_retries_after_issues_lock_busy_without_done_or_post(self):
        orig_acquire = ce.acquire_lock
        orig_release = ce.release_lock
        issues_acquires = []

        def fake_acquire(lock_path, run_id, ttl, wait=0):
            if os.path.basename(lock_path) == "ISSUES.lock":
                issues_acquires.append(run_id)
                return len(issues_acquires) > 1
            return True

        def fake_release(lock_path, run_id=None):
            pass

        try:
            ce.acquire_lock = fake_acquire
            ce.release_lock = fake_release
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"},
                executor=lambda task, project, image_path=None: self.fail("capture-only must not execute"),
                poster=self.posts.append)
            self.assertEqual(st, "retry")
            self.assertNotIn("default-task", ce._load_handled(self.tmp))
            self.assertEqual(self.posts, [])

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"},
                executor=lambda task, project, image_path=None: self.fail("capture-only must not execute"),
                poster=self.posts.append)
        finally:
            ce.acquire_lock = orig_acquire
            ce.release_lock = orig_release

        self.assertEqual(st, "captured")
        self.assertIn("default-task", ce._load_handled(self.tmp))
        self.assertEqual(len([p for p in self.posts if "已记入清单(执行器未开)" in p]), 1)

    def test_runs_full_path_with_injected_judge_and_executor(self):
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"},
                executor=lambda task, project, image_path=None: {"ok": True, "summary": "done", "commit": "abc1234"},
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
        self.assertEqual(st, "done")
        joined = "\n".join(self.posts)
        self.assertIn("开始执行", joined)     # ack
        self.assertIn("✅", joined)            # report
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("## Chat-Driven Tasks", issues)
        self.assertIn("- [x]", issues)
        self.assertIn("完成 — 做 ④ 英文化", issues)
        self.assertIn("abc1234", issues)

    def test_execute_once_creates_running_issue_before_executor(self):
        seen = {}
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md"), "w") as f:
            f.write("")
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            def executor(task, project, image_path=None):
                with open(os.path.join(project, ".collab", "ISSUES.md")) as f:
                    seen["during"] = f.read()
                return {"ok": True, "summary": "done", "commit": "abc1234"}

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"},
                executor=executor,
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertIn("## Chat-Driven Tasks", seen["during"])
        self.assertIn("- [ ]", seen["during"])
        self.assertIn("进行中 — 做 ④ 英文化", seen["during"])

    def test_execute_once_records_requirement_without_executor(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % self._signed_line(
                    "记下来：后面要支持导出聊天记录", "record-only-task"))

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "record_requirement",
                    "task": "支持导出聊天记录",
                },
                executor=lambda task, project, image_path=None: self.fail("record-only task must not execute"),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "recorded")
        self.assertIn("record-only-task", ce._load_handled(self.tmp))
        self.assertTrue(any("已记录" in p for p in self.posts))
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("待办 — 支持导出聊天记录", issues)

    def test_record_requirement_retries_after_issues_lock_busy_without_done_or_post(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % self._signed_line(
                    "记下来：后面要支持导出聊天记录", "record-lock-busy"))

        orig_acquire = ce.acquire_lock
        orig_release = ce.release_lock
        issues_acquires = []

        def fake_acquire(lock_path, run_id, ttl, wait=0):
            if os.path.basename(lock_path) == "ISSUES.lock":
                issues_acquires.append(run_id)
                return len(issues_acquires) > 1
            return True

        def fake_release(lock_path, run_id=None):
            pass

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            ce.acquire_lock = fake_acquire
            ce.release_lock = fake_release

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "record_requirement",
                    "task": "支持导出聊天记录",
                },
                executor=lambda task, project, image_path=None: self.fail("record-only task must not execute"),
                poster=self.posts.append)
            self.assertEqual(st, "retry")
            self.assertNotIn("record-lock-busy", ce._load_handled(self.tmp))
            self.assertEqual(self.posts, [])

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "record_requirement",
                    "task": "支持导出聊天记录",
                },
                executor=lambda task, project, image_path=None: self.fail("record-only task must not execute"),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
            ce.acquire_lock = orig_acquire
            ce.release_lock = orig_release

        self.assertEqual(st, "recorded")
        self.assertIn("record-lock-busy", ce._load_handled(self.tmp))
        self.assertEqual(len([p for p in self.posts if "已记录" in p]), 1)
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        marker = "chat-task:%s" % ce._task_id("支持导出聊天记录")
        self.assertEqual(issues.count(marker), 1)
        self.assertIn("待办 — 支持导出聊天记录", issues)

    def test_greenlight_reply_executes_pending_high_risk_issue(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % self._signed_line("发版吧", "release-request"))

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "actionable",
                    "task": "发版 v0.9",
                },
                executor=lambda task, project, image_path=None: self.fail("high-risk task must wait for greenlight"),
                poster=self.posts.append)
            self.assertEqual(st, "requested-greenlight")
            with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
                self.assertIn("等待确认 — 发版 v0.9", f.read())

            with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
                f.write(
                    "# Board\n\n## Chat\n\n"
                    "### 2026-06-29 10:01:00 PDT\n\n"
                    "%s\n"
                    "### 2026-06-29 10:00:00 PDT\n\n"
                    "%s\n" % (
                        self._signed_line("确认，执行发版", "release-greenlight"),
                        self._signed_line("发版吧", "release-request"),
                    ))

            calls = []
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "actionable",
                    "task": "发版 v0.9",
                },
                executor=lambda task, project, image_path=None: (
                    calls.append(task) or {"ok": True, "summary": "released", "commit": "def5678"}
                ),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertEqual(calls, ["发版 v0.9"])
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("完成 — 发版 v0.9", issues)
        self.assertIn("def5678", issues)

    def test_greenlight_confirmation_executes_persisted_pending_task_when_judge_task_differs(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % self._signed_line("发版吧", "release-request-diff"))

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "actionable",
                    "task": "发版 v0.9",
                },
                executor=lambda task, project, image_path=None: self.fail("high-risk task must wait for greenlight"),
                poster=self.posts.append)
            self.assertEqual(st, "requested-greenlight")

            with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
                f.write(
                    "# Board\n\n## Chat\n\n"
                    "### 2026-06-29 10:01:00 PDT\n\n"
                    "%s\n"
                    "### 2026-06-29 10:00:00 PDT\n\n"
                    "%s\n" % (
                        self._signed_line("确认", "release-greenlight-diff"),
                        self._signed_line("发版吧", "release-request-diff"),
                    ))

            calls = []
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "actionable",
                    "task": "发版",
                },
                executor=lambda task, project, image_path=None: (
                    calls.append(task) or {"ok": True, "summary": "released", "commit": "def5678"}
                ),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertEqual(calls, ["发版 v0.9"])
        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json")) as f:
            state = json.load(f)
        self.assertNotIn("pending_greenlight", state)

    def test_casual_greenlight_without_pending_task_does_not_execute_from_issues_marker(self):
        ce._upsert_task_item(self.tmp, "发版 v0.9", "awaiting_greenlight")
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % self._signed_line("可以", "casual-ok-no-pending"))

        calls = []
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "actionable",
                    "task": "发版 v0.9",
                },
                executor=lambda task, project, image_path=None: (
                    calls.append(task) or {"ok": True, "summary": "wrong"}
                ),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "requested-greenlight")
        self.assertEqual(calls, [])
        self.assertIn("casual-ok-no-pending", ce._load_handled(self.tmp))

    def test_repeated_high_risk_request_does_not_execute_pending_issue(self):
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % self._signed_line("发版吧", "release-request"))

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "actionable",
                    "task": "发版 v0.9",
                },
                executor=lambda task, project, image_path=None: self.fail("high-risk task must wait for greenlight"),
                poster=self.posts.append)
            self.assertEqual(st, "requested-greenlight")

            with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
                f.write(
                    "# Board\n\n## Chat\n\n"
                    "### 2026-06-29 10:01:00 PDT\n\n"
                    "%s\n"
                    "### 2026-06-29 10:00:00 PDT\n\n"
                    "%s\n" % (
                        self._signed_line("发版吧", "release-repeat"),
                        self._signed_line("发版吧", "release-request"),
                    ))

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "actionable",
                    "task": "发版 v0.9",
                },
                executor=lambda task, project, image_path=None: self.fail("repeat is not greenlight"),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "requested-greenlight")
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            issues = f.read()
        self.assertIn("等待确认 — 发版 v0.9", issues)
        self.assertNotIn("完成 — 发版 v0.9", issues)

    def test_execute_once_passes_resolved_image_path_to_executor(self):
        uploads = os.path.join(self.tmp, ".collab", "chat_uploads")
        os.makedirs(uploads)
        ref = "a1b2c3d4e5f6.png"
        image = os.path.join(uploads, ref)
        with open(image, "wb") as f:
            f.write(b"png")
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write(
                "# Board\n\n## Chat\n\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % self._signed_line(
                    "fix what is in the screenshot", "image-exec",
                    img="a1b2c3d4e5f6.png"))

        seen = {}
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {
                    "kind": "actionable",
                    "task": "fix the screenshot bug",
                },
                executor=lambda task, project, image_path=None: (
                    seen.update({"task": task, "project": project, "image_path": image_path})
                    or {"ok": True, "summary": "done"}
                ),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertEqual(seen["task"], "fix the screenshot bug")
        self.assertEqual(seen["image_path"], image)

    def test_execute_once_passes_none_to_executor_without_image(self):
        seen = {}
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "plain task"},
                executor=lambda task, project, image_path=None: (
                    seen.update({"image_path": image_path}) or {"ok": True, "summary": "done"}
                ),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)

        self.assertEqual(st, "done")
        self.assertIsNone(seen["image_path"])

    def test_default_poster_formats_executor_messages_as_lead(self):
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "做 ④ 英文化"},
                executor=lambda task, project, image_path=None: {"ok": True, "summary": "done"},
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
                "%s\n"
                "### 2026-06-29 09:59:00 PDT\n\n"
                "**Claude:** waiting for your greenlight\n"
                % self._signed_line("yes, do it", "greenlight-1"))
        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json"), "w") as f:
            json.dump({"handled": ["greenlight-1"]}, f)

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "do it"},
                executor=lambda task, project, image_path=None: self.fail("executor must not re-run"),
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
                "%s\n" % self._signed_line(
                    "yes, execute the next task", "human-greenlight-older"))

        seen = {}
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            def judge(t, c, image_path=None):
                seen["text"] = t
                return {"kind": "actionable", "task": "execute the next task"}

            st = ce.execute_once(
                self.tmp,
                judge=judge,
                executor=lambda task, project, image_path=None: {"ok": True, "summary": "done"},
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
                "%s\n"
                "### 2026-06-29 10:00:00 PDT\n\n"
                "%s\n" % (
                    self._signed_line("do the second task", "human-second"),
                    self._signed_line("do the first task", "human-first")))
        with open(os.path.join(self.tmp, ".collab", "chat_execute_state.json"), "w") as f:
            json.dump({"handled": ["human-first"]}, f)

        seen = {}
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            def judge(t, c, image_path=None):
                seen["text"] = t
                return {"kind": "actionable", "task": "second task"}

            st = ce.execute_once(
                self.tmp,
                judge=judge,
                executor=lambda task, project, image_path=None: {"ok": True, "summary": "done"},
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
                "%s\n"
                "### 2026-06-29 09:59:00 PDT\n\n"
                "**Claude:** waiting for your greenlight\n"
                % self._signed_line("yes, execute the task", "greenlight-2"))

        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "execute the task"},
                executor=lambda task, project, image_path=None: {"ok": True, "summary": "done"},
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
                "%s\n" % self._signed_line("ship the small safe fix", "crashy-task"))

        calls = []
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            def boom(task, project, image_path=None):
                calls.append(task)
                raise RuntimeError("executor crashed after start")

            with self.assertRaises(RuntimeError):
                ce.execute_once(
                    self.tmp,
                    judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "safe fix"},
                    executor=boom,
                    poster=self.posts.append)

            self.assertEqual(calls, ["safe fix"])

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "must not rerun"},
                executor=lambda task, project, image_path=None: self.fail("running task must not be rerun"),
                poster=self.posts.append)
            self.assertEqual(st, "none")
            warnings = [p for p in self.posts if "上一个任务执行中断" in p]
            self.assertEqual(len(warnings), 1)
            self.assertIn("ship the small safe fix", warnings[0])
            with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
                issues = f.read()
            self.assertIn("失败 — safe fix", issues)
            self.assertIn("执行中断", issues)

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "must not rerun"},
                executor=lambda task, project, image_path=None: self.fail("running task must not be rerun"),
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
                "%s\n" % self._signed_line("do a retryable task", "claimed-before-run"))
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
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "retryable task"},
                executor=lambda task, project, image_path=None: calls.append(task) or {"ok": True, "summary": "done"},
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
                "%s\n" % self._signed_line("do not double execute this", "fresh-live-claim"))
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
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "must not run"},
                executor=lambda task, project, image_path=None: calls.append(task) or {"ok": True, "summary": "wrong"},
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
                "%s\n" % self._signed_line("recover old live-pid claim", "stale-live-claim"))
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
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "recover stale claim"},
                executor=lambda task, project, image_path=None: calls.append(task) or {"ok": True, "summary": "done"},
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
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "should not execute"},
                executor=lambda task, project, image_path=None: self.fail("executor must not run when busy"),
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
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "single run"},
                executor=lambda task, project, image_path=None: calls.append(task) or {"ok": True, "summary": "done"},
                poster=self.posts.append)
            self.assertEqual(st, "done")

            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "single run"},
                executor=lambda task, project, image_path=None: self.fail("done task must not run twice"),
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

    def test_first_run_arms_watermark_and_skips_backlog(self):
        # Fresh (no watermark): the first execute_once baselines the existing board and
        # processes NOTHING — the pre-existing backlog must never be retro-judged/executed.
        os.remove(ce._watermark_path(self.tmp))  # undo setUp's empty watermark
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: self.fail("backlog must not be judged"),
                executor=lambda *a, **k: self.fail("backlog must not execute"),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
        self.assertEqual(st, "armed")
        self.assertEqual(self.posts, [])
        self.assertIsNotNone(ce._get_watermark(self.tmp))

    def test_only_messages_after_watermark_are_processed(self):
        # Watermark at the old message's ts: the old one is skipped, only the NEWER one is judged.
        ce._set_watermark(self.tmp, "2026-06-29 10:00:00 PDT")
        with open(os.path.join(self.tmp, ".collab", "collaboration.md"), "w") as f:
            f.write("# Board\n\n## Chat\n\n"
                    "### 2026-06-29 11:00:00 PDT\n\n%s\n"
                    "### 2026-06-29 10:00:00 PDT\n\n%s\n" % (
                        self._signed_line("新指令做⑤", "new-task"),
                        self._signed_line("老消息别碰", "old-task")))
        seen = []
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            ce.execute_once(
                self.tmp,
                judge=lambda t, c, image_path=None: (
                    seen.append(t) or {"kind": "record_requirement", "task": t}),
                executor=lambda *a, **k: self.fail("record-only must not execute"),
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
        self.assertEqual(seen, ["新指令做⑤"])  # only ts > watermark judged; old skipped


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
