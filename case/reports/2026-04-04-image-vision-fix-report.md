# 图像识别链路改造验证报告（2026-04-04）

## 背景
现场现象：
- agent 通过 `exec` 截图后，再通过 `read_file()` 读取 PNG 并尝试交给模型识图。
- 日志中反复出现：
  - `Non-transient LLM error with image content, retrying without images`
- 结果是模型退化为“去图重试”，后续无法继续基于图片做判断。

## 根因结论
本次问题不是“nanobot 完全不会传图”，而是“进入模型的 inline image 没有统一做尺寸/字节治理”。

关键链路：
- `nanobot/agent/tools/filesystem.py`
  - `read_file()` 读到图片后返回 `image_url(data:...)`
- `nanobot/agent/context.py`
  - 用户图片也直接编码成 `data:image/...;base64,...`
- `nanobot/providers/base.py`
  - 遇到带图的非瞬时错误时，会直接“去图重试”

原始问题点：
- 图片原始字节直接内联为 data URI，没有缩放、压缩、转码、限额控制。
- 大尺寸桌面截图容易触发模型侧 data URI 限额。
- 一旦报错，provider 直接删图重试，导致视觉能力丢失。

## 代码改造

### 1. 统一图像规范化
文件：
- `nanobot/utils/helpers.py`

新增能力：
- `prepare_image_for_llm()`
  - 对 inline image 做统一预处理
  - 限制最大边长
  - 在需要时自动缩放
  - 在需要时自动转 JPEG / 压缩
- `normalize_message_image_blocks_for_llm()`
  - 对消息中的 `image_url` / `input_image` data URI 做统一规范化

当前策略：
- inline 原始字节预算：`7_500_000`
- 最大边长：`2048`
- 过大图片自动缩放与压缩，优先保证能稳定进入模型

### 2. 统一图片入口
文件：
- `nanobot/utils/helpers.py`
- `nanobot/agent/context.py`

改造点：
- `build_image_content_blocks()` 现在先走图像规范化再生成 data URI
- 用户消息中的图片也改为复用这套规范化逻辑

效果：
- `read_file()` 返回图片时，不再把原始超大截图直接送进模型
- 用户主动上传图片时，也不会绕过这套限制

### 3. 优化 provider 重试顺序
文件：
- `nanobot/providers/base.py`

改造前：
- 带图非瞬时错误 -> 直接去图重试

改造后：
- 若识别为图像载荷/尺寸类错误
  - 先尝试对消息中的 inline image 统一规范化后重试
- 只有规范化后仍失败，才走原有“去图重试”兜底

效果：
- 兼容已有历史消息中的旧图片块
- 避免因为单张超大截图而直接丢失视觉能力

### 4. 依赖补充
文件：
- `pyproject.toml`

改动：
- 增加 `Pillow>=10.0.0,<13.0.0`

原因：
- 图像规范化需要稳定依赖 Pillow，而不应继续依赖偶然的传递安装

## 测试记录

### 1. 针对性子集
命令：
`uv run --with pytest --with pytest-asyncio python -m pytest tests\test_image_payloads.py tests\providers\test_provider_retry.py tests\tools\test_filesystem_tools.py -q`

结果：
- `49 passed`

覆盖点：
- 图片缩放/压缩
- 消息中的 inline image 重写
- 图像超限错误时优先走“规范化后重试”
- 文件系统图片读取仍返回多模态块

### 2. 相关回归子集
命令：
`uv run --with pytest --with pytest-asyncio python -m pytest tests\test_image_payloads.py tests\providers\test_provider_retry.py tests\tools\test_filesystem_tools.py tests\agent\test_loop_save_turn.py tests\agent\test_owner_capability_mode.py -q`

结果：
- `69 passed`

说明：
- 覆盖了 provider、filesystem、agent message 保存与上下文相关路径

### 3. 真实截图在线 smoke test
验证目标：
- 使用现场真实截图验证修复后模型侧仍能正常识图，而不是进入去图降级

参与截图：
- `desktop_171752.png`：原始大小 `11,879,660` bytes
- `desktop_172350.png`：原始大小 `1,810,124` bytes

规范化后发送结果：
- `desktop_171752.png` -> `image/jpeg`，`446,581` bytes
- `desktop_172350.png` -> `image/jpeg`，`345,701` bytes

实际调用结果：
- `finish_reason=stop`
- 模型成功返回两张截图的对比描述

结论：
- 修复后，真实大图场景下可以继续把图片发送到大模型并完成识图

### 4. 全量 pytest
命令：
`uv run --with pytest --with pytest-asyncio python -m pytest`

结果：
- `collected 650 items / 5 errors / 1 skipped`

收集期既有错误：
1. `tests/agent/test_task_cancel.py`
   - 含冲突标记 `<<<<<<< HEAD`
2. `tests/test_custom_provider.py`
   - 与 `tests/providers/test_custom_provider.py` 模块名冲突
3. `tests/test_gemini_thought_signature.py`
   - 与 `tests/agent/test_gemini_thought_signature.py` 模块名冲突
4. `tests/test_news_digest.py`
   - `nanobot.news_digest` 缺失
5. `tests/test_restart_command.py`
   - 与 `tests/cli/test_restart_command.py` 模块名冲突

评估：
- 这些错误均发生在测试收集阶段，属于仓库现有测试资产问题，不是本次图像修复引入。

## 留档文件
- Windows 控制能力设计方案：
  - `case/reports/2026-04-04-windows-control-design.md`
- 本次图像识别链路改造验证报告：
  - `case/reports/2026-04-04-image-vision-fix-report.md`

## 结论
本次改造已完成以下目标：
- 为所有进入模型的 inline image 增加统一预处理
- 修复超大截图触发 provider 去图降级的问题
- 保持 `read_file()` 图片识别链路可继续向模型传图
- 在真实桌面截图场景下验证可正常识图

当前结论可以认为：
- “传图到模型侧进行识图”已恢复并具备更稳的尺寸治理
- 全量测试仍受仓库既有问题阻断，但本次改造相关路径已通过针对性测试与真实在线 smoke test
