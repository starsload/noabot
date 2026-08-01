# QQ Personal Setup Guide

Status: draft
Date: 2026-03-28

## Goal

Run `nanobot` against a real personal QQ account using the `qq_personal` channel.

This guide assumes:

- Windows
- a local OneBot 11 compatible bridge
- NapCat as the primary bridge choice

## 5-Minute Checklist

If you only want the shortest path, do this:

1. Download `NapCat.Shell.Windows.OneKey.zip` from the official releases page.
2. Run `NapCatInstaller.exe`.
3. Open the generated `NapCat.*.Shell` directory and run `napcat.bat`.
4. Open the WebUI URL shown in the console.
5. Use QR code login for the target personal QQ account.
6. In NapCat WebUI, enable:
   - one HTTP server on `127.0.0.1:3000`
   - one Forward WebSocket server on `127.0.0.1:3001`
   - `messageFormat = array`
   - one shared token
7. In `nanobot` config, enable `qqPersonal` and fill:
   - `wsUrl`
   - `httpUrl`
   - `accessToken`
   - `allowFrom`
8. Run `nanobot gateway`.
9. First test private chat:
   - temporarily set `allowFrom` to `["*"]`
   - send a friend message to the logged-in QQ account
   - confirm nanobot replies
10. Then test group chat:
   - keep `groupPolicy = "mention"`
   - @mention the bot in a QQ group
   - confirm nanobot replies in the group

If you finish those 10 steps, the integration is basically live.

## What You Need To Do

To connect `nanobot` to a personal QQ account today, you need to provide or complete these items:

### On Your Machine

- Windows machine running both QQ and NapCat
- Node/runtime pieces bundled by the NapCat package
- a working `nanobot` environment

### In NapCat

- log in with the target personal QQ account
- enable local HTTP API
- enable local Forward WebSocket
- set `messageFormat = array`
- set an access token

### In nanobot Config

- enable `channels.qqPersonal`
- set `wsUrl`
- set `httpUrl`
- set `accessToken`
- set `allowFrom`
- optionally set `groupPolicy`
- optionally set `groupAllowFrom`

### For First Verification

- one friend account to DM the QQ account
- one QQ group where the QQ account is present
- one small file for upload/download testing

## Current Upstream Notes

As checked on 2026-03-28:

- NapCat official site: `napneko.github.io`
- NapCat official repository: `NapNeko/NapCatQQ`
- GitHub Releases page currently shows `v4.17.53` as latest
- The release notes state the recommended QQ version is `40768+`

Important upstream notes:

- NapCat docs recommend migrating to the Shell version instead of the older LiteLoader path
- Windows has both a manual Shell path and a OneKey path
- The WebUI token is generated randomly and shown in the console or stored in `webui.json`

## Recommended Windows Download Path

For most users on Windows, the most practical path is:

1. Download `NapCat.Shell.Windows.OneKey.zip` from the official NapCat releases page.
2. Run `NapCatInstaller.exe`.
3. Enter the generated `NapCat.XXXX.Shell` directory.
4. Start `napcat.bat`.

Why this path:

- It is the simplest Windows flow
- It bundles the required runtime pieces
- It avoids the older LiteLoader-centric flow

Alternative path:

1. Download `NapCat.Shell.zip` from the official releases page.
2. Install a compatible QQ version locally.
3. Start NapCat with `launcher.bat` or `launcher-win10.bat`.

Use this only if you deliberately want NapCat attached to an already-installed QQ.

## Where To Get It

Primary official sources:

- Official site: <https://napneko.github.io/>
- Install guide: <https://napneko.github.io/guide/install>
- Shell guide: <https://napneko.github.io/guide/boot/Shell>
- Framework guide: <https://napneko.github.io/guide/boot/Framework>
- Releases: <https://github.com/NapNeko/NapCatQQ/releases>
- GitHub repo: <https://github.com/NapNeko/NapCatQQ>

If you need the matching QQ installer, the NapCat release page also links Windows QQ builds in the release notes.

## First Launch

### Step 1: Start NapCat

If you used the OneKey package:

- run `NapCatInstaller.exe`
- open the generated shell directory
- run `napcat.bat`

If you used the manual Shell package:

- ensure a compatible QQ build is installed
- run `launcher.bat`
- on Windows 10, use `launcher-win10.bat`

### Step 2: Open WebUI

NapCat docs say the default WebUI port is `6099`.

After startup, the console should show a URL similar to:

```text
http://127.0.0.1:6099/webui?token=xxxxx
```

If you miss it:

- check the console output again
- or open `webui.json` and read the `token`

### Step 3: Log In To QQ

In the NapCat WebUI:

1. go to QQ login
2. choose `QRCode`
3. scan it with the target personal QQ account

NapCat docs note that the WebUI token may refresh after login, so if the panel asks again:

- check the NapCat console
- or check `webui.json` again

## Network Configuration For nanobot

`nanobot`'s `qq_personal` channel currently expects:

- a **forward WebSocket server** from NapCat for inbound events
- an **HTTP API server** from NapCat for outbound actions
- `messageFormat = array` so message segments remain explicit

### Recommended Local Ports

Recommended local-only setup:

- HTTP server: `127.0.0.1:3000`
- WebSocket server: `127.0.0.1:3001`
- use the same token for both
- `messageFormat = array`

This is a recommendation for local safety and compatibility.

### Why `array`

