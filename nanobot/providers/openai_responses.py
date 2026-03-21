"""Helpers for adapting chat-style requests to the OpenAI Responses API."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import json_repair

from nanobot.providers.base import LLMResponse, ToolCallRequest

_FINISH_REASON_MAP = {
    "completed": "stop",
    "incomplete": "length",
    "failed": "error",
    "cancelled": "error",
}


def convert_tools_to_responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert chat-completions function tools to Responses API tools."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = (tool.get("function") or {}) if tool.get("type") == "function" else tool
        name = fn.get("name")
        if not name:
            continue
        params = fn.get("parameters") or {}
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": fn.get("description") or "",
                "parameters": params if isinstance(params, dict) else {},
            }
        )
    return converted


def convert_tool_choice_to_responses(
    tool_choice: str | dict[str, Any] | None,
) -> str | dict[str, Any] | None:
    """Normalize chat-style tool_choice payloads for the Responses API."""
    if tool_choice is None or isinstance(tool_choice, str):
        return tool_choice

    if tool_choice.get("type") == "function":
        fn = tool_choice.get("function") or {}
        name = tool_choice.get("name") or fn.get("name")
        if name:
            return {"type": "function", "name": name}

    return tool_choice


def convert_messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Convert chat-style history into Responses API instructions + input items."""
    system_parts: list[str] = []
    input_items: list[dict[str, Any]] = []

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            text = _content_to_text(content)
            if text:
                system_parts.append(text)
            continue

        if role == "user":
            input_items.append(_convert_user_message(content))
            continue

        if role == "assistant":
            text = _content_to_text(content)
            if text:
                input_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                        "status": "completed",
                        "id": f"msg_{idx}",
                    }
                )
            for tool_call in msg.get("tool_calls", []) or []:
                fn = tool_call.get("function") or {}
                call_id, item_id = split_tool_call_id(tool_call.get("id"))
                arguments = fn.get("arguments") or "{}"
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                input_items.append(
                    {
                        "type": "function_call",
                        "id": item_id or f"fc_{idx}",
                        "call_id": call_id or f"call_{idx}",
                        "name": fn.get("name"),
                        "arguments": arguments,
                    }
                )
            continue

        if role == "tool":
            call_id, _ = split_tool_call_id(msg.get("tool_call_id"))
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _content_to_text(content, default=""),
                }
            )

    return "\n\n".join(system_parts), input_items


