import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class BridgePostAdoptionTests(unittest.TestCase):
    """The transactional locked write (bridge-post) is only useful if the documented
    workflow routes through it — guard that the canonical loop docs/scripts tell agents
    to post via bridge-post instead of hand-editing the board + bumping the signal."""

    def _assert_mentions(self, relpath):
        text = (ROOT / relpath).read_text()
        self.assertIn("bridge-post", text,
                      "%s should route board posts through bridge-post" % relpath)

    def test_readme_loop_uses_bridge_post(self):
        self._assert_mentions("README.md")

    def test_skill_loop_uses_bridge_post(self):
        self._assert_mentions("skill/SKILL.md")

    def test_join_announce_uses_bridge_post(self):
        self._assert_mentions("scripts/join-collaboration.sh")

    def test_protocol_loop_uses_bridge_post(self):
        self._assert_mentions("docs/agent-collaboration-protocol.md")

    def test_init_manual_mode_uses_bridge_post(self):
        self._assert_mentions("scripts/init-collaboration.sh")

    def test_board_template_uses_bridge_post(self):
        self._assert_mentions("templates/collaboration.md")


if __name__ == "__main__":
    unittest.main()
