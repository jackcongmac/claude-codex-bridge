#!/usr/bin/env python3
"""bridge-chat-web.py — a tiny local web window for the group chat (## Chat).

"Open group chat" → a browser tab pops up (WeChat-ish bubbles, yours on the right),
you type, agents' messages appear, and the ✕ in the top-right shuts it down. Posts go
through the same locked board write as bridge-chat; armed Claude/Codex see them via
board-wait. Pure stdlib — no install, no deps.

CLI: bridge-chat-web.py [--self <Me>] [--project DIR] [--port N] [--no-open]
"""
import argparse
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
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import (  # noqa: E402
    collab_paths, find_project_root, read_section, read_json, atomic_write,
    atomic_write_json, now_str, acquire_lock, release_lock,
)
from _post import post as _board_post  # noqa: E402

_ENTRY = re.compile(r'### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[^\n]*)\n+(.*)', re.S)
_SPEAKER = re.compile(r'\*\*(.+?):\*\*\s?(.*)', re.S)


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
        sm = _SPEAKER.match(body)
        speaker, text = (sm.group(1).strip(), sm.group(2).strip()) if sm else ("?", body)
        msgs.append({"ts": m.group(1).strip(), "speaker": speaker, "text": text})
    msgs.reverse()   # board stores newest-first
    return msgs


def mentions(text):
    """Parse @-mentions → the set of agents that must respond (the loop control):
    @All / @所有人 → both; @Claude (also '@Claude Code') / @Codex → that one; none →
    empty (nobody auto-replies)."""
    # Real @-mentions only: the @ must NOT be inside a word/email (lookbehind), the name
    # must end at a boundary (\b / not a CJK char) — so "a@codex.io", "@clauded",
    # "@codexical", "@所有人类" are NOT mentions.
    t = text or ""
    if (re.search(r'(?<![\w@])@all\b', t, re.I)
            or re.search(r'(?<![\w@])@所有人(?![一-鿿])', t)):
        return {"Claude", "Codex"}
    who = set()
    if re.search(r'(?<![\w@])@claude\b', t, re.I):
        who.add("Claude")
    if re.search(r'(?<![\w@])@codex\b', t, re.I):
        who.add("Codex")
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
        release_lock(p["lock"])


