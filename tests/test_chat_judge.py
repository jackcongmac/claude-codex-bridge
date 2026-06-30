import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_judge as judge


class ChatJudgeTests(unittest.TestCase):
    def test_is_actionable_identifies_actionable_verdicts(self):
        self.assertIs(judge.is_actionable({"kind": "actionable"}), True)
        self.assertIs(judge.is_actionable({"kind": "opinion"}), False)

    def test_clean_json_actionable_extracts_task(self):
        out = judge.classify(
            "把登录错误修一下",
            [],
            lambda prompt: '{"kind":"actionable","task":"修复登录错误"}',
        )

        self.assertEqual(out, {"kind": "actionable", "task": "修复登录错误"})

    def test_wrapped_json_actionable_is_still_parsed(self):
        raw = 'Here is the result:\n```json\n{"kind":"actionable","task":"跑测试"}\n```'

        out = judge.classify("跑一下测试", [], lambda prompt: raw)

        self.assertEqual(out, {"kind": "actionable", "task": "跑测试"})

    def test_opinion_json_returns_opinion_only(self):
        out = judge.classify(
            "我觉得这个方案不错",
            [],
            lambda prompt: '{"kind":"opinion","task":"ignored","question":"ignored"}',
        )

        self.assertEqual(out, {"kind": "opinion"})

    def test_bad_or_invalid_model_output_fails_safe_to_ambiguous(self):
        cases = [
            "not json",
            "",
            '{"kind":"execute","task":"do it"}',
            '{"task":"missing kind"}',
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                out = judge.classify("开始做吧", [], lambda prompt, raw=raw: raw)
                self.assertEqual(
                    out,
                    {"kind": "ambiguous", "question": "你是要现在就开始做吗?"},
                )

    def test_actionable_without_task_falls_back_to_original_text(self):
        out = judge.classify(
            "  把④英文化做了  ",
            [],
            lambda prompt: '{"kind":"actionable"}',
        )

        self.assertEqual(out, {"kind": "actionable", "task": "把④英文化做了"})


if __name__ == "__main__":
    unittest.main()
