# P0.5 — Low-Latency Resident Agents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut Codex per-reply latency (~12s cold-spawn → ~7s warm) with conversational context continuity, via an actor→capability registry (which also fixes roles-nominal), then a resident exec-server for few-second replies.

**Architecture:** One `_agent_cli.py` maps actor→spawn for chat/implement/review (no hardcoded codex/claude). The Codex chat path uses a tracked session (`codex exec --json` captures `thread_id`; subsequent replies `codex exec resume <thread_id>`). Phase B adds a resident `codex exec-server` supervisor (gated on a protocol spike).

**Tech Stack:** Python 3 stdlib, codex CLI (`exec`, `exec --json`, `exec resume`, `exec-server`), bash.

## Global Constraints

- The Codex chat session id is the `thread_id` from the FIRST `codex exec --json` event `{"type":"thread.started","thread_id":"..."}`.
- Track it in `.collab/.codex_chat_session` (JSON `{"session_id":"..."}`); gitignored/local.
- Resume with the TRACKED id (`codex exec resume <id>`), NEVER `--last` (it can grab an unrelated session).
- Resume failure / missing / corrupt session → fall back to a fresh `codex exec` and re-capture the id. Never hang: keep `stdin=/dev/null` + timeout on every spawn.
- No hardcoded codex/claude in default_implement/default_review — resolve from `_roles_for`.
- Every task leaves the full suite green: `python3 -m unittest discover -s tests -q`.
- Unit tests monkeypatch `subprocess.run` — never spawn real codex/claude in unit tests.

## File Structure

- `scripts/_agent_cli.py` (new) — actor→capability registry + Codex session-resume logic.
- `.collab/.codex_chat_session` (runtime, gitignored) — tracked Codex chat session id.
- `scripts/_chat_respond.py` (modify) — chat responder uses `_agent_cli` (resume session).
- `scripts/_chat_executor.py` (modify) — `default_implement/review` use `_agent_cli` with resolved roles.
- `scripts/bridge-chat-resident.sh` (new, Phase B) — supervisor hosting `codex exec-server`.
- Tests: `tests/test_agent_cli.py` (new), `tests/test_chat_respond.py` (modify), `tests/test_chat_executor.py` (modify).

---

# PHASE A.1 — actor→capability registry + roles-nominal

### Task 1: `_agent_cli.py` — spawn registry (chat/implement/review per actor)

**Files:**
- Create: `scripts/_agent_cli.py`
- Test: `tests/test_agent_cli.py`

**Interfaces:**
- Produces:
  - `codex_bin()` / `claude_bin()` (env override CODEX_BIN/CLAUDE_BIN else "codex"/"claude")
  - `chat_argv(actor, prompt, project, image_path=None, session_id=None) -> list[str]`
  - `implement_argv(actor, prompt, project, image_path=None) -> list[str]`
  - `review_argv(actor, prompt, project) -> list[str]`
  - `KNOWN = ("Claude","Codex")`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent_cli.py
import os, sys, unittest
sys.path.insert(0, "scripts")
import _agent_cli as ac

class ArgvTests(unittest.TestCase):
    def test_codex_chat_fresh_is_exec_json(self):
        a = ac.chat_argv("Codex", "hi", ".", session_id=None)
        self.assertEqual(a[:2], ["codex", "exec"])
        self.assertIn("--json", a)
        self.assertNotIn("resume", a)

    def test_codex_chat_with_session_is_resume(self):
        a = ac.chat_argv("Codex", "hi", ".", session_id="abc-123")
        self.assertEqual(a[:3], ["codex", "exec", "resume"])
        self.assertIn("abc-123", a)

    def test_codex_chat_carries_image(self):
        a = ac.chat_argv("Codex", "hi", ".", image_path="/tmp/x.png")
        self.assertIn("-i", a); self.assertIn("/tmp/x.png", a)

    def test_claude_chat_is_headless_p(self):
        a = ac.chat_argv("Claude", "hi", ".")
        self.assertEqual(a[0], "claude"); self.assertIn("-p", a)

    def test_codex_implement_is_workspace_write(self):
        a = ac.implement_argv("Codex", "do it", ".")
        self.assertIn("workspace-write", a); self.assertNotIn("danger-full-access", a)

    def test_codex_review_is_read_only(self):
        a = ac.review_argv("Codex", "review", ".")
        self.assertIn("read-only", a)

    def test_claude_review_is_headless_p(self):
        a = ac.review_argv("Claude", "review", ".")
        self.assertEqual(a[0], "claude"); self.assertIn("-p", a)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m unittest tests.test_agent_cli -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `scripts/_agent_cli.py`**

