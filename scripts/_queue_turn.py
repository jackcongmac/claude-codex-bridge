#!/usr/bin/env python3
"""_queue_turn.py — one MULTI-AGENT queue turn (--queue-mode, v2 scalability).

Separate from _auto_turn.py: the 2-actor sequential path is untouched. Here, N
agents (e.g. codex-exec-1, codex-exec-2, claude-rev) collaborate via a work queue
(collaboration_queue.json). An agent CLAIMS one eligible open task under the global
lock, runs the model with NO lock held, then commits (result -> its own outbox,
task -> done, enqueue follow-ups) under the lock with claim_epoch fencing.

Concurrency safety:
- claim/commit are short critical sections under the SAME global lock as v4.
- claim_epoch (a monotonic counter in control.next_epoch) fences a TTL-reclaimed
  task: a late zombie's commit is rejected because the epoch changed.
- lease_expires_at > model timeout, so a single turn never expires its own lease;
  an expired lease (a dead/crashed agent) is reclaimable by another eligible agent.
- enqueue is idempotent: a follow-up's idempotency_key IS its task id; if that id
  already exists, it is not re-created.
- per-agent state/session files keyed by sanitized agent_id (no clobber).
- a project turn budget (control.max_turns) bounds runaway spend.

Usage: _queue_turn.py --as claude|codex --agent-id ID [--role R] [--project DIR]
       [--allow-write] [--lock-ttl S] [--lease-sec S]
Exit: 0 acted, 10 nothing-to-do/skip, 20 failed/halted, 30 error.
"""
import os
import re
import sys
import json
import time
import argparse
import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("_auto_turn", os.path.join(_HERE, "_auto_turn.py"))
at = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(at)

TASK_TYPES = {"execute", "review", "fix"}


def sanitize_agent_id(aid):
    if not aid or not re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", aid):
        sys.stderr.write("invalid --agent-id (use [A-Za-z0-9_.-], 1-64 chars)\n")
        sys.exit(2)
    return aid


def deps_done(task, by_id):
    return all(by_id.get(d, {}).get("status") == "done" for d in (task.get("depends_on") or []))


def eligible(task, side, role, now):
    if task.get("needs_side") and task["needs_side"] != side:
        return False
    nr = task.get("needs_role")
    if nr and role and nr != role:
        return False
    st = task.get("status")
    if st == "open":
        return True
    if st == "claimed":
        return float(task.get("lease_expires_at", 0)) < now   # reclaim an expired lease
    return False


def pick(tasks, by_id, side, role, now, agent_id):
    cand = [t for t in tasks if eligible(t, side, role, now) and deps_done(t, by_id)]
    # soft preference: a task that names me in preferred_agent sorts first.
    cand.sort(key=lambda t: (t.get("preferred_agent") != agent_id,
                             t.get("priority", 100), t.get("created_at", "")))
    return cand[0] if cand else None


