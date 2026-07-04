#!/usr/bin/env python3
"""bridge-chat-web.py — a tiny local web window for the group chat (## Chat).

"Open group chat" → a browser tab pops up (WeChat-ish bubbles, yours on the right),
you type, agents' messages appear, and the ✕ in the top-right shuts it down. Posts go
through the same locked board write as bridge-chat; armed Claude/Codex see them via
board-wait. Pure stdlib — no install, no deps.

CLI: bridge-chat-web.py [--self <Me>] [--project DIR] [--port N] [--no-open]
"""
import argparse
import atexit
import base64
import errno
import html
import http.server
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import (  # noqa: E402
    collab_paths, find_project_root, read_section, read_json, atomic_write,
    atomic_write_json, now_str, acquire_lock, release_lock,
)
from _post import post as _board_post  # noqa: E402
from _liveness import verdict as liveness_verdict  # noqa: E402
import _sig  # noqa: E402
import _chat_roles  # noqa: E402
import _chat_server  # noqa: E402

_ENTRY = re.compile(r'### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[^\n]*)\n+(.*)', re.S)
_SPEAKER = re.compile(r'\*\*(.+?):\*\*\s?(.*)', re.S)
_BOARD_SECTION_LINE = re.compile(r'(?m)^(##)(?=\s|$)')
_CHAT_ID = re.compile(
    r'^<!--\s*chat-id:([A-Za-z0-9_.:-]+)((?:\s+[a-z_]+:[^\s>]+)*)\s*-->\s*', re.S)
_WORKER_SPEAKER = re.compile(r'^(?P<base>.+?) \(worker (?P<nonce>[0-9a-f]{4,6})\)$')

# Client-supplied send metadata. These are UNTRUSTED (they come from the browser), so we
# whitelist the trigger and format-check the timestamp before persisting or trusting them.
_VALID_TRIGGERS = {"click", "enter", "shortcut", "paste", "drop"}
_SENT_AT_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$')
# An image ref is a server-generated "<hex>.<ext>" — the SAME safe shape _chat_uploads
# enforces, re-checked here so a hand-edited board can't smuggle a path into an <img src>.
_IMG_RE = re.compile(r'^[0-9a-f]{8,}\.(?:png|jpg|gif|webp)$')
_CHAT_PEERS = _chat_roles.DEFAULT_AGENTS
_LIVE_VERDICTS = {"REACTIVE", "PRESENT"}


def _clean_sent_at(value):
    return value if (isinstance(value, str) and _SENT_AT_RE.match(value)) else None


def _clean_trigger(value):
    return value if value in _VALID_TRIGGERS else None


def _clean_img(value):
    return value if (isinstance(value, str) and _IMG_RE.match(value)) else None


def _parse_chat_attrs(attrs):
    """Parse the ' key:value key:value' tail of a chat-id comment into a dict."""
    out = {}
    for kv in (attrs or "").split():
        key, sep, val = kv.partition(":")
        if sep and val:
            out[key] = val
    return out


def sanitize_chat_text(text):
    """Escape board section headers inside a chat body before writing Markdown."""
    return _BOARD_SECTION_LINE.sub(r'\\##', text or "")


def worker_speaker_label(speaker, nonce=None):
    return "%s (worker %s)" % (speaker, nonce or secrets.token_hex(3))


def parse_worker_speaker(speaker):
    m = _WORKER_SPEAKER.match(speaker or "")
    return (m.group("base"), m.group("nonce")) if m else None


def format_chat_message(speaker, text, msg_id=None, sent_at=None, send_trigger=None, img=None, sig=None):
    msg_id = msg_id or secrets.token_hex(8)
    attrs = ""
    sent_at = _clean_sent_at(sent_at)
    send_trigger = _clean_trigger(send_trigger)
    img = _clean_img(img)
    if sent_at:
        attrs += " sent_at:%s" % sent_at
    if send_trigger:
        attrs += " trigger:%s" % send_trigger
    if img:
        attrs += " img:%s" % img
    if sig:
        attrs += " sig:%s" % sig
    return "<!-- chat-id:%s%s -->\n**%s:** %s" % (
        msg_id, attrs, speaker, sanitize_chat_text(text))


def parse_chat(section):
    """Parse the ## Chat section into [{ts, speaker, text}], OLDEST-first. Splits only
    on the full-timestamp entry header so a '### …' line inside a message is safe."""
    if not section:
        return []
    msgs = []
    for part in re.split(r'\n(?=### \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} )', section):
        m = _ENTRY.match(part.strip())
        if not m:
            continue
        body = m.group(2).strip()
        im = _CHAT_ID.match(body)
        msg_id = im.group(1) if im else None
        attrs = _parse_chat_attrs(im.group(2)) if im else {}
        if im:
            body = body[im.end():].strip()
        sm = _SPEAKER.match(body)
        speaker, text = (sm.group(1).strip(), sm.group(2).strip()) if sm else ("?", body)
        msg = {"ts": m.group(1).strip(), "speaker": speaker, "text": text}
        worker = parse_worker_speaker(speaker)
        if worker:
            msg["speaker_base"], msg["worker_nonce"] = worker
        if msg_id:
            msg["_id"] = msg_id
        sent_at = _clean_sent_at(attrs.get("sent_at"))
        send_trigger = _clean_trigger(attrs.get("trigger"))
        img = _clean_img(attrs.get("img"))
        sig = attrs.get("sig")
        if sent_at:
            msg["sent_at"] = sent_at
        if send_trigger:
            msg["send_trigger"] = send_trigger
        if img:
            msg["img"] = img
        if sig:
            msg["sig"] = sig
        msgs.append(msg)
    msgs.reverse()   # board stores newest-first
    counts = {}
    for msg in msgs:
        key = ("id", msg["_id"]) if msg.get("_id") else (msg["ts"], msg["speaker"], msg["text"])
        counts[key] = counts.get(key, 0) + 1
    seen = {}
    for msg in msgs:
        key = ("id", msg["_id"]) if msg.get("_id") else (msg["ts"], msg["speaker"], msg["text"])
        if counts[key] > 1:
            msg["_dup"] = seen.get(key, 0)
            seen[key] = msg["_dup"] + 1
    return msgs