`qq_personal` parses OneBot message segments and handles media like `text`, `image`, `record`, `video`, and `file`.

Using `array` format keeps message segments explicit and matches the implementation better than plain string payloads.

### WebUI Configuration Steps

In NapCat WebUI:

1. open network settings
2. create one HTTP server
3. create one WebSocket server
4. set both to enabled
5. set host to `127.0.0.1` if `nanobot` runs on the same machine
6. set HTTP port to `3000`
7. set WS port to `3001`
8. set `messageFormat` to `array`
9. set a non-empty token
10. save and enable both entries

## nanobot Configuration

Example config:

```json
{
  "channels": {
    "qq_personal": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3001",
      "httpUrl": "http://127.0.0.1:3000",
      "accessToken": "change-me",
      "allowFrom": ["123456789"],
      "groupPolicy": "mention",
      "groupAllowFrom": []
    }
  }
}
```

Notes:

- `allowFrom` should contain real QQ numeric IDs as strings
- for first-time testing you can temporarily use `["*"]`
- after you confirm the sender ID, tighten it back to your own allowlist
- `groupPolicy` controls whether groups are `mention`, `open`, or `allowlist`
- `groupAllowFrom` should contain group IDs when `groupPolicy` is `allowlist`

## How To Get Your QQ ID For `allowFrom`

Simplest ways:

- temporarily set `allowFrom` to `["*"]`
- send a private message to the logged-in personal account
- check nanobot logs for the sender ID
- copy that QQ number into `allowFrom`

Because this channel uses personal-account OneBot events, the sender identity is the QQ user ID rather than the QQ Open Platform `openid`.

## Start nanobot

Run:

```bash
nanobot gateway
```

If `NapCat` is already running and both ports are enabled, `nanobot` should connect automatically.

## Verification Checklist

### Text Message Test

1. send a plain text private message from a friend account to the logged-in QQ account
2. confirm nanobot replies

Expected:

- inbound event arrives on `qq_personal`
- outbound reply is sent through `send_private_msg`

### File Receive Test

1. send a file to the QQ account
2. confirm the file is downloaded into nanobot's `media/qq_personal/<user_id>/` directory
3. confirm the agent sees it as inbound media

Expected:

- nanobot receives a local media path
- message text includes a `[file: ...]` or `[image: ...]` style tag

### File Send Test

Ask the agent to send a file using the message tool, or trigger a workflow that produces a file.

Expected:

- `qq_personal` converts the file path into a OneBot file segment
- the friend receives the file in QQ

### Group Message Test

1. add the bot account to a QQ group
2. with `groupPolicy = "mention"`, send a plain group message without mentioning the bot
3. confirm nanobot stays silent
4. send another group message that explicitly `@` mentions the bot
5. confirm nanobot replies in the group

Expected:

- unmentioned group traffic is ignored in `mention` mode
- mentioned group traffic is processed and replied to

### Group File Test

1. send a file in a QQ group where the bot is active
2. confirm the file is downloaded into nanobot's `media/qq_personal/<group_id>/` directory
3. trigger a workflow that sends a file back to the same group

Expected:

- inbound group file is resolved through `get_group_file_url`
- outbound group file is sent through `send_group_msg`

## Useful NapCat API Notes

Relevant upstream interfaces:

- `send_private_msg`
- `send_group_msg`
- `get_private_file_url`
- `get_group_file_url`
- `get_file`

NapCat docs also expose:

- private image sending through `send_private_msg`
- private file sending through `send_private_msg`
- group image or file sending through `send_group_msg`
- `upload_private_file`
- `upload_group_file`

The current `nanobot` implementation uses `send_private_msg` and `send_group_msg` for outbound text and media, and uses `get_private_file_url` / `get_group_file_url` for inbound file download.

## Troubleshooting

### WebUI Opens But Cannot Log In

- re-check the refreshed token in console or `webui.json`
- ensure the QQ account scan completed

### nanobot Cannot Connect To WebSocket

- confirm the NapCat WS server is enabled
- confirm `wsUrl` matches host and port exactly
- confirm the token matches

### nanobot Can Receive But Not Send

- confirm the HTTP server is enabled
- confirm `httpUrl` matches host and port exactly
- confirm the token matches

### File Receive Fails

- confirm NapCat is reporting messages in `array` format
- confirm the file message includes enough file metadata for `get_private_file_url`
- confirm the file is still available in NapCat's cache window

### File Send Fails

- confirm the local file path really exists
- confirm the file type is supported by QQ and NapCat
- if needed, test first with a small image or a small text file

## Security Recommendations

- Bind HTTP and WS to `127.0.0.1` unless you really need remote access
- Always set a token if the service is reachable beyond localhost
- Keep `allowFrom` narrow after initial testing
- Avoid running WebUI exposed on a public interface without protection

## References

- NapCat official site: <https://napneko.github.io/>
- NapCat install guide: <https://napneko.github.io/guide/install>
- NapCat Shell guide: <https://napneko.github.io/guide/boot/Shell>
- NapCat Framework guide: <https://napneko.github.io/guide/boot/Framework>
- NapCat WebUI config guide: <https://napneko.github.io/config/basic>
- NapCat OneBot network guide: <https://napneko.github.io/onebot/network>
- NapCat file guide: <https://napneko.github.io/develop/file>
- NapCat API index: <https://napcat.apifox.cn/>
- NapCat releases: <https://github.com/NapNeko/NapCatQQ/releases>
