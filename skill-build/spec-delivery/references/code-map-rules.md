# Code Map Rules

Use these rules when generating or updating `code-map.md`.

## Purpose

`code-map.md` is a structural guide for future work. It should help another model find the right starting points quickly.

## Include

- Major subsystems
- Entry points
- Core data models
- Cross-module flows
- Background jobs, schedulers, or workers
- Integration boundaries
- Test locations when they reveal architecture

## Do Not Include

- Ticket history
- Temporary debugging notes
- Step-by-step implementation logs
- Full API docs copied verbatim
- Long changelog-style narrative

## Update Style

When the file already exists:

- Preserve existing useful structure.
- Update only the sections touched by the current work.
- Add a short "Last updated for" note only if the file already uses that style.

When the file does not exist:

- Start with system overview.
- Then list entrypoints and top-level modules.
- Then explain data flow and major external integrations.
- End with a "Hotspots / likely change points" section.

## Quality Bar

A good code map lets another model answer these quickly:

- Where does the request enter the system?
- Which files own the key state?
- Which modules are safe to change in isolation?
- Which modules are high-risk because they fan out widely?
- Where should tests be added or updated?
