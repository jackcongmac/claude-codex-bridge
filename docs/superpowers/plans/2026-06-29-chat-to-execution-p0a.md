# Chat-driven execution — P0a core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic core that turns a human's chat message into a gated, reported execution decision — roles config → greenlight decision → ack-before-act → enqueue → report + list update — with the LLM judgment and agent execution as injected boundaries.

**Architecture:** Pure-stdlib Python modules mirroring `scripts/_chat_respond.py` (a `*_once` pass with injected `judge`/`executor`, an `argparse` `once` CLI, a shell wrapper, and the existing responder supervisor as host). The deterministic orchestration is fully unit-testable with fakes; the real LLM judgment and the write-capable executor are pluggable boundaries wired in a later plan.

**Tech Stack:** Python 3 stdlib only; `unittest`; the existing `bridge_common`, `_post`, and `bridge-chat-web.py` (`parse_chat`).

## Global Constraints

- **Stdlib only** — no new dependencies. (Copy verbatim from spec §Tech Stack.)
- **No hardcoded identities (audit C⑩)** — names come from `.collab/roles.json`; in-code defaults are neutral: `human="Human"`, `lead=""` (unset). Never hardcode "Claude"/"Codex"/a person.
- **All instance data is local** — under `.collab/` (gitignored). Nothing instance-specific in shipped `files` (scripts/ skill/ templates/ docs/ bin/ AGENTS.md CLAUDE.md). `templates/` holds only an EMPTY/neutral template.
- **Direction only from the human** — only a message whose speaker equals the configured `human` may trigger anything.
- **Safe by default** — execution is gated behind `BRIDGE_CHAT_EXECUTE=1`; OFF returns `"disabled"` and does nothing (mirror `BRIDGE_CHAT_AUTORESPOND`).
- **Reuse, don't rebuild** — `bridge_common.{collab_paths,find_project_root,read_section,now_str,acquire_lock,release_lock}`; `bridge-chat-web.parse_chat`; `_post.post`.
- **Tests** — `python3 -m unittest`; use a `tempfile` project with a `.collab/` dir; inject all boundaries (no real LLM/agent/network in tests).

---

### Task 1: Roles config module

**Files:**
- Create: `scripts/_chat_roles.py`
- Create: `templates/roles.json`
- Modify: `scripts/init-collaboration.sh` (add one `copy_if_absent` line near the other `.collab` templates, ~line 38)
- Test: `tests/test_chat_roles.py`

**Interfaces:**
- Produces: `load_roles(project) -> {"human": str, "lead": str}`; `is_human(speaker, project) -> bool`; `lead_name(project) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_roles.py
import json, os, pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_roles as cr

class ChatRolesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.tmp, ".collab"))

    def _write(self, obj):
        with open(os.path.join(self.tmp, ".collab", "roles.json"), "w") as f:
            json.dump(obj, f)

    def test_defaults_when_missing(self):
        self.assertEqual(cr.load_roles(self.tmp), {"human": "Human", "lead": ""})

    def test_loads_configured_roles(self):
        self._write({"human": "Jack", "lead": "Claude"})
        self.assertEqual(cr.load_roles(self.tmp), {"human": "Jack", "lead": "Claude"})
        self.assertTrue(cr.is_human("Jack", self.tmp))
        self.assertFalse(cr.is_human("Claude", self.tmp))
        self.assertEqual(cr.lead_name(self.tmp), "Claude")

    def test_corrupt_file_falls_back_to_defaults(self):
        with open(os.path.join(self.tmp, ".collab", "roles.json"), "w") as f:
            f.write("{not json")
        self.assertEqual(cr.load_roles(self.tmp), {"human": "Human", "lead": ""})

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_roles -v`
Expected: FAIL — `ModuleNotFoundError: No module named '_chat_roles'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/_chat_roles.py
"""Per-instance role config for chat-driven execution. INSTANCE DATA — lives in
.collab/roles.json (gitignored). No identity is hardcoded; defaults are neutral."""
import json
import os

from bridge_common import collab_paths, find_project_root

DEFAULTS = {"human": "Human", "lead": ""}


def roles_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"], "roles.json")


def load_roles(project):
    out = dict(DEFAULTS)
    try:
        with open(roles_path(project)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return out
    if isinstance(data, dict):
        for key in ("human", "lead"):
            if isinstance(data.get(key), str) and data[key]:
                out[key] = data[key]
    return out


def is_human(speaker, project):
    return bool(speaker) and speaker == load_roles(project)["human"]


def lead_name(project):
    return load_roles(project)["lead"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_chat_roles -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the template + init wiring**

```json
// templates/roles.json  — neutral template shipped with the skill (NO real names)
{"human": "Human", "lead": ""}
```

In `scripts/init-collaboration.sh`, after the existing `collaboration_queue.json` copy line, add:

```bash
copy_if_absent "$REPO_DIR/templates/roles.json"               "$COLLAB/roles.json"
```

- [ ] **Step 6: Commit**

```bash
git add scripts/_chat_roles.py templates/roles.json scripts/init-collaboration.sh tests/test_chat_roles.py
git commit -m "feat(chat-exec): per-instance roles config (.collab/roles.json)"
```

---

### Task 2: High-risk action detection

**Files:**
- Create: `scripts/_chat_execute.py`
- Test: `tests/test_chat_execute.py`

**Interfaces:**
- Produces: `is_high_risk(task_text) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_execute.py
import os, pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_execute as ce