CONTRACT = (
    "You are agent '%s' (side=%s, role=%s) working a task from a shared queue with "
    "OTHER agents. Do THIS task only. Return ONLY a JSON object:\n"
    '{"result_markdown": "<your output / findings>", '
    '"status": "done|failed|needs_human", '
    '"summary": "<one line>", '
    '"followups": [ {"id": "<deterministic id, e.g. review:T1>", "type": "execute|review|fix", '
    '"title": "...", "needs_side": "claude|codex", "needs_role": "<role>", '
    '"depends_on": ["<task ids>"], "target_task": "<id>", "target_output_agent": "<agent id>", '
    '"priority": 100} ] }\n'
    "Rules: do NOT edit queue/board/state files — the harness commits. Enqueue a "
    "review follow-up after executing; a reviewer enqueues a fix follow-up on REVISE "
    "(reference target_task + target_output_agent). Keep follow-up ids deterministic "
    "so retries don't duplicate them. status=needs_human if a person must decide."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="side", required=True, choices=["claude", "codex"])
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--role", default="")
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--lock-ttl", type=int, default=600)
    ap.add_argument("--lease-sec", type=int, default=1800)
    a = ap.parse_args()

    agent_id = sanitize_agent_id(a.agent_id)
    project = os.path.abspath(a.project)
    queue_p = os.path.join(project, "collaboration_queue.json")
    board_p = os.path.join(project, "collaboration.md")
    lock_p = os.path.join(project, "collaboration.lock")
    sig_p = os.path.join(project, "collaboration_signal.json")
    sess_p = os.path.join(project, ".qagent_%s.session" % agent_id)

    q = at.read_json(queue_p)
    if q is None:
        print("no collaboration_queue.json -> queue mode off"); sys.exit(10)
    ctrl = q.get("control", {}) or {}
    if ctrl.get("status") != "active":
        print("queue control status != active (%s)" % ctrl.get("status")); sys.exit(10)
    if ctrl.get("turns_used", 0) >= ctrl.get("max_turns", 50):
        print("queue turn budget exhausted"); sys.exit(10)

    # ---- CLAIM (short critical section under the lock) ----
    if not at.acquire_lock(lock_p, "claim-%s" % agent_id, ttl=a.lock_ttl, wait=30):
        print("lock busy at claim"); sys.exit(10)
    try:
        q = at.read_json(queue_p) or {"control": {}, "tasks": []}
        ctrl = q.get("control", {}) or {}
        tasks = q.get("tasks", [])
        by_id = {t.get("id"): t for t in tasks}
        now = time.time()
        # Re-check status/budget UNDER the lock (the pre-lock check was stale).
        if ctrl.get("status") != "active":
            print("status != active under lock"); sys.exit(10)
        if ctrl.get("turns_used", 0) >= ctrl.get("max_turns", 50):
            print("budget exhausted under lock"); sys.exit(10)
        task = pick(tasks, by_id, a.side, a.role, now, agent_id)
        if task is None:
            print("no eligible task"); sys.exit(10)
        epoch = int(ctrl.get("next_epoch", 1))
        ctrl["next_epoch"] = epoch + 1
        # RESERVE the budget at CLAIM (not commit) so parallel claims can't oversell.
        ctrl["turns_used"] = ctrl.get("turns_used", 0) + 1
        reclaimed = (task.get("status") == "claimed")
        task.update({"status": "claimed", "claimed_by": agent_id, "claim_epoch": epoch,
                     "lease_expires_at": now + a.lease_sec,
                     "attempts": task.get("attempts", 0) + 1})
        q["control"] = ctrl
        at.atomic_write_json(queue_p, q)
        task_id, seen_epoch = task.get("id"), epoch
        task_snapshot = dict(task)
        at.log_event(project, "task_claimed", run_id=agent_id, task=task_id,
                     epoch=epoch, reclaimed=reclaimed, type=task.get("type"))
    finally:
        at.release_lock(lock_p)

    # ---- RUN the model (NO lock held) ----
    role = a.role or task_snapshot.get("needs_role", "")
    role_tmpl = at.load_role_template(project, role) if role else ""
    ctx_section = ""
    tgt = task_snapshot.get("target_output_agent")
    if tgt:
        ctx_section = at.read_section(board_p, "%s Outbox" % tgt)
    prompt = (CONTRACT % (agent_id, a.side, role or "peer")
              + ("\n\nROLE GUIDE:\n" + role_tmpl if role_tmpl else "")
              + "\n\nTASK:\n" + json.dumps({k: task_snapshot.get(k) for k in
                    ("id", "type", "title", "needs_role", "target_task", "target_output_agent", "depends_on")}, ensure_ascii=False)
              + ("\n\nRELEVANT OUTPUT (%s):\n%s" % (tgt, ctx_section) if ctx_section else "")
              + "\n\nAlso read collaboration.md / the project as needed.")
    write_ok = bool(a.allow_write) and (role in at.WRITE_ROLES or role == "" )
    try:
        runner = at.run_claude if a.side == "claude" else at.run_codex
        draft, cost = runner(prompt, project, write_ok, sess_p)
    except Exception as e:
        _fail_task(at, project, queue_p, sig_p, lock_p, agent_id, task_id, seen_epoch, "agent_error", str(e)[:160])

    new_status = draft.get("status", "done")
    if new_status not in ("done", "failed", "needs_human"):
        new_status = "done"

    # ---- COMMIT (under the lock, with epoch fencing) ----
    if not at.acquire_lock(lock_p, "commit-%s" % agent_id, ttl=a.lock_ttl, wait=60):
        print("lock busy at commit"); sys.exit(20)
    try:
        q = at.read_json(queue_p) or {}
        tasks = q.get("tasks", [])
        by_id = {t.get("id"): t for t in tasks}
        cur = by_id.get(task_id)
        if not cur or cur.get("claimed_by") != agent_id or cur.get("claim_epoch") != seen_epoch:
            at.log_event(project, "claim_lost", run_id=agent_id, task=task_id, seen_epoch=seen_epoch,
                         now_owner=cur.get("claimed_by") if cur else None,
                         now_epoch=cur.get("claim_epoch") if cur else None)
            sys.exit(10)   # fenced: a reclaim happened; drop our result
        # write our result to our OWN outbox (never another agent's)
        at._append_under_header(board_p, "## %s Outbox" % agent_id,
                                "[task %s / %s] %s" % (task_id, new_status, draft.get("result_markdown", "")))
        cur.update({"status": new_status, "claimed_by": None,
                    "result_summary": (draft.get("summary", "") or "")[:200]})
        # idempotent + validated enqueue of follow-ups (reject "poison" tasks whose
        # depends_on names a non-existent id or themselves — they'd never be claimable)
        existing_ids = set(by_id.keys())
        added, rejected = [], []
        for fu in (draft.get("followups") or []):
            fid = fu.get("id")
            deps = fu.get("depends_on") or []
            if not fid or fid in existing_ids or fu.get("type") not in TASK_TYPES:
                continue
            if fid in deps or any((d not in existing_ids and d != fid) for d in deps):
                rejected.append(fid); continue
            existing_ids.add(fid)
            tasks.append({"id": fid, "type": fu.get("type"), "title": fu.get("title", ""),
                          "needs_side": fu.get("needs_side"), "needs_role": fu.get("needs_role"),
                          "status": "open", "depends_on": deps,
                          "claimed_by": None, "claim_epoch": 0, "lease_expires_at": 0,
                          "created_at": at.now_str(), "priority": fu.get("priority", 100),
                          "attempts": 0, "idempotency_key": fid, "parent_task": task_id,
                          "target_task": fu.get("target_task"),
                          "target_output_agent": fu.get("target_output_agent"),
                          "preferred_agent": fu.get("preferred_agent")})
            added.append(fid)
        if rejected:
            at.log_event(project, "poison_followups_rejected", run_id=agent_id, task=task_id, rejected=rejected)
        ctrl = q.get("control", {}) or {}     # turns_used was already reserved at CLAIM
        q["tasks"] = tasks
        at.atomic_write_json(queue_p, q)
        # monotonic signal UNDER the lock (read current + 1); claim did NOT bump -> no stampede.
        prev = int((at.read_json(sig_p, {}) or {}).get("update_id", 0))
        at.atomic_write_json(sig_p, {"update_id": prev + 1, "updated_at": at.now_str(),
                                     "updated_by": agent_id, "changed_section": "%s Outbox" % agent_id,
                                     "summary": (draft.get("summary", "") or "")[:200]})
    except Exception as e:   # SystemExit (the fencing exit 10) is NOT caught here
        at.log_event(project, "queue_commit_error", run_id=agent_id, task=task_id, detail=str(e)[:200])
        sys.exit(30)
    finally:
        at.release_lock(lock_p)

    at.log_event(project, "task_committed", run_id=agent_id, task=task_id, status=new_status,
                 enqueued=added, cost=cost, turns_used=ctrl.get("turns_used"))
    if new_status == "needs_human":
        at.notify("Task %s needs a human (agent %s)." % (task_id, agent_id))
    sys.exit(0)