def _chat_sessions_path(paths):
    return os.path.join(paths["dir"], "chat_sessions.json")


def _speaker_base(speaker):
    worker = parse_worker_speaker(speaker or "")
    return worker[0] if worker else (speaker or "")


def _chat_peers(project):
    return _chat_roles.chat_peers(project) if project else _CHAT_PEERS


def _configured_human_name(project):
    if not project:
        return None
    try:
        return _chat_roles.human_name(project)
    except Exception:
        return None


def _compact_line(text, limit=80):
    compact = " ".join((text or "").split())
    return compact[:limit]


def _is_salient(msg, project=None):
    text = msg.get("text") or ""
    if not text.strip():
        return False
    speaker = msg.get("speaker") or ""
    base = msg.get("speaker_base") or _speaker_base(speaker)
    human = _configured_human_name(project)
    if (human and speaker == human) or base not in _chat_peers(project):
        return True
    lowered = text.lower()
    return text.lstrip().startswith("✅") or "完成" in text or "done" in lowered


def summarize_chat_session(msgs, project=None):
    speakers = {}
    for msg in msgs:
        speaker = msg.get("speaker") or "?"
        speakers[speaker] = speakers.get(speaker, 0) + 1
    highlights = []
    for msg in msgs:
        if not _is_salient(msg, project=project):
            continue
        highlights.append("%s: %s" % (
            msg.get("speaker") or "?",
            _compact_line(msg.get("text") or "")))
    span = {"from": msgs[0].get("ts"), "to": msgs[-1].get("ts")} if msgs else {
        "from": None, "to": None}
    return {
        "ended_at": now_str(),
        "count": len(msgs),
        "span": span,
        "speakers": speakers,
        "highlights": highlights[-6:],
    }


def _append_session_summary(paths, project, msgs, archive_path):
    entry = summarize_chat_session(msgs, project=project)
    entry["archive"] = os.path.basename(archive_path)
    session_path = _chat_sessions_path(paths)
    data = read_json(session_path, default={"sessions": []}) or {"sessions": []}
    sessions = data.get("sessions", [])
    if not isinstance(sessions, list):
        sessions = []
    sessions.append(entry)
    atomic_write_json(session_path, {"sessions": sessions[-50:]})


def session_summaries(project):
    p = collab_paths(find_project_root(project))
    try:
        data = read_json(_chat_sessions_path(p), default={"sessions": []}) or {"sessions": []}
    except RuntimeError:
        return []
    sessions = data.get("sessions", [])
    if not isinstance(sessions, list):
        return []
    return list(reversed(sessions))


def mentions(text, peers=None):
    """Parse @-mentions → the set of agents explicitly named in the text:
    @All / @所有人 → both; @Claude (also '@Claude Code') / @Codex → that one; none →
    empty set. (This is just the parser; who is compelled to reply — including a human's
    no-@ message broadcasting to both — is decided by _chat_respond._targets.)"""
    # Real @-mentions only: the @ must NOT be inside a word/email (lookbehind), the name
    # must end at a boundary (\b / not a CJK char) — so "a@codex.io", "@clauded",
    # "@codexical", "@所有人类" are NOT mentions.
    t = text or ""
    peers = tuple(peers or _CHAT_PEERS)
    if (re.search(r'(?<![\w@])@all\b', t, re.I)
            or re.search(r'(?<![\w@])@所有人(?![一-鿿])', t)):
        return set(peers)
    who = set()
    for name in peers:
        pattern = r'claude(?:\s+code)?' if name.lower() == "claude" else re.escape(name)
        if re.search(r'(?<![\w@])@%s(?=$|[^\w一-鿿])' % pattern, t, re.I):
            who.add(name)
    return who


