import os, pathlib, sys, tempfile, unittest
import subprocess
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_executor as ex

class RunTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.calls = []

    def _impl_ok(self, project, task, findings, image_path=None):
        self.calls.append(("implement", findings))
        return {"ok": True, "head_sha": "head1", "test_summary": "5 passed"}

    def _push_ok(self, project):
        self.calls.append(("push", None))
        return {"ok": True, "pushed_sha": "head1"}

    def test_go_path_pushes_and_returns_ok(self):
        r = ex.run_task(self.tmp, "做 ④",
                        implement=self._impl_ok,
                        review=lambda p, sha: {"verdict": "GO", "note": "clean"},
                        push=self._push_ok)
        self.assertTrue(r["ok"])
        self.assertEqual(r["commit"], "head1")
        self.assertEqual([c[0] for c in self.calls], ["implement", "push"])

    def test_fix_first_then_go_loops_once_then_pushes(self):
        verdicts = [{"verdict": "FIX-FIRST", "note": "missing test"},
                    {"verdict": "GO", "note": "ok"}]
        r = ex.run_task(self.tmp, "做 ④",
                        implement=self._impl_ok,
                        review=lambda p, sha: verdicts.pop(0),
                        push=self._push_ok)
        self.assertTrue(r["ok"])
        # implement called twice (2nd with findings), then push
        self.assertEqual([c[0] for c in self.calls], ["implement", "implement", "push"])
        self.assertIn("missing test", self.calls[1][1])      # findings threaded back

    def test_fix_first_exhausted_reports_failure_no_push(self):
        r = ex.run_task(self.tmp, "做 ④",
                        implement=self._impl_ok,
                        review=lambda p, sha: {"verdict": "FIX-FIRST", "note": "still broken"},
                        push=self._push_ok, max_fix_rounds=2)
        self.assertFalse(r["ok"])
        self.assertNotIn("push", [c[0] for c in self.calls])
        self.assertIn("still broken", r["summary"])

    def test_implement_failure_reports_no_review_no_push(self):
        r = ex.run_task(self.tmp, "做 ④",
                        implement=lambda p, t, f, image_path=None: {
                            "ok": False, "head_sha": "", "test_summary": "tests red"},
                        review=lambda p, sha: {"verdict": "GO", "note": ""},
                        push=self._push_ok)
        self.assertFalse(r["ok"])
        self.assertEqual(self.calls, [])              # no push
        self.assertIn("tests red", r["summary"])

    def test_run_task_forwards_image_path_to_initial_and_fix_implements(self):
        image_path = "/tmp/a1b2c3d4.png"
        seen = []
        verdicts = [{"verdict": "FIX-FIRST", "note": "missing image-based assertion"},
                    {"verdict": "GO", "note": "ok"}]

        def implement(project, task, findings, image_path=None):
            seen.append((task, findings, image_path))
            return {"ok": True, "head_sha": "head%d" % len(seen), "test_summary": "green"}

        r = ex.run_task(self.tmp, "fix screenshot bug",
                        implement=implement,
                        review=lambda p, sha: verdicts.pop(0),
                        push=self._push_ok,
                        image_path=image_path)

        self.assertTrue(r["ok"])
        self.assertEqual([call[2] for call in seen], [image_path, image_path])
        self.assertEqual(seen[1][1], "missing image-based assertion")

    def test_push_failure_reports_not_ok(self):
        r = ex.run_task(self.tmp, "做 ④",
                        implement=self._impl_ok,
                        review=lambda p, sha: {"verdict": "GO", "note": ""},
                        push=lambda p: {"ok": False, "pushed_sha": ""})
        self.assertFalse(r["ok"])
        self.assertIn("push", r["summary"].lower())

    def test_run_task_executor_forwards_image_path_to_default_implement(self):
        image_path = "/tmp/a1b2c3d4.png"
        seen = {}
        old_impl = ex.default_implement
        old_review = ex.default_review
        old_push = ex.default_push
        try:
            def fake_impl(project, task, findings, image_path=None):
                seen["implement"] = (project, task, findings, image_path)
                return {"ok": True, "head_sha": "head1", "test_summary": "green"}

            ex.default_implement = fake_impl
            ex.default_review = lambda project, head: {"verdict": "GO", "note": "ok"}
            ex.default_push = lambda project: {"ok": True, "pushed_sha": "head1"}

            r = ex.run_task_executor("fix screenshot bug", self.tmp, image_path=image_path)
        finally:
            ex.default_implement = old_impl
            ex.default_review = old_review
            ex.default_push = old_push

        self.assertTrue(r["ok"])
        self.assertEqual(seen["implement"], (self.tmp, "fix screenshot bug", "", image_path))

    def test_default_implement_adds_image_flag_only_when_image_path_is_set(self):
        old_run = ex.subprocess.run
        old_head = ex._git_head
        old_root = ex.find_project_root
        captured = []
        try:
            heads = iter(["base1", "head1", "base2", "head2"])
            ex._git_head = lambda project: next(heads)
            ex.find_project_root = lambda project: project

            class Result:
                stdout = ""

            def fake_run(cmd, **kwargs):
                captured.append((cmd, kwargs))
                return Result()

            ex.subprocess.run = fake_run
            with_image = ex.default_implement(
                self.tmp, "fix screenshot bug", "", image_path="/tmp/a1b2c3d4.png")
            without_image = ex.default_implement(
                self.tmp, "fix text bug", "", image_path=None)
        finally:
            ex.subprocess.run = old_run
            ex._git_head = old_head
            ex.find_project_root = old_root

        self.assertTrue(with_image["ok"])
        self.assertTrue(without_image["ok"])
        codex_cmds = [cmd for cmd, _kwargs in captured if cmd[:2] == ["codex", "exec"]]
        self.assertIn("-i", codex_cmds[0])
        self.assertEqual(codex_cmds[0][codex_cmds[0].index("-i") + 1], "/tmp/a1b2c3d4.png")
        self.assertNotIn("-i", codex_cmds[1])

    def test_default_implement_uses_workspace_write_not_danger_full_access(self):
        old_run = ex.subprocess.run
        old_head = ex._git_head
        old_root = ex.find_project_root
        captured = []
        try:
            heads = iter(["base", "head"])
            ex._git_head = lambda project: next(heads)
            ex.find_project_root = lambda project: project

            class Result:
                stdout = ""

            def fake_run(cmd, **kwargs):
                captured.append((cmd, kwargs))
                return Result()

            ex.subprocess.run = fake_run
            r = ex.default_implement(self.tmp, "fix push bypass", "")
        finally:
            ex.subprocess.run = old_run
            ex._git_head = old_head
            ex.find_project_root = old_root

        codex_cmds = [cmd for cmd, _kwargs in captured if cmd[:2] == ["codex", "exec"]]
        self.assertTrue(r["ok"])
        self.assertEqual(len(codex_cmds), 1)
        self.assertIn("-s", codex_cmds[0])
        self.assertEqual(codex_cmds[0][codex_cmds[0].index("-s") + 1], "workspace-write")
        self.assertNotIn("danger-full-access", codex_cmds[0])

    def test_default_implement_still_adds_image_flag_under_new_sandbox(self):
        old_run = ex.subprocess.run
        old_head = ex._git_head
        old_root = ex.find_project_root
        captured = []
        try:
            heads = iter(["base", "head"])
            ex._git_head = lambda project: next(heads)
            ex.find_project_root = lambda project: project

            class Result:
                stdout = ""

            def fake_run(cmd, **kwargs):
                captured.append((cmd, kwargs))
                return Result()

            ex.subprocess.run = fake_run
            r = ex.default_implement(
                self.tmp, "fix screenshot bug", "", image_path="/tmp/a1b2c3d4.png")
        finally:
            ex.subprocess.run = old_run
            ex._git_head = old_head
            ex.find_project_root = old_root

        codex_cmds = [cmd for cmd, _kwargs in captured if cmd[:2] == ["codex", "exec"]]
        self.assertTrue(r["ok"])
        self.assertEqual(len(codex_cmds), 1)
        self.assertEqual(codex_cmds[0][codex_cmds[0].index("-s") + 1], "workspace-write")
        self.assertIn("-i", codex_cmds[0])
        self.assertEqual(codex_cmds[0][codex_cmds[0].index("-i") + 1], "/tmp/a1b2c3d4.png")

    def test_default_implement_aborts_if_implementer_moved_remote_ref(self):
        old_head = ex._git_head
        old_root = ex.find_project_root
        sentinel = object()
        old_remote = getattr(ex, "_remote_tracking_ref", sentinel)
        old_run = ex.subprocess.run
        try:
            heads = iter(["base", "head"])
            refs = ["remote-before", "remote-after"]
            ex._git_head = lambda project: next(heads)
            ex.find_project_root = lambda project: project
            ex._remote_tracking_ref = lambda project: refs.pop(0)

            class Result:
                stdout = ""

            ex.subprocess.run = lambda *args, **kwargs: Result()
            r = ex.default_implement(self.tmp, "fix push bypass", "")
        finally:
            ex.subprocess.run = old_run
            ex._git_head = old_head
            ex.find_project_root = old_root
            if old_remote is sentinel:
                delattr(ex, "_remote_tracking_ref")
            else:
                ex._remote_tracking_ref = old_remote

        self.assertFalse(r["ok"])
        self.assertEqual(r["head_sha"], "head")
        self.assertIn("SECURITY: implementer moved the remote ref", r["test_summary"])

    def test_default_implement_ok_when_remote_ref_unchanged(self):
        old_head = ex._git_head
        old_root = ex.find_project_root
        sentinel = object()
        old_remote = getattr(ex, "_remote_tracking_ref", sentinel)
        old_run = ex.subprocess.run
        try:
            heads = iter(["base", "head"])
            refs = ["remote-before", "remote-before"]
            ex._git_head = lambda project: next(heads)
            ex.find_project_root = lambda project: project
            ex._remote_tracking_ref = lambda project: refs.pop(0)

            class Result:
                stdout = ""

            ex.subprocess.run = lambda *args, **kwargs: Result()
            r = ex.default_implement(self.tmp, "fix push bypass", "")
        finally:
            ex.subprocess.run = old_run
            ex._git_head = old_head
            ex.find_project_root = old_root
            if old_remote is sentinel:
                delattr(ex, "_remote_tracking_ref")
            else:
                ex._remote_tracking_ref = old_remote

        self.assertTrue(r["ok"])
        self.assertEqual(r["head_sha"], "head")
        self.assertEqual(refs, [])

