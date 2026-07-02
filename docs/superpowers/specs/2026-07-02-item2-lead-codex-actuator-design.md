# Item ② — Codex-as-lead can ACT event-driven (symmetric live-wake, Narrow scope)

**日期:** 2026-07-02  **状态:** Claude(lead)自主设计+自我批准(Jack 授权全权:"你自己决定,不用问我")
**Scope:** Jack 选「窄:复用+常开现有执行器 as 角色感知 lead 执行器」。
**流程:** Claude 设计+审 → Codex 实现 → Claude 对抗互审(重点安全)+QA → 签名推。

## 目标(一句话)

让 `lead=Codex` 时,群聊事件能真正驱动一个**可写** Codex 去干活(判定→实现→审→推),
不靠 Codex 的交互 pane(它 pull-only 醒不来)——用**已存在的常驻执行器 supervisor**当可写执行器,
把从没跑过的 `lead=Codex` 路径修通。

## 背景 / 审计结论(sub-agent 审 + 我读码确认)

**关键发现:可写执行器已经存在。** 常驻 `bridge-chat-execute.sh`(item① inc2 起已随 web server 自启)
是一个无头、可写的执行器:被板信号唤醒 → `execute_once` → `run_task`(implement→review→push)。
角色已按 `roles.json` 解析(`_chat_executor._roles_for`:reviewer=lead,implementer=另一个)。
**动作选择逻辑对 `lead=Codex` 已经对称正确**(poster=lead、implementer 推送、gate `--exclude` 排除
implementer、reviewer=lead)——审计逐条确认无 bug。

**唯一真 BLOCKER(两处同根):`_review._detected_recorder` 把记录身份绑在编排进程的 ambient env 上。**
```
_detected_recorder(self_name, env):
  if any CODEX_* set: return "Codex"
  if CLAUDECODE/CLAUDE_CODE_*: return "Claude"
  return self_name
record(): recorded_by = _detected_recorder(reviewer); if recorded_by != reviewer: return "actor_mismatch"
```
常驻 supervisor 在**同一进程树里**既跑 Claude 实现又跑 Codex 审——没有单一 ambient env 能同时满足两条记录:
- **审记录**(`default_review` → `record(reviewer=lead=Codex)`):supervisor 若从 Claude 环境起(CLAUDECODE=1),
  `_detected_recorder("Codex")→"Claude"` ≠ Codex → `actor_mismatch` → 每个任务在 max_fix_rounds 后失败。
  **这就是为什么 `lead=Codex` 从来跑不通。**
- **推送审计记录**(`bridge-push.sh` rebase 改写 SHA 时 `record(WHO=implementer=Claude, PUSHED, --bypass)`):
  若 env 是 Codex 身份则 `_detected_recorder("Claude")→"Codex"` → 失败。与审记录**冲突**(同 env 满足不了两者)。

**为什么 ambient-env 检查在这里是错的机制:** supervisor 是受信的 bridge 代码,它编排**两个** agent;
它的 ambient 身份本就无意义。而且 `_detected_recorder` 早已可被 `env -i` 轻易绕过——它只挡**意外**误标,
挡不住真攻击者。**真正的防伪锚是 EC3 签名**(`record` 用 reviewer 私钥签,`has_approval` 验签):
在 Jack 选定的**多用户威胁模型**里,密钥按用户/机器隔离,签名才是身份分隔的根据。

## 设计:让签名凌驾 ambient-env 检查(最小、正确、可测)

改 `scripts/_review.py record()`:当**签名开启**(默认 `BRIDGE_REQUIRE_SIGNATURES != "0"`)且
`_detected_recorder(reviewer) != reviewer` 时,**不再拒绝**,改为信任密钥:把 `recorded_by` 设为 `reviewer`,
让签名去证明身份。若签名开启但**签不出**(没有该 reviewer 的私钥)→ 返回 `actor_mismatch`(密钥也证明不了身份)。
签名关闭时 → 保持原 ambient-env 拒绝(遗留行为不变)。

```
recorded_by = _detected_recorder(reviewer)
if recorded_by != reviewer:
    if not _sigs_required():
        return "actor_mismatch"        # 遗留:无签名时 env 是唯一守卫
    recorded_by = reviewer             # 有签名:身份由密钥证明,而非 ambient env
entry = {... "recorded_by": recorded_by ...}
sig = _sig.sign(reviewer, _sig.review_payload(entry), project=project)
if _sigs_required() and not sig:
    return "actor_mismatch"            # 签不出=没有该 reviewer 的私钥=证明不了身份
entry["sig"] = sig
# ...原有 lock + append 不变
```

