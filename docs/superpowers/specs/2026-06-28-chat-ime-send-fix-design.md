# Group chat — Phase 1a: IME / premature-send fix

**Status:** approved by Jack 2026-06-28 (GO). Claude leads/specs+reviews, Codex implements.

## Problem (root cause, confirmed)

In the web group chat, pressing Return while an IME / predictive composition is active
sends the message before the user finishes typing, and leaves the just-committed
composition string behind in the input box.

Reproduced live by Jack (both Chinese and Latin input on macOS). Board ground-truth: a
message sent as `图片应该拽进来或者pa s te` (partial) while the input box still held
`paste`.

Root cause is at `scripts/bridge-chat-web.py` (the `_PAGE` template, keydown handler,
~line 233):

```js
document.getElementById('msg').addEventListener('keydown',
  e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
```

It does not check `e.isComposing`. On macOS, Return during an active composition fires a
keydown with `isComposing===true` / `keyCode===229`; the handler treats it as send,
snapshots the pre-commit (partial) value, clears the box, and the IME then commits the
composition string into the now-empty box. One root cause explains BOTH the partial send
and the residual text.

## Fix (minimal, single change)

Guard the Enter-to-send so it never fires during composition:

```js
if(e.key==='Enter' && !e.shiftKey && !e.isComposing && e.keyCode!==229){
  e.preventDefault(); send();
}
```

- During composition, Return falls through to the IME (commit/candidate select) — no send.
- `Shift+Enter` newline behavior unchanged. The 「发送」 button path unchanged.
- Do NOT bundle the separate latent async-race in `send()` (on failed POST, `t.value=v`
  can clobber concurrent typing). Note it as a follow-up; out of scope here.

## Verification (no "I tested it" hand-waving)

1. **Regression test (committed):** in `tests/test_chat_web.py`, following the existing
   `assertIn`-on-served-page pattern (cf. `test_index_polls_status_for_typing_indicator`),
   assert the served page's keydown handler guards on composition — i.e. the page contains
   `isComposing` (and `229`). Write it FAILING first against current code (TDD), then fix.
2. **Behavioral QA (Claude, post-implementation):** drive the live page via browser
   automation — dispatch a synthetic `KeyboardEvent('keydown',{key:'Enter',isComposing:true})`
   and assert NO `/send` fires and the input value is intact; then `isComposing:false`
   asserts send DOES fire. Plus Jack's manual confirmation.

## Process

Codex: write failing test → implement → run full suite green → return the diff. Do NOT
push, do NOT paste fix code into `## Chat`. Claude reviews the working tree + re-runs the
suite + runs the browser QA. Codex reverse-reviews Claude's verdict. Push via
`bridge-push.sh` only after GO; Jack approves direction.