```python
"""_agent_cli.py — actor->capability registry: how each actor is spawned for chat / implement /
review. Keeps codex/claude invocation OUT of the call sites so roles can rotate (roles-nominal)
and so the chat path can use a warm resumed session (P0.5 latency). Argv builders only — the
running/timeout/stdin=/dev/null is the caller's job."""
import os

KNOWN = ("Claude", "Codex")
NAMESPACE_TIMEOUT_ENV = "BRIDGE_CHAT_TURN_TIMEOUT"


def codex_bin():
    return os.environ.get("CODEX_BIN") or "codex"


def claude_bin():
    return os.environ.get("CLAUDE_BIN") or "claude"


def chat_argv(actor, prompt, project, image_path=None, session_id=None):
    if actor == "Codex":
        if session_id:
            argv = [codex_bin(), "exec", "resume", session_id]
        else:
            argv = [codex_bin(), "exec", "--json"]
        if image_path:
            argv += ["-i", image_path]
        argv += ["-C", project, "--skip-git-repo-check", "--ignore-user-config",
                 "-s", "read-only", prompt]
        return argv
    # Claude (headless, read-capable for images)
    p = prompt
    if image_path:
        p += ("\n\n[The user attached an image at: %s — use your Read tool to view it "
              "before replying.]" % image_path)
    return [claude_bin(), "-p", p, "--output-format", "json", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "default",
            "--allowedTools", "Read", "Grep", "Glob"]


def implement_argv(actor, prompt, project, image_path=None):
    if actor == "Codex":
        argv = [codex_bin(), "exec", "-s", "workspace-write"]
        if image_path:
            argv += ["-i", image_path]
        argv += [prompt]
        return argv
    # Claude headless implement (write-capable)
    return [claude_bin(), "-p", prompt, "--permission-mode", "acceptEdits",
            "--allowedTools", "Read", "Edit", "Write", "Bash"]


def review_argv(actor, prompt, project):
    if actor == "Codex":
        return [codex_bin(), "exec", "-s", "read-only", "--skip-git-repo-check",
                "--ignore-user-config", "-C", project, prompt]
    return [claude_bin(), "-p", prompt]
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest tests.test_agent_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/_agent_cli.py tests/test_agent_cli.py
git commit -m "feat(agent): actor->capability spawn registry (chat/implement/review, no hardcoded cli)

Co-Authored-By: Codex <codex@openai.com>"
```

---

### Task 2: roles-nominal — `default_implement/review` use resolved roles via `_agent_cli`

**Files:**
- Modify: `scripts/_chat_executor.py` (`default_implement` ~line 84, `default_review` ~line 111)
- Test: `tests/test_chat_executor.py`

**Interfaces:**
- Consumes: `_agent_cli.implement_argv(implementer, ...)`, `_agent_cli.review_argv(reviewer, ...)`, `_roles_for`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_executor.py — add
def test_default_implement_uses_resolved_implementer_argv(self):
    import _chat_executor as ex, _agent_cli
    calls = []
    ex.subprocess.run = lambda cmd, *a, **k: calls.append(list(cmd)) or type("R",(),{"returncode":0,"stdout":"","stderr":""})()
    ex.find_project_root = lambda p: p
    ex._git_head = lambda p: "BASE" if not calls else "HEAD2"
    ex._remote_tracking_ref = lambda r: "R1"
    ex._roles_for = lambda p: ("Codex", "Claude")   # implementer=Codex, reviewer=Claude
    ex.default_implement(self.tmp, "do it", "")
    argv = calls[-1]
    self.assertEqual(argv[:2], ["codex", "exec"])          # implementer Codex -> codex
    self.assertIn("workspace-write", argv)