class HighRiskTests(unittest.TestCase):
    def test_flags_release_delete_publish(self):
        for t in ["发版 v0.9", "打 tag v1", "删掉这个文件", "git push --force",
                  "publish to npm", "release the build", "drop the table"]:
            self.assertTrue(ce.is_high_risk(t), t)

    def test_allows_routine_work(self):
        for t in ["把④英文化做了", "加个键盘导航", "修一下时间戳显示", "run the tests"]:
            self.assertFalse(ce.is_high_risk(t), t)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_execute.HighRiskTests -v`
Expected: FAIL — `ModuleNotFoundError: No module named '_chat_execute'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/_chat_execute.py
"""Chat-driven execution — P0a deterministic core. Turns a human chat message into a
gated, reported execution decision. LLM judgment and the write-capable executor are
injected boundaries (see execute_once). Safe: gated behind BRIDGE_CHAT_EXECUTE=1."""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import collab_paths, find_project_root, now_str  # noqa: E402
import _chat_roles  # noqa: E402

_HIGH_RISK = re.compile(
    r"(发版|发布|publish|release|打?\s*tag\b|删\s*(除|文件|掉)|\bdelete\b|\brm\b|"
    r"force[-\s]?push|--force|\bdrop\b)", re.I)


def is_high_risk(task_text):
    return bool(_HIGH_RISK.search(task_text or ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_chat_execute.HighRiskTests -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/_chat_execute.py tests/test_chat_execute.py
git commit -m "feat(chat-exec): high-risk action detection"
```

---

### Task 3: Greenlight decision (injected judge)

**Files:**
- Modify: `scripts/_chat_execute.py`
- Test: `tests/test_chat_execute.py`

**Interfaces:**
- Consumes: `_chat_roles.is_human`; `is_high_risk` (Task 2)
- Produces: `decide(project, msgs, judge) -> dict` where the dict's `"action"` is one of
  `"none" | "ignore" | "ask" | "execute" | "request_greenlight"`. `judge(text, context_msgs)`
  returns `{"kind": "actionable"|"opinion"|"ambiguous", "task": str?, "question": str?}`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_chat_execute.py
class DecideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.tmp, ".collab"))
        import json
        with open(os.path.join(self.tmp, ".collab", "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": "Claude"}, f)

    def _msgs(self, *pairs):
        return [{"speaker": s, "text": t} for s, t in pairs]

    def test_non_human_latest_is_ignored(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "hi"), ("Codex", "我去做")),
                      judge=lambda t, c: {"kind": "actionable", "task": t})
        self.assertEqual(d["action"], "ignore")

    def test_actionable_low_risk_executes(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "把④英文化做了")),
                      judge=lambda t, c: {"kind": "actionable", "task": "做 ④ 英文化"})
        self.assertEqual(d["action"], "execute")
        self.assertEqual(d["task"], "做 ④ 英文化")

    def test_actionable_high_risk_requests_greenlight(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "发版吧")),
                      judge=lambda t, c: {"kind": "actionable", "task": "发版 v0.9"})
        self.assertEqual(d["action"], "request_greenlight")

    def test_ambiguous_asks(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "④ 怎么样")),
                      judge=lambda t, c: {"kind": "ambiguous", "question": "你是要现在做④吗?"})
        self.assertEqual(d["action"], "ask")
        self.assertIn("④", d["question"])

    def test_opinion_is_ignored(self):
        d = ce.decide(self.tmp, self._msgs(("Jack", "我觉得④挺重要")),
                      judge=lambda t, c: {"kind": "opinion"})
        self.assertEqual(d["action"], "ignore")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_execute.DecideTests -v`
Expected: FAIL — `AttributeError: module '_chat_execute' has no attribute 'decide'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/_chat_execute.py
def decide(project, msgs, judge):
    if not msgs:
        return {"action": "none"}
    latest = msgs[-1]
    if not _chat_roles.is_human(latest.get("speaker", ""), project):
        return {"action": "ignore", "reason": "not-human"}
    verdict = judge(latest.get("text", ""), msgs[:-1]) or {}
    kind = verdict.get("kind")
    if kind == "actionable":
        task = (verdict.get("task") or latest.get("text", "")).strip()
        if is_high_risk(task):
            return {"action": "request_greenlight", "task": task}
        return {"action": "execute", "task": task}
    if kind == "ambiguous":
        return {"action": "ask",
                "question": verdict.get("question") or "你是要现在就开始做吗?"}
    return {"action": "ignore", "reason": "opinion"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_chat_execute.DecideTests -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/_chat_execute.py tests/test_chat_execute.py
git commit -m "feat(chat-exec): greenlight decision with injected judge"
```

---

### Task 4: Ack-before-act + enqueue ordering

**Files:**
- Modify: `scripts/_chat_execute.py`
- Test: `tests/test_chat_execute.py`

**Interfaces:**
- Produces: `dispatch(project, decision, poster, enqueue) -> str`. `poster(text)` posts to the
  chat; `enqueue(task)` hands the task to the work pipeline. Returns one of
  `"acked-enqueued" | "asked" | "requested-greenlight" | "noop"`. CONTRACT: for an `execute`
  decision the ack MUST be posted before `enqueue` is called.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_chat_execute.py
class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.tmp, ".collab"))
        self.events = []

    def _poster(self, text): self.events.append(("post", text))
    def _enqueue(self, task): self.events.append(("enqueue", task))

    def test_execute_acks_before_enqueue(self):
        st = ce.dispatch(self.tmp, {"action": "execute", "task": "做 ④"},
                         self._poster, self._enqueue)
        self.assertEqual(st, "acked-enqueued")
        kinds = [e[0] for e in self.events]
        self.assertEqual(kinds, ["post", "enqueue"])          # ack strictly first
        self.assertIn("开始执行", self.events[0][1])
        self.assertIn("做 ④", self.events[0][1])

    def test_request_greenlight_posts_and_does_not_enqueue(self):
        st = ce.dispatch(self.tmp, {"action": "request_greenlight", "task": "发版"},
                         self._poster, self._enqueue)
        self.assertEqual(st, "requested-greenlight")
        self.assertEqual([e[0] for e in self.events], ["post"])
        self.assertIn("需要你点头", self.events[0][1])

    def test_ask_posts_question_only(self):
        st = ce.dispatch(self.tmp, {"action": "ask", "question": "现在做④吗?"},
                         self._poster, self._enqueue)
        self.assertEqual(st, "asked")
        self.assertEqual([e[0] for e in self.events], ["post"])

    def test_ignore_is_noop(self):
        st = ce.dispatch(self.tmp, {"action": "ignore"}, self._poster, self._enqueue)
        self.assertEqual(st, "noop")
        self.assertEqual(self.events, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_execute.DispatchTests -v`
Expected: FAIL — `AttributeError: module '_chat_execute' has no attribute 'dispatch'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/_chat_execute.py
def dispatch(project, decision, poster, enqueue):
    action = decision.get("action")
    if action == "execute":
        poster("开始执行:%s" % decision["task"])   # ack-before-act, strictly first
        enqueue(decision["task"])
        return "acked-enqueued"
    if action == "request_greenlight":
        poster("这个需要你点头才能做(高风险):%s" % decision.get("task", ""))
        return "requested-greenlight"
    if action == "ask":
        poster(decision.get("question") or "你是要现在就开始做吗?")
        return "asked"
    return "noop"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_chat_execute.DispatchTests -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/_chat_execute.py tests/test_chat_execute.py
git commit -m "feat(chat-exec): ack-before-act dispatch (ack strictly precedes enqueue)"
```

---

### Task 5: Report-back + execution log

**Files:**
- Modify: `scripts/_chat_execute.py`
- Test: `tests/test_chat_execute.py`

**Interfaces:**
- Produces: `report(project, task, result, poster) -> None`. `result` is
  `{"ok": bool, "summary": str, "commit": str?}`. Posts a chat line AND appends a row to
  `.collab/ISSUES.md` under a `## 执行记录` section (created if absent).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_chat_execute.py
class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.tmp, ".collab"))
        self.posts = []

    def _issues(self):
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            return f.read()

    def test_success_posts_and_logs_commit(self):
        ce.report(self.tmp, "做 ④ 英文化",
                  {"ok": True, "summary": "英文化完成", "commit": "abc1234"},
                  self.posts.append)
        self.assertTrue(any("✅" in p and "abc1234" in p for p in self.posts))
        log = self._issues()
        self.assertIn("## 执行记录", log)
        self.assertIn("做 ④ 英文化", log)
        self.assertIn("abc1234", log)

    def test_failure_posts_and_logs_reason(self):
        ce.report(self.tmp, "做 X", {"ok": False, "summary": "测试没过"}, self.posts.append)
        self.assertTrue(any("❌" in p and "测试没过" in p for p in self.posts))
        self.assertIn("测试没过", self._issues())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_execute.ReportTests -v`
Expected: FAIL — `AttributeError: module '_chat_execute' has no attribute 'report'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/_chat_execute.py
def _issues_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"], "ISSUES.md")


