# AI PR Gate — 让 AI 安全出货代码(设计)

**日期:** 2026-07-03  **状态:** Jack 批准转型方向(群聊→治理)+ 选定「开真 GitHub PR」形态。
**一句话:** 把已验证的治理管道(沙箱实现 + 跨 AI 互审 + 签名批准 + 审计)从群聊壳里抽出来,
做成一条干净的独立命令:一个任务 → 一个带「签名互审证据」的 GitHub PR,人类点 merge。

## 为什么(战略,Claude+Codex 一致结论)

护城河**不是群聊/编排**,是 **git 边界上可审计的治理**:AI 改代码,没有**独立、签名、可验证**的审查就上不了车。
群聊是烧 token 的多余壳(已砍/降级)。目标客户:**让 AI 改真实仓库的 solo/创始工程师 + 小团队**。
差异化:**「合规级本地 AI 编码治理」**——别人是"笔记+一个 agent"或"编排 flow",我们是"两个厂商 agent 在真代码上、
带密码学签名的互审 + 审计留痕"。杀手 demo:**AI 想推坏代码,门禁拦下 / PR 里白纸黑字写着谁审的、签名验过**。

## 核心命令

```
claude-codex-bridge ship "<明确的任务描述>"     # 别名: pr / gate
```

**无群聊、无判定器(你直接给明确 task,不分类=不会幻觉)、无应答器、无常驻 supervisor、无 board-wait。**
一条命令 = 一次受治理出货。

### 流程(确定性、可读状态)

```
1. 建分支      ai/<slug>-<shortsha>  (从明确 base;要求 worktree 干净)
2. 沙箱实现    实现者 agent 在分支上实现 (见下"网络策略"); remote-ref-unmoved 断言(不能直推)
3. 跑测试      跑 --test-cmd/配置的命令必须绿 (P0 必须显式给命令; 没测试/红 => 不出 PR)
4. 跨 AI 互审  另一个 agent 审 merge-base(base,HEAD)..HEAD (给 diffstat/文件表/base·head SHA/task)
              => GO / FIX-FIRST; FIX-FIRST => 有界修复循环 => 复审
5. 签名证据    审查者用私钥签一个"证据信封"(见下"信任不变量") => 可密码学验证、绑定到确切 diff
6. 开 PR 前校验 本地 HEAD == 签名的 head 且 `git ls-remote origin <branch>` == 签名的 head
              (审后被篡改/rebase 就中止,不出 PR)
7. 开 PR       推该签名 head + `gh pr create`; PR body 带证据信封的可读摘要
8. 收尾        打印: 分支 · PR URL · 审 verdict · 测试 · 证据条目。人类去 merge。
```

**关键:AI 永远只开 PR、绝不推 main。** 强制点 = "AI 只能提带签名证据的 PR + 人类 merge(+ 可配 GitHub 分支保护)"。

### 信任不变量(核心——这是产品,别做弱了)【Codex 审出的 Blocker,已纳入】

现有 `_sig.review_payload()` 只签 `sha|verdict`,**对 PR 模型太弱**。PR Gate 的签名必须绑定一个**证据信封**:
```
evidence = { task_hash, base_sha, head_sha, patch_id_set(或 tree_sha),
             test_cmd, test_log_hash, branch, implementer, reviewer, verdict, nonce }
sig = SSHSIG-sign(reviewer_key, canonical(evidence))
```
- 只有跑过**这个确切 diff**(head_sha/patch-id)+ **这个测试**(test_log_hash)的审查,签名才成立。
- **开 PR 前必须校验**:本地 HEAD 和远端分支 head 都 == `head_sha`;任何审后改动(新 commit/rebase/force)
  → head 不匹配 → **中止,不出 PR**。这挡住"审完再偷改"。
- **不能绕过现有门禁**:`bridge-push.sh` 有 exact-head 审批校验 + rebase patch-id 保护——PR 的推分支步骤要么
  走一个 branch-mode 的同款校验,要么在 `_pr.py` 里实现同样的 exact-head 验证,**绝不能退化成裸 `git push`**。
- ("EC3" 是旧叫法;实际是 `_sig.py` 的 **SSHSIG/Ed25519 签名证据**,以此为准。)

### PR body(差异化就在这)

```
🤖 AI-authored change · governed by claude-codex-bridge

Task: <原始任务>
Implemented by: <Agent A>  (sandboxed: workspace-write, no network)
Independently reviewed by: <Agent B> — verdict GO ✅  [signature verified: <key id>]
Tests: <suite> PASSED (<n> tests)
Audit: review ledger entry <sha>/<nonce>; test log; fix rounds: <k>

Human: you make the final merge.
```

## 复用 vs 新建