def test_default_implement_rotated_lead_spawns_claude(self):
    import _chat_executor as ex
    calls = []
    ex.subprocess.run = lambda cmd, *a, **k: calls.append(list(cmd)) or type("R",(),{"returncode":0,"stdout":"","stderr":""})()
    ex.find_project_root = lambda p: p
    ex._git_head = lambda p: "BASE" if not calls else "HEAD2"
    ex._remote_tracking_ref = lambda r: "R1"
    ex._roles_for = lambda p: ("Claude", "Codex")   # ROTATED: implementer=Claude
    ex.default_implement(self.tmp, "do it", "")
    self.assertEqual(calls[-1][0], "claude")               # implementer Claude -> claude
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m unittest tests.test_chat_executor -v`
Expected: FAIL — still hardcodes codex.

- [ ] **Step 3: Modify `default_implement` and `default_review`**

In `scripts/_chat_executor.py`, `import _agent_cli`. In `default_implement`, resolve the implementer and
build argv from the registry:

```python
def default_implement(project, task, findings, image_path=None):
    root = find_project_root(project)
    implementer, _reviewer = _roles_for(root)
    base = _git_head(root); remote_before = _remote_tracking_ref(root)
    prompt = ("Implement this task with TDD (write a failing test, make it pass), commit your work, "
              "and DO NOT push. Task: %s" % task
              + (("\n\nReviewer findings to address:\n%s" % findings) if findings else ""))
    cmd = _agent_cli.implement_argv(implementer, prompt, root, image_path=image_path)
    subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=1800)
    remote_after = _remote_tracking_ref(root); head = _git_head(root)
    if remote_before and remote_after and remote_before != remote_after:
        return {"ok": False, "head_sha": head,
                "test_summary": "SECURITY: implementer moved the remote ref during implement — aborting, no push"}
    return {"ok": head != base, "head_sha": head, "test_summary": "see implementer output"}
```

In `default_review`, build the reviewer's LLM call from `_agent_cli.review_argv(reviewer, prompt, root)`
instead of the hardcoded `["claude","-p",prompt]` in `_review_call_llm`. Keep `_run_tests` +
record + FIX-FIRST-on-red logic UNCHANGED (only the spawn argv is resolved by role).

- [ ] **Step 4: Run to verify pass + full suite**

Run: `python3 -m unittest tests.test_chat_executor -v` → PASS
Run: `python3 -m unittest discover -s tests -q` → OK

- [ ] **Step 5: Commit**

```bash
git add scripts/_chat_executor.py tests/test_chat_executor.py
git commit -m "fix(chat-exec): resolve implementer/reviewer CLI from roles (roles-nominal) via _agent_cli

Co-Authored-By: Codex <codex@openai.com>"
```

---

# PHASE A.2 — Codex chat responder uses warm resumed session

### Task 3: session-tracked resume in the Codex chat path

**Files:**
- Modify: `scripts/_chat_respond.py` (`_spawn_codex` ~line 310) + `scripts/_agent_cli.py` (add run helper)
- Test: `tests/test_chat_respond.py`

**Interfaces:**
- Produces in `_agent_cli`: `run_codex_chat(prompt, project, image_path=None) -> str` which:
  reads the tracked session id from `.collab/.codex_chat_session`; builds `chat_argv("Codex", ...,
  session_id=that)`; runs it (`stdin=/dev/null`, timeout, `--output-last-message` temp file for the reply);
  on a FRESH run (no id) captures `thread_id` from the `--json` `thread.started` line on stdout and SAVES
  it; on resume failure, deletes the id file and retries fresh once. Returns the reply text ("" on failure).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_respond.py — add
def test_codex_chat_fresh_captures_thread_id_then_resumes(self):
    import _agent_cli, os, json, tempfile
    proj = tempfile.mkdtemp(); os.makedirs(os.path.join(proj, ".collab"))
    calls = []
    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))
        # write the reply file (--output-last-message)
        if "--output-last-message" in cmd:
            open(cmd[cmd.index("--output-last-message")+1], "w").write("hi from codex")
        # fresh run emits --json thread.started on stdout
        out = ""
        if "--json" in cmd:
            out = json.dumps({"type":"thread.started","thread_id":"tid-1"}) + "\n"
        return type("R",(),{"returncode":0,"stdout":out,"stderr":""})()
    _agent_cli.subprocess.run = fake_run
    r1 = _agent_cli.run_codex_chat("hello", proj)
    self.assertEqual(r1, "hi from codex")
    self.assertEqual(json.load(open(os.path.join(proj, ".collab", ".codex_chat_session")))["session_id"], "tid-1")
    # second call resumes tid-1
    calls.clear()
    _agent_cli.run_codex_chat("again", proj)
    self.assertIn("resume", calls[-1]); self.assertIn("tid-1", calls[-1])
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m unittest tests.test_chat_respond -v` → FAIL (run_codex_chat missing).