def report(project, task, result, poster):
    ok = bool(result.get("ok"))
    summary = result.get("summary") or ""
    commit = result.get("commit") or ""
    if ok:
        line = "✅ 完成:%s%s" % (task, ("(commit %s)" % commit) if commit else "")
    else:
        line = "❌ 失败:%s — %s" % (task, summary)
    poster(line)

    path = _issues_path(project)
    try:
        with open(path) as f:
            body = f.read()
    except OSError:
        body = ""
    if "## 执行记录" not in body:
        body = body.rstrip() + "\n\n## 执行记录\n" if body else "## 执行记录\n"
    row = "- [%s] %s — %s%s%s\n" % (
        now_str()[:16], task, ("成功" if ok else "失败"),
        (" · " + summary) if summary else "",
        (" · commit " + commit) if commit else "")
    with open(path, "w") as f:
        f.write(body.rstrip() + "\n" + row)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_chat_execute.ReportTests -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/_chat_execute.py tests/test_chat_execute.py
git commit -m "feat(chat-exec): report-back to chat + .collab/ISSUES.md execution log"
```

---

### Task 6: `execute_once` pass + CLI + shell wrapper

**Files:**
- Modify: `scripts/_chat_execute.py`
- Create: `scripts/bridge-chat-execute.sh`
- Test: `tests/test_chat_execute.py`

**Interfaces:**
- Consumes: `decide` (Task 3), `dispatch` (Task 4), `report` (Task 5), `bridge-chat-web.parse_chat`,
  `bridge_common.read_section`, `_post.post`.
- Produces: `execute_once(project, judge=None, executor=None, poster=None) -> str` returning
  `"disabled" | "none" | "ignore" | "asked" | "requested-greenlight" | "done"`. `executor(task) ->
  result dict` (the write-capable boundary — defaults to a stub that returns
  `{"ok": False, "summary": "executor not wired (see next plan)"}`).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_chat_execute.py
class ExecuteOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        collab = os.path.join(self.tmp, ".collab"); os.mkdir(collab)
        import json
        with open(os.path.join(collab, "roles.json"), "w") as f:
            json.dump({"human": "Jack", "lead": "Claude"}, f)
        with open(os.path.join(collab, "collaboration_signal.json"), "w") as f:
            json.dump({"update_id": 0}, f)
        with open(os.path.join(collab, "collaboration.md"), "w") as f:
            f.write("# Board\n\n## Chat\n\n### 2026-06-29 10:00:00 PDT\n\n**Jack:** 把④英文化做了\n")
        self.posts = []

    def test_disabled_by_default(self):
        os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
        self.assertEqual(ce.execute_once(self.tmp), "disabled")

    def test_runs_full_path_with_injected_judge_and_executor(self):
        os.environ["BRIDGE_CHAT_EXECUTE"] = "1"
        try:
            st = ce.execute_once(
                self.tmp,
                judge=lambda t, c: {"kind": "actionable", "task": "做 ④ 英文化"},
                executor=lambda task: {"ok": True, "summary": "done", "commit": "abc1234"},
                poster=self.posts.append)
        finally:
            os.environ.pop("BRIDGE_CHAT_EXECUTE", None)
        self.assertEqual(st, "done")
        joined = "\n".join(self.posts)
        self.assertIn("开始执行", joined)     # ack
        self.assertIn("✅", joined)            # report
        with open(os.path.join(self.tmp, ".collab", "ISSUES.md")) as f:
            self.assertIn("abc1234", f.read())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_execute.ExecuteOnceTests -v`
