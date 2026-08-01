# Windows Control Test Report (2026-04-04)

## Environment
- Project: `D:\Projects\AI\noabot`
- Platform: Windows
- Python: 3.13.3
- pytest: 9.0.2
- pytest-asyncio: 1.3.0

## Validation Goals
- confirm the wrapper loads `workspace/skills/windows-automation`
- confirm the tool remains available only in the intended capability modes
- confirm image observation and action safety behavior still work
- confirm the refactor does not regress related multimodal paths

## Test Runs

### 1. Workspace skill historical unit tests
Command:
`python -m unittest discover workspace\skills\windows-automation\test -v`

Result:
- `8 passed`

Coverage:
- dry-run behavior
- missing dependency handling
- structured action execution
- safety guard history
- click-rate and confirmation checks

### 2. Wrapper and capability-mode core tests
Command:
`uv run --with pytest --with pytest-asyncio python -m pytest tests\tools\test_windows_control_tool.py tests\agent\test_owner_capability_mode.py -q`

Result:
- `22 passed`

Coverage:
- wrapper loads workspace skill package
- `inspect_screen` returns multimodal image blocks
- mutating actions default to dry-run
- confirmed execution path works
- OCR lookup path works
- non-Windows host is rejected
- `full` / `chat_only` / `automation` exposure rules are correct

### 3. Broader related regression suite
Command:
`uv run --with pytest --with pytest-asyncio python -m pytest tests\agent\test_loop_save_turn.py tests\test_image_payloads.py tests\agent\test_loop_consolidation_tokens.py tests\providers\test_provider_retry.py tests\tools\test_filesystem_tools.py tests\tools\test_windows_control_tool.py tests\agent\test_owner_capability_mode.py -q`

Result:
- `90 passed`

Coverage:
- session persistence and image placeholder handling
- image normalization and token estimation
- provider image retry behavior
- filesystem multimodal image reads
- windows control wrapper and permissions

### 4. Local wrapper smoke test
Command:
run `WindowsControlTool(workspace=Path(".../workspace"))` locally, then call:
- `capabilities`
- `inspect_screen`

Result:
- wrapper successfully loaded the real workspace skill from `workspace/skills/windows-automation`
- `capabilities` returned runtime flags
- `inspect_screen` returned multimodal image blocks

## Full pytest
Command:
`uv run --with pytest --with pytest-asyncio python -m pytest`

Result:
- `collected 662 items / 5 errors / 1 skipped`

Existing collection blockers:
1. `tests/agent/test_task_cancel.py`
   - unresolved conflict markers
2. `tests/test_custom_provider.py`
   - import-name clash with `tests/providers/test_custom_provider.py`
3. `tests/test_gemini_thought_signature.py`
   - import-name clash with `tests/agent/test_gemini_thought_signature.py`
4. `tests/test_news_digest.py`
   - missing `nanobot.news_digest`
5. `tests/test_restart_command.py`
   - import-name clash with `tests/cli/test_restart_command.py`

Assessment:
- these are pre-existing collection issues
- no new collection blocker was introduced by the Windows control refactor

## Conclusion
- the historical workspace skill unit tests still pass
- the Windows control capability still works
- the framework wrapper now depends on the workspace skill instead of framework-owned Windows runtime code
- permission boundaries remain correct
- related multimodal regressions remain green
