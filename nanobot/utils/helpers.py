"""Utility functions for nanobot."""

import base64
import binascii
import io
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import tiktoken

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - Pillow is optional at import time
    Image = None
    ImageOps = None

if Image is not None:
    _RESAMPLING_LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
else:  # pragma: no cover - Pillow unavailable
    _RESAMPLING_LANCZOS = None


LLM_INLINE_IMAGE_MAX_BYTES = 7_500_000
LLM_INLINE_IMAGE_MAX_EDGE = 2048
LLM_INLINE_IMAGE_MIN_EDGE = 512
LLM_INLINE_IMAGE_DEFAULT_JPEG_QUALITY = 85
LLM_INLINE_IMAGE_MIN_JPEG_QUALITY = 55


def strip_think(text: str) -> str:
    """Remove <think>…</think> blocks and any unclosed trailing <think> tag."""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"<think>[\s\S]*$", "", text)
    return text.strip()


def detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes, ignoring file extension."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def prepare_image_for_llm(
    raw: bytes,
    mime: str | None = None,
    *,
    max_bytes: int = LLM_INLINE_IMAGE_MAX_BYTES,
    max_edge: int = LLM_INLINE_IMAGE_MAX_EDGE,
) -> tuple[bytes, str]:
    """Normalize an image for inline LLM transport.

    We keep images under a conservative byte budget so the base64 data URI
    stays below provider limits, and we bound the maximum edge length so large
    screenshots do not balloon token/transport cost.
    """
    detected_mime = mime or detect_image_mime(raw) or "application/octet-stream"
    if not detected_mime.startswith("image/") or Image is None:
        return raw, detected_mime

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = opened.copy()
    except Exception:
        return raw, detected_mime

    if ImageOps is not None:
        try:
            image = ImageOps.exif_transpose(image)
        except Exception:
            pass

    width, height = image.size
    if width <= 0 or height <= 0:
        return raw, detected_mime
    if len(raw) <= max_bytes and max(width, height) <= max_edge:
        return raw, detected_mime

    image.load()
    best_bytes = raw
    best_mime = detected_mime
    current_edge = min(max(width, height), max_edge)
    quality = LLM_INLINE_IMAGE_DEFAULT_JPEG_QUALITY

    while True:
        resized = _resize_image_to_edge(image, current_edge)
        candidates = _encode_image_candidates(
            resized,
            detected_mime,
            quality=quality,
        )
        if candidates:
            encoded_bytes, encoded_mime = min(candidates, key=lambda item: len(item[0]))
            if len(encoded_bytes) < len(best_bytes):
                best_bytes = encoded_bytes
                best_mime = encoded_mime
            if len(encoded_bytes) <= max_bytes:
                return encoded_bytes, encoded_mime

        if current_edge <= LLM_INLINE_IMAGE_MIN_EDGE and quality <= LLM_INLINE_IMAGE_MIN_JPEG_QUALITY:
            return best_bytes, best_mime

        if current_edge > LLM_INLINE_IMAGE_MIN_EDGE:
            current_edge = max(int(current_edge * 0.85), LLM_INLINE_IMAGE_MIN_EDGE)
        if quality > LLM_INLINE_IMAGE_MIN_JPEG_QUALITY:
            quality = max(quality - 5, LLM_INLINE_IMAGE_MIN_JPEG_QUALITY)


def normalize_message_image_blocks_for_llm(
    messages: list[dict[str, Any]],
    *,
    max_bytes: int = LLM_INLINE_IMAGE_MAX_BYTES,
    max_edge: int = LLM_INLINE_IMAGE_MAX_EDGE,
) -> list[dict[str, Any]]:
    """Rewrite inline data-uri images in messages to fit LLM transport limits."""
    changed = False
    normalized_messages: list[dict[str, Any]] = []

    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            normalized_messages.append(message)
            continue

        normalized_content: list[Any] = []
        content_changed = False
        for block in content:
            normalized_block = _normalize_message_image_block(
                block,
                max_bytes=max_bytes,
                max_edge=max_edge,
            )
            if normalized_block is not block:
                content_changed = True
            normalized_content.append(normalized_block)

        if content_changed:
            normalized_messages.append({**message, "content": normalized_content})
            changed = True
        else:
            normalized_messages.append(message)

    return normalized_messages if changed else messages