def archive_and_clear_chat(project):
    """On close: archive the ## Chat thread to .collab/chat_archive/chat-<time>.md and
    clear the live thread (under the lock), so past sessions aren't lost and the next
    one starts fresh. Returns the archive path, or None if the chat was empty."""
    p = collab_paths(find_project_root(project))
    if not acquire_lock(p["lock"], "chat-archive-%d" % os.getpid(), ttl=30, wait=10):
        return None
    try:
        try:
            with open(p["board"]) as f:
                text = f.read()
        except OSError:
            text = ""
        # extract the EXACT "## Chat" section (anchored header — not "## Chat Archive")
        hdr = re.search(r'(?m)^## Chat[ \t]*$', text)
        if not hdr:
            return None
        i = hdr.start()
        j = text.find("\n## ", i + len("## Chat"))
        section = text[i:] if j == -1 else text[i:j]
        msgs = parse_chat(section)
        if not msgs:
            return None

        archive_dir = os.path.join(p["dir"], "chat_archive")
        os.makedirs(archive_dir, exist_ok=True)
        stamp = now_str()[:19].replace(":", "").replace(" ", "-")
        path = os.path.join(archive_dir, "chat-%s.md" % stamp)
        body = ["# Chat — archived %s\n" % now_str()]
        for m in msgs:
            body.append("**%s** (%s):\n%s\n" % (m["speaker"], m["ts"], m["text"]))
        atomic_write(path, "\n".join(body))
        try:
            _append_session_summary(p, project, msgs, path)
        except Exception:
            pass

        # remove the ## Chat section from the board (keep all other sections)
        start = text.rfind("\n", 0, i)
        start = i if start == -1 else start
        text = text[:start].rstrip() + ("\n" + text[j + 1:] if j != -1 else "\n")
        atomic_write(p["board"], text)

        sig = read_json(p["signal"], default={}) or {}
        atomic_write_json(p["signal"], {
            "update_id": int(sig.get("update_id", 0)) + 1, "updated_at": now_str(),
            "updated_by": "chat", "changed_section": "Chat",
            "summary": "chat session archived (%d messages) -> %s" % (len(msgs), path)})
        return path
    finally:
        release_lock(p["lock"], "chat-archive-%d" % os.getpid())


