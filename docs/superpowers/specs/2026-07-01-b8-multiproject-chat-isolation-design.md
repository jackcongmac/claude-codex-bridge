# B⑧ 多项目独立群聊 — 稳定端口 + 服务注册 + 隔离回归测试(设计)

**日期:** 2026-07-01
**状态:** 已批准(Jack 2026-07-01「B⑧ 最小实现…不做 --list」)
**流程:** Claude(lead)写 spec → Codex 实现 → Claude review+QA+签名推。

## 目标(一句话)

让多个项目的群聊能同时可靠共存:每个项目落在**稳定、可预测**的端口,启动时把服务信息写进该项目
`.collab/chat_server.json`(退出自清),并用一条回归测试把「互不串」钉死。**不做** `bridge-chat.sh --list`。

## 背景与现状(探索结论)

群聊的隔离**在存储层已经天然成立**——一切都锚在每个项目自己的 `.collab/`(经 `find_project_root`):
消息/图片(`chat_uploads`)/执行队列(`chat_execute_state.json`)/presence/会话文件(`.claude_chat_session`)/
应答器互斥(`.chatrespond_<self>.pid`)全部 per-project;窗口标题已是 `Group chat — <项目名>`(+ B⑦ 项目条);
应答器互斥注释即「per (project, self)」。代码里**无** `/tmp`、`$HOME`、全局注册表等跨项目状态。

**唯一真缺口 = 端口分配。** 现状:`make_server_with_default_fallback(preferred_port=8765)` 先试 8765,
`EADDRINUSE` 就退到临时端口 `0`(随机)。后果:第二个及以后的项目群落在**随机端口**,只在 stdout 打印一次,
**没有任何持久记录** → 关掉/刷新后难找、难重开,也无法检测「这个项目其实已经开着了」。

## 设计

三个互相独立、边界清晰的单元:

### 1. 稳定的 per-project 默认端口 —— `preferred_port(project_root)`

- 纯函数,由项目根路径确定性推导端口:`8765 + (sha1(abspath(normpath(root))) 的整数 % 1000)` → 端口域 `8765–9764`。
- 同一个项目**永远**落同一端口;不同项目**几乎总是**错开(极小概率碰撞由下面的 `EADDRINUSE` 兜底)。
- `make_server_with_default_fallback` 的 `preferred_port` 由硬编码 8765 改为 `preferred_port(project_root)`。
- 显式 `--port N` 仍然优先,行为不变。
- 绑定失败(`EADDRINUSE`,别的进程占了)仍退到临时端口 `0`,保证「总能起来」。

### 2. 服务注册 + 自清 —— `.collab/chat_server.json`

- 结构:`{"pid": <int>, "port": <int>, "url": "http://127.0.0.1:<port>", "started_at": "<ISO8601>"}`。
- **启动时(bind 成功后)** 原子写入(`tmp`+`os.replace`,沿用 `_agent_cli._save_session` 模式)。
- **已在运行检测**:启动前读该文件;若存在、`pid` 存活(`os.kill(pid,0)`)且 `port` 仍被占用(connect 探测)
  → 判定「这个项目已经开着了」,**打印现有 URL 并退出 0**,不重复起第二个同项目窗口。
- **陈旧文件**:若文件存在但 pid 已死(或端口空着)→ 视为陈旧,覆盖写入,正常启动。
- **退出自清**:正常退出、`Ctrl-C`、`SIGTERM`、`/quit` 关闭时删除该文件;删除只在「文件里的 pid == 自己」时执行
  (避免删掉别的实例的注册,沿用 `bridge-chat-respond.sh` pidfile 的 `cleanup` 守卫思路)。用 `atexit` + 现有
  信号/关闭路径挂钩。

### 3. 隔离回归测试

把「互不串」变成可执行断言(而不是靠人读代码相信),覆盖:
- 两个不同临时项目根 → `preferred_port` 得到**不同**端口;同一根重复调用 → **相同**端口(确定性)。
- 两个项目各写各的 `.collab/chat_server.json`,互不覆盖;各读回自己的值。
- 「已在运行」检测:pid 存活 + 端口占用 → 判定 running;pid 死 → 判定 stale(可覆盖)。
- 自清守卫:pid ≠ 自己时不删别人的注册文件。

## 文件结构

- **新增** `scripts/_chat_server.py` —— 端口 + 注册 seam(纯逻辑,易测,把网络/进程探测做成可注入):
  `preferred_port(root)`、`server_info_path(root)`、`read_server_info(root)`、`write_server_info(root, port, pid, started_at)`、
  `clear_server_info(root, pid)`、`is_running(info, *, alive=..., port_open=...)`(依赖注入便于测试)。
- **修改** `scripts/bridge-chat-web.py`:`make_server_with_default_fallback` 用 `preferred_port`;`main()` 接「已在运行检测 →
  打印 URL 退出」;bind 成功后 `write_server_info`;退出路径(`serve_chat` 的 `finally` + `atexit` + 信号)`clear_server_info`。
- **新增** `tests/test_chat_server.py` —— 单元 + 隔离回归测试(纯函数 + 注入的探测,不真开端口/进程)。

## 边界 / 非目标(YAGNI)

- **不做** `bridge-chat.sh --list`(碰 launcher 边,Jack 明确缓做)。
- 不做统一 launcher、通知、跨机端口协调。
- 不改隔离本身(已天然成立);本任务只补端口 + 注册 + 用测试锁定既有隔离。

## 错误处理

- 端口全被占用极端情况 → 临时端口 `0` 兜底,`chat_server.json` 记录真实端口(仍可发现)。
- `chat_server.json` 损坏/非法 JSON → 当作不存在(`read_server_info` 返回 `None`),正常启动并覆盖。
- 写注册文件失败(权限等)→ 记录但**不**阻断服务启动(注册是增强,不是硬依赖)。

## 测试策略

- 纯函数(`preferred_port` 确定性、路径推导)直接断言。
- I/O(读写/清理 `chat_server.json`)用临时目录。
- 进程/端口探测(`is_running`)用注入的假 `alive`/`port_open`,不触真网络/真进程 → 测试快且确定。
- 交给 Codex 时:每个单元 TDD(先写失败测试)。全绿后 Claude review+QA+签名推。