**为什么安全**(要交给对抗 review 重点验证):
- env 匹配的正常路径**完全不变**(recorded_by=detected=reviewer)。签名关闭路径**完全不变**(仍 actor_mismatch)。
  只有「env 不匹配 + 签名开启」这一格新增了 key-based 路径。爆炸半径最小。
- 伪造者(无 reviewer 私钥):`_sig.sign` 签不出 → 返回 actor_mismatch(或即便存了,`has_approval` 验签失败,
  不计为批准)。**批准 = 持有私钥**,正是多用户模型的意图。
- 单机同用户:攻击者能读两把钥匙 → 能伪造——但这在改动前就能用 `env -i` 绕过 `_detected_recorder` 做到,
  净安全无损失。签名仍是锚。
- `has_approval` 的 `recorded_by == reviewer` 检查:合法签名记录 recorded_by=reviewer → 通过;伪造记录验签失败 → 不计。

## 附带硬化 [Minor,审计发现]

`scripts/_agent_cli.py review_argv(Claude)` 目前是裸 `claude -p prompt`,**无工具限制**——Claude 当 reviewer
时能写文件,和 Codex reviewer 的 `-s read-only`、只读 responder 的 `--allowedTools Read Grep Glob` 不对称。
改为把 Claude reviewer 也钉成只读(`--allowedTools Read Grep Glob`,只读 permission mode)。防 reviewer 越权动手。

## 文件结构

- **改** `scripts/_review.py`:`record()` 按上面的签名凌驾逻辑(约 6 行)。
- **改** `scripts/_agent_cli.py`:`review_argv` 的 Claude 分支钉只读工具。
- **改** `tests/test_review.py`:签名凌驾 + 无钥拒绝 + 签名关闭遗留行为 + push-trace 场景。
- **改** `tests/test_agent_cli.py`:`review_argv(Claude)` 只读断言。
- (可选)`tests/test_chat_executor.py`:`roles.json lead=Codex` 端到端 `default_review` 记录成功(mock 审子进程返 GO,
  真 record;测试环境模拟 CLAUDECODE=1 也能记成 Codex)。

## 测试策略(TDD,真断言,安全为重)

1. **lead=Codex 审记录修通**:`record(project, reviewer="Codex", ..., "GO")`,在 `_detected_recorder` 会返回
   "Claude" 的 env(注入 `env={"CLAUDECODE":"1"}` 或等价)下,签名开启 + Codex 私钥已注册 → 返回 "ok",
   `has_approval(exclude="Claude")` 计为批准(验签通过)。
2. **无钥拒绝(防伪)**:以一个**没有注册私钥**的 reviewer 记录 → 签名开启 → 返回 "actor_mismatch"
   (或即便存了,`has_approval` 不计)。证明「批准=持钥」。
3. **签名关闭遗留不变**:`BRIDGE_REQUIRE_SIGNATURES=0` + env 不匹配 → 仍 "actor_mismatch"。
4. **env 匹配正常路径不变**:reviewer 与 env 一致 → "ok",recorded_by 正确。
5. **push-trace 场景**:`record(WHO="Claude"(implementer), PUSHED, bypass=True)` 在不匹配 env + 签名开启 + Claude 私钥
   → "ok"。
6. **review_argv(Claude) 只读**:argv 含 `Read/Grep/Glob`,不含 `Edit/Write/Bash`。

## QA(Claude,真实行为,不 hand-wave)

- 全套测试绿(socket-capable env)。
- 端到端 dry-run:临时项目设 `roles.json lead=Codex`,构造一条签名指令,armed 跑一遍 `execute_once`,
  用 mock/受控 executor 验证:审记录以 reviewer=Codex 落 `collaboration_reviews.json` 且验签通过、
  push gate 认可(不真推真项目)。若真起 codex/claude 子进程成本高,用 `_review`/`_chat_executor` 注入边界做集成 QA。

## 非目标(YAGNI / 明确不做)

- 不做实验性常驻 `codex exec-server`(Phase B,Jack 选了窄 scope)。
- 不重构 `_detected_recorder` 的启发式本身(只在 record 层让签名凌驾);不动手动/交互路径的 env 检查。
- 不做 Codex-as-lead 的主动判断/派活循环(那是「中」scope,Jack 选了窄)。
- 不改 `_roles_for` 的 roster 解析(非 blocker)。
