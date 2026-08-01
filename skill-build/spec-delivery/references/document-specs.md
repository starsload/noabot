# Document Specs

Use these document quality bars when filling the packet.

## 00-intake.md

Required:

- Problem statement
- User-visible goal
- Inputs supplied
- Constraints
- Non-goals
- Acceptance signals
- Open questions

Good output:

- Short and concrete
- No architecture speculation unless the request already implies it

## 01-code-map-findings.md

Required:

- Relevant entrypoints
- Key modules and responsibilities
- Data flow or control flow
- State ownership
- External dependencies
- Risks and unknowns

Good output:

- Names real files, classes, handlers, jobs, or routes
- Distinguishes facts from inferred structure

## 02-feature-breakdown.md

Required:

- Atomic feature points
- Boundary and failure cases
- Interface definitions
- Data contracts
- Migration or compatibility notes

Good output:

- Each feature point is independently implementable
- Each edge case is tied to a feature point or interface

## 03-implementation-plan.md

Required:

- Change targets
- New or changed data structures
- New or changed methods
- Test plan
- Execution order

Good output:

- Explains why each file changes
- Avoids vague items like "update backend logic"

## 04-progress.md

Required per step:

- Feature point id
- Status
- Files changed
- Tests run
- Result
- Follow-up note if blocked

Good output:

- Reads like a ledger, not a narrative essay

## 05-verification.md

Required:

- Unit or slice test evidence
- Integration scenarios
- Failed runs and fixes
- Residual risks
- Code-map writeback summary

Good output:

- Includes commands, test files, or scenario labels
- Makes it obvious what was actually verified
