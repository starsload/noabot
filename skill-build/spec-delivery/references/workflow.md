# Workflow

Use this workflow when the user supplies a PRD, requirement ticket, bug-fix request, or feature brief together with a code repository.

## Goal

Produce code through a stable delivery packet instead of free-form reasoning.

## Sequence

1. Create the packet folder and files.
2. Build or refresh `code-map.md`.
3. Decompose the requirement into atomic feature points.
4. Plan the implementation in file-level and API-level detail.
5. Implement one feature point at a time.
6. Test immediately after each feature point.
7. Run broader integration checks.
8. Update `code-map.md` to reflect the final structure.

## Narrow-Step Strategy

Weak models tend to fail when they must keep too much latent structure in working memory. Compensate by:

- Writing down intermediate decisions in the packet files before coding.
- Keeping one active atomic feature point at a time.
- Using the progress ledger as an external memory surface.
- Re-reading the current packet file before moving to the next stage.

## Default Packet Root

Use:

```text
<repo>/.spec-work/YYYYMMDD-feature-slug/
```

Override only if the repository already has a clearly established planning location.

## Packet Roles

- `00-intake.md`: stable problem statement
- `01-code-map-findings.md`: local architecture and entrypoint findings
- `02-feature-breakdown.md`: atomic requirements and edge cases
- `03-implementation-plan.md`: exact code change plan
- `04-progress.md`: step-by-step execution log
- `05-verification.md`: tests, failures, fixes, residual risk

## Must-Do Checks

Before coding:

- Confirm the acceptance target.
- Confirm the affected subsystem.
- Confirm whether `code-map.md` already exists.
- Confirm the smallest first feature point.

Before marking complete:

- Confirm all feature points are closed or deferred.
- Confirm tests are recorded.
- Confirm `code-map.md` was updated for structural changes.
