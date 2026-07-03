# 群聊瘦身 — on-demand「对话即执行」,砍掉烧 token 的壳(设计)

**日期:** 2026-07-03  **状态:** Jack 已批准方向(② 瘦身)。这轮 Codex token 告急,Claude 全程实现+自审。
**根因存档:** `.collab/token-burn-claude.md` + memory `groupchat-slim-down-decision-2026-07-02`。

## 目标

保住「对话即执行」价值内核,砍掉烧 token 的壳。一条指令的成本 ≈ 一次 Haiku 分类 + 一次真实执行,
不再有:每消息 7× Opus 调用、agent 互聊乒乓、board-wait 反复唤醒主会话。

## 根因 → 对症(只做这几刀,小而准)

1. **[#1 最狠] board-wait 每条群聊帖唤醒主会话** → 瘦身群聊**不进主会话环路**:不 arm board-wait、不靠
   coding pane 回话。群聊 = 输入框 + 按需 runner + 框内回报,自成闭环。**操作 + 默认不拉起 responders** 实现。
2. **[#2] judge 等跑 Opus** → `_chat_judge.default_call_llm` **钉 Haiku**(`claude -p --model claude-haiku-4-5-20251001`,
   可用 `BRIDGE_CHAT_JUDGE_MODEL` 覆盖)。分类器不该用最贵模型。
3. **[#3] 热 resume O(n²) 重发历史** → 元凶是**两个自动应答器**;瘦身**默认删掉 responders**,O(n²) 随之消失。
   (judge 上下文本就 cap 到最近 8 条,无需再改。)

## 改动(全 Claude 实现)

### 1. `scripts/bridge-chat-web.py` — 默认不再自动拉起应答器,保留按需执行器
- 新 argparse:`--responders`(**opt-in**,默认 False,才起那套烧钱的自动应答器)、`--no-execute`(默认 False,
  关掉按需执行 supervisor)。`--no-responders` 保留为可接受的旧参(现在默认就是不起,变成 no-op,向后兼容)。
- main() 逻辑:`if a.responders: start_responders(...)`(默认不起);`if not a.no_execute: start_execute_supervisor(...)`
  (按需执行器默认起——它空闲不烧,只在有新签名指令时动)。responder-supervisor 线程只在真起了 responders 时才建。
- 「已自动拉起…应答器」的 print 只在 responders 真起时显示。

### 2. `scripts/_chat_judge.py` — judge 钉 Haiku
- `default_call_llm` 两条 `claude -p` 都加 `--model <model>`,`model = os.environ.get("BRIDGE_CHAT_JUDGE_MODEL") or "claude-haiku-4-5-20251001"`。

### 3. 操作/文档
- 瘦身群聊启动:`bridge-chat-web.py`(默认无 responders、有按需执行器)。**coding pane 不 arm board-wait**——
  这是 #1 的关键,群聊自成闭环,不重启主会话。

## 测试(Claude 自测 + 一个 Claude 子 agent 对抗审)

- `tests/test_chat_web.py`:默认 `main()` **不** spawn responders(注入 spawn 断言 responder 命令没被起);
  默认仍 spawn 执行 supervisor;`--responders` 才起 responders;`--no-execute` 不起执行器。更新旧的
  `test_main_no_responders_suppresses_execute_supervisor_autostart`(旧耦合已解除:默认就无 responders、有执行器)。
- `tests/test_chat_judge.py`:`default_call_llm` 的 argv 含 `--model` 且默认是 haiku id;`BRIDGE_CHAT_JUDGE_MODEL` 可覆盖
  (把 subprocess.run monkeypatch 掉断言 argv,不真 spawn)。

## 非目标
- 不删 responder 代码本身(留着 `--responders` 可选);只是**默认不起**。
- 不改执行管道(implement→review→push)、签名门、沙箱——那是价值内核。
- 不做新 UI。输入框 + 回报沿用现有 web 页。

## 这轮分工(Codex 省着用)
Claude 实现 + 自测 + 一个 Claude 子 agent 对抗审。签名推:HEAD 由 Claude 记 GO 需 peer≠pusher——
用 Codex 推一次(最省的 Codex 用法),或按需再定。
