# P0-1 增量2 — 群聊需求「常开」自动落 ISSUES(驱动≠记录)设计

**日期:** 2026-07-02  **状态:** Claude(lead)自主推进(Jack 授权通宵做完 Phase 1)
**流程:** Claude 写 spec → Codex 实现 → Claude 对抗 review+QA+签名推。

## 目标(一句话)

让群聊里 Jack 的**签名**需求 / 决定**无论执行器开关都自动落进 `.collab/ISSUES.md`**,
真正消灭人肉中继 —— 兑现 Phase 1 第①条「驱动≠记录」。增量1 已把「记录机器」做好但**全 gated
在 `BRIDGE_CHAT_EXECUTE=1`** 后面;增量2 把**安全的记录**从**危险的执行**解耦成常开。

## 背景

增量1(`a2da5f1`):`_chat_execute.execute_once` 一进门就 `if BRIDGE_CHAT_EXECUTE != "1": return
"disabled"`,所以记录也只在执行器 armed 时发生。而执行器 armed 是危险操作(会跑 Codex 改代码),
Jack 只在受控点火时开。结果:日常聊天里需求**不会**自动落盘 —— 第①条没真兑现。

**安全性论证(为什么记录可以常开):** `record`/capture 只做两件事 —— 往本地文件 `.collab/ISSUES.md`
追加一行 + 在群里回一句;**永不调用 executor、永不改仓库代码、永不 push**;且 `decide` 前置 EC1
签名门(`_human_sig_ok`),只有 Jack 的签名消息才会被记;去重靠 handled-state + `chat-task:<id>`
marker;单实例互斥。因此常开无风险。

## 设计

### 1. `execute_once` 去掉「disabled」早退,改按 flag 分支

`execute_enabled = os.environ.get("BRIDGE_CHAT_EXECUTE") == "1"`。claim + `decide` 照常(包括 EC1
签名门、判定)。然后:

| decision.action | flag ON(执行器 armed) | flag OFF(常开 capture-only) |
|---|---|---|
| `execute` | 现行:ack→executor→report→push | **记录**为 open 任务 + 回「已记入清单(执行器未开)」,标 done,返回 `"captured"` —— **不跑码** |
| `request_greenlight`(高风险) | 现行:awaiting_greenlight + pending | **记录**为 open 任务(注明高风险)+ 回执,标 done,返回 `"captured"` —— **不跑码、不建 pending** |
| `record`(record_requirement) | 记录 + 回「已记录」(同两态) | 同左 —— 记录 + 回「已记录」,返回 `"recorded"` |
| `ask` | 回澄清问题 | 同左 |
| `ignore`/opinion | 无 | 无 |

要点:
- flag ON 的路径**行为完全不变**(增量1 的实现原样保留,仅把 `disabled` 早退去掉、把执行分支包在
  `execute_enabled` 里)。
- flag OFF 时**绝不**调用 `executor`,**绝不**设 `pending_greenlight`(没有执行器可点头进)。
- 两种模式共享 handled-state(`chat_execute_state.json`),每条消息只处理一次;capture 记录后标 done。
- 语义:capture 记录的 actionable 任务是「记录并归档」——**不会**在 Jack 之后开执行器时被自动补跑
  (刻意为之:执行是深思熟虑的动作,不能因为历史消息被自动触发)。锁竞争重试路径(增量1 FIX1)保留。

### 2. capture supervisor 自启(web server 拉起)

现状:`bridge-chat-web.py` 的 `main()` 只拉起 responders,**不**拉起 execute supervisor
(`bridge-chat-execute.sh`),所以即使 execute_once 改好了也没人常开地驱动它。

改动:web server 启动时**额外自启** `bridge-chat-execute.sh --project <root>`(它已有单实例互斥
`.chatexecute.pid`,重复启动是安静 no-op;它每次板信号更新调 `_chat_execute.py once`)。关闭时随
responders 一起停。supervisor 脚本**本身不需要改**(它无条件调 execute_once,由 execute_once 内部
决定 capture-vs-execute)。

安全:supervisor 常开只意味着「常开地记录签名需求」,执行仍要显式 `BRIDGE_CHAT_EXECUTE=1`。

### 3. 成本说明(如实记录)

常开 capture 会对**每条新的人类消息**多跑一次 judge 分类(LLM)。这是「自动分类并捕获」的固有成本
(约在两个 responder 的 LLM 调用之上再加一次)。可接受:这正是自动化换掉人肉中继的代价。以 LAST
update_id 去重,一批新消息只跑一次。

## 文件结构

- **改** `scripts/_chat_execute.py`:`execute_once` 去 disabled 早退 + flag 分支;新增 capture 分支
  逻辑(记录 open + 回执 + 标 done,不执行)。可能抽一个 `_capture_only(project, decision, poster, mid)`
  小函数保持可读。
- **改** `scripts/bridge-chat-web.py`:`main()` 自启 execute supervisor;关闭时停;跟随现有 responder
  spawn/stop 模式(`start_new_session`、`_kill_tree`)。加一个可注入 spawn 便于测试。
- **改** `tests/test_chat_execute.py`:capture-only 模式测试(见下)。
- **改** `tests/test_chat_web.py`:自启 + 关闭停 supervisor 测试(注入 spawn/stop,不真起进程)。

## 测试策略(TDD,真断言)

- flag OFF + `record_requirement` → 落 open 任务 + 回「已记录」+ **executor 不被调**(`self.fail`)+ 标 done。
- flag OFF + 非高风险 `actionable` → 落 open 任务 + **executor 不被调** + 返回 `"captured"`。
- flag OFF + 高风险 `actionable` → 落 open 任务(不建 pending_greenlight)+ **executor 不被调**。
- flag ON 的现行路径全绿(回归:execute/greenlight/record 行为不变)。
- flag OFF 幂等:同一条消息两趟只记一行、只回一次、第二趟 no-op(handled)。
- web server 自启:`main()`/启动函数注入 spawn,断言 execute-supervisor 命令被拉起;关闭注入 stop,断言被停。

## 非目标(YAGNI)

- 不做「开执行器时自动补跑历史 capture 任务」(刻意)。
- 不做 ISSUES.md 任务状态的鉴权(增量1 已留 follow-up 注释;签名门在消息侧)。
- 不改 judge/responder 的成本结构(常开一次分类是固有成本)。

## 待 Jack 拍板(不阻塞,先按保守默认实现)

flag OFF 时是否也把**非 record_requirement 的 actionable**记为 open(本设计默认:**是** —— 更忠于
「群聊需求自动落」)。若 Jack 想只记显式「记一下」类,可后续一行开关收窄。
