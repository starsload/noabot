"""Utility functions for nanobot."""

import base64
import binascii
import io
import json
import os
import re
import shutil
import stat
import time
import uuid
from contextlib import suppress
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar, cast, overload

import tiktoken
from loguru import logger

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
ESTIMATED_TOKENS_PER_INLINE_IMAGE = 1024


def strip_think(text: str) -> str:
    """Remove thinking blocks, unclosed trailing tags, and tokenizer-level
    template leaks occasionally emitted by some models (notably Gemma 4's
    Ollama renderer).

    Covers:
      1. Well-formed `<think>...</think>`, `<thinking>...</thinking>`,
         and `<thought>...</thought>` blocks.
      2. Streaming prefixes where the block is never closed.
      3. *Malformed* opening tags missing the `>` — e.g. `<think广场…`. The
         model sometimes emits the tag name directly followed by user-facing
         content with no delimiter; without this step the literal `<think`
         leaks into the rendered message.
      4. Harmony-style channel markers like `<channel|>` / `<|channel|>`
         **at the start of the text** — conservative to avoid eating
         explanatory prose that mentions these tokens.
      5. Orphan closing tags `</think>` / `</thinking>` / `</thought>`
         **at the very start or end of the text** only, for the same reason.
      6. Trailing partial control tags split across stream chunks, such as
         `<thi`, `<thin`, or `<tho`.

    Since this is also applied before persisting to history (memory.py),
    the edge-only stripping of (4) and (5) is deliberate: stripping those
    tokens mid-text would silently rewrite any message where a user or the
    assistant discusses the tokens themselves.
    """
    # Well-formed blocks first.
    text = re.sub(rf"<(?P<tag>{_THINKING_TAG})>[\s\S]*?</(?P=tag)>", "", text)
    text = re.sub(rf"^\s*<{_THINKING_TAG}>[\s\S]*$", "", text)
    # Self-closing `<thinking/>` is an empty marker, not user-visible text.
    text = re.sub(rf"^\s*<{_INLINE_SELF_CLOSING_THINKING_TAG}/>\s*", "", text)
    text = re.sub(rf"\s*<{_INLINE_SELF_CLOSING_THINKING_TAG}/>\s*$", "", text)
    # Malformed opening tags: `<think` / `<thinking` / `<thought` where the next char is
    # NOT one that could continue a valid tag / identifier name. Explicitly
    # listing ASCII tag-name chars (letters, digits, `_`, `-`, `:`) plus
    # `>` / `/` — we can't use `\w` here because in Python's default
    # Unicode regex mode it matches CJK characters too, which would defeat
    # the primary fix for `<think广场…` leaks.
    text = re.sub(rf"<{_THINKING_TAG}(?![A-Za-z0-9_\-:>/])", "", text)
    # Edge-only orphan closing tags (start or end of text).
    text = re.sub(rf"^\s*</{_THINKING_TAG}>\s*", "", text)
    text = re.sub(rf"\s*</{_THINKING_TAG}>\s*$", "", text)
    # Edge-only channel markers (harmony / Gemma 4 variant leaks).
    text = re.sub(r"^\s*<\|?channel\|?>\s*", "", text)
    # Stream chunks may end in the middle of a control tag. Strip only known
    # control-token prefixes at the very end.
    partial_control_tag = (
        rf"{_PARTIAL_THINKING_TAG}|"
        r"<\|?(?:c|ch|cha|chan|chann|channe|channel)(?:\|?>?)?"
    )
    text = re.sub(rf"(?:{partial_control_tag})$", "", text)
    text = re.sub(r"^\s*<\|?$", "", text)
    return text.strip()


