import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PUSH = ROOT / "scripts" / "bridge-push.sh"


def _run(snippet, repo):
    """Source bridge-push.sh in test-seam mode (no git, no push) and run `snippet`,
    which can call the real acquire/release functions against $LOCK."""
    script = (
        'set -euo pipefail\n'
        'export BRIDGE_PUSH_SOURCE_ONLY=1 BRIDGE_PUSH_REPO=%s BRIDGE_PUSH_BRANCH=test\n'
        'export BRIDGE_PUSH_TTL=1 BRIDGE_PUSH_WAIT=0 BRIDGE_AGENT=tester\n'
        'source "%s"\n'
        '%s\n' % (repo, PUSH, snippet)
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


class PushLockTests(unittest.TestCase):
    """bridge-push.sh's lock mirrors bridge_common: an owner-checked release and an
    atomic (rename-claim) stale break, so two pushers can't both break one stale lock
    and a late release can't delete a different pusher's fresh lock."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.lock = os.path.join(self.repo, ".bridge_push.lock")

    def test_acquire_creates_lock_owned_by_this_process(self):
        r = _run('acquire; test -f "$LOCK" && grep -q "\\"pid\\":$$" "$LOCK" && echo HELD', self.repo)
        self.assertIn("HELD", r.stdout, r.stderr)

    def test_release_with_foreign_pid_is_a_noop(self):
        r = _run(
            'printf \'{"who":"x","pid":1,"host":"h","since":0}\\n\' > "$LOCK"\n'
            'release\n'
            'test -f "$LOCK" && echo KEPT',
            self.repo)
        self.assertIn("KEPT", r.stdout, r.stderr)

    def test_release_with_our_pid_removes_the_lock(self):
        r = _run(
            'printf \'{"who":"me","pid":\'$$\',"host":"h","since":0}\\n\' > "$LOCK"\n'
            'release\n'
            'test ! -f "$LOCK" && echo GONE',
            self.repo)
        self.assertIn("GONE", r.stdout, r.stderr)

    def test_stale_lock_is_broken_and_reacquired(self):
        # A lock with since=0 is far older than TTL=1 -> stale -> broken atomically,
        # then this caller acquires it fresh (now owned by us).
        r = _run(
            'printf \'{"who":"ghost","pid":999999,"host":"h","since":0}\\n\' > "$LOCK"\n'
            'acquire\n'
            'grep -q "\\"pid\\":$$" "$LOCK" && echo REACQUIRED',
            self.repo)
        self.assertIn("REACQUIRED", r.stdout, r.stderr)
        # the temporary .breaking.* claim file must not be left behind
        leftovers = [p for p in os.listdir(self.repo) if ".breaking." in p]
        self.assertEqual(leftovers, [], "stale-break temp file leaked: %r" % leftovers)


if __name__ == "__main__":
    unittest.main()
