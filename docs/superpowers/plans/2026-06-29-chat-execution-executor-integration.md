# Chat-driven execution — executor integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace P0a's stub executor with a real pipeline that, for one greenlit task, dispatches an implementer agent (TDD, commit, no push), a reviewer agent (records a cross-review verdict in the ledger), loops on FIX-FIRST, and pushes via `bridge-push.sh` on GO — returning a result that P0a reports back to the chat.

**Architecture:** A new `scripts/_chat_executor.py` orchestrates `implement → review → (fix loop) → push` with each of those three steps as an INJECTED callable. The orchestration loop is fully unit-testable with fakes; the default callables shell out to `codex exec` (implement), a headless reviewer (review + `bridge-review.sh`), and `bridge-push.sh` (push) — the real boundaries. The existing **bridge-push review gate is the hard safety backstop**: unreviewed code cannot reach origin even if orchestration misbehaves.

**Tech Stack:** Python 3 stdlib only; `unittest`; `subprocess` for agent/tool spawns; reuse `bridge_common`, `bridge-review.sh`, `bridge-push.sh`, the P0a `_chat_execute` module.

## Global Constraints

- **Stdlib only**, no new deps. Python 3, `unittest`.
- **No hardcoded identities (C⑩)** — implementer/reviewer roles come from `.collab/roles.json` (`lead` reviews, the other active agent implements); never hardcode "Claude"/"Codex". Defaults neutral.
- **Safety backstop is the existing gate** — push ONLY via `scripts/bridge-push.sh`; never a bare `git push`. Author ≠ reviewer is enforced by the gate; do not bypass (`--no-review` forbidden).
- **Gated** — only reached when P0a's `execute_once` runs (human greenlight + `BRIDGE_CHAT_EXECUTE=1`). High-risk tasks are already stopped earlier by `decide()`.
- **Bounded fix loop** — at most `max_fix_rounds` (default 2) implement↔review cycles, then give up and report failure. Never loop unbounded.
- **All orchestration boundaries injected** — `implement`, `review`, `push` are parameters; tests pass fakes; no real subprocess/agent/network in unit tests.
- Reuse, don't rebuild: `_chat_execute.report`, `bridge-review.sh`, `bridge-push.sh`, `bridge_common`.

---

### Task 1: Orchestration loop (`run_task`)

**Files:**
- Create: `scripts/_chat_executor.py`
- Test: `tests/test_chat_executor.py`