def strip_reasoning_tags(text: object) -> str:
    """Remove wrapper tags from text that is already known to be reasoning."""
    if not isinstance(text, str):
        return ""
    text = re.sub(rf"^\s*<{_THINKING_TAG}/>\s*", "", text)
    text = re.sub(rf"\s*<{_THINKING_TAG}/>\s*$", "", text)
    text = re.sub(rf"^\s*<{_THINKING_TAG}>\s*", "", text)
    text = re.sub(rf"\s*</{_THINKING_TAG}>\s*$", "", text)
    text = re.sub(rf"\s*(?:{_PARTIAL_THINKING_TAG})$", "", text)
    return text.strip()


def extract_think(text: str) -> tuple[str | None, str]:
    """Extract thinking content from inline thinking tags.

    Returns ``(thinking_text, cleaned_text)``. Only closed blocks are
    extracted; unclosed streaming prefixes are stripped from the cleaned
    text but not surfaced — :func:`strip_think` handles that case.
    """
    parts: list[str] = []
    for m in re.finditer(rf"<(?P<tag>{_THINKING_TAG})>([\s\S]*?)</(?P=tag)>", text):
        parts.append(m.group(2).strip())
    thinking = "\n\n".join(parts) if parts else None
    return thinking, strip_think(text)


class IncrementalThinkExtractor:
    """Stateful inline ``<think>`` extractor for streaming buffers.

    Streaming providers expose only a single content delta channel. When a
    model embeds reasoning in ``<think>...</think>`` blocks inside that
    channel, callers need to surface the reasoning incrementally as it
    arrives without re-emitting earlier text. This holds the "already
    emitted" cursor so the runner and the loop hook share one shape.
    """

    __slots__ = ("_emitted",)

    def __init__(self) -> None:
        self._emitted = ""

    def reset(self) -> None:
        self._emitted = ""

    async def feed(self, buf: str, emit: Any) -> bool:
        """Emit any new thinking text found in ``buf``.

        Returns True if anything was emitted this call. ``emit`` is an
        async callable taking a single string (typically
        ``hook.emit_reasoning``).
        """
        thinking, _ = extract_think(buf)
        if not thinking or thinking == self._emitted:
            return False
        new = thinking[len(self._emitted) :].strip()
        self._emitted = thinking
        if not new:
            return False
        await emit(new)
        return True


def extract_reasoning(
    reasoning_content: str | None,
    thinking_blocks: list[dict[str, Any]] | None,
    content: str | None,
) -> tuple[str | None, str | None]:
    """Return ``(reasoning_text, cleaned_content)`` from one model response.

    Single source of truth for "what reasoning did this response carry, and
    what answer text remains after we peel it out". Fallback order:

    1. Dedicated ``reasoning_content`` (DeepSeek-R1, Kimi, MiMo, OpenAI
       reasoning models, Bedrock).
    2. Anthropic ``thinking_blocks``.
    3. Inline ``<think>`` / ``<thought>`` blocks in ``content``.

    Only one source contributes per response; lower-priority sources are
    ignored if a higher-priority one is present, but inline ``<think>``
    tags are still stripped from ``content`` so they never leak into the
    final answer.
    """
    if reasoning_content:
        return strip_reasoning_tags(reasoning_content), strip_think(content) if content else content
    if thinking_blocks:
        parts = [
            strip_reasoning_tags(tb.get("thinking", ""))
            for tb in thinking_blocks
            if tb.get("type") == "thinking"
        ]
        joined = "\n\n".join(p for p in parts if p)
        return (joined or None), strip_think(content) if content else content
    if content:
        return extract_think(content)
    return None, content


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


def image_block_placeholder(block: dict[str, Any]) -> str | None:
    """Return a compact text placeholder for a multimodal image block."""
    if not isinstance(block, dict):
        return None

    block_type = block.get("type")
    if block_type not in {"image_url", "input_image"}:
        return None

    path = (block.get("_meta") or {}).get("path", "")
    if path:
        return f"[image: {path}]"
    return "[image]"


