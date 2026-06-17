import importlib.util
import json
import pathlib
import threading
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
_spec = importlib.util.spec_from_file_location("chatsupervisor", SCRIPTS / "bridge-chat-supervise.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


class ChatSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 1}))
        (self.collab / "collaboration.md").write_text("# Board\n")

    def test_run_supervisor_starts_supervises_and_stops_responders(self):
        calls = []

        class FakeChatWeb:
            def find_project_root(self, project):
                calls.append(("find", project))
                return project

            def start_responders(self, project, scripts_dir=None, spawn=None):
                calls.append(("start", project, scripts_dir))
                return ["claude-handle", "codex-handle"]

            def responder_cmds(self, scripts_dir, project):
                return [["respond", "--self", "Claude"], ["respond", "--self", "Codex"]]

            def _responder_handle_alive(self, handle, project, self_name, owner_alive=None):
                return True

            def shutdown_supervised_responders(self, handles, stop_event=None, supervisor=None,
                                               join_timeout=None, stop=None):
                calls.append(("shutdown", list(handles), stop_event.is_set(),
                              isinstance(supervisor, threading.Thread), join_timeout))

        def wait(stop_event):
            calls.append(("wait", stop_event.is_set()))
            stop_event.set()

        status = cs.run_supervisor(
            self.tmp,
            scripts_dir="/scripts",
            interval=0.01,
            chatweb=FakeChatWeb(),
            wait=wait)

        self.assertEqual(status, 0)
        self.assertEqual(calls[0], ("find", self.tmp))
        self.assertEqual(calls[1], ("start", self.tmp, "/scripts"))
        self.assertEqual(calls[2], ("wait", False))
        self.assertEqual(calls[3], ("shutdown", ["claude-handle", "codex-handle"],
                                    True, True, 2.01))

    def test_signal_handler_sets_stop_event(self):
        event = threading.Event()
        handler = cs._make_stop_handler(event)

        handler(None, None)

        self.assertTrue(event.is_set())

    def test_heartbeat_writes_state_without_bumping_signal(self):
        handles = [object(), object()]
        tracker = cs.new_tracker()

        cs.write_supervisor_state(self.tmp, tracker, handles, now=100.0)

        state = json.loads((self.collab / "chat_supervisor.json").read_text())
        sig = json.loads((self.collab / "collaboration_signal.json").read_text())
        self.assertEqual(state["supervisor"]["heartbeat_at"], cs._stamp(100.0))
        self.assertEqual(sorted(state["agents"]), ["Claude", "Codex"])
        self.assertEqual(sig["update_id"], 1)

    def test_alert_posts_liveness_and_bumps_signal(self):
        tracker = cs.new_tracker()

        cs.emit_alert(self.tmp, tracker, "Claude responder down", notify=lambda msg: None)

        board = (self.collab / "collaboration.md").read_text()
        sig = json.loads((self.collab / "collaboration_signal.json").read_text())
        self.assertIn("## Liveness", board)
        self.assertIn("Claude responder down", board)
        self.assertEqual(sig["changed_section"], "Liveness")
        self.assertEqual(sig["update_id"], 2)
        self.assertEqual(tracker["alerts"], ["Claude responder down"])

    def test_backoff_prevents_immediate_respawn_after_crash(self):
        class DeadHandle:
            pid = 111

            def poll(self):
                return 1

        class FakeChatWeb:
            def responder_cmds(self, scripts_dir, project):
                return [["respond", "--self", "Claude"], ["respond", "--self", "Codex"]]

            def _responder_handle_alive(self, handle, project, self_name, owner_alive=None):
                return self_name == "Codex"

        spawned = []
        handles = [DeadHandle(), object()]
        tracker = cs.new_tracker()
        tracker["agents"]["Claude"]["failure_count"] = 1
        tracker["agents"]["Claude"]["backoff_until"] = 200.0

        cs.supervisor_cycle(
            handles, tracker, self.tmp, scripts_dir="/scripts", chatweb=FakeChatWeb(),
            now=150.0, spawn=lambda cmd: spawned.append(cmd) or object(),
            notify=lambda msg: None)

        self.assertEqual(spawned, [])
        self.assertEqual(handles[0].pid, 111)

    def test_restart_after_dead_responder_uses_backoff_metadata(self):
        class DeadHandle:
            pid = 111

            def poll(self):
                return 1

        class NewHandle:
            pid = 222

            def poll(self):
                return None

        class FakeChatWeb:
            def responder_cmds(self, scripts_dir, project):
                return [["respond", "--self", "Claude"], ["respond", "--self", "Codex"]]

            def _responder_handle_alive(self, handle, project, self_name, owner_alive=None):
                return self_name == "Codex"

        handles = [DeadHandle(), object()]
        tracker = cs.new_tracker()

        cs.supervisor_cycle(
            handles, tracker, self.tmp, scripts_dir="/scripts", chatweb=FakeChatWeb(),
            now=100.0, spawn=lambda cmd: NewHandle(), backoff_base=2, notify=lambda msg: None)

        self.assertEqual(handles[0].pid, 222)
        self.assertEqual(tracker["agents"]["Claude"]["restart_count"], 1)
        self.assertEqual(tracker["agents"]["Claude"]["failure_count"], 1)
        self.assertEqual(tracker["agents"]["Claude"]["backoff_until"], 102.0)

    def test_restart_limit_alerts_instead_of_respawning(self):
        class DeadHandle:
            pid = 111

            def poll(self):
                return 1

        class FakeChatWeb:
            def responder_cmds(self, scripts_dir, project):
                return [["respond", "--self", "Claude"], ["respond", "--self", "Codex"]]

            def _responder_handle_alive(self, handle, project, self_name, owner_alive=None):
                return self_name == "Codex"

        spawned = []
        handles = [DeadHandle(), object()]
        tracker = cs.new_tracker()
        tracker["agents"]["Claude"]["failure_count"] = 3

        cs.supervisor_cycle(
            handles, tracker, self.tmp, scripts_dir="/scripts", chatweb=FakeChatWeb(),
            now=100.0, spawn=lambda cmd: spawned.append(cmd), max_restarts=3,
            notify=lambda msg: None)

        self.assertEqual(spawned, [])
        self.assertIn("Claude responder restart limit reached", tracker["alerts"][0])

    def test_short_alive_flap_does_not_reset_restart_failures(self):
        class DeadHandle:
            pid = 111

            def poll(self):
                return 1

        class LiveHandle:
            pid = 222

            def poll(self):
                return None

        class FakeChatWeb:
            def __init__(self, alive):
                self.alive = alive

            def responder_cmds(self, scripts_dir, project):
                return [["respond", "--self", "Claude"], ["respond", "--self", "Codex"]]

            def _responder_handle_alive(self, handle, project, self_name, owner_alive=None):
                return self_name == "Codex" or self.alive

        handles = [DeadHandle(), object()]
        tracker = cs.new_tracker()

        cs.supervisor_cycle(
            handles, tracker, self.tmp, scripts_dir="/scripts", chatweb=FakeChatWeb(False),
            now=100.0, spawn=lambda cmd: LiveHandle(), max_restarts=1, backoff_base=1,
            healthy_after=10, notify=lambda msg: None)
        self.assertEqual(tracker["agents"]["Claude"]["failure_count"], 1)

        cs.supervisor_cycle(
            handles, tracker, self.tmp, scripts_dir="/scripts", chatweb=FakeChatWeb(True),
            now=101.0, max_restarts=1, healthy_after=10, notify=lambda msg: None)
        self.assertEqual(tracker["agents"]["Claude"]["failure_count"], 1)

        handles[0] = DeadHandle()
        cs.supervisor_cycle(
            handles, tracker, self.tmp, scripts_dir="/scripts", chatweb=FakeChatWeb(False),
            now=102.0, spawn=lambda cmd: LiveHandle(), max_restarts=1, healthy_after=10,
            notify=lambda msg: None)

        self.assertIn("Claude responder restart limit reached", tracker["alerts"][0])

    def test_sustained_health_resets_restart_failures(self):
        class LiveHandle:
            pid = 222

            def poll(self):
                return None

        class FakeChatWeb:
            def responder_cmds(self, scripts_dir, project):
                return [["respond", "--self", "Claude"], ["respond", "--self", "Codex"]]

            def _responder_handle_alive(self, handle, project, self_name, owner_alive=None):
                return True

        handles = [LiveHandle(), object()]
        tracker = cs.new_tracker()
        tracker["agents"]["Claude"]["failure_count"] = 2
        tracker["agents"]["Claude"]["first_down_at"] = 100.0
        tracker["agents"]["Claude"]["backoff_until"] = 110.0
        tracker["agents"]["Claude"]["alive_since"] = 100.0

        cs.supervisor_cycle(
            handles, tracker, self.tmp, scripts_dir="/scripts", chatweb=FakeChatWeb(),
            now=111.0, healthy_after=10, notify=lambda msg: None)

        self.assertEqual(tracker["agents"]["Claude"]["failure_count"], 0)
        self.assertIsNone(tracker["agents"]["Claude"]["first_down_at"])
        self.assertIsNone(tracker["agents"]["Claude"]["backoff_until"])


if __name__ == "__main__":
    unittest.main()
