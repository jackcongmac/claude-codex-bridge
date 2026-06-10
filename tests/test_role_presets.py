import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PRESETS = ROOT / "presets"
APPLY = ROOT / "scripts" / "apply-role-preset.py"
STATE_TEMPLATE = ROOT / "templates" / "collaboration_state.json"


class RolePresetTests(unittest.TestCase):
    def load_preset(self, name):
        return json.loads((PRESETS / f"{name}.json").read_text())

    def test_all_presets_use_canonical_state_fields(self):
        allowed_top_level = {
            "name",
            "description",
            "roles",
            "resource_profiles",
            "routing_rules",
        }
        preset_files = sorted(PRESETS.glob("*.json"))

        self.assertGreaterEqual(len(preset_files), 2)
        for path in preset_files:
            with self.subTest(path=path.name):
                preset = json.loads(path.read_text())
                self.assertLessEqual(set(preset), allowed_top_level)
                self.assertIn("name", preset)
                self.assertIn("roles", preset)
                self.assertIsInstance(preset["roles"], dict)
                self.assertEqual(set(preset["roles"]), {"Claude", "Codex"})
                for actor, role in preset["roles"].items():
                    self.assertIn(actor, {"Claude", "Codex"})
                    self.assertIn(role, {"peer", "planner", "executor", "reviewer"})
                if "resource_profiles" in preset:
                    self.assertEqual(set(preset["resource_profiles"]), {"Claude", "Codex"})
                    for profile in preset["resource_profiles"].values():
                        self.assertIn("tier", profile)
                        self.assertIn("best_for", profile)
                        self.assertIn("avoid", profile)
                        self.assertIsInstance(profile["best_for"], list)
                        self.assertIsInstance(profile["avoid"], list)

    def test_apply_max_claude_pro_codex_preset_lands_in_state(self):
        preset = self.load_preset("max-claude-pro-codex")

        with tempfile.TemporaryDirectory() as tmp:
            project = pathlib.Path(tmp)
            state_path = project / "collaboration_state.json"
            state_path.write_text(STATE_TEMPLATE.read_text())

            result = subprocess.run(
                [
                    sys.executable,
                    str(APPLY),
                    "--project",
                    str(project),
                    "--preset",
                    "max-claude-pro-codex",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(state_path.read_text())
            self.assertEqual(state["roles"], preset["roles"])
            self.assertEqual(state["resource_profiles"], preset["resource_profiles"])
            self.assertIn("max-claude-pro-codex", result.stdout)

    def test_apply_preserves_unrelated_state_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = pathlib.Path(tmp)
            state_path = project / "collaboration_state.json"
            state = json.loads(STATE_TEMPLATE.read_text())
            state["status"] = "active"
            state["turn"] = 7
            state["last_writer"] = "Claude"
            state_path.write_text(json.dumps(state, indent=2) + "\n")

            command = [
                sys.executable,
                str(APPLY),
                "--project",
                str(project),
                "--preset",
                "reviewer-implementer",
            ]
            first = subprocess.run(command, check=False, capture_output=True, text=True)
            second = subprocess.run(command, check=False, capture_output=True, text=True)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            applied = json.loads(state_path.read_text())
            self.assertEqual(applied["status"], "active")
            self.assertEqual(applied["turn"], 7)
            self.assertEqual(applied["last_writer"], "Claude")
            self.assertEqual(applied["resource_profiles"], state["resource_profiles"])
            self.assertEqual(
                applied["roles"],
                {"Claude": "reviewer", "Codex": "executor"},
            )
            self.assertNotIn("resource_profiles", self.load_preset("reviewer-implementer"))


if __name__ == "__main__":
    unittest.main()
