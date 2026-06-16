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
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import collab_paths, find_project_root, read_section  # noqa: E402
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


_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>群聊</title><style>
body{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#ededed;height:100vh;display:flex;flex-direction:column}
header{background:#393a3f;color:#eee;padding:10px 14px;display:flex;justify-content:space-between;align-items:center}
#close{cursor:pointer;font-size:20px;color:#bbb}#close:hover{color:#fff}
#log{flex:1;overflow-y:auto;padding:12px}
.row{display:flex;margin:6px 0}.row.me{justify-content:flex-end}
.who{font-size:11px;color:#888;margin:0 8px 2px}
.bubble{max-width:70%;padding:8px 11px;border-radius:8px;background:#fff;white-space:pre-wrap;word-break:break-word}
.me .bubble{background:#95ec69}
footer{display:flex;padding:8px;background:#f7f7f7;border-top:1px solid #ddd}
#msg{flex:1;padding:9px;border:1px solid #ccc;border-radius:6px;font-size:15px}
button{margin-left:8px;padding:0 16px;border:0;border-radius:6px;background:#07c160;color:#fff;font-size:15px;cursor:pointer}
</style></head><body>
<header><b>群聊 · __SELF__</b><span id=close title=关闭>✕</span></header>
<div id=log></div>
<footer><input id=msg placeholder="说点什么…" autofocus><button id=send>发送</button></footer>
<script>
const SELF=__SELFJSON__,TOKEN=__TOKEN__;
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
document.getElementById('close').onclick=async()=>{try{await fetch('/quit',{method:'POST',headers:{'X-Token':TOKEN}});}catch(e){}
  document.body.innerHTML='<p style="padding:24px;font-family:sans-serif">群聊已关闭,可以关掉这个标签页。</p>';};
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
            self._send(200, json.dumps({"ok": True}))
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, "{}")


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
    a = ap.parse_args()
    httpd, port = make_server(a.project, a.self_name, a.port)
    url = "http://127.0.0.1:%d" % port
    print("群聊已打开:%s  (Ctrl-C 或页面右上角 ✕ 关闭)" % url)
    if not a.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
