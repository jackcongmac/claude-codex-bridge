import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class SingleMachineFramingDocsTests(unittest.TestCase):
    """The board lives in local .collab/ (gitignored) — agents coordinate on ONE
    machine or a shared filesystem, NOT across two independent machines (each would
    get its own disconnected board). Docs must not imply cross-machine coordination;
    feature-inventory.md already states the single-machine reality."""

    def test_readme_does_not_claim_two_machines_for_the_watcher(self):
        text = (ROOT / "README.md").read_text()
        self.assertNotIn("two machines", text)
        self.assertIn("shared filesystem", text)

    def test_watch_collaboration_help_does_not_claim_two_machines(self):
        text = (ROOT / "scripts" / "watch-collaboration.sh").read_text()
        self.assertNotIn("two machines", text)

    def test_feature_inventory_states_single_machine_reality(self):
        text = (ROOT / "docs" / "feature-inventory.md").read_text()
        self.assertIn("Single-machine", text)
        self.assertIn("cross-machine", text)


if __name__ == "__main__":
    unittest.main()
