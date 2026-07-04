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
    for msg in (context or [])[-3:]:   # minimal context — reference ONLY, never a task source
        if not isinstance(msg, dict):
            continue
        recent.append({
            "speaker": str(msg.get("speaker", "")),
            "text": str(msg.get("text", "")),
        })

    return """You are a STRICT gatekeeper classifying ONE latest human message in a
software-development group chat. A wrong "actionable" makes the team AUTO-RUN code, so
be conservative: when in doubt, DO NOT say actionable.

Classify the LATEST message as exactly one of:
- "actionable": the LATEST message ITSELF contains a concrete, self-contained instruction
  to do/implement/fix/run/change something now (e.g. "add a Y function to file X",
  "rename Z to W", "run the tests"). The instruction must be explicit IN THE LATEST MESSAGE.
- "record_requirement": the LATEST message explicitly asks to remember/track a future
  requirement without doing it now (e.g. "note that we should later support X").
- "opinion": greetings, acknowledgements, vague affirmations ("好", "ok", "yes", "hello",
  "在吗", "谢谢", "嗯"), questions, discussion, or anything with no concrete instruction.
- "ambiguous": the human clearly wants SOMETHING done but the latest message is too
  underspecified to extract a task — give a short clarifying question.

HARD RULES (violating these causes wrong auto-execution):
- Extract the task ONLY from the words of the LATEST message. NEVER invent, infer, or
  carry a task over from the prior context. Context is ONLY to resolve pronouns/references
  ("it", "that file") — it is NEVER the source of the task.
- A bare affirmation/greeting/question with no concrete instruction of its own is
  "opinion", NOT actionable — even if the prior context discussed work to do. A vague
  "好"/"ok"/"proceed" is NOT a directive on its own.
- Prefer "opinion"/"ambiguous" over "actionable" whenever unsure.

Reply with ONLY one JSON object, no prose/Markdown/fences:
{"kind":"actionable|record_requirement|opinion|ambiguous","task":"...","question":"..."}

Prior context (reference ONLY — NEVER a source of the task), up to last 3 messages:
%s

Latest human message (classify THIS; extract any task ONLY from here):
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
