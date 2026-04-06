# Windows Control Implementation Report (2026-04-04)

## Goal

Refactor the Windows desktop control work so that:
- the framework keeps only a thin integration wrapper
- Windows-specific runtime code lives with the workspace skill
- the capability remains usable through the stable `windows_control` tool name
- future platform migrations, such as macOS, do not inherit hard-wired Windows internals

## Final Structure

### Framework layer
- `nanobot/agent/tools/windows_control.py`
  - thin wrapper
  - stable tool schema
  - dynamic loading of the workspace implementation

- `nanobot/agent/loop.py`
  - registers `windows_control` only when:
    - host platform is Windows
    - `workspace/skills/windows-automation` exists

### Workspace layer
- `workspace/skills/windows-automation/core.py`
  - Windows-specific automation runtime
- `workspace/skills/windows-automation/safety.py`
  - safety guards and audit history
- `workspace/skills/windows-automation/__init__.py`
  - exported interface for the wrapper to load
- `workspace/skills/windows-automation/SKILL.md`
  - skill instructions

### Documentation
- `docs/WINDOWS_CONTROL_README.md`
- `case/reports/2026-04-04-windows-control-design.md`
- `case/reports/2026-04-04-windows-control-test-report.md`

## What Changed

### 1. Windows implementation moved out of the framework
Removed the framework-owned runtime package:
- `nanobot/windows_control/`

The enhanced runtime now lives inside:
- `workspace/skills/windows-automation/`

This is the key architectural change.

### 2. Wrapper retained at the framework boundary
The framework still exposes:
- `windows_control`

But that tool no longer owns the Windows implementation.
It dynamically loads the workspace skill package and delegates execution.

### 3. Built-in Windows skill removed
Removed the built-in skill path:
- `nanobot/skills/windows-control/`

The authoritative skill now lives in the workspace:
- `workspace/skills/windows-automation/`

### 4. Existing behavior preserved
The refactor keeps the first-phase feature set:
- screenshot observation
- OCR lookup
- template matching
- confirmed mouse and keyboard actions
- action history review

## Why This Is Better

This structure better matches the original long-term goal:
- platform-specific logic stays outside the core framework
- the framework remains easier to port to macOS
- the Windows implementation stays cohesive with its skill and dependencies
- the tool surface remains stable for the agent

## Current Capability Boundary

Implemented:
- `inspect_screen`
- `screenshot`
- `find_text`
- `click_text`
- `locate_template`
- `click_template`
- `move_mouse`
- `click`
- `drag_to`
- `press_key`
- `hotkey`
- `type_text`
- `run_actions`
- `recent_events`

Not yet implemented:
- full Windows UI Automation tree
- app-specific connectors
- structured verifier/planner protocol

## Result

The Windows desktop control work is no longer embedded as a framework-owned platform package.

The framework now provides:
- a stable integration point
- permission gating
- tool registration

The Windows-specific implementation now lives where it belongs:
- `workspace/skills/windows-automation/`
