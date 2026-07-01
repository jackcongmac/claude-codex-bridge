import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_judge as judge


class ChatJudgeTests(unittest.TestCase):
    def test_is_actionable_identifies_actionable_verdicts(self):
        self.assertIs(judge.is_actionable({"kind": "actionable"}), True)
        self.assertIs(judge.is_actionable({"kind": "opinion"}), False)

    def test_is_ambiguous_identifies_ambiguous_verdicts(self):
        self.assertIs(judge.is_ambiguous({"kind": "ambiguous"}), True)
        self.assertIs(judge.is_ambiguous({"kind": "actionable"}), False)

    def test_clean_json_actionable_extracts_task(self):
        out = judge.classify(
            "把登录错误修一下",
            [],
            lambda prompt, image_path=None: '{"kind":"actionable","task":"修复登录错误"}',
        )

        self.assertEqual(out, {"kind": "actionable", "task": "修复登录错误"})

    def test_wrapped_json_actionable_is_still_parsed(self):
        raw = 'Here is the result:\n```json\n{"kind":"actionable","task":"跑测试"}\n```'

        out = judge.classify("跑一下测试", [], lambda prompt, image_path=None: raw)

        self.assertEqual(out, {"kind": "actionable", "task": "跑测试"})

    def test_opinion_json_returns_opinion_only(self):
        out = judge.classify(
            "我觉得这个方案不错",
            [],
            lambda prompt, image_path=None: '{"kind":"opinion","task":"ignored","question":"ignored"}',
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
                out = judge.classify(
                    "开始做吧",
                    [],
                    lambda prompt, image_path=None, raw=raw: raw,
                )
                self.assertEqual(
                    out,
                    {"kind": "ambiguous", "question": "你是要现在就开始做吗?"},
                )

    def test_actionable_without_task_falls_back_to_original_text(self):
        out = judge.classify(
            "  把④英文化做了  ",
            [],
            lambda prompt, image_path=None: '{"kind":"actionable"}',
        )

        self.assertEqual(out, {"kind": "actionable", "task": "把④英文化做了"})

    def test_classify_with_image_appends_read_hint_and_forwards_image_path(self):
        seen = {}
        image_path = "/tmp/a1b2c3d4.png"

        def call_llm(prompt, image_path=None):
            seen["prompt"] = prompt
            seen["image_path"] = image_path
            return '{"kind":"actionable","task":"fix the bug shown in the screenshot"}'

        out = judge.classify("please fix", [], call_llm, image_path=image_path)

        self.assertEqual(out["kind"], "actionable")
        self.assertEqual(seen["image_path"], image_path)
        self.assertIn(image_path, seen["prompt"])
        self.assertIn("use your Read tool", seen["prompt"])

    def test_classify_without_image_does_not_append_image_hint(self):
        seen = {}

        def call_llm(prompt, image_path=None):
            seen["prompt"] = prompt
            seen["image_path"] = image_path
            return '{"kind":"opinion"}'

        out = judge.classify("looks good", [], call_llm, image_path=None)

        self.assertEqual(out, {"kind": "opinion"})
        self.assertIsNone(seen["image_path"])
        self.assertNotIn("The human attached an image", seen["prompt"])
        self.assertNotIn("use your Read tool", seen["prompt"])

    def test_image_only_message_can_be_actionable_from_model_task(self):
        out = judge.classify(
            "",
            [],
            lambda prompt, image_path=None: (
                '{"kind":"actionable","task":"implement the layout shown in the attached mockup"}'
            ),
            image_path="/tmp/a1b2c3d4.png",
        )

        self.assertEqual(
            out,
            {"kind": "actionable", "task": "implement the layout shown in the attached mockup"},
        )

    def test_default_call_llm_with_image_allows_read_and_returns_json_result(self):
        captured = {}
        old_run = judge.subprocess.run
        try:
            class Result:
                stdout = '{"result":"{\\"kind\\":\\"opinion\\"}"}'

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs
                return Result()

            judge.subprocess.run = fake_run
            out = judge.default_call_llm("prompt", image_path="/tmp/a1b2c3d4.png")
        finally:
            judge.subprocess.run = old_run

        self.assertEqual(out, '{"kind":"opinion"}')
        self.assertIn("--allowedTools", captured["cmd"])
        self.assertIn("Read", captured["cmd"])
        self.assertIn("--permission-mode", captured["cmd"])
        self.assertEqual(captured["kwargs"]["timeout"], 180)


if __name__ == "__main__":
    unittest.main()
