# 权限改造改动报告（2026-04-01）

## 需求背景
用户要求：
- 非 owner 允许 `web_search` / `web_fetch`，其余高风险能力继续限制。
- `cron` / `heartbeat` 的 automation 执行不应被错误限制工具调用。
- 先实现“开放业务工具”，但仍保留委托类高风险能力限制。

## 变更概览
本次改造将权限模型收敛为：
- `chat_only`（非 owner）：仅 `web_search`、`web_fetch`。
- `automation`（cron/heartbeat 内部触发）：开放业务工具；仍限制委托工具 `spawn`、`codex_delegate`、`codex_status`、`codex_resume`。
- `full`（owner/本地 CLI）：不变，保持全能力。

## 代码变更明细

### 1) automation 工具放行调整
- 文件：`nanobot/agent/loop.py`
- 变更：从 `_AUTOMATION_BLOCKED_TOOLS` 中移除 `cron`。
- 结果：internal automation 会继续拦截委托类工具，但不再屏蔽 `cron` 业务工具。

### 2) automation 系统提示同步
- 文件：`nanobot/agent/context.py`
- 变更：更新 automation 模式提示文案，改为“可使用业务工具完成定时任务，委托类工具仍受限”。
- 结果：模型行为约束与真实权限一致，避免提示与实现冲突。

### 3) 移除 cron 上下文的 add-job 硬限制
- 文件：`nanobot/agent/tools/cron.py`
- 变更：删除 `execute(action="add")` 中对 `cron` 上下文的拒绝逻辑。
- 结果：在 cron 回调触发的 automation 场景下，允许调用 `cron add` 进行业务级调度。

### 4) 测试更新
- 文件：`tests/agent/test_owner_capability_mode.py`
  - 新增/调整：
    - chat_only 在注册了 `cron` 工具时，仍明确不暴露 `cron`。
    - internal automation 在注册了 `cron` 工具时，确认 `cron` 可用。
    - automation 仍不暴露委托类工具（`spawn`、`codex_*`）。
- 文件：`tests/cron/test_cron_tool_list.py`
  - 新增：`test_cron_context_can_still_add_job`，验证 cron 上下文可创建任务。

## 风险与边界
- 本次未放开委托链路（subagent/codex delegation），保持自动化执行可控。
- `/status`、`/restart`、`/stop` 的 owner-only 命令策略未变。
- 本次未改动 owner 识别与 internal metadata 信任模型。

## 结论
本次实现已满足“automation 开放业务工具、非 owner 保持 web-only、cron/heartbeat 不再被错误工具限制”的目标，并通过对应测试验证。