def _typing_active(info, now=None):
    if not isinstance(info, dict) or info.get("status") != "thinking":
        return False
    stale = float(os.environ.get("BRIDGE_CHAT_TYPING_STALE", "300"))
    try:
        since = time.mktime(time.strptime((info.get("since") or "")[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return False
    return ((now or time.time()) - since) <= stale


def chat_status(project, now=None):
    p = collab_paths(find_project_root(project))
    peers = _chat_peers(project)
    try:
        state = read_json(p["chat_typing"], default={"agents": {}}) or {"agents": {}}
    except RuntimeError:
        state = {"agents": {}}
    if not isinstance(state, dict):
        state = {"agents": {}}
    agents = state.get("agents", {}) or {}
    if not isinstance(agents, dict):
        agents = {}
    typing = sorted(name for name, info in agents.items() if _typing_active(info, now=now))
    responders = [{"name": name, "alive": responder_owner_alive(project, name)}
                  for name in peers]
    presence_rows = participant_liveness(project, now=now)
    pres_alive = {row["name"]: bool(row.get("alive")) for row in presence_rows}
    resp_alive = {row["name"]: bool(row.get("alive")) for row in responders}
    online = []
    for name in peers:
        pane = pres_alive.get(name, False)
        responder = resp_alive.get(name, False)
        if pane:
            mode, writable = "pane", True
        elif responder:
            mode, writable = "responder", False
        else:
            mode, writable = "offline", False
        online.append({"name": name, "online": pane or responder,
                       "mode": mode, "writable": writable})
    return {"typing": typing, "presence": presence_rows,
            "responders": responders, "online": online}


def participant_liveness(project, now=None):
    project = find_project_root(project)
    peers = _chat_peers(project)
    p = collab_paths(project)
    try:
        reg = read_json(p["participants"], default={"participants": []}) or {"participants": []}
    except RuntimeError:
        reg = {"participants": []}
    stale_after = float(os.environ.get("BRIDGE_PRESENCE_STALE", 1800))
    observed_at = time.time() if now is None else now
    rows = {}
    for part in reg.get("participants", []) or []:
        if not isinstance(part, dict) or not part.get("name"):
            continue
        row = liveness_verdict(part, p["dir"], observed_at, stale_after, stale_after)
        rows[row["name"]] = row
    if not rows:
        return []
    out = []
    for name in peers:
        row = rows.get(name)
        if not row:
            out.append({"name": name, "alive": False, "verdict": "OFFLINE",
                        "reactive": False, "armed": False, "age": None})
            continue
        out.append({"name": name, "alive": row["verdict"] in _LIVE_VERDICTS,
                    "verdict": row["verdict"], "reactive": row["reactive"],
                    "armed": row["armed"], "age": row["age"]})
    return out


_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Group chat — __PROJECT__</title><style>
body{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#ededed;height:100vh;display:flex;flex-direction:column}
header{background:#393a3f;color:#eee;padding:10px 14px;display:flex;align-items:center}
#group{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#viewer{color:#9a9a9a;font-size:12px;margin-left:8px;overflow:hidden;text-overflow:ellipsis;max-width:32%;white-space:nowrap;flex-shrink:0}
#close{cursor:pointer;font-size:20px;color:#bbb;margin-left:auto}#close:hover{color:#fff}
#past{display:none;padding:8px 12px;background:#f4f4f4;border-bottom:1px solid #ddd;color:#777;font-size:12px}
#past summary{cursor:pointer;color:#666}#pastlist{margin-top:6px}
.sess{padding:6px 0;border-top:1px solid #ddd}.sess:first-child{border-top:0}
.sess .t{color:#555;font-variant-numeric:tabular-nums}.hl{color:#888;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#log{flex:1;overflow-y:auto;padding:12px}
#typing{min-height:20px;padding:0 14px 8px;color:#777;font-size:13px}
#presence{min-height:18px;padding:0 14px 8px;color:#666;font-size:12px}
#sendstat{min-height:18px;padding:0 14px 6px;background:#f7f7f7;color:#777;font-size:12px;text-align:right}
#sendstat.fail{color:#b00020;cursor:pointer}
.row{display:flex;margin:6px 0}.row.me{justify-content:flex-end}
.who{font-size:11px;color:#888;margin:0 8px 2px}
.bubble{max-width:70%;padding:8px 11px;border-radius:8px;background:#fff;white-space:pre-wrap;word-break:break-word}
.bubble ul,.bubble ol{margin:4px 0;padding-left:20px}.bubble li{margin:2px 0}
.bubble code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:rgba(0,0,0,.08);border-radius:4px;padding:1px 4px}
.bubble strong{font-weight:700}
.me .bubble{background:#95ec69}
.time{font-size:10px;color:#b2b2b2;margin:2px 8px 0}.me .time{text-align:right}
.cimg{max-width:220px;max-height:220px;border-radius:6px;display:block;margin-top:4px;cursor:zoom-in}
body.drag:after{content:"Drop to send image";position:fixed;inset:0;background:rgba(7,193,96,.12);border:3px dashed #07c160;display:flex;align-items:center;justify-content:center;font-size:20px;color:#07820;pointer-events:none;z-index:99}
footer{display:flex;padding:8px;background:#f7f7f7;border-top:1px solid #ddd;position:relative}
#at{position:absolute;bottom:100%;left:8px;margin-bottom:4px;background:#fff;border:1px solid #ccc;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;z-index:10;min-width:140px}
#at div{padding:9px 14px;cursor:pointer}#at div:hover{background:#eef}#at div.sel{background:#eef}
#msg{flex:1;padding:9px;border:1px solid #ccc;border-radius:6px;font-size:15px}
button{margin-left:8px;padding:0 16px;border:0;border-radius:6px;background:#07c160;color:#fff;font-size:15px;cursor:pointer}
button:disabled{opacity:.55;cursor:default}
</style></head><body>
<header><b id=group title="__PROJECTPATH__">Group chat · __PROJECT__</b><span id=viewer title="Signed in as __SELF__">__SELF__</span><span id=close title=Close>✕</span></header>
<details id=past><summary>过往会话</summary><div id=pastlist></div></details>
<div id=log></div>
<div id=typing></div>
<div id=presence></div>
<div id=sendstat></div>
<footer><div id=at></div><input id=msg placeholder="Type a message…  (@ to mention)" autofocus><button id=send>Send</button></footer>
<script>
const SELF=__SELFJSON__,TOKEN=__TOKEN__;
const msg=document.getElementById('msg'),AT=document.getElementById('at');
const sendBtn=document.getElementById('send'),sendstat=document.getElementById('sendstat');
const PEOPLE=__PEOPLEJSON__;
let lastRender='',atSel=0,sendInFlight=false,retryText='',sendstatTimer=null;
function setSendStatus(text,fail,clickable){
  if(sendstatTimer){clearTimeout(sendstatTimer);sendstatTimer=null;}
  sendstat.textContent=text||'';
  sendstat.classList.toggle('fail',!!fail);
  if(clickable){sendstat.setAttribute('role','button');sendstat.tabIndex=0;}
  else{sendstat.removeAttribute('role');sendstat.removeAttribute('tabindex');}
}
function renderMd(text){
  let s=String(text||'')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
  s=s
    .replace(/`([^`\\n]+?)`/g,'<code>$1</code>')
    .replace(/\\*\\*([^*\\n]+?)\\*\\*/g,'<strong>$1</strong>');
  const out=[];
  let list=null;
  const closeList=()=>{if(list){out.push(`</${list}>`);list=null;}};
  s.split(/\\n/).forEach((line,i,lines)=>{
    const bullet=line.match(/^\\s*(?:- |• )(.*)$/);
    const numbered=line.match(/^\\s*\\d+\\. (.*)$/);
    if(bullet||numbered){
      const kind=bullet?'ul':'ol';
      if(list!==kind){closeList();out.push(`<${kind}>`);list=kind;}
      out.push(`<li>${bullet?bullet[1]:numbered[1]}</li>`);
      return;
    }
    closeList();
    out.push(line);
    if(i<lines.length-1)out.push('<br>');
  });
  closeList();
  return out.join('');
}
function styleAt(){AT.querySelectorAll('div[data-i]').forEach((d,i)=>d.classList.toggle('sel',i===atSel));}
function showAt(){const m=msg.value.match(/@(\\S*)$/);
  if(!m){AT.style.display='none';return;}
  atSel=0;
  AT.innerHTML=PEOPLE.map((p,i)=>`<div data-i=${i} class="${i===atSel?'sel':''}">@${p[0]}</div>`).join('');
  AT.style.display='block';}
AT.addEventListener('mousedown',e=>{const d=e.target.closest('div[data-i]');if(!d)return;e.preventDefault();
  msg.value=msg.value.replace(/@\\S*$/,PEOPLE[+d.dataset.i][1]);AT.style.display='none';msg.focus();});
msg.addEventListener('input',showAt);
msg.addEventListener('blur',()=>setTimeout(()=>{AT.style.display='none';},150));
function fmtTime(m){const s=m.sent_at||m.ts||'';const t=s.match(/\\d{2}:\\d{2}/);return t?t[0]:'';}
function presenceInfo(row){
  const name=String((row&&row.name)||'');
  const online=!!(row&&(row.online!==undefined?row.online:row.alive));
  const mode=row&&row.mode?row.mode:(online?'pane':'offline');
  if(mode==='pane')return {label:`${name} ✍️ writable`,title:'Writable pane: can reply and make code changes.'};
  if(mode==='responder')return {label:`${name} 💬 read-only`,title:'Read-only responder: can reply in chat, but cannot edit files or run commands.'};
  return {label:`${name} ⚪ offline`,title:'Offline: no writable pane or read-only responder is alive.'};
}
function renderPresence(rows){
  const presence=document.getElementById('presence');
  const nodes=[];
  rows.forEach((row,i)=>{
    if(i)nodes.push(document.createTextNode(' · '));
    const info=presenceInfo(row),span=document.createElement('span');
    span.textContent=info.label;
    span.title=info.title;
    nodes.push(span);
  });
  presence.replaceChildren(...nodes);
}
async function load(){
  let ms; try{ms=await (await fetch('/messages')).json();}catch(e){return;}
  let st={typing:[],responders:[]}; try{st=await (await fetch('/status')).json();}catch(e){}
  const log=document.getElementById('log');
  const sig=JSON.stringify(ms);
  if(sig!==lastRender){
    lastRender=sig;
    const atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<60;
    log.innerHTML=ms.map(m=>`<div class="row ${m.speaker===SELF?'me':''}"><div><div class=who></div><div class=bubble></div><div class=time></div></div></div>`).join('');
    const whos=log.querySelectorAll('.who'),bubs=log.querySelectorAll('.bubble'),tms=log.querySelectorAll('.time');
    ms.forEach((m,i)=>{whos[i].textContent=m.speaker;bubs[i].innerHTML=renderMd(m.text);tms[i].textContent=fmtTime(m);
      if(m.img){const im=document.createElement('img');im.className='cimg';im.src='/uploads/'+m.img;im.onclick=()=>window.open(im.src);bubs[i].appendChild(im);}});
    if(atBottom)log.scrollTop=log.scrollHeight;
  }
  document.getElementById('typing').textContent=st.typing.length?`${st.typing.join(', ')} thinking…`:'';
  const online=st.online||st.presence||st.responders||[];
  renderPresence(online);
}
async function loadSessions(){
  const past=document.getElementById('past'),list=document.getElementById('pastlist');
  let ss=[];try{ss=await (await fetch('/sessions')).json();}catch(e){ss=[];}
  if(!ss.length){past.style.display='none';return;}
  past.style.display='block';list.innerHTML='';
  ss.forEach(s=>{const row=document.createElement('div');row.className='sess';
    const head=document.createElement('div'),t=document.createElement('span');t.className='t';t.textContent=s.ended_at||'';
    head.appendChild(t);head.appendChild(document.createTextNode(` · ${s.count||0} msgs`));row.appendChild(head);
    (s.highlights||[]).forEach(h=>{const d=document.createElement('div');d.className='hl';d.textContent=h;row.appendChild(d);});
    list.appendChild(row);});
}
function nowStamp(){const d=new Date(),p=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;}
async function send(trigger){const t=document.getElementById('msg');const v=t.value;if(!v.trim())return;
  if(sendInFlight)return;
  sendInFlight=true;sendBtn.disabled=true;retryText='';setSendStatus('发送中…',false,false);
  t.value='';
  const body=JSON.stringify({text:v,sent_at:nowStamp(),send_trigger:trigger||'click'});
  try{const r=await fetch('/send',{method:'POST',headers:{'Content-Type':'application/json','X-Token':TOKEN},body});
    const j=await r.json(); if(!j.ok){throw {status:r.status,error:j.error};}
    setSendStatus('已送达',false,false);sendstatTimer=setTimeout(()=>setSendStatus('',false,false),1200);
  }catch(e){t.value=v;retryText=v;
    const expired=e&&e.status===403&&String(e.error||'').includes('session expired');
    setSendStatus(expired?'会话过期,请刷新页面':'发送失败 — 点击重发',true,!expired);}
  finally{sendInFlight=false;sendBtn.disabled=false;}
  load();}
sendBtn.onclick=()=>send('click');
sendstat.addEventListener('click',()=>{if(retryText&&sendstat.classList.contains('fail')){msg.value=retryText;send('click');}});
sendstat.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();sendstat.click();}});
let lastEnterAt=0;
document.getElementById('msg').addEventListener('keydown',e=>{
  if(AT.style.display==='block'){
    if(e.key==='ArrowDown'){atSel=(atSel+1)%PEOPLE.length;styleAt();e.preventDefault();return;}
    if(e.key==='ArrowUp'){atSel=(atSel-1+PEOPLE.length)%PEOPLE.length;styleAt();e.preventDefault();return;}
    if(e.key==='Enter'||e.key==='Tab'){msg.value=msg.value.replace(/@\\S*$/,PEOPLE[atSel][1]);AT.style.display='none';msg.focus();e.preventDefault();return;}
    if(e.key==='Escape'){AT.style.display='none';e.preventDefault();return;}
    return;
  }
  if(e.key!=='Enter'||e.shiftKey)return;
  const composing=e.isComposing||e.keyCode===229;
  const now=Date.now();
  const dbl=(now-lastEnterAt)<=500;
  lastEnterAt=now;
  if(dbl){e.preventDefault();lastEnterAt=0;send((e.metaKey||e.ctrlKey)?'shortcut':'enter');return;}
  if(!composing){e.preventDefault();send((e.metaKey||e.ctrlKey)?'shortcut':'enter');}
});
async function uploadImage(file){
  try{const r=await fetch('/upload',{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream','X-Token':TOKEN},body:file});
    const j=await r.json();return j.ok?j.id:null;}catch(e){return null;}}
async function sendImage(file,trigger){const id=await uploadImage(file);
  if(!id){alert('Image upload failed (png/jpg/gif/webp, ≤10MB only)');return;}
  try{await fetch('/send',{method:'POST',headers:{'Content-Type':'application/json','X-Token':TOKEN},
    body:JSON.stringify({text:'',img:id,sent_at:nowStamp(),send_trigger:trigger||'drop'})});}catch(e){}
  load();}
function imageFilesFrom(list){return [...(list||[])].filter(f=>f&&(f.type||'').startsWith('image/'));}
document.addEventListener('dragover',e=>{e.preventDefault();document.body.classList.add('drag');});
document.addEventListener('dragleave',e=>{if(e.relatedTarget===null)document.body.classList.remove('drag');});
document.addEventListener('drop',e=>{e.preventDefault();document.body.classList.remove('drag');
  imageFilesFrom(e.dataTransfer&&e.dataTransfer.files).forEach(f=>sendImage(f,'drop'));});
document.addEventListener('paste',e=>{const items=[...((e.clipboardData&&e.clipboardData.items)||[])];
  const files=items.filter(it=>(it.type||'').startsWith('image/')).map(it=>it.getAsFile());
  imageFilesFrom(files).forEach(f=>sendImage(f,'paste'));});
document.getElementById('close').onclick=async()=>{let p=null;
  try{const r=await fetch('/quit',{method:'POST',headers:{'X-Token':TOKEN}});const j=await r.json();
    if(!j.ok){throw new Error(j.error||'close failed');}p=j.archived;}catch(e){alert('Close failed, please retry');return;}
  document.body.innerHTML='<p style="padding:24px;font-family:sans-serif">Group chat closed.<span id=ar></span><br>You can close this tab.</p>';
  if(p)document.getElementById('ar').textContent=' This session was archived to: '+p;};
load();loadSessions();setInterval(load,1500);
</script></body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _board(self):
        return collab_paths(self.server.project)["board"]

    def do_GET(self):
        if self.path == "/":
            # Escape "<" when embedding JSON in <script> so a name with "</script>"
            # can't break out of the script tag.
            selfjson = json.dumps(self.server.self_name).replace("<", "\\u003c")
            root = find_project_root(self.server.project)
            proj_name = os.path.basename(os.path.normpath(root)) or root
            people = [[name, "@%s " % name] for name in _chat_peers(self.server.project)]
            people.append(["All", "@All "])
            peoplejson = json.dumps(people).replace("<", "\\u003c")
            page = (_PAGE.replace("__SELF__", html.escape(self.server.self_name))
                    .replace("__PROJECT__", html.escape(proj_name))
                    .replace("__PROJECTPATH__", html.escape(root, quote=True))
                    .replace("__SELFJSON__", selfjson)
                    .replace("__PEOPLEJSON__", peoplejson)
                    .replace("__TOKEN__", json.dumps(self.server.token)))
            self._send(200, page, "text/html")
        elif self.path == "/messages":
            self._send(200, json.dumps(parse_chat(read_section(self._board(), "Chat"))))
        elif self.path == "/sessions":
            self._send(200, json.dumps(session_summaries(self.server.project)))
        elif self.path == "/status":
            self._send(200, json.dumps(chat_status(self.server.project)))
        elif self.path.startswith("/uploads/"):
            self._serve_upload(self.path[len("/uploads/"):])
        else:
            self._send(404, "{}")

    def _serve_upload(self, ref):
        # _chat_uploads.load_image re-validates ref (traversal-safe) and returns None for
        # anything unknown/malformed. Imported lazily so the module is only needed when an
        # image is actually requested.
        try:
            import _chat_uploads
            got = _chat_uploads.load_image(self.server.project, ref)
        except Exception:
            got = None
        if not got:
            self._send(404, "{}")
            return
        data, ctype = got
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bad_token(self):
        # CSRF guard: a foreign page can't set this custom header without our token.
        return self.headers.get("X-Token") != self.server.token

    def _stale_token(self):
        # A failed token check almost always means the tab is stale (its token predates
        # a server restart). Reply 403 with a message the client can surface DISTINCTLY
        # from the format/size errors, so a "reload fixes it" 403 isn't blamed on the image.
        self._send(403, json.dumps(
            {"ok": False, "error": "session expired — reload the page"}))

    def _content_length(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            return 0
        # Clamp negatives to 0: a negative Content-Length would otherwise reach
        # rfile.read(-1), which reads the whole body to EOF and bypasses the
        # size-before-buffer guard (a "Content-Length: -1" OOM vector).
        return n if n > 0 else 0

    def do_POST(self):
        if self.path == "/send":
            if self._bad_token():
                self._stale_token()
                return
            n = self._content_length()
            raw = self.rfile.read(n) if n else b"{}"
            try:
                data = json.loads(raw or b"{}")
                text = (data.get("text") or "").strip()
            except Exception:
                data, text = {}, ""
            img = _clean_img(data.get("img"))
            if not text and not img:
                self._send(200, json.dumps({"ok": False, "error": "empty"}))
                return
            msg_id = secrets.token_hex(8)
            sent_at = _clean_sent_at(data.get("sent_at"))
            canonical_text = sanitize_chat_text(text)
            msg_like = {
                "speaker": self.server.self_name,
                "text": canonical_text,
                "sent_at": sent_at,
                "_id": msg_id,
            }
            raw_sig = _sig.sign(
                self.server.self_name,
                _sig.chat_payload(msg_like),
                project=self.server.project)
            sig = base64.b64encode(raw_sig.encode()).decode() if raw_sig else None
            st = _board_post(
                self.server.project, self.server.self_name,
                format_chat_message(self.server.self_name, canonical_text,
                                    msg_id=msg_id,
                                    sent_at=sent_at,
                                    send_trigger=data.get("send_trigger"),
                                    img=img,
                                    sig=sig),
                section="Chat")
            ok = (st == "ok")
            self._send(200 if ok else 503,
                       json.dumps({"ok": ok, "error": None if ok else st}))
        elif self.path == "/upload":
            if self._bad_token():
                self._stale_token()
                return
            import _chat_uploads
            limit = _chat_uploads.MAX_UPLOAD_BYTES
            # SIZE BEFORE BUFFER: reject a too-large declared body up front (413) and
            # never pull more than limit+1 bytes off the socket, so a huge/lying
            # Content-Length can't be buffered into memory and OOM the process.
            n = self._content_length()
            if n > limit:
                self._send(413, json.dumps({"ok": False, "error": "upload too large"}))
                return
            raw = self.rfile.read(min(n, limit + 1)) if n else b""
            # raw is the image bytes; _chat_uploads.save_image SNIFFS the real type from the
            # bytes (the Content-Type header is only a hint) and rejects non-images / oversize.
            try:
                ref = _chat_uploads.save_image(
                    self.server.project, raw, self.headers.get("Content-Type"))
                self._send(200, json.dumps({"ok": True, "id": ref}))
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "error": str(e)}))
        elif self.path == "/quit":
            if self._bad_token():
                self._send(403, json.dumps({"ok": False}))
                return
            self.rfile.read(self._content_length())
            try:
                archived = archive_and_clear_chat(self.server.project)
            except Exception:
                self._send(500, json.dumps({"ok": False, "error": "archive_failed"}))
                return
            self._send(200, json.dumps({"ok": True, "archived": archived}))
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, "{}")


def responder_cmds(scripts_dir, project):
    """Background commands that make the room live: one chat auto-responder per agent.
    Their own single-instance mutex makes a duplicate launch a quiet no-op."""
    sh = os.path.join(scripts_dir, "bridge-chat-respond.sh")
    return [[sh, "--self", who, "--project", project] for who in _chat_peers(project)]


def start_responders(project, scripts_dir=None, spawn=None):
    """Spawn the Claude/Codex chat responders in the background. Returns their handles.
    Each runs in its OWN session (start_new_session) so we can later signal the whole
    process tree — the shell loop, its python pass, AND the spawned claude/codex — not
    just the wrapper. `spawn` is injectable for testing."""
    scripts_dir = scripts_dir or os.path.dirname(os.path.abspath(__file__))
    spawn = spawn or (lambda cmd: subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    return [spawn(cmd) for cmd in responder_cmds(scripts_dir, project)]


def execute_supervisor_cmd(scripts_dir, project):
    sh = os.path.join(scripts_dir, "bridge-chat-execute.sh")
    return [sh, "--project", project]


def start_execute_supervisor(project, scripts_dir=None, spawn=None):
    """Spawn the chat requirement capture/execution supervisor. The wrapper owns the
    per-project single-instance mutex, so a duplicate process exits quietly."""
    scripts_dir = scripts_dir or os.path.dirname(os.path.abspath(__file__))
    spawn = spawn or (lambda cmd: subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    return [spawn(execute_supervisor_cmd(scripts_dir, project))]


def _safe_responder_name(name):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name)


def responder_owner_alive(project, self_name):
    pidfile = os.path.join(collab_paths(find_project_root(project))["dir"],
                           ".chatrespond_%s.pid" % _safe_responder_name(self_name))
    try:
        with open(pidfile) as f:
            pid = int((f.read() or "").strip())
    except Exception:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _responder_handle_alive(handle, project, self_name, owner_alive=None):
    if handle is None:
        return False
    code = getattr(handle, "poll", lambda: None)()
    if code is None:
        return True
    if code == 0:
        return (owner_alive or responder_owner_alive)(project, self_name)
    return False


def refresh_responders(handles, project, scripts_dir=None, spawn=None, owner_alive=None):
    """Replace any responder process that has exited, preserving Claude/Codex order."""
    scripts_dir = scripts_dir or os.path.dirname(os.path.abspath(__file__))
    spawn = spawn or (lambda cmd: subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    cmds = responder_cmds(scripts_dir, project)
    refreshed = list(handles or [])
    for i, cmd in enumerate(cmds):
        h = refreshed[i] if i < len(refreshed) else None
        self_name = cmd[cmd.index("--self") + 1]
        alive = _responder_handle_alive(h, project, self_name, owner_alive=owner_alive)
        if not alive:
            nh = spawn(cmd)
            if i < len(refreshed):
                refreshed[i] = nh
            else:
                refreshed.append(nh)
    return refreshed


def supervise_responders(handles, project, stop_event, interval=None, scripts_dir=None, spawn=None):
    """Best-effort in-process supervisor while the web chat server is open."""
    interval = float(interval or os.environ.get("BRIDGE_CHAT_RESPONDER_SUPERVISE_INTERVAL", "5"))
    while not stop_event.wait(interval):
        handles[:] = refresh_responders(handles, project, scripts_dir=scripts_dir, spawn=spawn)


def shutdown_supervised_responders(handles, stop_event=None, supervisor=None, join_timeout=None, stop=None):
    """Stop the supervisor before killing responder trees.

    The supervisor mutates `handles` in place. Joining it first makes sure any
    in-flight restart is reflected in `handles` before we terminate the process set.
    """
    if stop_event is not None:
        stop_event.set()
    if supervisor is not None:
        supervisor.join(timeout=join_timeout)
    (stop or stop_responders)(handles)


def _kill_tree(h):
    """SIGTERM the responder's whole process group (a bare terminate() would leave the
    spawned claude/codex running and delay the shell's cleanup trap); SIGKILL the group
    if it doesn't exit promptly. Handles without a real pid fall back to terminate()."""
    pid = getattr(h, "pid", None)
    if not pid:
        h.terminate()
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:                       # not a group leader / already gone
        try:
            h.terminate()
        except Exception:
            pass
        return
    try:
        h.wait(timeout=3)
    except Exception:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass


def stop_responders(handles, kill=None):
    """Best-effort stop every responder tree; one that's already gone must not stop us
    from stopping the rest."""
    kill = kill or _kill_tree
    for h in handles or []:
        try:
            kill(h)
        except Exception:
            pass


def make_server(project, self_name, port):
    httpd = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    httpd.project = find_project_root(project)
    httpd.self_name = self_name
    httpd.token = secrets.token_hex(16)   # CSRF token embedded in the page
    return httpd, httpd.server_address[1]


def make_server_with_default_fallback(project, self_name, preferred_port=None):
    if preferred_port is None:
        preferred_port = _chat_server.preferred_port(find_project_root(project))
    try:
        return make_server(project, self_name, preferred_port)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        return make_server(project, self_name, 0)


def _clear_registered_server(project):
    if project:
        _chat_server.clear_server_info(project, os.getpid())


def _sigterm_as_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt


def serve_chat(httpd, responders, supervisor_stop, supervisor, supervisor_interval,
               execute_supervisors=None, shutdown=shutdown_supervised_responders,
               stop_execute=None):
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(
            responders,
            supervisor_stop if responders else None,
            supervisor,
            join_timeout=supervisor_interval + 2)
        if execute_supervisors:
            (stop_execute or stop_responders)(execute_supervisors)
        _clear_registered_server(getattr(httpd, "project", None))
        httpd.server_close()
    return 0


def main(argv=None, responder_spawn=None, execute_spawn=None, stop_execute=None,
         make_server_default=None, make_server_explicit=None, browser_open=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--self", dest="self_name", default="Human")
    ap.add_argument("--project", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-responders", action="store_true",
                    help="(deprecated no-op: auto-responders are OFF by default now)")
    ap.add_argument("--responders", action="store_true",
                    help="opt in to the always-on auto-responders (token-heavy: they reply "
                         "to every message and chat with each other). OFF by default.")
    ap.add_argument("--no-execute", action="store_true",
                    help="don't auto-start the on-demand chat->execution supervisor")
    a = ap.parse_args(argv)
    make_server_default = make_server_default or make_server_with_default_fallback
    make_server_explicit = make_server_explicit or make_server
    browser_open = browser_open or webbrowser.open
    project = find_project_root(a.project)
    info = _chat_server.read_server_info(project)
    if _chat_server.is_running(
            info, alive=_chat_server._pid_alive, port_open=_chat_server._port_open):
        url = info.get("url") or "http://127.0.0.1:%d" % int(info.get("port"))
        print("群聊已在运行:%s" % url)
        return 0
    if a.port is None:
        httpd, port = make_server_default(project, a.self_name)
    else:
        httpd, port = make_server_explicit(project, a.self_name, a.port)
    url = "http://127.0.0.1:%d" % port
    _chat_server.write_server_info(
        httpd.project, port, os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S"))
    atexit.register(_clear_registered_server, httpd.project)
    signal.signal(signal.SIGTERM, _sigterm_as_keyboard_interrupt)
    print("群聊已打开:%s  (Ctrl-C 或页面右上角 ✕ 关闭)" % url)
    responders = []
    execute_supervisors = []
    # Slim default: NO always-on auto-responders. They LLM-reply to EVERY message and
    # ping-pong with each other — that was the token burn. Opt in with --responders only
    # if you really want a live agent-chat room.
    if a.responders and not a.no_responders:
        responders = start_responders(httpd.project, spawn=responder_spawn)
    # The on-demand chat->execution supervisor stays on by default: it's the cheap
    # "对话即执行" core — fires only on a new signed directive, idle polls spawn no LLM.
    if not a.no_execute:
        execute_supervisors = start_execute_supervisor(httpd.project, spawn=execute_spawn)
    supervisor_stop = threading.Event()
    supervisor = None
    supervisor_interval = float(os.environ.get("BRIDGE_CHAT_RESPONDER_SUPERVISE_INTERVAL", "5"))
    if responders:
        print("已自动拉起 %s 自动应答器:@ 谁谁就回(@All 全部都回)。" %
              " / ".join(_chat_peers(httpd.project)))
        supervisor = threading.Thread(
            target=supervise_responders,
            args=(responders, httpd.project, supervisor_stop),
            kwargs={"interval": supervisor_interval},
            daemon=True)
        supervisor.start()
    if not a.no_open:
        try:
            browser_open(url)
        except Exception:
            pass
    return serve_chat(
        httpd, responders, supervisor_stop, supervisor, supervisor_interval,
        execute_supervisors=execute_supervisors, stop_execute=stop_execute)


if __name__ == "__main__":
    sys.exit(main())