_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>群聊</title><style>
body{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#ededed;height:100vh;display:flex;flex-direction:column}
header{background:#393a3f;color:#eee;padding:10px 14px;display:flex;justify-content:space-between;align-items:center}
#close{cursor:pointer;font-size:20px;color:#bbb}#close:hover{color:#fff}
#log{flex:1;overflow-y:auto;padding:12px}
.row{display:flex;margin:6px 0}.row.me{justify-content:flex-end}
.who{font-size:11px;color:#888;margin:0 8px 2px}
.bubble{max-width:70%;padding:8px 11px;border-radius:8px;background:#fff;white-space:pre-wrap;word-break:break-word}
.me .bubble{background:#95ec69}
footer{display:flex;padding:8px;background:#f7f7f7;border-top:1px solid #ddd;position:relative}
#at{position:absolute;bottom:100%;left:8px;margin-bottom:4px;background:#fff;border:1px solid #ccc;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;z-index:10;min-width:140px}
#at div{padding:9px 14px;cursor:pointer}#at div:hover{background:#eef}
#msg{flex:1;padding:9px;border:1px solid #ccc;border-radius:6px;font-size:15px}
button{margin-left:8px;padding:0 16px;border:0;border-radius:6px;background:#07c160;color:#fff;font-size:15px;cursor:pointer}
</style></head><body>
<header><b>群聊 · __SELF__</b><span id=close title=关闭>✕</span></header>
<div id=log></div>
<footer><div id=at></div><input id=msg placeholder="说点什么…(打 @ 选人)" autofocus><button id=send>发送</button></footer>
<script>
const SELF=__SELFJSON__,TOKEN=__TOKEN__;
const msg=document.getElementById('msg'),AT=document.getElementById('at');
const PEOPLE=[['Claude','@Claude '],['Codex','@Codex '],['所有人','@All ']];
function showAt(){const m=msg.value.match(/@(\\S*)$/);
  if(!m){AT.style.display='none';return;}
  AT.innerHTML=PEOPLE.map((p,i)=>`<div data-i=${i}>@${p[0]}</div>`).join('');
  AT.style.display='block';}
AT.addEventListener('mousedown',e=>{const d=e.target.closest('div[data-i]');if(!d)return;e.preventDefault();
  msg.value=msg.value.replace(/@\\S*$/,PEOPLE[+d.dataset.i][1]);AT.style.display='none';msg.focus();});
msg.addEventListener('input',showAt);
msg.addEventListener('blur',()=>setTimeout(()=>{AT.style.display='none';},150));
async function load(){
  let ms; try{ms=await (await fetch('/messages')).json();}catch(e){return;}
  const log=document.getElementById('log');
  const atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<60;
  log.innerHTML=ms.map(m=>`<div class="row ${m.speaker===SELF?'me':''}"><div><div class=who></div><div class=bubble></div></div></div>`).join('');
  const whos=log.querySelectorAll('.who'),bubs=log.querySelectorAll('.bubble');
  ms.forEach((m,i)=>{whos[i].textContent=m.speaker;bubs[i].textContent=m.text;});
  if(atBottom)log.scrollTop=log.scrollHeight;
}
async function send(){const t=document.getElementById('msg');const v=t.value;if(!v.trim())return;
  t.value='';
  try{const r=await fetch('/send',{method:'POST',headers:{'Content-Type':'application/json','X-Token':TOKEN},body:JSON.stringify({text:v})});
    const j=await r.json(); if(!j.ok){t.value=v; alert('发送失败,请重试');}}catch(e){t.value=v;}
  load();}
document.getElementById('send').onclick=send;
document.getElementById('msg').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
document.getElementById('close').onclick=async()=>{let p=null;
  try{const r=await fetch('/quit',{method:'POST',headers:{'X-Token':TOKEN}});p=(await r.json()).archived;}catch(e){}
  document.body.innerHTML='<p style="padding:24px;font-family:sans-serif">群聊已关闭。<span id=ar></span><br>可以关掉这个标签页。</p>';
  if(p)document.getElementById('ar').textContent=' 本次记录已存到:'+p;};
load();setInterval(load,1500);
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
            page = (_PAGE.replace("__SELF__", html.escape(self.server.self_name))
                    .replace("__SELFJSON__", selfjson)
                    .replace("__TOKEN__", json.dumps(self.server.token)))
            self._send(200, page, "text/html")
        elif self.path == "/messages":
            self._send(200, json.dumps(parse_chat(read_section(self._board(), "Chat"))))
        else:
            self._send(404, "{}")

    def _bad_token(self):
        # CSRF guard: a foreign page can't set this custom header without our token.
        return self.headers.get("X-Token") != self.server.token

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        if self.path == "/send":
            if self._bad_token():
                self._send(403, json.dumps({"ok": False, "error": "forbidden"}))
                return
            try:
                text = (json.loads(raw or b"{}").get("text") or "").strip()
            except Exception:
                text = ""
            if not text:
                self._send(200, json.dumps({"ok": False, "error": "empty"}))
                return
            st = _board_post(self.server.project, self.server.self_name,
                             "**%s:** %s" % (self.server.self_name, text), section="Chat")
            ok = (st == "ok")
            self._send(200 if ok else 503,
                       json.dumps({"ok": ok, "error": None if ok else st}))
        elif self.path == "/quit":
            if self._bad_token():
                self._send(403, json.dumps({"ok": False}))
                return
            try:
                archived = archive_and_clear_chat(self.server.project)
            except Exception:
                archived = None
            self._send(200, json.dumps({"ok": True, "archived": archived}))
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, "{}")


def responder_cmds(scripts_dir, project):
    """The two background commands that make the room live: one chat auto-responder
    per agent. Their own single-instance mutex makes a duplicate launch a quiet no-op."""
    sh = os.path.join(scripts_dir, "bridge-chat-respond.sh")
    return [[sh, "--self", who, "--project", project] for who in ("Claude", "Codex")]


def start_responders(project, scripts_dir=None, spawn=None):
    """Spawn the Claude/Codex chat responders in the background. Returns their handles.
    Each runs in its OWN session (start_new_session) so we can later signal the whole
    process tree — the shell loop, its python pass, AND the spawned claude/codex — not
    just the wrapper. `spawn` is injectable for testing."""
    scripts_dir = scripts_dir or os.path.dirname(os.path.abspath(__file__))
    spawn = spawn or (lambda cmd: subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    return [spawn(cmd) for cmd in responder_cmds(scripts_dir, project)]


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self", dest="self_name", default="Human")
    ap.add_argument("--project", default=None)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-responders", action="store_true",
                    help="don't auto-start the Claude/Codex chat responders")
    a = ap.parse_args()
    httpd, port = make_server(a.project, a.self_name, a.port)
    url = "http://127.0.0.1:%d" % port
    print("群聊已打开:%s  (Ctrl-C 或页面右上角 ✕ 关闭)" % url)
    responders = [] if a.no_responders else start_responders(httpd.project)
    if responders:
        print("已自动拉起 Claude / Codex 自动应答器:@ 谁谁就回(@All 两个都回)。")
    if not a.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_responders(responders)
    return 0


if __name__ == "__main__":
    sys.exit(main())