def build_prompt_cache_key(messages: list[dict[str, Any]], model: str | None = None) -> str:
    """Build a stable prompt cache key for one conversation when possible."""
    scope = _extract_runtime_scope(messages)
    if scope:
        raw = json.dumps(
            {"model": model or "", "scope": scope},
            ensure_ascii=True,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    raw = json.dumps(messages, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def split_tool_call_id(tool_call_id: Any) -> tuple[str, str | None]:
    """Split our persisted tool_call_id into Responses API call/item IDs."""
    if isinstance(tool_call_id, str) and tool_call_id:
        if "|" in tool_call_id:
            call_id, item_id = tool_call_id.split("|", 1)
            return call_id, item_id or None
        return tool_call_id, None
    return "call_0", None


def join_tool_call_id(call_id: str | None, item_id: str | None) -> str:
    """Rebuild the persisted tool_call_id from Responses API identifiers."""
    call_id = call_id or "call_0"
    if item_id:
        return f"{call_id}|{item_id}"
    return call_id


def map_responses_finish_reason(status: str | None) -> str:
    """Map Responses API status values into the shared LLMResponse finish reasons."""
    return _FINISH_REASON_MAP.get(status or "completed", "stop")


def parse_responses_api_response(response: Any) -> LLMResponse:
    """Parse a non-streaming Responses API object into the shared response model."""
    error = getattr(response, "error", None)
    if error is not None:
        message = getattr(error, "message", None) or str(error)
        return LLMResponse(content=f"Error: {message}", finish_reason="error")

    tool_calls: list[ToolCallRequest] = []
    reasoning_content: str | None = None
    for item in getattr(response, "output", []) or []:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            tool_calls.append(
                ToolCallRequest(
                    id=join_tool_call_id(
                        getattr(item, "call_id", None),
                        getattr(item, "id", None),
                    ),
                    name=getattr(item, "name", None) or "",
                    arguments=_parse_tool_arguments(getattr(item, "arguments", None)),
                )
            )
        elif item_type == "reasoning" and reasoning_content is None:
            reasoning_content = _extract_reasoning_content(item)

    usage = _map_usage(getattr(response, "usage", None))
    content = getattr(response, "output_text", None) or None
    finish_reason = map_responses_finish_reason(getattr(response, "status", None))
    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        reasoning_content=reasoning_content,
    )


def _convert_user_message(content: Any) -> dict[str, Any]:
    if isinstance(content, str):
        return {"role": "user", "content": [{"type": "input_text", "text": content}]}

    if isinstance(content, list):
        converted: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"text", "input_text", "output_text"}:
                converted.append({"type": "input_text", "text": item.get("text", "")})
            elif item_type == "image_url":
                url = (item.get("image_url") or {}).get("url")
                if url:
                    converted.append({"type": "input_image", "image_url": url, "detail": "auto"})
            elif item_type == "input_image" and item.get("image_url"):
                converted.append(
                    {
                        "type": "input_image",
                        "image_url": item.get("image_url"),
                        "detail": item.get("detail", "auto"),
                    }
                )
        if converted:
            return {"role": "user", "content": converted}

    return {"role": "user", "content": [{"type": "input_text", "text": ""}]}


def _extract_runtime_scope(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        scope = _extract_runtime_scope_from_content(message.get("content"))
        if scope:
            return scope
    return None


def _extract_runtime_scope_from_content(content: Any) -> str | None:
    if isinstance(content, str):
        return _extract_runtime_scope_from_text(content)

    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"text", "input_text", "output_text"}:
                scope = _extract_runtime_scope_from_text(item.get("text", ""))
                if scope:
                    return scope
    return None


def _extract_runtime_scope_from_text(text: Any) -> str | None:
    if not isinstance(text, str) or "[Runtime Context" not in text:
        return None

    channel: str | None = None
    chat_id: str | None = None
    for line in text.splitlines():
        if line.startswith("Channel: "):
            channel = line[len("Channel: "):].strip()
        elif line.startswith("Chat ID: "):
            chat_id = line[len("Chat ID: "):].strip()
        elif not line.strip() and (channel or chat_id):
            break

    if channel and chat_id:
        return f"{channel}:{chat_id}"
    if channel:
        return channel
    if chat_id:
        return chat_id
    return None


def _content_to_text(content: Any, default: str = "") -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "input_text", "output_text"}:
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
        if texts:
            return "".join(texts)
    if content is None:
        return default
    return json.dumps(content, ensure_ascii=False)


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, str):
        try:
            parsed = json_repair.loads(arguments)
        except Exception:
            return {"raw": arguments}
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    return arguments if isinstance(arguments, dict) else {"raw": arguments}


def _extract_reasoning_content(item: Any) -> str | None:
    encrypted = getattr(item, "encrypted_content", None)
    if isinstance(encrypted, str) and encrypted:
        return encrypted

    texts: list[str] = []
    for part in getattr(item, "summary", []) or []:
        if isinstance(part, dict):
            text = part.get("text")
        else:
            text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            texts.append(text)
    return "\n".join(texts) or None


def _map_usage(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}

    def _get(name: str) -> Any:
        if isinstance(usage, dict):
            return usage.get(name)
        return getattr(usage, name, None)

    prompt_tokens = _get("input_tokens")
    completion_tokens = _get("output_tokens")
    total_tokens = _get("total_tokens")

    mapped = {
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int(total_tokens or 0),
    }
    return mapped if any(mapped.values()) else {}
