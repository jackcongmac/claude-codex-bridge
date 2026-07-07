import os
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bridge_route  # noqa: E402


NOW = 10_000


def claude_usage(five=10, weekly=10, age=10):
    return {
        "five_hour_pct": five,
        "seven_day_pct": weekly,
        "five_hour_reset": NOW + 300,
        "seven_day_reset": NOW + 900,
        "ctx_pct": 12,
        "mtime": NOW - age,
    }


def codex_usage(five=10, weekly=10, age=10):
    return {
        "primary_pct": five,
        "secondary_pct": weekly,
        "primary_reset": NOW + 400,
        "secondary_reset": NOW + 1000,
        "event_ts": NOW - age,
    }


class BridgeRouteTests(unittest.TestCase):
    def test_default_implementer_when_no_pressure_and_no_current(self):
        result = bridge_route.recommend(claude_usage(), codex_usage(), now_ts=NOW)

        self.assertEqual(result["implementer"], "Codex")
        self.assertEqual(result["reviewer"], "Claude")
        self.assertEqual(result["confidence"], "high")

    def test_claude_five_hour_pressure_routes_heavy_work_to_codex(self):
        result = bridge_route.recommend(
            claude_usage(five=80, weekly=20),
            codex_usage(five=10, weekly=10),
            now_ts=NOW,
        )

        self.assertEqual(result["implementer"], "Codex")
        self.assertIn("Claude 5h 80%", result["reason"])

    def test_codex_five_hour_pressure_routes_heavy_work_to_claude(self):
        result = bridge_route.recommend(
            claude_usage(five=10, weekly=10),
            codex_usage(five=80, weekly=20),
            now_ts=NOW,
        )

        self.assertEqual(result["implementer"], "Claude")
        self.assertEqual(result["reviewer"], "Codex")

    def test_codex_weekly_pressure_routes_heavy_work_to_claude(self):
        result = bridge_route.recommend(
            claude_usage(five=10, weekly=10),
            codex_usage(five=20, weekly=85),
            now_ts=NOW,
        )

        self.assertEqual(result["implementer"], "Claude")
        self.assertIn("Codex weekly 85%", result["reason"])

    def test_both_pressured_chooses_less_pressured_with_warning(self):
        result = bridge_route.recommend(
            claude_usage(five=82, weekly=20),
            codex_usage(five=90, weekly=30),
            now_ts=NOW,
        )

        self.assertEqual(result["implementer"], "Claude")
        self.assertIn("both models under quota pressure", result["warnings"])

    def test_hysteresis_lease_keeps_current_below_heavy_thresholds(self):
        result = bridge_route.recommend(
            claude_usage(five=5, weekly=5),
            codex_usage(five=70, weekly=20),
            now_ts=NOW,
            current="Codex",
        )

        self.assertEqual(result["implementer"], "Codex")
        self.assertIn("keep current Codex", result["reason"])

    def test_hysteresis_switches_away_after_current_crosses_heavy_threshold(self):
        result = bridge_route.recommend(
            claude_usage(five=5, weekly=5),
            codex_usage(five=85, weekly=20),
            now_ts=NOW,
            current="Codex",
        )

        self.assertEqual(result["implementer"], "Claude")
        self.assertIn("Codex 5h 85%", result["reason"])

    def test_staleness_lowers_confidence_and_fresh_low_model_is_preferred(self):
        stale_default = bridge_route.recommend(
            claude_usage(five=10, weekly=10, age=700),
            codex_usage(five=10, weekly=10, age=700),
            now_ts=NOW,
        )
        fresh_alternative = bridge_route.recommend(
            claude_usage(five=10, weekly=10, age=10),
            codex_usage(five=10, weekly=10, age=700),
            now_ts=NOW,
        )

        self.assertEqual(stale_default["implementer"], "Codex")
        self.assertEqual(stale_default["confidence"], "low")
        self.assertTrue(any("stale" in warning for warning in stale_default["warnings"]))
        self.assertEqual(fresh_alternative["implementer"], "Claude")
        self.assertEqual(fresh_alternative["confidence"], "low")

    def test_missing_side_is_unknown_not_free_and_warns(self):
        result = bridge_route.recommend(None, codex_usage(), now_ts=NOW)

        self.assertEqual(result["implementer"], "Codex")
        self.assertEqual(result["reviewer"], "Claude")
        self.assertEqual(result["confidence"], "low")
        self.assertTrue(any("Claude reading missing" in warning for warning in result["warnings"]))

    def test_env_override_changes_heavy_five_hour_threshold(self):
        old = os.environ.get("BRIDGE_ROUTE_HEAVY_5H")
        os.environ["BRIDGE_ROUTE_HEAVY_5H"] = "50"
        self.addCleanup(
            lambda: os.environ.__setitem__("BRIDGE_ROUTE_HEAVY_5H", old)
            if old is not None
            else os.environ.pop("BRIDGE_ROUTE_HEAVY_5H", None)
        )

        result = bridge_route.recommend(
            claude_usage(five=10, weekly=10),
            codex_usage(five=55, weekly=10),
            now_ts=NOW,
        )

        self.assertEqual(result["implementer"], "Claude")

    def test_signals_are_populated_for_both_models(self):
        result = bridge_route.recommend(
            claude_usage(five=11, weekly=22, age=5),
            codex_usage(five=33, weekly=44, age=700),
            now_ts=NOW,
        )

        self.assertEqual(
            result["signals"]["Claude"],
            {
                "five_h": 11,
                "weekly": 22,
                "reset_in_s": 300,
                "stale": False,
                "pressured": False,
            },
        )
        self.assertEqual(
            result["signals"]["Codex"],
            {
                "five_h": 33,
                "weekly": 44,
                "reset_in_s": 400,
                "stale": True,
                "pressured": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
