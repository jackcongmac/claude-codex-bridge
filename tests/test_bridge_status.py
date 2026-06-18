import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "bridge-status.py"
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bridge_inbox  # noqa: E402


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class BridgeStatusCliTests(unittest.TestCase):
    def run_status(self, project, *extra_args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--project", str(project), *extra_args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_git(self, project, *args):
        return subprocess.run(
            ["git", *args],
            cwd=project,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_shows_state_signal_and_recent_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_json(
                project / "collaboration_state.json",
                {
                    "status": "awaiting_human",
                    "turn": 7,
                    "max_turns": 12,
                    "next_actor": "human",
                    "roles": {"Claude": "planner", "Codex": "executor"},
                    "resource_profiles": {
                        "Claude": {
                            "tier": "max",
                            "best_for": ["architecture", "review"],
                            "avoid": ["bulk_editing"],
                        },
                        "Codex": {
                            "tier": "pro",
                            "best_for": ["implementation", "tests"],
                            "avoid": ["large_context_synthesis"],
                        },
                    },
                    "cost_so_far_usd": 1.25,
                    "max_cost_usd": 5.0,
                    "last_writer": "Codex",
                    "failure": "budget review needed",
                },
            )
            write_json(
                project / "collaboration_signal.json",
                {
                    "update_id": 42,
                    "updated_at": "2026-06-09 10:11:12 PDT",
                    "updated_by": "Codex",
                    "summary": "Asked human to approve higher budget.",
                },
            )
            (project / "collaboration_auto.log").write_text(
                "\n".join(
                    [
                        "event 1",
                        "event 2",
                        "event 3",
                        "event 4",
                        "event 5",
                        "event 6",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = self.run_status(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Status: awaiting_human", result.stdout)
            self.assertIn("Turn: 7/12", result.stdout)
            self.assertIn("Next actor: human", result.stdout)
            self.assertIn("Roles: Claude=planner, Codex=executor", result.stdout)
            self.assertIn("Resource profiles:", result.stdout)
            self.assertIn(
                "  Claude: tier=max; best_for=architecture, review; avoid=bulk_editing",
                result.stdout,
            )
            self.assertIn(
                "  Codex: tier=pro; best_for=implementation, tests; avoid=large_context_synthesis",
                result.stdout,
            )
            self.assertIn("Cost: $1.25 / $5.00", result.stdout)
            self.assertIn("Last writer: Codex", result.stdout)
            self.assertIn(
                "Last signal: #42 by Codex at 2026-06-09 10:11:12 PDT - Asked human to approve higher budget.",
                result.stdout,
            )
            self.assertIn("Inbox Claude: clear from Codex Outbox", result.stdout)
            self.assertIn("Inbox Codex: clear from Claude Outbox", result.stdout)
            self.assertIn("Recent events:", result.stdout)
            self.assertNotIn("event 1", result.stdout)
            self.assertIn("  event 2", result.stdout)
            self.assertIn("  event 6", result.stdout)
            self.assertIn("Failure: budget review needed", result.stdout)

    def test_degrades_gracefully_when_signal_and_log_are_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_json(
                project / "collaboration_state.json",
                {
                    "status": "paused",
                    "turn": 0,
                    "max_turns": 12,
                    "next_actor": None,
                    "roles": {"Claude": "peer", "Codex": "peer"},
                    "cost_so_far_usd": 0,
                    "max_cost_usd": 5,
                    "last_writer": None,
                },
            )

            result = self.run_status(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Status: paused", result.stdout)
            self.assertIn("Turn: 0/12", result.stdout)
            self.assertIn("Next actor: none", result.stdout)
            self.assertIn("Roles: Claude=peer, Codex=peer", result.stdout)
            self.assertNotIn("Resource profiles:", result.stdout)
            self.assertIn("Cost: $0.00 / $5.00", result.stdout)
            self.assertIn("Last writer: none", result.stdout)
            self.assertIn("Last signal: unavailable", result.stdout)
            self.assertIn("Inbox Claude: clear from Codex Outbox", result.stdout)
            self.assertIn("Inbox Codex: clear from Claude Outbox", result.stdout)
            self.assertIn("Recent events: none", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_does_not_modify_project_files_or_create_new_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_json(
                project / "collaboration_state.json",
                {"status": "active", "turn": 2, "max_turns": 4},
            )
            write_json(
                project / "collaboration_signal.json",
                {"update_id": 3, "updated_by": "Claude", "summary": "Continuing."},
            )
            (project / "collaboration_auto.log").write_text(
                "started\ncontinued\n", encoding="utf-8"
            )
            before_names = sorted(path.name for path in project.iterdir())
            before_contents = {
                path.name: path.read_bytes()
                for path in project.iterdir()
                if path.is_file()
            }

            result = self.run_status(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(before_names, sorted(path.name for path in project.iterdir()))
            after_contents = {
                path.name: path.read_bytes()
                for path in project.iterdir()
                if path.is_file()
            }
            self.assertEqual(before_contents, after_contents)

    def test_shows_pending_inbox_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            collab = project / ".collab"
            collab.mkdir()
            write_json(collab / "collaboration_state.json", {"status": "active"})
            write_json(
                collab / "collaboration_signal.json",
                {"update_id": 3, "updated_by": "Claude", "summary": "task"},
            )
            (collab / "collaboration_auto.log").write_text("", encoding="utf-8")
            (collab / "collaboration.md").write_text(
                "# Board\n\n"
                "## Claude Outbox\n\n"
                "### 2026-06-17 10:00:00 PDT\n\n"
                "Codex please respond.\n\n"
                "## Codex Outbox\n\n",
                encoding="utf-8",
            )

            result = self.run_status(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Inbox Codex: ACTION_REQUIRED 1 pending from Claude Outbox", result.stdout)

    def test_delivery_dashboard_shows_lifecycle_states_from_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            collab = project / ".collab"
            collab.mkdir()

            self.run_git(project, "init", "-q")
            self.run_git(project, "config", "user.email", "test@example.com")
            self.run_git(project, "config", "user.name", "Test User")
            (project / "work.txt").write_text("shipped\n", encoding="utf-8")
            self.run_git(project, "add", "work.txt")
            self.run_git(project, "commit", "-q", "-m", "shipped commit")
            shipped_sha = self.run_git(project, "rev-parse", "HEAD").stdout.strip()
            self.run_git(project, "update-ref", "refs/remotes/origin/main", shipped_sha)
            (project / "work.txt").write_text("reviewed\n", encoding="utf-8")
            self.run_git(project, "commit", "-am", "reviewed commit", "-q")
            reviewed_sha = self.run_git(project, "rev-parse", "HEAD").stdout.strip()

            (collab / "collaboration.md").write_text(
                "# Board\n\n"
                "## Roles\n\n"
                "Codex is the executor; this section is board metadata, not delivery work.\n\n"
                "## Claude Outbox\n\n"
                "### 2026-06-17 10:05:00 PDT\n\n"
                "Open delivery item without a receipt.\n\n"
                "### 2026-06-17 10:00:00 PDT\n\n"
                "Claimed delivery item with a receipt.\n\n"
                "## Codex Outbox\n\n"
                "### 2026-06-17 10:20:00 PDT\n\n"
                "Overdue delivery item waiting on Claude.\n\n"
                "### 2026-06-17 10:10:00 PDT\n\n"
                f"Reviewed delivery item for {reviewed_sha}.\n\n"
                "### 2026-06-17 09:55:00 PDT\n\n"
                f"Shipped delivery item for {shipped_sha}.\n\n"
                "### 2026-06-17 09:50:00 PDT\n\n"
                "Stale historical open item superseded by later shipped work.\n\n",
                encoding="utf-8",
            )

            claude_outbox = bridge_inbox.load_entries(str(project), "Codex", "Claude")
            codex_outbox = bridge_inbox.load_entries(str(project), "Claude", "Codex")
            write_json(
                collab / "inbox_ack.json",
                {
                    "Codex<- Claude": {
                        "last_digest": claude_outbox[0]["digest"],
                        "last_index": claude_outbox[0]["index"],
                        "status": "CLAIM",
                    },
                    "esc:Claude<- Codex": {
                        "watch_since": 0,
                        "last_escalated_digest": codex_outbox[-1]["digest"],
                    },
                },
            )
            write_json(
                collab / "collaboration_reviews.json",
                {
                    "reviews": [
                        {
                            "reviewer": "Claude",
                            "sha": reviewed_sha,
                            "verdict": "REVISE",
                        },
                        {
                            "reviewer": "Claude",
                            "sha": shipped_sha,
                            "verdict": "SHIP",
                        },
                    ]
                },
            )

            result = self.run_status(project, "--delivery")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "Delivery: 1 open · 1 claimed · 1 overdue · 1 reviewed-unshipped · 1 shipped",
                result.stdout,
            )
            self.assertIn("Delivery Dashboard", result.stdout)
            self.assertIn("Work items: 4 actionable (6 total)", result.stdout)
            self.assertRegex(result.stdout, r"open\s+Claude #2 .*Open delivery item")
            self.assertRegex(result.stdout, r"claimed\s+Claude #1 .*Claimed delivery item")
            self.assertRegex(result.stdout, r"reviewed\s+Codex #3 .*REVISE .*Reviewed delivery item")
            self.assertRegex(result.stdout, r"overdue\s+Codex #4 .*Overdue delivery item")
            self.assertNotIn("Roles", result.stdout)
            self.assertNotIn("Shipped delivery item", result.stdout)
            self.assertNotIn("Stale historical open item", result.stdout)

            all_result = self.run_status(project, "--delivery", "--all")

            self.assertEqual(all_result.returncode, 0, all_result.stderr)
            self.assertIn(
                "Delivery: 2 open · 1 claimed · 1 overdue · 1 reviewed-unshipped · 1 shipped",
                all_result.stdout,
            )
            self.assertIn("Work items: 6", all_result.stdout)
            self.assertRegex(
                all_result.stdout,
                r"shipped\s+Codex #2 .*SHIP .*Shipped delivery item",
            )
            self.assertIn("Stale historical open item", all_result.stdout)


if __name__ == "__main__":
    unittest.main()