class GitHeadTests(unittest.TestCase):
    def test_git_head_returns_current_sha(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", d], check=True)
        subprocess.run(["git", "-C", d, "-c", "user.email=a@b.c",
                        "-c", "user.name=t", "commit", "--allow-empty", "-m", "x"],
                       check=True)
        sha = ex._git_head(d)
        self.assertRegex(sha, r"^[0-9a-f]{40}$")

    def test_remote_tracking_ref_returns_upstream_sha(self):
        old_run = ex.subprocess.run
        try:
            calls = []

            class Result:
                def __init__(self, stdout=""):
                    self.stdout = stdout

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                if cmd[-1] == "@{u}":
                    return Result("refs/remotes/origin/main\n")
                if cmd[-1] == "refs/remotes/origin/main":
                    return Result("abc123\n")
                return Result("")

            ex.subprocess.run = fake_run
            self.assertEqual(ex._remote_tracking_ref("/repo"), "abc123")
        finally:
            ex.subprocess.run = old_run

        self.assertIn(
            ["git", "-C", "/repo", "rev-parse", "--verify", "-q", "refs/remotes/origin/main"],
            calls)

    def test_remote_tracking_ref_falls_back_to_origin_current_branch(self):
        old_run = ex.subprocess.run
        try:
            calls = []

            class Result:
                def __init__(self, stdout=""):
                    self.stdout = stdout

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                if cmd[-1] == "@{u}":
                    return Result("")
                if cmd[-2:] == ["--abbrev-ref", "HEAD"]:
                    return Result("feature/ec2\n")
                if cmd[-1] == "refs/remotes/origin/feature/ec2":
                    return Result("def456\n")
                return Result("")

            ex.subprocess.run = fake_run
            self.assertEqual(ex._remote_tracking_ref("/repo"), "def456")
        finally:
            ex.subprocess.run = old_run

        self.assertIn(
            ["git", "-C", "/repo", "rev-parse", "--verify", "-q",
             "refs/remotes/origin/feature/ec2"],
            calls)

class RolesWiringTests(unittest.TestCase):
    def test_implementer_and_reviewer_roles_resolved_from_config(self):
        d = tempfile.mkdtemp(); os.mkdir(os.path.join(d, ".collab"))
        import json
        with open(os.path.join(d, ".collab", "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": "Claude"}, f)
        impl, rev = ex._roles_for(d)        # (implementer, reviewer)
        self.assertEqual(rev, "Claude")     # lead reviews
        self.assertNotEqual(impl, rev)      # implementer is the other side
        self.assertNotEqual(impl, "")       # and is concrete

class ParseVerdictTests(unittest.TestCase):
    def test_parses_go_with_reason(self):
        v, note = ex._parse_verdict("blah blah\nVERDICT: GO looks clean and tests pass")
        self.assertEqual(v, "GO")
        self.assertIn("looks clean", note)

    def test_parses_fix_first(self):
        v, note = ex._parse_verdict("VERDICT: FIX-FIRST missing a test")
        self.assertEqual(v, "FIX-FIRST")

    def test_missing_verdict_fails_safe_to_fix_first(self):
        v, note = ex._parse_verdict("I reviewed it, looks fine to me")
        self.assertEqual(v, "FIX-FIRST")

    def test_last_verdict_line_wins(self):
        v, note = ex._parse_verdict(
            "I briefly thought VERDICT: GO but on reflection\nVERDICT: FIX-FIRST needs a test")
        self.assertEqual(v, "FIX-FIRST")
        self.assertIn("needs a test", note)


class DefaultReviewRecordTests(unittest.TestCase):
    def _proj(self):
        import json
        import _review
        d = tempfile.mkdtemp()
        os.mkdir(os.path.join(d, ".collab"))
        me = _review._detected_recorder("Claude")   # match THIS env so record() succeeds
        with open(os.path.join(d, ".collab", "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": me}, f)
        return d

    def test_records_parsed_verdict_to_the_ledger(self):
        v = ex.default_review(self._proj(), "deadbeefcafe1234",
                              call_llm=lambda prompt, root: "reasoning here\nVERDICT: GO clean",
                              run_tests=lambda root: True)
        self.assertEqual(v["verdict"], "GO")
        self.assertIn("clean", v.get("note", ""))

    def test_failing_tests_force_fix_first_even_if_llm_says_go(self):
        v = ex.default_review(self._proj(), "deadbeefcafe5678",
                              call_llm=lambda prompt, root: "VERDICT: GO looks fine to me",
                              run_tests=lambda root: False)
        self.assertEqual(v["verdict"], "FIX-FIRST")   # red tests override an LLM GO
        self.assertIn("test", v.get("note", "").lower())

    def test_passing_tests_with_go_records_go(self):
        v = ex.default_review(self._proj(), "deadbeefcafe9012",
                              call_llm=lambda prompt, root: "VERDICT: GO clean",
                              run_tests=lambda root: True)
        self.assertEqual(v["verdict"], "GO")

    def test_record_failure_forces_fix_first_even_if_llm_and_tests_pass(self):
        import _review
        old_record = _review.record
        old_latest = _review.latest_verdict
        try:
            _review.record = lambda *a, **kw: "actor_mismatch"
            _review.latest_verdict = lambda *a, **kw: {"verdict": "GO", "note": "stale go"}

            v = ex.default_review(self._proj(), "deadbeefcafe3456",
                                  call_llm=lambda prompt, root: "VERDICT: GO clean",
                                  run_tests=lambda root: True)
        finally:
            _review.record = old_record
            _review.latest_verdict = old_latest

        self.assertEqual(v["verdict"], "FIX-FIRST")
        self.assertIn("could not record review: actor_mismatch", v.get("note", ""))


class RunTestsGateTests(unittest.TestCase):
    def test_empty_unittest_discovery_is_not_green(self):
        d = tempfile.mkdtemp()
        os.mkdir(os.path.join(d, "tests"))
        self.assertFalse(ex._run_tests(d))


if __name__ == "__main__":
    unittest.main()
