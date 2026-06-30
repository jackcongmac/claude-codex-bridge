import os, pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_execute as ce

class HighRiskTests(unittest.TestCase):
    def test_flags_release_delete_publish(self):
        for t in ["发版 v0.9", "打 tag v1", "删掉这个文件", "git push --force",
                  "publish to npm", "release the build", "drop the table"]:
            self.assertTrue(ce.is_high_risk(t), t)

    def test_allows_routine_work(self):
        for t in ["把④英文化做了", "加个键盘导航", "修一下时间戳显示", "run the tests"]:
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

if __name__ == "__main__":
    unittest.main()