**复用——但只在"注入的 loop 骨架"层,不是默认回调**【Codex 审:run_task 已解耦成注入的 implement/review/push,
可复用;但 `default_implement/review/push` 是"当前 worktree/HEAD + bridge-push main"取向,要换掉】:
- `scripts/_chat_executor.py run_task`:implement→review→(fix loop)→push 的**注入式骨架**,原样复用。
- `_agent_cli`(spawn 实现/审)、`_review.py`(记/验)、`_sig.py`(SSHSIG 签名)、角色解析(`_roles_for`/roles.json)。
- **替换掉默认回调**:写 branch-aware 的 `implement`(在分支上)、`review`(审 merge-base..HEAD)、
  `push`(exact-head 校验 → 推分支 → 开 PR),注入给 `run_task`。

**网络策略(如实,别过度承诺)**【Codex 审:现在的实现"无网"是夸大的】:
- Codex 实现:`codex exec -s workspace-write`(codex 沙箱本身限网)。
- Claude 实现:`claude -p ... Bash`(**能跑 Bash = 能联网**)。P0 要么给 Claude 实现也套只读网/受限工具,
  要么 spec 就**不承诺"无网"**,只承诺"沙箱写权限 + remote-ref-unmoved(不能直推)+ 只开 PR"。先选后者(诚实)。

**新建:**
- `scripts/bridge-ship.sh`(+ bin `ship`/`pr` 子命令):干净 CLI 入口,不碰 chat。
- PR 模式:建分支 + push branch + `gh pr create`(带证据 body);替换"直推 main"。
- 一个 `_pr.py`(或扩 `_chat_executor`):把"push 到 main"抽象成"push branch + open PR",PR body 组装。

**降级/砍:**
- 群聊(web server / 应答器 / 判定器 / supervisor / board-wait)→ `--chat` 可选 debug,**不再是主入口**。代码留着不删。

## 关键决策(已定)/ 默认
- 推送形态:**开真 GitHub PR**(Jack 定)。不直推 main。
- 实现者/审查者:roles.json 或 `--implementer/--reviewer`;默认两个不同厂商 agent(跨厂商互审)。
- 无 `gh` / 无 GitHub 远端时:降级为"推到本地分支 + 打印 diff + 签名审证据",提示装 gh(不硬失败)。

## 错误处理 / 安全
- 实现者移动了远端 ref(想直推)→ 中止,不出 PR(现有 backstop)。
- 测试红 / 无测试 → 不出 PR。
- 审 FIX-FIRST 超过 max 轮 → 不出 PR,打印最后 verdict + 分支供人接手。
- 签不出(无审查者私钥)→ 不出 PR(身份证明不了)。
- 全程沙箱无网;`gh` 用用户已有认证;不碰密钥明文。

## 测试策略
- 单元:分支名生成、PR body 组装(含签名 verdict/测试/审计)、`gh pr create` argv(注入,不真调)、
  降级(无 gh)、各失败门(测试红/无测试/移动远端 ref/签不出)都不出 PR。
- 集成:注入假实现/审/测,端到端跑一次 → 断言 建分支→审→签→(假)开 PR 的顺序与证据内容。
- 复用现有 `_chat_executor`/`_review`/`_sig` 测试。

## 非目标(v1)
- 不做异步多任务队列(以后可加)。
- 不做 GitHub 分支保护集成("AI 签名满足 required review")——高级,后置。
- 不删群聊代码(降级为可选)。
- 不做新 GUI;状态就是命令行确定性输出。

## 阶段【P0 按 Codex 审收得更小,先证明"信任不变量"】
1. **P0(先证信任不变量,越小越好)**:`ship --test-cmd "<cmd>" "<task>"`,**要求干净 repo + 显式 base + 显式测试命令**。
   建分支 → 一次实现 + 有界修复 → 跑给定测试 → 审确切分支 diff → **签确切证据信封** → 校验本地+远端 head==签名head
   → 只推那个签名 head → 开 PR。**跳过**:测试自动探测、无 gh 降级、审后 rebase、异步队列、丰富 onboarding。
   验收:证据信封绑定确切 diff+测试,审后偷改会被 head 校验中止。
2. **P1**:PR body 证据打磨 + 测试命令配置/探测 + 无 gh 降级 + secret 脱敏 + 大小写规范化的 actor 校验 +
   onboarding("装→ship 一个任务→看 PR→merge")。
3. **P2**:异步队列 / GitHub 分支保护集成("AI 签名满足 required review")/ 多语言测试自适应 / conflict·base-moved 策略。

## 运维待补(P0 最小,其余 P1)【Codex 审补】
`gh auth status` 检查、远端/默认 base 探测、干净 worktree 策略、分支名冲突/幂等、已存在 PR 的重跑行为、
base 移动/冲突策略、PR body 与测试日志的 **secret 脱敏**、`has_approval` 的**大小写规范化**(现有自审排除大小写敏感,见 `_review.py`)。
