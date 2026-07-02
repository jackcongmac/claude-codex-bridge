# Phase 1 第③条 — 身份清晰(C⑤:只读应答器 vs 可写 pane)设计

**日期:** 2026-07-02  **状态:** Claude(lead)自主设计(Jack 授权通宵做完 Phase 1)
**流程:** Claude 写 spec → Codex 实现 → Claude 对抗 review+QA+签名推。

## 目标(一句话)

让用户在群聊 UI 里**一眼分清**每个在线 agent 是**只读应答器**(能回话、改不了代码)还是
**可写 pane**(交互式 coding pane,能真正动手)—— 根治 C⑤「身份混淆」(只读应答器被误当成能写代码)。

## 背景(现状,已探)

`/status` 端点(`bridge-chat-web.py` `chat_status`)**已经**分开算了两种在线来源:
- `pres_alive`:`participant_liveness`(心跳)。心跳由**交互式/可写 pane** 续期(只读 responder 不写心跳)。
- `resp_alive`:`responder_owner_alive`(`.chatrespond_<name>.pid`)。这是**只读应答器**存活。

但前端(`_PAGE` 第~495 行)只把两者 OR 成 `name:online/offline`,**丢掉了 kind 信息** ——
用户看到「Claude:online」却不知道是"只读 bot 在线"还是"能干活的 pane 在线"。这正是今天反复踩的坑
(Jack 对着只读 responder 说"去做 X",它改不了代码,还得人肉转达)。

## 设计

### 1. 后端:`/status` 每个 agent 带 kind

`chat_status` 的 `online` 列表每项从 `{"name","online"}` 扩成:
```
{"name": <str>, "online": <bool>,
 "mode": "pane" | "responder" | "offline",
 "writable": <bool>}
```
判定(用已算好的 `pres_alive` / `resp_alive`,不新增探测):
- `pres_alive[name]` 为真 → `mode="pane"`, `writable=True`(可写 pane 在线,优先级高于 responder)。
- 否则 `resp_alive[name]` 为真 → `mode="responder"`, `writable=False`(只读应答器在线)。
- 都不在 → `mode="offline"`, `writable=False`。

优先级:pane 高于 responder —— 若同一 agent 既有可写 pane 又有只读 responder 在线,显示为 pane(能干活
的那个)。向后兼容:保留 `online` 布尔字段,老前端不炸。

### 2. 前端:presence 行显示 kind + 图标

`presence` 行把 `name:online/offline` 换成带模式的可读标签,例如:
- 可写 pane 在线:`Claude ✍️ 可写` (writable — 能动手)
- 只读应答器在线:`Codex 💬 只读` (responder — 只回话)
- 离线:`Codex ⚪ 离线`

具体文案/图标以简洁可辨为准(英文 UI 已是基线,B④):`✍️ writable pane` / `💬 read-only` / `⚪ offline`。
悬停 tooltip 给一句解释(「只读:能在群里回话,但改不了代码/不能执行;动手要找可写 pane」)。转义安全
(沿用现有 `renderMd`/`textContent` 路径,不引入 XSS)。

### 3. (可选,YAGNI 边界)自己是谁的提示

header 已有 `Group chat · <SELF>`。**不**在本条扩展(避免范围蔓延);kind 显示已足够消除混淆。

## 文件结构

- **改** `scripts/bridge-chat-web.py`:`chat_status` 给 `online` 每项加 `mode`/`writable`;`_PAGE` 前端
  presence 渲染带 kind + tooltip。
- **改** `tests/test_chat_web.py`:`/status` 的 mode 判定测试(pane 优先、responder、offline、pane>responder)。

## 测试策略(TDD,真断言)

- 只有心跳存活 → `mode="pane"`, `writable=True`。
- 只有 responder pid 存活 → `mode="responder"`, `writable=False`。
- 两者都在 → `mode="pane"`(pane 优先)。
- 都不在 → `mode="offline"`, `writable=False`。
- 向后兼容:`online` 布尔字段仍在。
- (前端渲染逻辑若抽成纯函数可另测;否则后端 kind 测试 + 一次浏览器 QA 覆盖。)

## QA(Claude,真实浏览器,不 hand-wave)

开一个临时项目群聊,构造:(a)只跑 responder → UI 显示「只读」;(b)写一条心跳(模拟 pane)→ UI 翻成
「可写」。浏览器实测两态显示正确 + tooltip 文案。

## 非目标(YAGNI)

- 不改 presence/心跳的产生机制(只改**展示**)。
- 不做 per-message「这条是 responder 还是 pane 发的」标注(header + presence 行足够)。
- 不碰 item ②(对称 live-wake)—— 那是独立的架构条目。