def _fail_task(at, project, queue_p, sig_p, lock_p, agent_id, task_id, seen_epoch, reason, detail):
    """Mark a claimed task failed under the lock (epoch-fenced) + bump the signal so
    a reviewer/human/other watcher wakes, then exit 20."""
    if at.acquire_lock(lock_p, "fail-%s" % agent_id, ttl=0, wait=10):
        try:
            q = at.read_json(queue_p) or {}
            changed = False
            for t in q.get("tasks", []):
                if t.get("id") == task_id and t.get("claimed_by") == agent_id and t.get("claim_epoch") == seen_epoch:
                    t.update({"status": "failed", "claimed_by": None, "result_summary": "%s: %s" % (reason, detail)})
                    changed = True
            at.atomic_write_json(queue_p, q)
            if changed:
                prev = int((at.read_json(sig_p, {}) or {}).get("update_id", 0))
                at.atomic_write_json(sig_p, {"update_id": prev + 1, "updated_at": at.now_str(),
                                             "updated_by": agent_id, "changed_section": "queue",
                                             "summary": "task %s failed: %s" % (task_id, reason)})
        finally:
            at.release_lock(lock_p)
    at.log_event(project, "task_failed", run_id=agent_id, task=task_id, reason=reason, detail=detail)
    sys.exit(20)


if __name__ == "__main__":
    main()
