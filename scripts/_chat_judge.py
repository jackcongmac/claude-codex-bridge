"""LLM-backed classifier for chat-triggered execution greenlights."""
import json
import os
import subprocess


_DEFAULT_QUESTION = "你是要现在就开始做吗?"
_VALID_KINDS = {"actionable", "record_requirement", "opinion", "ambiguous"}


def is_actionable(verdict):
    return verdict.get('kind') == 'actionable'


def is_ambiguous(verdict):
    return verdict.get('kind') == 'ambiguous'


def _safe_ambiguous():
    return {"kind": "ambiguous", "question": _DEFAULT_QUESTION}


def _build_prompt(text, context):
    recent = []
    for msg in (context or [])[-8:]:
        if not isinstance(msg, dict):
            continue
        recent.append({
            "speaker": str(msg.get("speaker", "")),
            "text": str(msg.get("text", "")),
        })

    return """You classify ONE latest human message in a software-development group chat.

Decide whether the human is directing the team to DO, implement, fix, run, change,
or otherwise act now ("actionable"), asking the team to remember/track a future
requirement without doing it now ("record_requirement"), merely discussing or
giving an opinion ("opinion"), or unclear enough that the team should ask a
follow-up ("ambiguous").

If actionable, extract a concrete task the team can execute. If record_requirement,
extract the durable task/requirement to remember but not run. If ambiguous, provide
a short clarifying question. Reply with ONLY one JSON object and no prose, no
Markdown, no code fences:
{"kind":"actionable|record_requirement|opinion|ambiguous","task":"...","question":"..."}

Prior context, oldest to newest, up to the last 8 messages:
%s

Latest human message:
%s
""" % (
        json.dumps(recent, ensure_ascii=False, indent=2),
        json.dumps(str(text or ""), ensure_ascii=False),
    )


def _parse_json_object(raw):
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def classify(text, context, call_llm, image_path=None):
    """text: the human's latest message. context: list of prior msg dicts ({speaker,text}).
    call_llm(prompt:str,image_path=None)->str: injected boundary that returns the model's raw text.
    Returns {"kind": "actionable"|"record_requirement"|"opinion"|"ambiguous",
    "task": str?, "question": str?}.
    Robust: if call_llm output can't be parsed into a valid kind, return
    {"kind":"ambiguous","question":"你是要现在就开始做吗?"} (fail safe — never guess 'actionable').
    """
    prompt = _build_prompt(text, context)
    if image_path:
        prompt += (
            "\n\n[The human attached an image at: %s — use your Read tool to view it; "
            "it may BE the instruction.]" % image_path)
    try:
        parsed = _parse_json_object(call_llm(prompt, image_path))
    except Exception:
        return _safe_ambiguous()
    if not parsed:
        return _safe_ambiguous()

    kind = parsed.get("kind")
    if kind not in _VALID_KINDS:
        return _safe_ambiguous()
    if kind == "actionable":
        task = (parsed.get("task") or text or "").strip()
        return {"kind": "actionable", "task": task}
    if kind == "record_requirement":
        task = (parsed.get("task") or text or "").strip()
        return {"kind": "record_requirement", "task": task}
    if kind == "ambiguous":
        question = (parsed.get("question") or _DEFAULT_QUESTION).strip()
        return {"kind": "ambiguous", "question": question or _DEFAULT_QUESTION}
    return {"kind": "opinion"}


def default_call_llm(prompt, image_path=None):
    """Spawn a headless model and return its stdout. The judge is a cheap JSON classifier —
    pin it to Haiku (not the inherited Opus default) so classification costs ~10-15x less.
    Override with BRIDGE_CHAT_JUDGE_MODEL."""
    claude = os.environ.get("CLAUDE_BIN") or "claude"
    model = os.environ.get("BRIDGE_CHAT_JUDGE_MODEL") or "claude-haiku-4-5-20251001"
    if image_path:
        cmd = [claude, "-p", prompt, "--model", model, "--output-format", "json",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--permission-mode", "default",
               "--allowedTools", "Read", "Grep", "Glob"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return json.loads(r.stdout).get("result") or ""
    return subprocess.run(
        [claude, "-p", prompt, "--model", model],
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout


def default_judge(text, context, image_path=None):
    return classify(text, context, default_call_llm, image_path=image_path)
