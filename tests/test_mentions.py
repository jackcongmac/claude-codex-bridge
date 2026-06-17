import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
_spec = importlib.util.spec_from_file_location("chatweb", SCRIPTS / "bridge-chat-web.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)

import unittest  # noqa: E402


class MentionsTests(unittest.TestCase):
    """The mentions() parser: returns the set of agents explicitly @-mentioned.
    @All / @所有人 → both; @Claude (also '@Claude Code') / @Codex → that one; no mention
    → empty set. (Who is *compelled* to reply is decided by _chat_respond._targets,
    not here — a human's no-@ message still broadcasts to both.)"""

    def test_no_mention_is_empty(self):
        self.assertEqual(cw.mentions("just chatting"), set())

    def test_at_claude(self):
        self.assertEqual(cw.mentions("@Claude what do you think?"), {"Claude"})

    def test_at_claude_code_alias(self):
        self.assertEqual(cw.mentions("hey @Claude Code please look"), {"Claude"})

    def test_at_codex(self):
        self.assertEqual(cw.mentions("@Codex run the tests"), {"Codex"})

    def test_at_all_english(self):
        self.assertEqual(cw.mentions("@All standup time"), {"Claude", "Codex"})

    def test_at_everyone_chinese(self):
        self.assertEqual(cw.mentions("@所有人 开会"), {"Claude", "Codex"})

    def test_both_named(self):
        self.assertEqual(cw.mentions("@Claude and @Codex sync up"), {"Claude", "Codex"})

    def test_case_insensitive(self):
        self.assertEqual(cw.mentions("@claude hi"), {"Claude"})

    def test_email_local_part_is_not_a_mention(self):
        self.assertEqual(cw.mentions("ping a@codex.io about it"), set())

    def test_name_suffix_is_not_a_mention(self):
        self.assertEqual(cw.mentions("@clauded and @codexical"), set())

    def test_chinese_suffix_is_not_everyone(self):
        self.assertEqual(cw.mentions("@所有人类 都来"), set())

    def test_mention_at_end_and_with_punctuation(self):
        self.assertEqual(cw.mentions("thanks @Claude!"), {"Claude"})
        self.assertEqual(cw.mentions("done @Codex"), {"Codex"})


if __name__ == "__main__":
    unittest.main()
