---
name: spec-delivery
description: Turn a PRD, requirement ticket, or feature request plus a codebase into a staged delivery workflow. Use when Codex must create a folder for intermediate markdown artifacts, generate or refresh code-map.md, break the requirement into atomic feature points and edge cases, define interfaces and data contracts, write an implementation plan, implement one feature point at a time with tests after each step, run integration checks and fixes, and then write the final architectural changes back into code-map.md. Especially useful for weaker day-to-day models that need a strict sequence and fixed templates.
---

# Spec Delivery

Use this skill to force a weak model through a deterministic delivery loop instead of letting it jump straight into coding.

## Start

1. Read `references/workflow.md`.
2. Run `scripts/scaffold_delivery_packet.py` to create the delivery packet inside the target repository.
3. Read the generated packet files and fill them in order.
4. Read `references/code-map-rules.md` before generating or updating `code-map.md`.
5. Read `references/document-specs.md` whenever a packet document needs to be filled or updated.

## Delivery Packet

The script creates this default structure inside the target repository:

```text
<repo>/.spec-work/YYYYMMDD-feature-slug/
  00-intake.md
  01-code-map-findings.md
  02-feature-breakdown.md
  03-implementation-plan.md
  04-progress.md
  05-verification.md
```

If `<repo>/code-map.md` does not exist, the script also creates a stub for it.

## Script

Run:

```bash
python scripts/scaffold_delivery_packet.py --repo /abs/path/to/repo --feature feature-slug --title "Feature title"
```

Optional flags:

- `--packet-dir .spec-work`
- `--date-prefix YYYYMMDD`
- `--force`

## Required Workflow

### 1. Intake

Write `00-intake.md` first.

- Summarize the user request in one paragraph.
- Record acceptance signals, constraints, non-goals, and missing inputs.
- Do not start code changes before this file is usable.

### 2. Code Map

Check whether `code-map.md` already exists.

- If missing, create or fill it before implementation.
- If present, refresh only the sections needed for the current feature.
- Save feature-specific findings in `01-code-map-findings.md`.
- Record entrypoints, important files, data flow, state owners, and risky coupling.

### 3. Requirement Breakdown

Write `02-feature-breakdown.md`.

- Split the requirement into atomic feature points.
- Add boundary cases, error cases, and compatibility cases.
- Define API shapes, method signatures, data contracts, and migration concerns.
- Mark open questions explicitly instead of silently guessing.

### 4. Implementation Plan

Write `03-implementation-plan.md`.

- List the exact files or modules likely to change.
- Describe data structures before editing code.
- Describe new methods, changed methods, and deletion risks.
- Define unit-test targets and integration-test targets before implementation.
- Order work by atomic feature point, not by file.

### 5. Incremental Implementation

Use `04-progress.md` as the execution ledger.

For each atomic feature point:

1. Mark the feature point as `in_progress`.
2. Implement only that slice.
3. Run the narrowest relevant tests immediately.
4. Record what changed, what passed, and what still blocks.
5. Mark the slice as `done` only after tests pass.

Do not batch multiple feature points into one large unverified edit unless the codebase makes that unavoidable.

### 6. Integration and Repair

Write `05-verification.md`.

- Run broader integration checks after all atomic points are complete.
- Record failing scenarios with concrete reproduction steps.
- Fix, rerun, and update the verification file.
- Leave residual risks in a dedicated section.

### 7. Code Map Writeback

After implementation is stable:

- Update `code-map.md` with the new or changed architecture.
- Keep it structural. Do not turn it into a changelog.
- Record which sections were added or updated in `05-verification.md`.

## Execution Rules

- Always create the packet first. Do not skip directly to coding.
- Keep the packet files updated as the source of truth.
- Prefer appending evidence over rewriting history out of the packet.
- Keep each step narrow enough that a weak model can finish it in one pass.
- When the repository is large, scope code-map work to the affected area, then update the global map summary.
- If the repository already has its own planning folder, reuse it only when it matches the packet purpose. Otherwise keep `.spec-work`.
- If tests are unavailable, record the exact reason and provide the next-best verification.

## Completion Bar

The task is not complete until all of the following are true:

- The packet exists and is filled through verification.
- `code-map.md` exists and reflects the final architecture.
- Every atomic feature point is either delivered or explicitly deferred.
- Unit or slice tests have been run after each implemented feature point.
- Integration validation has been recorded.
- Residual risks and follow-ups are written down.

## Hand-off Format

When reporting progress or completion, summarize in this order:

1. Current stage in the packet.
2. Feature points completed.
3. Tests run and outcomes.
4. Open risks or questions.