Expected: FAIL — `AttributeError: module '_chat_execute' has no attribute 'execute_once'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/_chat_execute.py — imports at top of file
import importlib.util as _ilu
from _post import post as _board_post   # noqa: E402

def _parse_chat_fn():
    # bridge-chat-web.py is hyphen-named; load parse_chat the same way _chat_respond does
    here = os.path.dirname(os.path.abspath(__file__))
    spec = _ilu.spec_from_file_location("_cw", os.path.join(here, "bridge-chat-web.py"))
    mod = _ilu.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.parse_chat


def _stub_executor(task):
    return {"ok": False, "summary": "executor not wired (see next plan)"}


def execute_once(project, judge=None, executor=None, poster=None):
    if os.environ.get("BRIDGE_CHAT_EXECUTE") != "1":
        return "disabled"
    if judge is None or executor is None:
        # real boundaries are wired in the executor-integration plan; refuse to guess here
        executor = executor or _stub_executor
        if judge is None:
            return "none"
    from bridge_common import read_section
    msgs = _parse_chat_fn()(read_section(
        collab_paths(find_project_root(project))["board"], "Chat"))
    if poster is None:
        lead = _chat_roles.lead_name(project) or "Claude"
        poster = lambda text: _board_post(project, lead, text, section="Chat")
    decision = decide(project, msgs, judge)

    captured = {}
    def _enqueue(task):
        captured["task"] = task
    st = dispatch(project, decision, poster, _enqueue)
    if st != "acked-enqueued":
        return {"asked": "asked", "requested-greenlight": "requested-greenlight",
                "noop": "ignore"}.get(st, "none")
    result = executor(captured["task"])
    report(project, captured["task"], result, poster)
    return "done"


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    o = sub.add_parser("once")
    o.add_argument("--project", default=None)
    a = ap.parse_args()
    if a.cmd == "once":
        print(execute_once(a.project))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_chat_execute.ExecuteOnceTests -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the FULL suite (no regressions)**

Run: `python3 -m unittest discover -s tests -q`
Expected: `OK`

- [ ] **Step 6: Add the shell wrapper (mirrors bridge-chat-respond.sh)**

```bash
# scripts/bridge-chat-execute.sh
#!/usr/bin/env bash
# One pass of the chat-driven executor. OFF unless BRIDGE_CHAT_EXECUTE=1.
# Host it the same way as bridge-chat-respond.sh (supervised loop) when wiring P0a end-to-end.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${1:-$PWD}"
exec python3 "$HERE/_chat_execute.py" once --project "$PROJECT"
```

Then: `chmod +x scripts/bridge-chat-execute.sh`

- [ ] **Step 7: Commit**

```bash
git add scripts/_chat_execute.py scripts/bridge-chat-execute.sh tests/test_chat_execute.py
git commit -m "feat(chat-exec): execute_once pass + CLI + shell wrapper (executor injected)"
```

---

## What this plan deliberately defers (next plans)

- **Executor integration** — replace the injected `executor` with a real write-capable agent
  spawn that runs TDD → opens a review (no auto-push) → cross-AI review → push per the existing
  gate. This is where it touches real agents/LLM and deserves its own focused plan.
- **Real `judge`** — the default LLM-backed greenlight classifier (the injected `judge`).
- **Supervisor hosting** — run `bridge-chat-execute.sh` under the existing responder supervisor
  loop so it reacts in real time.
- **P0b** — persistent role-aware lead/executor + genuine deliberation + consensus + dynamic roster.

## Self-Review

- **Spec coverage:** §3 (human-only direction → Task 3 `is_human` gate; ack-before-act → Task 4;
  proactive greenlight on high-risk → Tasks 2+3+4; ask-when-unsure → Task 3 ambiguous). §4 safety
  gates 1/2/3/5 → Tasks 3/4. §6 local/config/no-hardcoded → Task 1 + Global Constraints. §7 files
  (`_chat_execute.py`, `roles.json`, tests, shell wrapper) → all tasks. Gates 4/6 (cross-review,
  consensus) and the real executor are explicitly deferred to the next plans (noted above).
- **Placeholder scan:** no TBD/TODO; every code step shows full code; the one stub (`_stub_executor`)
  is an intentional, named boundary with a clear message, not a placeholder.
- **Type consistency:** `decide` returns `{"action": ...}` consumed by `dispatch`; `dispatch`
  returns `"acked-enqueued"` checked in `execute_once`; `executor(task)->result` shape matches
  `report(project, task, result, poster)`. Names consistent across tasks.
