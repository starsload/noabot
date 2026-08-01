# Windows Control Design (2026-04-04)

## Design Intent

The framework should not permanently own Windows-specific runtime logic if the broader product may later move to macOS.

Therefore the target design is:
- keep a stable framework integration point
- keep platform-specific runtime logic outside the core framework
- allow the implementation to live with the workspace skill and its dependencies

## Final Design

### Framework responsibilities
- stable tool name: `windows_control`
- permission gating
- tool registration
- loading the workspace implementation

### Workspace responsibilities
- actual Windows automation runtime
- safety logic
- platform-specific dependencies
- skill guidance

This means the framework is the shell, and the workspace skill is the implementation.

## Why This Design

Benefits:
- lower platform coupling inside nanobot core
- easier future migration to macOS
- clearer ownership of Windows-only code
- no need to keep a built-in Windows skill inside the framework
- implementation can evolve alongside workspace-specific needs

## Layered Control Strategy

The broader control strategy remains the same:
1. system interfaces first
2. app connectors when available
3. UI automation when available
4. visual observation and localization
5. input simulation last

Within the current first-phase implementation, the practical loop is:
1. `inspect_screen`
2. `find_text` or `locate_template`
3. one small confirmed action
4. inspect again
5. verify the result

## Visual Observation

The visual chain remains a formal part of the design:
- capture screenshot
- preprocess image for the model
- let the model inspect the screen
- use OCR/template lookup for localization
- perform the next smallest action

This is now implemented through the workspace skill while the framework wrapper keeps the public tool contract stable.

## Safety Model

The design keeps the same safety posture:
- mutating actions default to dry-run
- actual side effects require confirmation
- read-only observation remains available
- action history is preserved for audit and debugging

## Portability Implication

This refactor is specifically intended to improve portability.

After the change:
- nanobot core no longer owns a `nanobot/windows_control/` runtime package
- the authoritative Windows implementation is `workspace/skills/windows-automation`

That makes a future macOS path easier:
- keep the same idea of a framework wrapper
- add a separate macOS implementation package/skill
- avoid carrying Windows internals into the platform-neutral core

## Conclusion

The design is now better aligned with a cross-platform future:
- framework = integration shell
- workspace skill = Windows implementation