def _normalize_message_image_block(
    block: Any,
    *,
    max_bytes: int,
    max_edge: int,
) -> Any:
    if not isinstance(block, dict):
        return block

    block_type = block.get("type")
    if block_type == "image_url":
        image_url = block.get("image_url") or {}
        url = image_url.get("url")
        if not isinstance(url, str):
            return block
        normalized = _normalize_data_uri_image(url, max_bytes=max_bytes, max_edge=max_edge)
        if normalized is None or normalized == url:
            return block
        return {
            **block,
            "image_url": {**image_url, "url": normalized},
        }

    if block_type == "input_image":
        url = block.get("image_url")
        if not isinstance(url, str):
            return block
        normalized = _normalize_data_uri_image(url, max_bytes=max_bytes, max_edge=max_edge)
        if normalized is None or normalized == url:
            return block
        return {
            **block,
            "image_url": normalized,
        }

    return block


def _normalize_data_uri_image(
    url: str,
    *,
    max_bytes: int,
    max_edge: int,
) -> str | None:
    match = re.match(r"^data:(image/[\w.+-]+);base64,(.+)$", url, re.DOTALL)
    if not match:
        return None

    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error):
        return None

    normalized_raw, normalized_mime = prepare_image_for_llm(
        raw,
        match.group(1),
        max_bytes=max_bytes,
        max_edge=max_edge,
    )
    if normalized_raw == raw and normalized_mime == match.group(1):
        return url

    encoded = base64.b64encode(normalized_raw).decode()
    return f"data:{normalized_mime};base64,{encoded}"


def _resize_image_to_edge(image: Any, max_edge: int) -> Any:
    width, height = image.size
    if max(width, height) <= max_edge:
        return image.copy()
    scale = max_edge / float(max(width, height))
    new_size = (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )
    return image.resize(new_size, _RESAMPLING_LANCZOS)


def _encode_image_candidates(
    image: Any,
    source_mime: str,
    *,
    quality: int,
) -> list[tuple[bytes, str]]:
    candidates: list[tuple[bytes, str]] = []
    has_alpha = image.mode in {"RGBA", "LA"} or ("transparency" in image.info)

    if source_mime in {"image/png", "image/gif", "image/webp"} and has_alpha:
        png_bytes = _encode_image(image, fmt="PNG")
        if png_bytes is not None:
            candidates.append((png_bytes, "image/png"))

    if source_mime == "image/png" and not has_alpha:
        png_bytes = _encode_image(image, fmt="PNG")
        if png_bytes is not None:
            candidates.append((png_bytes, "image/png"))

    jpeg_ready = image
    if jpeg_ready.mode not in {"RGB", "L"}:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, color="white")
        jpeg_ready = Image.alpha_composite(background, rgba).convert("RGB")
    elif jpeg_ready.mode == "L":
        jpeg_ready = jpeg_ready.convert("RGB")

    jpeg_bytes = _encode_image(jpeg_ready, fmt="JPEG", quality=quality)
    if jpeg_bytes is not None:
        candidates.append((jpeg_bytes, "image/jpeg"))

    return candidates


def _encode_image(image: Any, *, fmt: str, quality: int | None = None) -> bytes | None:
    buffer = io.BytesIO()
    try:
        if fmt == "PNG":
            image.save(buffer, format="PNG", optimize=True)
        elif fmt == "JPEG":
            image.save(
                buffer,
                format="JPEG",
                quality=quality or LLM_INLINE_IMAGE_DEFAULT_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
        else:
            return None
    except Exception:
        return None
    return buffer.getvalue()


def build_image_content_blocks(raw: bytes, mime: str, path: str, label: str) -> list[dict[str, Any]]:
    """Build native image blocks plus a short text label."""
    normalized_raw, normalized_mime = prepare_image_for_llm(raw, mime)
    b64 = base64.b64encode(normalized_raw).decode()
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{normalized_mime};base64,{b64}"},
            "_meta": {"path": path},
        },
        {"type": "text", "text": label},
    ]


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def timestamp() -> str:
    """Current ISO timestamp."""
    return datetime.now().isoformat()


def current_time_str(timezone: str | None = None) -> str:
    """Human-readable current time with weekday and UTC offset.

    When *timezone* is a valid IANA name (e.g. ``"Asia/Shanghai"``), the time
    is converted to that zone.  Otherwise falls back to the host local time.
    """
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(timezone) if timezone else None
    except (KeyError, Exception):
        tz = None

    now = datetime.now(tz=tz) if tz else datetime.now().astimezone()
    offset = now.strftime("%z")
    offset_fmt = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    tz_name = timezone or (time.strftime("%Z") or "UTC")
    return f"{now.strftime('%Y-%m-%d %H:%M (%A)')} ({tz_name}, UTC{offset_fmt})"


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')