- [ ] **Step 3: Implement `run_codex_chat` in `_agent_cli.py` + wire `_spawn_codex`**

Add to `_agent_cli.py` (import os, json, subprocess, tempfile at top):

```python
def _session_path(project):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bridge_common import collab_paths, find_project_root
    return os.path.join(collab_paths(find_project_root(project))["dir"], ".codex_chat_session")

def _load_session(project):
    try:
        with open(_session_path(project)) as f:
            return (json.load(f) or {}).get("session_id")
    except (OSError, ValueError):
        return None

def _save_session(project, sid):
    p = _session_path(project); tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"session_id": sid}, f)
    os.replace(tmp, p)

def _thread_id_from_json(stdout):
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "thread.started" and o.get("thread_id"):
            return o["thread_id"]
    return None

def run_codex_chat(prompt, project, image_path=None, _retried=False):
    import time as _t  # noqa (timeouts only)
    timeout = int(os.environ.get(NAMESPACE_TIMEOUT_ENV, "180"))
    sid = _load_session(project)
    td = tempfile.mkdtemp()
    last = os.path.join(td, "last.txt")
    argv = chat_argv("Codex", prompt, project, image_path=image_path, session_id=sid)
    argv += ["--output-last-message", last]
    try:
        with open(os.devnull) as devnull:
            r = subprocess.run(argv, stdin=devnull, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 and sid and not _retried:
            # resume failed -> drop the stale session and retry fresh once
            try: os.remove(_session_path(project))
            except OSError: pass
            return run_codex_chat(prompt, project, image_path=image_path, _retried=True)
        if not sid:                      # fresh run: capture the new thread id
            tid = _thread_id_from_json(r.stdout)
            if tid:
                _save_session(project, tid)
        try:
            with open(last) as f:
                return f.read().strip()
        except OSError:
            return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        import shutil; shutil.rmtree(td, ignore_errors=True)
```

Then in `scripts/_chat_respond.py`, replace `_spawn_codex`'s body with a call to
`_agent_cli.run_codex_chat(prompt, project, image_path=image_path)` (keep the `_spawn_codex(prompt,
project, image_path=None)` signature for its callers).

- [ ] **Step 4: Run to verify pass + full suite**

Run: `python3 -m unittest tests.test_chat_respond -v` → PASS
Run: `python3 -m unittest discover -s tests -q` → OK

- [ ] **Step 5: Commit**

```bash
git add scripts/_agent_cli.py scripts/_chat_respond.py tests/test_chat_respond.py
git commit -m "feat(chat): Codex chat replies use a warm resumed session (~12s->~7s + context memory)

Co-Authored-By: Codex <codex@openai.com>"
```

- [ ] **Step 6 (manual QA by lead, not a unit test):** live — send two chat messages to Codex, confirm the
  2nd is faster and Codex remembers the 1st (context continuity). Confirm `.collab/.codex_chat_session`
  holds a thread id. Add `.codex_chat_session` handling to `.gitignore` if `.collab` isn't already ignored.

---

# PHASE B — resident exec-server (SPIKE FIRST, then decide)

### Task 4: exec-server protocol spike (feasibility gate — no production wiring yet)

**Files:** none shipped; produce a findings note `docs/superpowers/specs/p05-phaseB-execserver-spike.md`.

- [ ] **Step 1:** Start `codex exec-server --listen stdio` (or `ws://127.0.0.1:PORT`) and determine the
  request/response message format for "run this prompt, return the reply" (inspect stderr/stdout on start;
  try a minimal JSON request; check `codex exec-server --help` and any `codex mcp-server` parallels).
- [ ] **Step 2:** Measure a WARM round-trip latency (second request on the same live server).
- [ ] **Step 3:** Write the findings note: protocol shape, warm latency, stability, and a GO/NO-GO for
  building the resident supervisor. If GO, a follow-up plan defines `bridge-chat-resident.sh`; if NO-GO,
  Phase A (~7s) is the shipped state and this is revisited later.

(Phase B production implementation is intentionally deferred behind this spike — it's experimental.)

---

## Notes

- Phase A.1 + A.2 are the immediately shippable latency+coherence win Jack validates first.
- roles-nominal is closed by Task 2 (folded into P0.5 as agreed).
- Claude's own chat latency (poll + re-invoke) is already tuned (1s poll, direct posting); no code task here.
