import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _pr  # noqa: E402
import _pr_evidence as ev  # noqa: E402
import _ship  # noqa: E402
import _agent_cli  # noqa: E402
import _chat_executor  # noqa: E402

BIN = ROOT / "bin" / "claude-codex-bridge"


def _have_sshkeygen():
    return shutil.which("ssh-keygen") is not None


def _gen_key(path):
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "test", "-f", path],
        check=True, capture_output=True)


def _pub_line(actor, keypath):
    with open(keypath + ".pub") as f:
        pub = f.read().split()
    return "%s %s %s\n" % (actor, pub[0], pub[1])


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRun:
    def __init__(self, responses=None):
        self.responses = responses or []
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs)))
        if not self.responses:
            return Result()
        response = self.responses.pop(0)
        if callable(response):
            return response(cmd, kwargs)
        return response

    def commands(self):
        return [cmd for cmd, _kwargs in self.calls]


class FakeGh:
    def __init__(self):
        self.calls = []
        self.bodies = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs)))
        body_file = cmd[cmd.index("--body-file") + 1]
        with open(body_file) as f:
            self.bodies.append(f.read())
        return Result(stdout="https://github.example/pr/7\n")


def _evidence(head="h" * 40):
    return ev.build_evidence(
        task="fix the PR gate",
        base_sha="b" * 40,
        head_sha=head,
        patch_ids=["patch-2", "patch-1"],
        test_cmd="python3 -m unittest tests.test_pr",
        test_log="Ran 4 tests in 0.01s\n\nOK\n",
        tests_ok=True,
        branch="ai/fix-pr-gate-bbbbbbb",
        implementer="Codex",
        reviewer="Claude",
        verdict="GO",
    )


class BranchAndPatchTests(unittest.TestCase):
    def test_create_branch_requires_clean_worktree_and_checks_out_base(self):
        run = FakeRun([
            Result(stdout=""),
            Result(stdout="b" * 40 + "\n"),
            Result(),
        ])

        branch = _pr.create_branch("/repo", "main", "fix gate", run=run)

        self.assertEqual(branch, "ai/fix-gate-%s" % ("b" * 7))
        self.assertEqual(run.commands()[0], ["git", "-C", "/repo", "status", "--porcelain"])
        self.assertEqual(
            run.commands()[2],
            ["git", "-C", "/repo", "checkout", "-b", branch, "main"],
        )

    def test_create_branch_rejects_dirty_worktree(self):
        run = FakeRun([Result(stdout=" M scripts/x.py\n")])

        with self.assertRaisesRegex(RuntimeError, "worktree must be clean"):
            _pr.create_branch("/repo", "main", "fix gate", run=run)

        self.assertEqual(len(run.commands()), 1)

    def test_branch_patch_ids_returns_sorted_unique_patch_ids(self):
        run = FakeRun([
            Result(stdout="c2\nc1\n"),
            Result(stdout="diff c2"),
            Result(stdout="patch-b c2\n"),
            Result(stdout="diff c1"),
            Result(stdout="patch-a c1\n"),
        ])

        self.assertEqual(
            _pr.branch_patch_ids("/repo", "base", "head", run=run),
            ["patch-a", "patch-b"],
        )

    def test_remote_head_returns_sha_or_none(self):
        run = FakeRun([
            Result(stdout="abc123\trefs/heads/ai/x\n"),
            Result(stdout=""),
            Result(returncode=2, stderr="missing"),
        ])

        self.assertEqual(_pr.remote_head("/repo", "ai/x", run=run), "abc123")
        self.assertIsNone(_pr.remote_head("/repo", "ai/missing", run=run))
        self.assertIsNone(_pr.remote_head("/repo", "ai/error", run=run))


