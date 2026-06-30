import os, pathlib, sys, tempfile, unittest
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

if __name__ == "__main__":
    unittest.main()
