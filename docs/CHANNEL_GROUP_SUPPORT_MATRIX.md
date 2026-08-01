# Channel Group Support Matrix

Status: draft
Date: 2026-03-28

This matrix summarizes **current code behavior** in the repository. It is meant to answer:

- whether a channel can receive and respond to group messages
- whether group replies are gated by mentions or policy
- whether thread or topic context is preserved
- whether group file flow appears supported

## Summary Matrix

| Channel | Group Message Support | Default Group Policy | Thread / Topic Scope | Group File Flow | Notes |
| --- | --- | --- | --- | --- | --- |
| Discord | Yes | `mention` | No explicit thread session handling | Partial | Group messages only respond when mentioned unless `groupPolicy=open`. |
| Telegram | Yes | `mention` | Yes | Partial | Supports groups and forum topics; also accepts replies to the bot as a trigger. |
| Slack | Yes | `mention` | Yes | Partial | Supports `open`, `mention`, and `allowlist`; channel messages can be thread-scoped. |
| Matrix | Yes | `open` | Yes | Partial | Supports `open`, `mention`, and `allowlist`; room mention handling is configurable. |
| Feishu | Yes | `mention` | Yes | Partial | Supports `open` and `mention`; thread metadata is preserved. |
| WhatsApp | Yes | `open` | No dedicated thread model | Partial | Supports `open` and `mention` for group chats. |
| DingTalk | Yes | No dedicated group policy | No dedicated thread model | Partial | Supports both 1:1 and group chats; group reply routing uses `group:<id>`. |
| WeCom | Yes | No dedicated group policy | No dedicated thread model | Unknown | Code distinguishes single vs group chat via `chat_type` and `chatid`. |
| QQ Bot (开放平台) | Yes | Event-driven `@` entrypoint | No dedicated thread model | Partial | Code supports C2C and group `@` messages even though older README text used to say private only. |
| QQ Personal (NapCat / OneBot) | Yes | `mention` | No dedicated thread model | Partial | Supports private and group messages; group replies are mention-gated by default and can be set to `open` or `allowlist`. |
| Weixin | Not evident in current code | N/A | N/A | Private only today | Current implementation uses the sender as `chat_id`, which looks like a 1:1 flow. |
| Email | Not applicable | N/A | N/A | Attachments in mail flow | Email is not a group chat channel in the same sense. |

## Code Pointers

### Discord

- Group policy config: `group_policy: Literal["mention", "open"]`
- Mention check: `_should_respond_in_group`

References:

- [discord.py](/D:/Projects/AI/noabot/nanobot/channels/discord.py)

### Telegram

- Group policy config: `group_policy: Literal["open", "mention"]`
- Group trigger logic: `_is_group_message_for_bot`
- Topic-scoped session key: `_derive_topic_session_key`

References:

- [telegram.py](/D:/Projects/AI/noabot/nanobot/channels/telegram.py)

### Slack

- Group policy config: `group_policy`, `group_allow_from`
- Group trigger logic: `_should_respond_in_channel`
- Channel thread session key: `slack:{chat_id}:{thread_ts}`

References:

- [slack.py](/D:/Projects/AI/noabot/nanobot/channels/slack.py)

### Matrix

- Group policy config: `group_policy: Literal["open", "mention", "allowlist"]`
- Mention detection: `_is_bot_mentioned`
- Room-scoped handling: `chat_id=room.room_id`

References:

- [matrix.py](/D:/Projects/AI/noabot/nanobot/channels/matrix.py)

### Feishu

- Group policy config: `group_policy: Literal["open", "mention"]`
- Group trigger logic: `_is_group_message_for_bot`
- Thread metadata: `thread_id`

References:

- [feishu.py](/D:/Projects/AI/noabot/nanobot/channels/feishu.py)

### WhatsApp

- Group policy config: `group_policy: Literal["open", "mention"]`
- Group event fields: `isGroup`, `wasMentioned`

References:

- [whatsapp.py](/D:/Projects/AI/noabot/nanobot/channels/whatsapp.py)

### DingTalk

- Code comment states both private and group chats are supported
- Group routing uses `group:<conversation_id>`

References:

- [dingtalk.py](/D:/Projects/AI/noabot/nanobot/channels/dingtalk.py)

### WeCom

- `chat_type = body.get("chattype", "single")`
- `chat_id = body.get("chatid", sender_id)`

References:

- [wecom.py](/D:/Projects/AI/noabot/nanobot/channels/wecom.py)

### QQ Bot (开放平台)

- Group event hook: `on_group_at_message_create`
- Startup log says `C2C & Group supported`
- Group routing uses `group_openid`

References:

- [qq.py](/D:/Projects/AI/noabot/nanobot/channels/qq.py)
- [test_qq_channel.py](/D:/Projects/AI/noabot/tests/channels/test_qq_channel.py)

### QQ Personal

- Accepts OneBot `private` and `group` events
- Outbound path uses `send_private_msg` and `send_group_msg`
- Group replies are gated by `groupPolicy`

References:

- [qq_personal.py](/D:/Projects/AI/noabot/nanobot/channels/qq_personal.py)

## QQ Personal Group Support Feasibility

`qq_personal` is custom code on top of a OneBot 11 compatible bridge, and this repository now implements group support on top of those upstream capabilities.

As of 2026-03-28, NapCat official docs show support for:

- `send_group_msg`
- group `file` message segments
- `upload_group_file`
- `get_group_file_url`

Primary upstream references:

- NapCat API compatibility: <https://napneko.github.io/develop/api>
- NapCat message compatibility: <https://napneko.github.io/develop/msg>
- NapCat file handling guide: <https://napneko.github.io/develop/file>
- NapCat API index: <https://napcat.apifox.cn/>

This means the implemented `qq_personal` group path can reasonably support:

- inbound group text
- mention-gated or open group replies
- group file receive
- group file send

## Current Recommendation

Treat `qq_personal` as:

- stable for friend private chat
- group-capable with `mention`, `open`, or `allowlist` policy
- technically extensible further without changing nanobot's overall channel architecture
