import os, pathlib, sys, tempfile, unittest
import subprocess
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_executor as ex

class RunTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.calls = []

    def _impl_ok(self, project, task, findings):
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
                        implement=lambda p, t, f: {"ok": False, "head_sha": "", "test_summary": "tests red"},
                        review=lambda p, sha: {"verdict": "GO", "note": ""},
                        push=self._push_ok)
        self.assertFalse(r["ok"])
        self.assertEqual(self.calls, [])              # no push
        self.assertIn("tests red", r["summary"])

    def test_push_failure_reports_not_ok(self):
        r = ex.run_task(self.tmp, "做 ④",
                        implement=self._impl_ok,
                        review=lambda p, sha: {"verdict": "GO", "note": ""},
                        push=lambda p: {"ok": False, "pushed_sha": ""})
        self.assertFalse(r["ok"])
        self.assertIn("push", r["summary"].lower())

class GitHeadTests(unittest.TestCase):
    def test_git_head_returns_current_sha(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", d], check=True)
        subprocess.run(["git", "-C", d, "-c", "user.email=a@b.c",
                        "-c", "user.name=t", "commit", "--allow-empty", "-m", "x"],
                       check=True)
        sha = ex._git_head(d)
        self.assertRegex(sha, r"^[0-9a-f]{40}$")

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


if __name__ == "__main__":
    unittest.main()
