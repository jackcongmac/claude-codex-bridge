import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bridge_common as bc  # noqa: E402


DEAD_PID = 2147480000  # a pid that is not alive on any sane system


class StaleBreakAtomicityTests(unittest.TestCase):
    """acquire_lock's stale-lock break must be atomic: two waiters facing the SAME
    stale lock must NOT both break it and then both create a fresh one. Exactly one
    ends up holding the lock (the TOCTOU double-hold the audit flagged as must-fix #1)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, "collaboration.lock")

    def _plant_stale(self):
        # A lock held by a dead pid, acquired long ago -> age > ttl and not alive.
        with open(self.lock, "w") as f:
            json.dump({"pid": DEAD_PID, "host": "old", "run_id": "ghost",
                       "acquired_at": 0.0}, f)

    def test_two_racers_breaking_same_stale_lock_yield_exactly_one_holder(self):
        # Deterministically force the classic TOCTOU double-break interleaving:
        #   (1) both threads read the SAME stale lock and decide to break it, then
        #   (2) T2's break runs AFTER T1 has already re-created a fresh lock.
        # With a non-atomic read->unlink->create break, T2 deletes T1's fresh lock and
        # creates its own -> BOTH believe they hold it. An atomic (rename-claim) break
        # lets only one win. The instrument below pins the schedule so the result does
        # not depend on luck.
        self._plant_stale()
        results = {}
        orig_unlink = os.unlink
        orig_pid_alive = bc._pid_alive
        both_read = {"n": 0}
        gate = threading.Event()        # released once both threads have read the stale lock
        t1_done = threading.Event()     # released once T1 has fully acquired

        def pid_alive_gate(pid):
            # Reached only after a thread has read the stale lock and is about to break.
            if not gate.is_set():
                both_read["n"] += 1
                if both_read["n"] >= 2:
                    gate.set()
                else:
                    gate.wait(timeout=5)
            return orig_pid_alive(pid)

        def unlink_gate(path):
            if (path == self.lock and threading.current_thread().name == "T2"
                    and not t1_done.is_set()):
                t1_done.wait(timeout=5)   # hold T2's break until T1 has re-created the lock
            return orig_unlink(path)

        def t1():
            results["T1"] = bc.acquire_lock(self.lock, "T1", ttl=1, wait=0)
            t1_done.set()

        def t2():
            results["T2"] = bc.acquire_lock(self.lock, "T2", ttl=1, wait=0)

        bc._pid_alive = pid_alive_gate
        os.unlink = unlink_gate
        try:
            threads = [threading.Thread(target=t1, name="T1"),
                       threading.Thread(target=t2, name="T2")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        finally:
            bc._pid_alive = orig_pid_alive
            os.unlink = orig_unlink

        winners = [n for n, ok in results.items() if ok]
        self.assertEqual(len(winners), 1, "exactly one racer must win, got %r" % results)
        with open(self.lock) as f:
            held = json.load(f)
        self.assertEqual(held["run_id"], winners[0],
                         "the surviving lock must belong to the winner")

    def test_real_stale_lock_is_broken_by_a_single_acquirer(self):
        self._plant_stale()
        self.assertTrue(bc.acquire_lock(self.lock, "fresh", ttl=1, wait=0))
        with open(self.lock) as f:
            self.assertEqual(json.load(f)["run_id"], "fresh")


class OwnerCheckedReleaseTests(unittest.TestCase):
    """release_lock must only remove the lock when the caller still owns it. A late
    release from a former holder must not delete a DIFFERENT holder's fresh lock."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, "collaboration.lock")

    def test_release_with_foreign_run_id_is_a_noop(self):
        self.assertTrue(bc.acquire_lock(self.lock, "A", ttl=30, wait=0))
        bc.release_lock(self.lock, "B")  # not the holder
        self.assertTrue(os.path.exists(self.lock), "foreign release must not remove the lock")
        with open(self.lock) as f:
            self.assertEqual(json.load(f)["run_id"], "A")

    def test_release_with_owning_run_id_removes_the_lock(self):
        self.assertTrue(bc.acquire_lock(self.lock, "A", ttl=30, wait=0))
        bc.release_lock(self.lock, "A")
        self.assertFalse(os.path.exists(self.lock))

    def test_release_missing_lock_is_a_noop(self):
        bc.release_lock(self.lock, "A")  # nothing to remove
        self.assertFalse(os.path.exists(self.lock))


class NormalLockCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, "collaboration.lock")

    def test_acquire_then_owner_release_then_reacquire(self):
        self.assertTrue(bc.acquire_lock(self.lock, "A", ttl=30, wait=0))
        self.assertFalse(bc.acquire_lock(self.lock, "B", ttl=30, wait=0),
                         "a live lock must not be acquirable by another")
        bc.release_lock(self.lock, "A")
        self.assertTrue(bc.acquire_lock(self.lock, "B", ttl=30, wait=0))

    def test_legacy_release_without_run_id_still_removes_lock(self):
        # Back-compat: callers not yet threaded with run_id keep working.
        self.assertTrue(bc.acquire_lock(self.lock, "A", ttl=30, wait=0))
        bc.release_lock(self.lock)
        self.assertFalse(os.path.exists(self.lock))


if __name__ == "__main__":
    unittest.main()
