# QQ Personal Channel Plan

Status: implemented draft
Date: 2026-03-28

## Problem

The previous `qq_personal` implementation used `botpy`, which still belongs to the QQ Open Platform bot ecosystem.

That means it did **not** provide a true personal QQ account integration, even though the channel name suggested otherwise.

## Decision

Replace the old `qq_personal` implementation with a real personal-account solution based on a local **OneBot 11 compatible bridge**, with NapCat as the primary target.

## Why This Direction

This path is the best fit for nanobot's existing channel architecture because it supports:

- inbound private friend messages
- inbound group messages
- outbound private replies
- outbound group replies
- file segments and private-file retrieval
- group-file retrieval
- local deployment without adding an HTTP server into nanobot itself

It also matches how nanobot already handles protocol bridges for channels like WhatsApp.

## Architecture

`qq_personal` now uses:

- **Forward WebSocket** for inbound events
- **HTTP API** for outbound actions

Expected bridge capabilities:

- `send_private_msg`
- `send_group_msg`
- `get_private_file_url`
- `get_group_file_url`
- optional `get_image`, `get_record`, `get_file`

## Message Handling

Inbound:

- accept OneBot `private` and `group` message events
- deduplicate by `message_id`
- map text segments into message content
- materialize `image`, `video`, `record`, and `file` segments into local media files when possible
- publish to the standard nanobot bus as `qq_personal`
- apply group policy when the message comes from a group

Outbound:

- send plain text via `send_private_msg`
- send plain group replies via `send_group_msg`
- send media via OneBot message segments for both private and group targets
- map images, audio, video, and generic files from `msg.media`

## Configuration

Main settings:

- `wsUrl`
- `httpUrl`
- `accessToken`
- `allowFrom`
- `groupPolicy`
- `groupAllowFrom`
- `mediaDir`

## Verification Scope

The implementation is covered by unit tests for:

- private text routing
- group text routing
- group mention gating
- ignoring OneBot action-response frames
- outbound private text via HTTP API
- outbound group text via HTTP API
- outbound file sending via file segment
- inbound private file download and attachment exposure
- inbound group file download and attachment exposure

## Known Limits

- This channel assumes the user already runs a compatible OneBot bridge such as NapCat.
- nanobot does not currently install or log in to NapCat for the user.
- Advanced QQ-specific segment types beyond text/image/video/record/file are not yet interpreted.