class OpenPrTests(unittest.TestCase):
    def setUp(self):
        if not _have_sshkeygen():
            self.skipTest("ssh-keygen not available")
        self.tmp = tempfile.mkdtemp()
        self.keys = pathlib.Path(self.tmp) / "keys"
        self.keys.mkdir()
        os.environ["BRIDGE_KEYS_DIR"] = str(self.keys)
        self.proj = pathlib.Path(self.tmp) / "proj"
        (self.proj / ".collab" / "keys").mkdir(parents=True)
        _gen_key(str(self.keys / "Claude.key"))
        (self.proj / ".collab" / "keys" / "allowed_signers").write_text(
            _pub_line("Claude", str(self.keys / "Claude.key")))

    def tearDown(self):
        os.environ.pop("BRIDGE_KEYS_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _signed_evidence(self, **kwargs):
        evidence = _evidence(**kwargs)
        evidence["base_ref"] = "origin/main"
        sig = ev.sign(evidence, "Claude", str(self.proj))
        self.assertTrue(sig)
        return evidence, sig

    def _push_commands(self, run):
        return [cmd for cmd in run.commands() if cmd[:4] == ["git", "-C", "/repo", "push"]]

    def _successful_open_pr_run(self):
        return FakeRun([
            Result(stdout="h" * 40 + "\n"),
            Result(stdout="b" * 40 + "\trefs/heads/main\n"),
            Result(stdout="c2\nc1\n"),
            Result(stdout="diff c2"),
            Result(stdout="patch-2 c2\n"),
            Result(stdout="diff c1"),
            Result(stdout="patch-1 c1\n"),
            Result(),
            Result(stdout="h" * 40 + "\trefs/heads/ai/x\n"),
        ])

    def test_head_mismatch_aborts_before_push_or_pr(self):
        evidence, sig = self._signed_evidence(head="h" * 40)
        run = FakeRun([Result(stdout="x" * 40 + "\n")])
        gh = FakeGh()

        with self.assertRaisesRegex(RuntimeError, "head moved since review"):
            _pr.open_pr("/repo", evidence["branch"], evidence, sig, str(self.proj), run=run, gh=gh)

        self.assertEqual(len(run.commands()), 1)
        self.assertEqual(gh.calls, [])

    def test_base_advanced_aborts_before_push_or_pr(self):
        evidence, sig = self._signed_evidence(head="h" * 40)
        run = FakeRun([
            Result(stdout="h" * 40 + "\n"),
            Result(stdout="x" * 40 + "\trefs/heads/main\n"),
        ])
        gh = FakeGh()

        with self.assertRaisesRegex(RuntimeError, "base advanced since review"):
            _pr.open_pr("/repo", evidence["branch"], evidence, sig, str(self.proj), run=run, gh=gh)

        self.assertEqual(self._push_commands(run), [])
        self.assertEqual(gh.calls, [])

    def test_patch_id_mismatch_aborts_before_push_or_pr(self):
        evidence, sig = self._signed_evidence(head="h" * 40)
        run = FakeRun([
            Result(stdout="h" * 40 + "\n"),
            Result(stdout="b" * 40 + "\trefs/heads/main\n"),
            Result(stdout="c1\n"),
            Result(stdout="diff c1"),
            Result(stdout="different-patch c1\n"),
        ])
        gh = FakeGh()

        with self.assertRaisesRegex(RuntimeError, "diff patch-ids do not match signed evidence"):
            _pr.open_pr("/repo", evidence["branch"], evidence, sig, str(self.proj), run=run, gh=gh)

        self.assertEqual(self._push_commands(run), [])
        self.assertEqual(gh.calls, [])

    def test_remote_head_mismatch_aborts_after_push_before_pr(self):
        evidence, sig = self._signed_evidence(head="h" * 40)
        run = FakeRun([
            Result(stdout="h" * 40 + "\n"),
            Result(stdout="b" * 40 + "\trefs/heads/main\n"),
            Result(stdout="c2\nc1\n"),
            Result(stdout="diff c2"),
            Result(stdout="patch-2 c2\n"),
            Result(stdout="diff c1"),
            Result(stdout="patch-1 c1\n"),
            Result(),
            Result(stdout="x" * 40 + "\trefs/heads/ai/x\n"),
        ])
        gh = FakeGh()

        with self.assertRaisesRegex(RuntimeError, "remote head differs from signed evidence"):
            _pr.open_pr("/repo", evidence["branch"], evidence, sig, str(self.proj), run=run, gh=gh)

        self.assertIn(["git", "-C", "/repo", "push", "origin", evidence["branch"]], run.commands())
        self.assertEqual(gh.calls, [])

    def test_invalid_signature_aborts_before_push_or_pr(self):
        evidence, _sig = self._signed_evidence(head="h" * 40)
        run = FakeRun([Result(stdout="h" * 40 + "\n")])
        gh = FakeGh()

        with self.assertRaisesRegex(RuntimeError, "signed evidence did not verify"):
            _pr.open_pr("/repo", evidence["branch"], evidence, "junk", str(self.proj), run=run, gh=gh)

        self.assertEqual(self._push_commands(run), [])
        self.assertEqual(gh.calls, [])

    def test_fix_first_verdict_aborts_before_push_or_pr(self):
        evidence, _sig = self._signed_evidence(head="h" * 40)
        evidence["verdict"] = "FIX-FIRST"
        sig = ev.sign(evidence, "Claude", str(self.proj))
        run = FakeRun([Result(stdout="h" * 40 + "\n")])
        gh = FakeGh()

        with self.assertRaisesRegex(RuntimeError, "approving reviewer verdict"):
            _pr.open_pr("/repo", evidence["branch"], evidence, sig, str(self.proj), run=run, gh=gh)

        self.assertEqual(self._push_commands(run), [])
        self.assertEqual(gh.calls, [])

    def test_branch_mismatch_aborts_before_push_or_pr(self):
        evidence, sig = self._signed_evidence(head="h" * 40)
        run = FakeRun([Result(stdout="h" * 40 + "\n")])
        gh = FakeGh()

        with self.assertRaisesRegex(RuntimeError, "signed evidence branch"):
            _pr.open_pr("/repo", "ai/other-branch", evidence, sig, str(self.proj), run=run, gh=gh)

        self.assertEqual(self._push_commands(run), [])
        self.assertEqual(gh.calls, [])

    def test_tests_ok_false_aborts_before_push_or_pr(self):
        # Actuator defense-in-depth: even a validly-signed GO evidence must not open a PR
        # if it doesn't attest passing tests.
        evidence, _sig = self._signed_evidence(head="h" * 40)
        evidence["tests_ok"] = False
        sig = ev.sign(evidence, "Claude", str(self.proj))
        run = FakeRun([Result(stdout="h" * 40 + "\n")])
        gh = FakeGh()

        with self.assertRaisesRegex(RuntimeError, "passing tests"):
            _pr.open_pr("/repo", evidence["branch"], evidence, sig, str(self.proj), run=run, gh=gh)

        self.assertEqual(self._push_commands(run), [])
        self.assertEqual(gh.calls, [])

    def test_happy_path_opens_pr_with_correct_evidence_body(self):
        evidence, sig = self._signed_evidence(head="h" * 40)
        run = self._successful_open_pr_run()
        gh = FakeGh()

        url = _pr.open_pr("/repo", evidence["branch"], evidence, sig, str(self.proj), run=run, gh=gh)

        self.assertEqual(url, "https://github.example/pr/7")
        self.assertEqual(len(gh.calls), 1)
        argv = gh.calls[0][0]
        self.assertEqual(argv[:3], ["gh", "pr", "create"])
        self.assertIn("--head", argv)
        self.assertEqual(argv[argv.index("--head") + 1], evidence["branch"])
        self.assertIn("--base", argv)
        self.assertEqual(argv[argv.index("--base") + 1], "main")

        body = gh.bodies[0]
        self.assertIn("AI-authored change", body)
        self.assertIn("Implemented by: Codex", body)
        self.assertIn("Reviewed by: Claude", body)
        self.assertIn("VERDICT: GO", body)
        self.assertIn("Tests: PASSED (python3 -m unittest tests.test_pr)", body)
        self.assertIn("Tamper-evident", body)
        self.assertIn("single host", body)
        self.assertNotIn("cryptographically proven independent review", body)
        self.assertIn(evidence["test_log_hash"], body)
        self.assertIn(evidence["nonce"], body)
        self.assertIn("SSHSIG", body)
        self.assertNotIn("Ran 4 tests", body)
        self.assertIn(ev.canonical(evidence), body)

    def test_dry_run_verifies_without_push_or_gh(self):
        evidence, sig = self._signed_evidence(head="h" * 40)
        run = FakeRun([
            Result(stdout="h" * 40 + "\n"),
            Result(stdout="b" * 40 + "\trefs/heads/main\n"),
            Result(stdout="c2\nc1\n"),
            Result(stdout="diff c2"),
            Result(stdout="patch-2 c2\n"),
            Result(stdout="diff c1"),
            Result(stdout="patch-1 c1\n"),
        ])
        gh = FakeGh()

        url = _pr.open_pr(
            "/repo", evidence["branch"], evidence, sig, str(self.proj),
            run=run, gh=gh, dry_run=True)

        self.assertEqual(url, "")
        self.assertEqual(self._push_commands(run), [])
        self.assertEqual(gh.calls, [])

    def test_pr_body_tests_line_uses_tests_ok_not_verdict(self):
        evidence, sig = self._signed_evidence(head="h" * 40)
        evidence["tests_ok"] = False

        body = _pr.pr_body(evidence, sig)

        self.assertIn("Tests: FAILED (python3 -m unittest tests.test_pr)", body)


class TestCommandTests(unittest.TestCase):
    def test_true_command_without_output_is_not_tests_ok(self):
        ok, log = _ship._run_test_cmd(str(ROOT), "true")

        self.assertFalse(ok)
        self.assertEqual(log, "")


class ShipDryRunTests(unittest.TestCase):
    def test_ship_dry_run_completes_flow_and_prints_would_open_pr(self):
        base_sha = "b" * 40
        head_sha = "h" * 40
        branch = "ai/dry-run-bbbbbbb"
        open_pr_calls = []

        def fake_run_task(project, task, implement, review, push, max_fix_rounds=2,
                          image_path=None):
            impl = implement(project, task, "", image_path)
            self.assertTrue(impl["ok"])
            verdict = review(project, impl["head_sha"])
            self.assertEqual(verdict["verdict"], "GO")
            pushed = push(project)
            self.assertTrue(pushed["ok"])
            return {"ok": True, "commit": pushed["pushed_sha"], "summary": "dry"}

        def fake_open_pr(repo, actual_branch, evidence, sig, project, **kwargs):
            open_pr_calls.append((repo, actual_branch, evidence, sig, project, kwargs))
            self.assertTrue(kwargs.get("dry_run"))
            return ""

        args = types.SimpleNamespace(
            project="/repo",
            task=["dry", "run", "task"],
            base="main",
            test_cmd="python3 -m unittest tests.test_pr",
            implementer="Codex",
            reviewer="Claude",
            max_fix_rounds=1,
            dry_run=True,
        )

        with mock.patch.object(_ship, "find_project_root", return_value="/repo"), \
                mock.patch.object(_ship, "_git", return_value=base_sha), \
                mock.patch.object(_pr, "create_branch", return_value=branch), \
                mock.patch.object(_chat_executor, "_roles_for", return_value=("Codex", "Claude")), \
                mock.patch.object(_ship, "_git_head", side_effect=[base_sha, head_sha, head_sha]), \
                mock.patch.object(_pr, "remote_head", return_value=None), \
                mock.patch.object(_agent_cli, "implement_argv", return_value=["implement"]), \
                mock.patch.object(_agent_cli, "review_argv", return_value=["review"]), \
                mock.patch.object(_ship.subprocess, "run", side_effect=[
                    Result(returncode=0),
                    Result(returncode=0, stdout="VERDICT: GO ok\n"),
                ]), \
                mock.patch.object(_ship, "_run_test_cmd", return_value=(True, "Ran 1 test\nOK\n")), \
                mock.patch.object(_ship, "_changed_files", return_value=["scripts/x.py"]), \
                mock.patch.object(_ship, "_diffstat", return_value="scripts/x.py | 1 +"), \
                mock.patch.object(_pr, "branch_patch_ids", return_value=["patch-1"]), \
                mock.patch.object(ev, "sign", return_value="signed-evidence"), \
                mock.patch.object(ev, "verify", return_value=True), \
                mock.patch.object(_pr, "open_pr", side_effect=fake_open_pr), \
                mock.patch.object(_chat_executor, "run_task", side_effect=fake_run_task):
            out = StringIO()
            with redirect_stdout(out):
                rc = _ship.run(args)

        self.assertEqual(rc, 0)
        self.assertEqual(len(open_pr_calls), 1)
        text = out.getvalue()
        self.assertIn("status: DRY_RUN", text)
        self.assertIn("DRY RUN", text)
        self.assertIn("would open PR", text)
        self.assertIn('"tests_ok": true', text)
        self.assertIn("verdict: GO", text)
        self.assertIn("tests_ok: True", text)
        self.assertIn("pr_title: AI PR Gate: %s" % branch, text)
        self.assertIn("pr_base: main", text)
        self.assertIn("pr_head: %s" % branch, text)
        self.assertIn("AI-authored change", text)


class CliDispatchTests(unittest.TestCase):
    def test_ship_and_pr_aliases_show_ship_help_without_running_flow(self):
        for cmd in ("ship", "pr"):
            with self.subTest(cmd=cmd):
                r = subprocess.run(
                    [str(BIN), cmd, "--help"],
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("--test-cmd", r.stdout)
                self.assertIn("AI PR Gate", r.stdout)


if __name__ == "__main__":
    unittest.main()