def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores."""
    return _UNSAFE_CHARS.sub("_", name).strip()


def split_message(content: str, max_len: int = 2000) -> list[str]:
    """
    Split content into chunks within max_len, preferring line breaks.

    Args:
        content: The text content to split.
        max_len: Maximum length per chunk (default 2000 for Discord compatibility).

    Returns:
        List of message chunks, each within max_len.
    """
    if not content:
        return []
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        # Try to break at newline first, then space, then hard break
        pos = cut.rfind('\n')
        if pos <= 0:
            pos = cut.rfind(' ')
        if pos <= 0:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


def build_assistant_message(
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
    thinking_blocks: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a provider-safe assistant message with optional reasoning fields."""
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning_content is not None:
        msg["reasoning_content"] = reasoning_content
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    return msg


def estimate_prompt_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Estimate prompt tokens with tiktoken.

    Counts all fields that providers send to the LLM: content, tool_calls,
    reasoning_content, tool_call_id, name, plus per-message framing overhead.
    """
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        txt = part.get("text", "")
                        if txt:
                            parts.append(txt)

            tc = msg.get("tool_calls")
            if tc:
                parts.append(json.dumps(tc, ensure_ascii=False))

            rc = msg.get("reasoning_content")
            if isinstance(rc, str) and rc:
                parts.append(rc)

            for key in ("name", "tool_call_id"):
                value = msg.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)

        if tools:
            parts.append(json.dumps(tools, ensure_ascii=False))

        per_message_overhead = len(messages) * 4
        return len(enc.encode("\n".join(parts))) + per_message_overhead
    except Exception:
        return 0


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate prompt tokens contributed by one persisted message."""
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    parts.append(text)
            else:
                parts.append(json.dumps(part, ensure_ascii=False))
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False))

    for key in ("name", "tool_call_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    if message.get("tool_calls"):
        parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))

    rc = message.get("reasoning_content")
    if isinstance(rc, str) and rc:
        parts.append(rc)

    payload = "\n".join(parts)
    if not payload:
        return 4
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return max(4, len(enc.encode(payload)) + 4)
    except Exception:
        return max(4, len(payload) // 4 + 4)


def estimate_prompt_tokens_chain(
    provider: Any,
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """Estimate prompt tokens via provider counter first, then tiktoken fallback."""
    provider_counter = getattr(provider, "estimate_prompt_tokens", None)
    if callable(provider_counter):
        try:
            tokens, source = provider_counter(messages, tools, model)
            if isinstance(tokens, (int, float)) and tokens > 0:
                return int(tokens), str(source or "provider_counter")
        except Exception:
            pass

    estimated = estimate_prompt_tokens(messages, tools)
    if estimated > 0:
        return int(estimated), "tiktoken"
    return 0, "none"


def build_status_content(
    *,
    version: str,
    model: str,
    start_time: float,
    last_usage: dict[str, int],
    context_window_tokens: int,
    session_msg_count: int,
    context_tokens_estimate: int,
) -> str:
    """Build a human-readable runtime status snapshot."""
    uptime_s = int(time.time() - start_time)
    uptime = (
        f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m"
        if uptime_s >= 3600
        else f"{uptime_s // 60}m {uptime_s % 60}s"
    )
    last_in = last_usage.get("prompt_tokens", 0)
    last_out = last_usage.get("completion_tokens", 0)
    ctx_total = max(context_window_tokens, 0)
    ctx_pct = int((context_tokens_estimate / ctx_total) * 100) if ctx_total > 0 else 0
    ctx_used_str = f"{context_tokens_estimate // 1000}k" if context_tokens_estimate >= 1000 else str(context_tokens_estimate)
    ctx_total_str = f"{ctx_total // 1024}k" if ctx_total > 0 else "n/a"
    return "\n".join([
        f"\U0001f408 nanobot v{version}",
        f"\U0001f9e0 Model: {model}",
        f"\U0001f4ca Tokens: {last_in} in / {last_out} out",
        f"\U0001f4da Context: {ctx_used_str}/{ctx_total_str} ({ctx_pct}%)",
        f"\U0001f4ac Session: {session_msg_count} messages",
        f"\u23f1 Uptime: {uptime}",
    ])


def sync_workspace_templates(workspace: Path, silent: bool = False) -> list[str]:
    """Sync bundled templates to workspace. Only creates missing files."""
    from importlib.resources import files as pkg_files
    try:
        tpl = pkg_files("nanobot") / "templates"
    except Exception:
        return []
    if not tpl.is_dir():
        return []

    added: list[str] = []

    def _write(src, dest: Path):
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    for item in tpl.iterdir():
        if item.name.endswith(".md") and not item.name.startswith("."):
            _write(item, workspace / item.name)
    _write(tpl / "memory" / "MEMORY.md", workspace / "memory" / "MEMORY.md")
    _write(None, workspace / "memory" / "HISTORY.md")
    (workspace / "skills").mkdir(exist_ok=True)

    if added and not silent:
        from rich.console import Console
        for name in added:
            Console().print(f"  [dim]Created {name}[/dim]")
    return added
