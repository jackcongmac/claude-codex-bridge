import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _ship  # noqa: E402


def reco(implementer="Codex", reviewer="Claude", confidence="high",
         reason="Codex has fresh low quota", warnings=None, signals=None):
    return {
        "implementer": implementer,
        "reviewer": reviewer,
        "confidence": confidence,
        "reason": reason,
        "warnings": warnings or [],
        "signals": signals or {
            "Claude": {"five_h": 11, "weekly": 22},
            "Codex": {"five_h": 33, "weekly": 44},
        },
    }


class ResolveRolesTests(unittest.TestCase):
    def test_no_cli_high_confidence_route_uses_route_roles(self):
        resolved = _ship.resolve_roles(
            reco(implementer="Claude", reviewer="Codex", confidence="high"),
            None,
            None,
            "Codex",
            "Claude",
        )

        self.assertEqual(resolved["implementer"], "Claude")
        self.assertEqual(resolved["reviewer"], "Codex")
        self.assertEqual(resolved["source"], "route")
        self.assertEqual(resolved["notes"], [])

    def test_no_cli_low_confidence_route_uses_default_roles_with_confidence_note(self):
        resolved = _ship.resolve_roles(
            reco(confidence="low", warnings=["Codex reading stale"]),
            None,
            None,
            "Codex",
            "Claude",
        )

        self.assertEqual(resolved["implementer"], "Codex")
        self.assertEqual(resolved["reviewer"], "Claude")
        self.assertEqual(resolved["source"], "default")
        self.assertTrue(any("route confidence low" in note for note in resolved["notes"]))
        self.assertTrue(any("Codex reading stale" in note for note in resolved["notes"]))

    def test_cli_implementer_honored_with_disagreement_note(self):
        resolved = _ship.resolve_roles(
            reco(implementer="Codex", reviewer="Claude", confidence="high",
                 reason="Codex below threshold"),
            "Claude",
            None,
            "Codex",
            "Claude",
        )

        self.assertEqual(resolved["implementer"], "Claude")
        self.assertEqual(resolved["reviewer"], "Codex")
        self.assertEqual(resolved["source"], "cli")
        self.assertIn(
            "quota suggests Codex implements (Codex below threshold), you specified Claude",
            resolved["notes"],
        )

    def test_cli_reviewer_only_sets_implementer_to_other_actor(self):
        resolved = _ship.resolve_roles(
            reco(),
            None,
            "Codex",
            "Codex",
            "Claude",
        )

        self.assertEqual(resolved["implementer"], "Claude")
        self.assertEqual(resolved["reviewer"], "Codex")
        self.assertEqual(resolved["source"], "cli")

    def test_equal_resolved_roles_raise_value_error(self):
        with self.assertRaises(ValueError):
            _ship.resolve_roles(reco(), "Claude", "Claude", "Codex", "Claude")

    def test_undecidable_route_without_cli_uses_default_roles(self):
        resolved = _ship.resolve_roles(
            reco(implementer=None, reviewer="Claude", confidence="high"),
            None,
            None,
            "Codex",
            "Claude",
        )

        self.assertEqual(resolved["implementer"], "Codex")
        self.assertEqual(resolved["reviewer"], "Claude")
        self.assertEqual(resolved["source"], "default")


class RouteBannerLinesTests(unittest.TestCase):
    def test_banner_recommend_using_unknown_signals_and_warnings(self):
        recommendation = reco(
            implementer="Codex",
            reviewer="Claude",
            confidence="high",
            reason="Codex has fresh low quota",
            warnings=["Claude reading missing; quota unknown"],
            signals={
                "Claude": {"five_h": None, "weekly": 22},
                "Codex": {"five_h": 33, "weekly": None},
            },
        )
        resolved = {
            "implementer": "Claude",
            "reviewer": "Codex",
            "source": "cli",
            "notes": ["quota suggests Codex implements (Codex has fresh low quota), you specified Claude"],
        }

        lines = _ship.route_banner_lines(recommendation, resolved)

        self.assertIn(
            "[route] recommend: implement->Codex review->Claude  [high]  (Codex has fresh low quota)",
            lines,
        )
        self.assertIn("[route] using cli roles: implement->Claude review->Codex", lines)
        self.assertTrue(any("—" in line for line in lines))
        self.assertFalse(any("0%" in line for line in lines))
        self.assertIn("[route] WARN Claude reading missing; quota unknown", lines)


if __name__ == "__main__":
    unittest.main()