def replace_image_blocks_with_placeholders(content: list[Any]) -> tuple[list[Any], bool]:
    """Replace multimodal image blocks with lightweight text placeholders."""
    replaced = False
    normalized: list[Any] = []
    for part in content:
        if isinstance(part, dict):
            placeholder = image_block_placeholder(part)
            if placeholder is not None:
                normalized.append({"type": "text", "text": placeholder})
                replaced = True
                continue
        normalized.append(part)
    return normalized, replaced


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
    """Return the current time string."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone) if timezone else None
    now = datetime.now(tz=tz) if tz else datetime.now().astimezone()
    offset = now.strftime("%z")
    offset_fmt = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    tz_name = timezone or (time.strftime("%Z") or "UTC")
    return f"{now.strftime('%Y-%m-%d %H:%M (%A)')} ({tz_name}, UTC{offset_fmt})"


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_TOOL_RESULT_PREVIEW_CHARS = 1200
_TOOL_RESULTS_DIR = ".nanobot/tool-results"
_TOOL_RESULT_RETENTION_SECS = 7 * 24 * 60 * 60
_TOOL_RESULT_MAX_BUCKETS = 32
_TRUNCATED_SUFFIX = "\n... (truncated)"


def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores."""
    return _UNSAFE_CHARS.sub("_", name).strip()


def image_placeholder_text(path: str | None, *, empty: str = "[image]") -> str:
    """Build an image placeholder string."""
    return f"[image: {path}]" if path else empty


def content_with_media_breadcrumbs(
    role: str | None,
    content: Any,
    media: Any,
) -> Any:
    """Append persisted user-media breadcrumbs to plain-text content."""
    if role != "user" or not isinstance(content, str) or not isinstance(media, list):
        return content
    breadcrumbs = "\n".join(
        image_placeholder_text(path)
        for path in cast(list[object], media)
        if isinstance(path, str) and path
    )
    if not breadcrumbs:
        return content
    return f"{content}\n{breadcrumbs}" if content else breadcrumbs


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text with a stable suffix."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + _TRUNCATED_SUFFIX


