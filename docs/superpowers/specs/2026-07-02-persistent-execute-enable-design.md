# 持久化 BRIDGE_CHAT_EXECUTE 开关（"以后默认开"）设计

**日期:** 2026-07-02  **状态:** Claude(lead)设计,Jack 明确要求"常开、以后默认开"
**流程:** Claude 设计 → Codex 实现 → Claude 对抗安全审 + QA → 签名推 → 给 Jack 开。

## 目标

让 Jack 不用每次手动 `BRIDGE_CHAT_EXECUTE=1` —— 加一个**持久**的执行开关,默认对他的实例开。
**关键约束:不改发布包的 fail-closed 默认**(别的用户 `npm i` 装到的仍是关的);只让**本地/用户级**配置能持久开。

## 现状

`_chat_execute.execute_once` 目前:`execute_enabled = os.environ.get("BRIDGE_CHAT_EXECUTE") == "1"`。
env 关时走 capture-only(只记不跑码)。env 是唯一开关,不持久(重启/重开群聊就没了)。

## 设计:分级优先的执行开关(fail-closed 保底)

新增 `_chat_execute._execution_enabled(project) -> bool`,优先级从高到低:

1. `env BRIDGE_CHAT_EXECUTE == "0"` → **False(急停,最高优先,永远能一键关死)**
2. `env BRIDGE_CHAT_EXECUTE == "1"` → True
3. 项目级 `.collab/chat_execute.json` 里 `{"enabled": bool}` 若存在 → 用它(某项目想单独关掉)
4. 用户级 `~/.claude-bridge/chat_execute.json` 里 `{"enabled": bool}` 若存在 → 用它(**Jack 的"以后默认开"设这个**)
5. 都没有 → **False(发布包 fail-closed 默认不变)**

`execute_once` 改用 `_execution_enabled(project)` 替换那行直接 env 检查。其余逻辑(capture-only 分支、
高风险 greenlight、EC1 签名门、沙箱、跨审、push gate)**全不动**。

读配置容错:文件不存在 / 非法 JSON / 无 `enabled` 键 → 视为"未设置",继续往下一级(绝不因读错而误开)。
`enabled` 必须是布尔 `true` 才算开;任何非 `true` 值当 False。

## CLI(便于开关 + 排障)

`_chat_execute.py` 加子命令(main 里):
- `execute-enable [--user|--project] [--project DIR]` → 写 `{"enabled": true}` 到对应层(默认 `--user`)。
- `execute-disable [--user|--project] [--project DIR]` → 写 `{"enabled": false}`。
- `execute-status [--project DIR]` → 打印最终判定 + 每一级的来源(env/项目/用户/缺省),方便看"到底谁生效"。

原子写(tmp+replace),`~/.claude-bridge/` 不存在则建(0700 目录合宜,和现有 keys 目录同级)。

## 文件结构

- **改** `scripts/_chat_execute.py`:`_execution_enabled(project)` + 三个 CLI 子命令;`execute_once` 换用它。
- **改** `tests/test_chat_execute.py`:优先级 + fail-closed + 容错测试。

## 测试策略(TDD,安全为重)

- env "0" 压过用户级/项目级 true → False(急停有效)。
- env "1" → True。
- 无 env + 项目级 true → True;项目级 false 压过用户级 true → False(项目可单独关)。
- 无 env + 无项目级 + 用户级 true → True(Jack 的默认开)。
- 无 env + 无任何配置文件 → **False(fail-closed 默认)**。
- 配置文件损坏 / 无 enabled 键 / enabled 非布尔 → 当未设置,落到下一级,最终 fail-closed。
- `execute_once`:用户级开 + 无 env 时,真会走执行分支(mock executor 断言被调);用户级关时走 capture-only。
- CLI:enable/disable 写对文件对层级;status 报告正确来源。

## 安全审重点(交给对抗 review)

- 有没有任何路径能让"缺省 / 读错 / 空文件"误判成开?(必须 fail-closed)
- env "0" 急停是否绝对优先、不可被配置覆盖?
- 是否只放宽了"要不要执行"这一层,而 EC1 签名门 / 高风险 greenlight / 沙箱 / 跨审 / push gate 全部原样保留?
- 发布包默认(无用户级/项目级文件)是否仍是关?

## 非目标

- 不改发布包 fail-closed 默认。
- 不动高风险 greenlight、签名门、沙箱、跨审、push gate。
- 不做 per-message 开关粒度(太细,YAGNI)。