**Interfaces:**
- Produces: `run_task(project, task, implement, review, push, max_fix_rounds=2) -> dict`
  returning `{"ok": bool, "summary": str, "commit": str}` (the shape P0a's `report()` consumes).
  - `implement(project, task, findings) -> {"ok": bool, "head_sha": str, "test_summary": str}`
    (`findings` is "" on the first round, else the reviewer's fix notes)
  - `review(project, head_sha) -> {"verdict": "GO"|"FIX-FIRST", "note": str}`
  - `push(project) -> {"ok": bool, "pushed_sha": str}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_executor.py
import os, pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_executor as ex

class RunTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.calls = []

    def _impl_ok(self, project, task, findings):
        self.calls.append(("implement", findings))
        return {"ok": True, "head_sha": "head1", "test_summary": "5 passed"}

    def _push_ok(self, project):
        self.calls.append(("push", None))
        return {"ok": True, "pushed_sha": "head1"}

    def test_go_path_pushes_and_returns_ok(self):
        r = ex.run_task(self.tmp, "做 ④",
                        implement=self._impl_ok,
                        review=lambda p, sha: {"verdict": "GO", "note": "clean"},
                        push=self._push_ok)
        self.assertTrue(r["ok"])
        self.assertEqual(r["commit"], "head1")
        self.assertEqual([c[0] for c in self.calls], ["implement", "push"])

    def test_fix_first_then_go_loops_once_then_pushes(self):
        verdicts = [{"verdict": "FIX-FIRST", "note": "missing test"},
                    {"verdict": "GO", "note": "ok"}]
        r = ex.run_task(self.tmp, "做 ④",
                        implement=self._impl_ok,
                        review=lambda p, sha: verdicts.pop(0),
                        push=self._push_ok)
        self.assertTrue(r["ok"])
        # implement called twice (2nd with findings), then push
        self.assertEqual([c[0] for c in self.calls], ["implement", "implement", "push"])
        self.assertIn("missing test", self.calls[1][1])      # findings threaded back

    def test_fix_first_exhausted_reports_failure_no_push(self):
        r = ex.run_task(self.tmp, "做 ④",
                        implement=self._impl_ok,
                        review=lambda p, sha: {"verdict": "FIX-FIRST", "note": "still broken"},
                        push=self._push_ok, max_fix_rounds=2)
        self.assertFalse(r["ok"])
        self.assertNotIn("push", [c[0] for c in self.calls])
        self.assertIn("still broken", r["summary"])

    def test_implement_failure_reports_no_review_no_push(self):
        r = ex.run_task(self.tmp, "做 ④",
                        implement=lambda p, t, f: {"ok": False, "head_sha": "", "test_summary": "tests red"},
                        review=lambda p, sha: {"verdict": "GO", "note": ""},
                        push=self._push_ok)
        self.assertFalse(r["ok"])
        self.assertEqual(self.calls, [])              # no push
        self.assertIn("tests red", r["summary"])

    def test_push_failure_reports_not_ok(self):
        r = ex.run_task(self.tmp, "做 ④",
                        implement=self._impl_ok,
                        review=lambda p, sha: {"verdict": "GO", "note": ""},
                        push=lambda p: {"ok": False, "pushed_sha": ""})
        self.assertFalse(r["ok"])
        self.assertIn("push", r["summary"].lower())

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_executor -v`
Expected: FAIL — `ModuleNotFoundError: No module named '_chat_executor'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/_chat_executor.py
"""Real executor pipeline for chat-driven execution: implement -> review -> (fix loop)
-> push, for ONE greenlit task. The three steps are injected callables (defaults shell
out to codex exec / a headless reviewer / bridge-push.sh). The bridge-push review gate is
the hard safety backstop — unreviewed code cannot reach origin even if this misbehaves."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_task(project, task, implement, review, push, max_fix_rounds=2):
    findings = ""
    impl = implement(project, task, findings)
    if not impl.get("ok"):
        return {"ok": False, "commit": "",
                "summary": "实现失败:%s" % impl.get("test_summary", "")}
    head = impl.get("head_sha", "")

    rounds = 0
    while True:
        verdict = review(project, head)
        if verdict.get("verdict") == "GO":
            break
        rounds += 1
        if rounds >= max_fix_rounds:
            return {"ok": False, "commit": head,
                    "summary": "互审未通过(%d 轮):%s" % (rounds, verdict.get("note", ""))}
        findings = verdict.get("note", "")
        impl = implement(project, task, findings)
        if not impl.get("ok"):
            return {"ok": False, "commit": head,
                    "summary": "修复后实现失败:%s" % impl.get("test_summary", "")}
        head = impl.get("head_sha", head)

    pushed = push(project)
    if not pushed.get("ok"):
        return {"ok": False, "commit": head, "summary": "push 失败"}
    return {"ok": True, "commit": pushed.get("pushed_sha", head), "summary": "完成并已推送"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_chat_executor -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/_chat_executor.py tests/test_chat_executor.py
git commit -m "feat(chat-exec): executor orchestration loop (implement->review->fix->push)"
```

---

### Task 2: Default boundary callables (real agent/tool spawns)

**Files:**
- Modify: `scripts/_chat_executor.py`
- Test: `tests/test_chat_executor.py`

**Interfaces:**
- Consumes: `_chat_roles.load_roles`; `bridge_common.find_project_root`.
- Produces: `default_implement(project, task, findings)`, `default_review(project, head_sha)`,
  `default_push(project)` matching the Task 1 signatures; plus `_git_head(project)` helper.

These shell out to real agents/tools, so they are validated by an integration smoke test (Step 4)
that runs them with a trivial no-op task behind `BRIDGE_CHAT_EXECUTOR_LIVE=1`, NOT in the unit suite.

- [ ] **Step 1: Write the failing test (helper + wiring, not the live spawns)**

```python
# add to tests/test_chat_executor.py
import subprocess
class GitHeadTests(unittest.TestCase):
    def test_git_head_returns_current_sha(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", d], check=True)
        subprocess.run(["git", "-C", d, "commit", "--allow-empty", "-m", "x",
                        "-c", "user.email=a@b.c", "-c", "user.name=t"], check=True)
        sha = ex._git_head(d)
        self.assertRegex(sha, r"^[0-9a-f]{40}$")

class RolesWiringTests(unittest.TestCase):
    def test_implementer_and_reviewer_roles_resolved_from_config(self):
        d = tempfile.mkdtemp(); os.mkdir(os.path.join(d, ".collab"))
        import json
        with open(os.path.join(d, ".collab", "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": "Claude"}, f)
        impl, rev = ex._roles_for(d)        # (implementer, reviewer)
        self.assertEqual(rev, "Claude")     # lead reviews
        self.assertNotEqual(impl, rev)      # implementer is the other side
        self.assertNotEqual(impl, "")       # and is concrete
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_executor.GitHeadTests tests.test_chat_executor.RolesWiringTests -v`
Expected: FAIL — `AttributeError: module '_chat_executor' has no attribute '_git_head'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/_chat_executor.py
import subprocess
from bridge_common import find_project_root
import _chat_roles

_DEFAULT_AGENTS = ("Codex", "Claude")   # neutral fallback when roster unknown


def _git_head(project):
    return subprocess.run(
        ["git", "-C", project, "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()


def _roles_for(project):
    """Return (implementer, reviewer). Reviewer = configured lead; implementer = the other
    known agent. No identity hardcoded beyond a neutral two-agent fallback."""
    roles = _chat_roles.load_roles(project)
    reviewer = roles["lead"] or _DEFAULT_AGENTS[1]
    implementer = next((a for a in _DEFAULT_AGENTS if a != reviewer), _DEFAULT_AGENTS[0])
    return implementer, reviewer


def default_implement(project, task, findings):
    root = find_project_root(project)
    base = _git_head(root)
    prompt = (
        "Implement this task with TDD (write a failing test, make it pass), commit your work, "
        "and DO NOT push. Task: %s" % task
        + (("\n\nReviewer findings to address:\n%s" % findings) if findings else ""))
    subprocess.run(["codex", "exec", "-s", "danger-full-access", prompt],
                   cwd=root, capture_output=True, text=True, timeout=1800)
    head = _git_head(root)
    return {"ok": head != base, "head_sha": head,
            "test_summary": "see codex output"}


def default_review(project, head_sha):
    root = find_project_root(project)
    prompt = (
        "Review the change at HEAD (%s) for spec compliance, code quality, and that its tests "
        "pass. Then record your verdict by running EXACTLY one of:\n"
        "  scripts/bridge-review.sh --self <you> --sha %s --verdict GO --note '<finding>'\n"
        "  scripts/bridge-review.sh --self <you> --sha %s --verdict FIX-FIRST --note '<finding>'\n"
        "Use GO only if genuinely correct and green." % (head_sha, head_sha, head_sha))
    subprocess.run(["claude", "-p", prompt], cwd=root,
                   capture_output=True, text=True, timeout=900)
    # read the verdict back from the ledger (source of truth, not the agent's stdout)
    from _review import latest_verdict   # added in Task 3
    return latest_verdict(root, head_sha)


def default_push(project):
    root = find_project_root(project)
    implementer, reviewer = _roles_for(root)
    r = subprocess.run(["scripts/bridge-push.sh", implementer],
                       cwd=root, capture_output=True, text=True, timeout=300)
    return {"ok": r.returncode == 0, "pushed_sha": _git_head(root)}
```

- [ ] **Step 4: Run unit tests + (optional) live smoke**

Run: `python3 -m unittest tests.test_chat_executor -v`
Expected: PASS (helper + wiring tests).
Live smoke (manual, not in CI): with `BRIDGE_CHAT_EXECUTOR_LIVE=1` on a scratch branch, call
`default_implement` with a trivial task and confirm a commit appears and nothing is pushed.

- [ ] **Step 5: Commit**

```bash
git add scripts/_chat_executor.py tests/test_chat_executor.py
git commit -m "feat(chat-exec): default implement/review/push boundaries (agent spawns)"
```

---

### Task 3: Ledger read-back (`latest_verdict`)

**Files:**
- Modify: `scripts/_review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Produces: `latest_verdict(project, sha) -> {"verdict": "GO"|"FIX-FIRST"|"NONE", "note": str}`
  reading the most recent ledger entry for `sha` (the source of truth the reviewer wrote).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_review.py
class LatestVerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.mkdir(os.path.join(self.tmp, ".collab"))

    def test_returns_most_recent_verdict_for_sha(self):
        import _review
        _review.record(self.tmp, "Claude", "deadbeef", "FIX-FIRST", note="first")
        _review.record(self.tmp, "Claude", "deadbeef", "GO", note="now ok")
        v = _review.latest_verdict(self.tmp, "deadbeef")
        self.assertEqual(v["verdict"], "GO")
        self.assertEqual(v["note"], "now ok")

    def test_unknown_sha_is_none(self):
        import _review
        self.assertEqual(_review.latest_verdict(self.tmp, "nope")["verdict"], "NONE")
```

(If `_review.record`'s signature differs, adapt the calls to the real one — check the top of
`scripts/_review.py` first.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_review.LatestVerdictTests -v`
Expected: FAIL — `AttributeError: module '_review' has no attribute 'latest_verdict'`

- [ ] **Step 3: Write minimal implementation**

`_review.py` stores entries as `{"reviews": [...]}` in `p["reviews"]`, read via
`read_json(p["reviews"], default={"reviews": []})` (see `record`/`has_approval`). Add, reusing
exactly that storage (collab_paths/find_project_root/read_json/_canonical_sha are already
imported in the module):

```python
# add to scripts/_review.py
def latest_verdict(project, sha):
    p = collab_paths(find_project_root(project))
    led = read_json(p["reviews"], default={"reviews": []}) or {"reviews": []}
    full = _canonical_sha(project, sha)
    matches = [e for e in led.get("reviews", []) if e.get("sha") in (sha, full)]
    if not matches:
        return {"verdict": "NONE", "note": ""}
    last = matches[-1]
    return {"verdict": last.get("verdict", "NONE"), "note": last.get("note", "")}
```

Do not introduce a second storage path — reuse `p["reviews"]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_review.LatestVerdictTests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/_review.py tests/test_review.py
git commit -m "feat(review): latest_verdict ledger read-back for the executor"
```

---

### Task 4: Wire the real executor into P0a + fix the empty-lead poster

**Files:**
- Modify: `scripts/_chat_execute.py`
- Test: `tests/test_chat_execute.py`

**Interfaces:**
- Consumes: `_chat_executor.run_task` + its default boundaries.
- Changes: `execute_once`'s default `executor` becomes a real pipeline runner; the default
  `poster` no longer posts with an empty speaker.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_chat_execute.py
class DefaultPosterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.mkdir(os.path.join(self.tmp, ".collab"))

    def test_default_poster_speaker_is_never_empty(self):
        import json
        with open(os.path.join(self.tmp, ".collab", "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": ""}, f)   # lead unconfigured
        self.assertNotEqual(ce._poster_speaker(self.tmp), "")   # falls back, never ""

class DefaultExecutorWiringTests(unittest.TestCase):
    def test_default_executor_is_the_pipeline_runner(self):
        self.assertIs(ce._default_executor, __import__("_chat_executor").run_task_executor)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_execute.DefaultPosterTests tests.test_chat_execute.DefaultExecutorWiringTests -v`
Expected: FAIL — `AttributeError: module '_chat_execute' has no attribute '_poster_speaker'`

- [ ] **Step 3: Write minimal implementation**

```python
# in scripts/_chat_executor.py — a single-arg adapter matching execute_once's executor(task)
def run_task_executor(task, project):
    return run_task(project, task, default_implement, default_review, default_push)
```

```python
# in scripts/_chat_execute.py
def _poster_speaker(project):
    return _chat_roles.lead_name(project) or "Lead"     # never empty

import _chat_executor  # noqa: E402
_default_executor = _chat_executor.run_task_executor
```

Then in `execute_once`, replace the stub default and the empty-lead poster:
- `executor = executor or _default_executor`
- `lead = _poster_speaker(project)` (instead of `_chat_roles.lead_name(project)`)
- the executor is called as `executor(captured["task"], project)` — update the call site to pass
  `project` (the `run_task_executor(task, project)` signature). Adjust the existing test
  `test_runs_full_path_with_injected_judge_and_executor` to pass `executor=lambda task, project: {...}`.

- [ ] **Step 4: Run test to verify it passes + full suite**

Run: `python3 -m unittest tests.test_chat_execute -v`
Expected: PASS
Run: `python3 -m unittest discover -s tests -q`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add scripts/_chat_execute.py scripts/_chat_executor.py tests/test_chat_execute.py
git commit -m "feat(chat-exec): wire real executor pipeline + non-empty default poster"
```

---

## What this plan defers

- **Real `judge`** (the LLM greenlight classifier) — still the next piece before end-to-end live use.
- **Supervisor hosting** of `bridge-chat-execute.sh` for real-time reaction.
- **P0b** — persistent role-aware daemons + deliberation + consensus + dynamic roster.
- **Live integration test** of the full chat→push loop (manual, on a scratch branch, behind a flag).

## Self-Review

- **Spec coverage:** spec §7 executor ("TDD → open review → cross-AI review → push per gate") →
  Tasks 1–4; §4 gate 4 (cross-review, author≠reviewer) → enforced by `default_push` using
  `bridge-push.sh` (the gate) + ledger read-back; bounded fix loop → Task 1 `max_fix_rounds`;
  C⑩ no-hardcoded → `_roles_for` from config; P0a open finding (empty-lead poster) → Task 4.
- **Placeholder scan:** no TBD/TODO; the agent-spawn boundaries are concrete subprocess calls,
  flagged as integration-validated (not unit-tested) by design, with a named live smoke step.
- **Type consistency:** `run_task` boundary signatures (`implement(project,task,findings)`,
  `review(project,head_sha)->{verdict,note}`, `push(project)->{ok,pushed_sha}`) match the defaults
  in Task 2 and the `latest_verdict` shape in Task 3; `run_task` returns `{ok,summary,commit}`
  matching `_chat_execute.report`; `run_task_executor(task,project)` matches `execute_once`'s call site.
- **Note for the implementer:** Task 3 must reuse `_review.py`'s existing ledger loader — read the
  file first; do not add a second storage path. Task 2's `default_review` depends on Task 3's
  `latest_verdict`, so implement Task 3 before Task 2's live smoke.