def truncate_text_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to a token budget with a stable suffix.

    Unlike :func:`truncate_text`, this measures actual tokens, so the cap holds
    regardless of language or content (CJK and code cost more tokens per char).
    Falls back to a conservative UTF-8 byte budget if tiktoken is unavailable.
    """
    if max_tokens <= 0:
        return text
    try:
        enc = _get_token_encoding()
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        suffix_tokens = enc.encode(_TRUNCATED_SUFFIX)
        body_budget = max_tokens - len(suffix_tokens)
        if body_budget <= 0:
            return enc.decode(tokens[:max_tokens])
        for candidate_budget in range(body_budget, -1, -1):
            result = enc.decode(tokens[:candidate_budget]) + _TRUNCATED_SUFFIX
            if len(enc.encode(result)) <= max_tokens:
                return result
        return enc.decode(tokens[:max_tokens])
    except Exception:
        if len(text.encode("utf-8")) <= max_tokens:
            return text
        suffix_bytes = len(_TRUNCATED_SUFFIX.encode("utf-8"))
        if max_tokens <= suffix_bytes:
            return _truncate_text_to_utf8_bytes(text, max_tokens)
        body = _truncate_text_to_utf8_bytes(text, max_tokens - suffix_bytes)
        return body + _TRUNCATED_SUFFIX


def _truncate_text_to_utf8_bytes(text: str, max_bytes: int) -> str:
    """Return the longest code-point prefix within a UTF-8 byte budget."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def recent_message_start_index(
    messages: list[dict[str, Any]],
    max_messages: int,
    *,
    extend_to_user: bool = False,
) -> int:
    """Return the start index for a recent replay window."""
    if max_messages <= 0:
        return len(messages)
    start_idx = max(0, len(messages) - max_messages)
    if not extend_to_user or len(messages) <= max_messages:
        return start_idx
    if any(messages[i].get("role") == "user" for i in range(start_idx, len(messages))):
        return start_idx

    recovered_user = next(
        (i for i in range(start_idx - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if recovered_user is None:
        return start_idx
    if recovered_user > 0 and messages[recovered_user - 1].get("_channel_delivery"):
        return recovered_user - 1
    return recovered_user


def find_legal_message_start(messages: list[dict[str, Any]]) -> int:
    """Find the first index whose tool results have matching assistant calls."""
    declared: set[str] = set()
    start = 0
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for raw_call in cast(list[object], msg.get("tool_calls") or []):
                tool_call = cast(dict[str, Any], raw_call) if isinstance(raw_call, dict) else None
                if tool_call is not None and tool_call.get("id"):
                    declared.add(str(tool_call["id"]))
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if tid and str(tid) not in declared:
                start = i + 1
                declared.clear()
    return start


def stringify_text_blocks(content: list[object]) -> str | None:
    parts: list[str] = []
    for raw_block in content:
        if not isinstance(raw_block, dict):
            return None
        block = cast(dict[str, Any], raw_block)
        if block.get("type") != "text":
            return None
        text = block.get("text")
        if not isinstance(text, str):
            return None
        parts.append(text)
    return "\n".join(parts)


def _render_tool_result_reference(
    filepath: Path,
    *,
    original_size: int,
    preview: str,
    truncated_preview: bool,
) -> str:
    result = (
        f"[tool output persisted]\n"
        f"Full output saved to: {filepath}\n"
        f"Original size: {original_size} chars\n"
        f"Preview:\n{preview}"
    )
    if truncated_preview:
        result += "\n...\n(Read the saved file if you need the full output.)"
    return result


def _bucket_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cleanup_tool_result_buckets(root: Path, current_bucket: Path) -> None:
    siblings = [path for path in root.iterdir() if path.is_dir() and path != current_bucket]
    cutoff = time.time() - _TOOL_RESULT_RETENTION_SECS
    for path in siblings:
        if _bucket_mtime(path) < cutoff:
            shutil.rmtree(path, ignore_errors=True)
    keep = max(_TOOL_RESULT_MAX_BUCKETS - 1, 0)
    siblings = [path for path in siblings if path.exists()]
    if len(siblings) <= keep:
        return
    siblings.sort(key=_bucket_mtime, reverse=True)
    for path in siblings[keep:]:
        shutil.rmtree(path, ignore_errors=True)


def _write_text_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    existing_mode: int | None = None
    with suppress(OSError):
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            if existing_mode is not None:
                os.chmod(tmp, existing_mode)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
        with suppress(OSError, NotImplementedError):
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def maybe_persist_tool_result(
    workspace: Path | None,
    session_key: str | None,
    tool_call_id: str,
    content: Any,
    *,
    max_chars: int,
) -> Any:
    """Persist oversized tool output and replace it with a stable reference string."""
    if workspace is None or max_chars <= 0:
        return content

    text_payload: str | None = None
    suffix = "txt"
    if isinstance(content, str):
        text_payload = content
    elif isinstance(content, list):
        text_payload = stringify_text_blocks(cast(list[object], content))
        if text_payload is None:
            return cast(Any, content)
        suffix = "json"
    else:
        return content

    if len(text_payload) <= max_chars:
        return cast(Any, content)

    root = ensure_dir(workspace / _TOOL_RESULTS_DIR)
    bucket = ensure_dir(root / safe_filename(session_key or "default"))
    try:
        _cleanup_tool_result_buckets(root, bucket)
    except Exception:
        logger.exception("Failed to clean stale tool result buckets in {}", root)
    path = bucket / f"{safe_filename(tool_call_id)}.{suffix}"
    if not path.exists():
        if suffix == "json" and isinstance(content, list):
            _write_text_atomic(path, json.dumps(content, ensure_ascii=False, indent=2))
        else:
            _write_text_atomic(path, text_payload)

    preview = text_payload[:_TOOL_RESULT_PREVIEW_CHARS]
    return _render_tool_result_reference(
        path,
        original_size=len(text_payload),
        preview=preview,
        truncated_preview=len(text_payload) > _TOOL_RESULT_PREVIEW_CHARS,
    )


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
    # Non-positive max_len cannot advance the cut pointer; return unsplit.
    if max_len <= 0:
        return [content]
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        # Try to break at newline first, then space, then hard break
        pos = cut.rfind("\n")
        if pos <= 0:
            pos = cut.rfind(" ")
        if pos <= 0:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


def build_assistant_message(
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
    thinking_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a provider-safe assistant message with optional reasoning fields."""
    msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning_content is not None or thinking_blocks:
        msg["reasoning_content"] = (
            strip_reasoning_tags(reasoning_content)
            if reasoning_content is not None
            else ""
        )
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    return msg


def _estimate_prompt_tokens_with_source(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """Estimate prompt tokens and identify the counter used.

    Counts all fields that providers send to the LLM: content, tool_calls,
    reasoning_content, tool_call_id, name, plus per-message framing overhead.
    """
    parts: list[str] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for raw_part in cast(list[object], content):
                part = cast(dict[str, Any], raw_part) if isinstance(raw_part, dict) else None
                if part is not None and part.get("type") == "text":
                    text = part.get("text", "")
                    if isinstance(text, str) and text:
                        parts.append(text)

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

    message_payload = "\n".join(parts)
    per_message_overhead = len(messages) * 4
    try:
        enc = _get_token_encoding()
        tool_tokens = (
            _estimate_tools_tokens(enc, tools, leading_separator=bool(parts)) if tools else 0
        )
        message_tokens = len(enc.encode(message_payload)) if message_payload else 0
        return message_tokens + tool_tokens + per_message_overhead, "tiktoken"
    except Exception:
        tool_payload = (
            ("\n" if message_payload else "") + json.dumps(tools, ensure_ascii=False)
            if tools
            else ""
        )
        payload = message_payload + tool_payload
        estimated = len(payload.encode("utf-8"))
        return estimated + per_message_overhead, "heuristic"


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
        image_count = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            txt = part.get("text", "")
                            if txt:
                                parts.append(txt)
                            continue
                        placeholder = image_block_placeholder(part)
                        if placeholder:
                            parts.append(placeholder)
                            image_count += 1

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
        return (
            len(enc.encode("\n".join(parts)))
            + per_message_overhead
            + image_count * ESTIMATED_TOKENS_PER_INLINE_IMAGE
        )
    except Exception:
        return 0


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate prompt tokens contributed by one persisted message."""
    content = message.get("content")
    parts: list[str] = []
    image_count = 0
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        parts.append(text)
                    continue
                placeholder = image_block_placeholder(part)
                if placeholder:
                    parts.append(placeholder)
                    image_count += 1
                    continue
            else:
                parts.append(json.dumps(raw_part, ensure_ascii=False))
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
        return max(4, image_count * ESTIMATED_TOKENS_PER_INLINE_IMAGE)
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return max(4, len(enc.encode(payload)) + 4 + image_count * ESTIMATED_TOKENS_PER_INLINE_IMAGE)
    except Exception:
        return max(4, len(payload) // 4 + 4 + image_count * ESTIMATED_TOKENS_PER_INLINE_IMAGE)


def estimate_prompt_tokens_chain(
    provider: object,
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """Estimate prompt tokens via provider, tiktoken, then a byte heuristic."""
    provider_counter = getattr(provider, "estimate_prompt_tokens", None)
    if callable(provider_counter):
        with suppress(Exception):
            tokens, source = cast(tuple[object, object], provider_counter(messages, tools, model))
            if isinstance(tokens, (int, float)) and tokens > 0:
                return int(tokens), str(source or "provider_counter")
    estimated, source = _estimate_prompt_tokens_with_source(messages, tools)
    if estimated > 0:
        return int(estimated), source
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
    search_usage_text: str | None = None,
    active_task_count: int = 0,
    max_completion_tokens: int = 8192,
) -> str:
    """Build a human-readable runtime status snapshot.

    Args:
        search_usage_text: Optional pre-formatted web search usage string
                           (produced by SearchUsageInfo.format()). When provided
                           it is appended as an extra section.
    """
    uptime_s = int(time.time() - start_time)
    uptime = (
        f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m"
        if uptime_s >= 3600
        else f"{uptime_s // 60}m {uptime_s % 60}s"
    )
    last_in = last_usage.get("prompt_tokens", 0)
    last_out = last_usage.get("completion_tokens", 0)
    cached = last_usage.get("cached_tokens", 0)
    ctx_total = max(context_window_tokens, 0)
    # Budget mirrors Consolidator formula: ctx_window - max_completion - _SAFETY_BUFFER
    ctx_budget = max(ctx_total - int(max_completion_tokens) - 1024, 1)
    ctx_pct = min(int((context_tokens_estimate / ctx_budget) * 100), 999) if ctx_budget > 0 else 0
    ctx_used_str = (
        f"{context_tokens_estimate // 1000}k"
        if context_tokens_estimate >= 1000
        else str(context_tokens_estimate)
    )
    ctx_total_str = f"{ctx_total // 1000}k" if ctx_total > 0 else "n/a"
    token_line = f"\U0001f4ca Tokens: {last_in} in / {last_out} out"
    if cached and last_in:
        token_line += f" ({cached * 100 // last_in}% cached)"
    lines = [
        f"\U0001f408 nanobot v{version}",
        f"\U0001f9e0 Model: {model}",
        token_line,
        f"\U0001f4da Context: {ctx_used_str}/{ctx_total_str} ({ctx_pct}% of input budget)",
        f"\U0001f4ac Session: {session_msg_count} messages",
        f"\u23f1 Uptime: {uptime}",
        f"\u26a1 Tasks: {active_task_count} active",
    ]
    if search_usage_text:
        lines.append(search_usage_text)
    return "\n".join(lines)


def sync_workspace_templates(workspace: Path, silent: bool = False) -> list[str]:
    """Sync bundled templates to workspace. Creates missing files without overwriting user files."""
    from importlib.resources import files as pkg_files

    try:
        tpl = pkg_files("nanobot") / "templates"
    except Exception:
        return []
    if not tpl.is_dir():
        return []

    added: list[str] = []

    def _write(src: Any, dest: Path) -> None:
        content = src.read_text(encoding="utf-8") if src else ""
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    for item in tpl.iterdir():
        if item.name.endswith(".md") and not item.name.startswith("."):
            _write(item, workspace / item.name)
    _write(tpl / "memory" / "MEMORY.md", workspace / "memory" / "MEMORY.md")
    _write(tpl / "prompts" / "README.md", workspace / "prompts" / "README.md")
    _write(None, workspace / "memory" / "history.jsonl")
    (workspace / "skills").mkdir(exist_ok=True)

    if added and not silent:
        from rich.console import Console

        for name in added:
            Console().print(f"  [dim]Created {name}[/dim]")

    # Initialize git for memory version control
    try:
        from nanobot.utils.gitstore import GitStore

        gs = GitStore(
            workspace,
            tracked_files=[
                "SOUL.md",
                "USER.md",
                "memory/MEMORY.md",
            ],
        )
        gs.init()
    except Exception:
        logger.exception("Failed to initialize git store for {}", workspace)

    return added


def load_bundled_template(template_name: str) -> str | None:
    """Read a bundled template file from the nanobot package."""
    from importlib.resources import files as pkg_files

    with suppress(Exception):
        tpl = pkg_files("nanobot") / "templates" / template_name
        if tpl.is_file():
            return tpl.read_text(encoding="utf-8")
    return None
