# pyright: reportMissingTypeStubs=false
"""QQ personal-account channel via OneBot 11 compatible bridges (NapCat, etc.)."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import shutil
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

import aiohttp
from loguru import logger
from pydantic import ConfigDict, Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.paths import get_media_dir
from nanobot.config.schema import Base

_SAFE_NAME_RE = re.compile(r"[^\w.\-()\[\]（）【】一-鿿]+", re.UNICODE)
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".aac", ".m4a", ".flac", ".amr"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}


def _sanitize_filename(name: str) -> str:
    name = Path((name or "").strip()).name
    return _SAFE_NAME_RE.sub("_", name).strip("._ ")


def _is_url(ref: str) -> bool:
    return ref.startswith("http://") or ref.startswith("https://")


def _is_image_name(name: str) -> bool:
    return Path(name).suffix.lower() in _IMAGE_EXTS


def _guess_segment_type(ref: str) -> str:
    parsed = urlparse(ref)
    probe = parsed.path if parsed.scheme else ref
    ext = Path(probe).suffix.lower()
    mime, _ = mimetypes.guess_type(probe)

    if ext in _IMAGE_EXTS or (mime and mime.startswith("image/")):
        return "image"
    if ext in _AUDIO_EXTS or (mime and mime.startswith("audio/")):
        return "record"
    if ext in _VIDEO_EXTS or (mime and mime.startswith("video/")):
        return "video"
    return "file"


def _maybe_numeric_id(value: str) -> int | str:
    raw = str(value).strip()
    return int(raw) if raw.isdigit() else raw


class QQPersonalConfig(Base):
    """QQ personal-account channel configuration via OneBot 11 bridge."""

    model_config = ConfigDict(extra="allow")  # accept merge_owner_in_group etc.

    enabled: bool = False
    ws_url: str = "ws://127.0.0.1:3001"
    http_url: str = "http://127.0.0.1:3000"
    access_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    group_policy: Literal["open", "mention", "allowlist"] = "mention"
    group_allow_from: list[str] = Field(default_factory=list)
    media_dir: str = ""
    reconnect_delay_s: int = 5
    download_chunk_size: int = 1024 * 256
    download_max_bytes: int = 1024 * 1024 * 200
    merge_owner_in_group: bool = False  # merge owner's group messages into shared session


_GROUP_AUTH_PREFIX = "group:"


class QQPersonalChannel(BaseChannel):
    """QQ personal account channel backed by OneBot 11 compatible bridges."""

    name = "qq_personal"
    display_name = "QQ Personal"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return QQPersonalConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = QQPersonalConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: QQPersonalConfig = config
        self._ws = None
        self._http: aiohttp.ClientSession | None = None
        self._connected = False
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._chat_type_cache: dict[str, str] = {}
        self._media_root = self._init_media_root()

    def _init_media_root(self) -> Path:
        if self.config.media_dir:
            root = Path(self.config.media_dir).expanduser()
        else:
            root = Path(get_media_dir("qq_personal"))
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _auth_headers(self) -> dict[str, str]:
        if not self.config.access_token:
            return {}
        return {"Authorization": f"Bearer {self.config.access_token}"}

    async def _ensure_http(self) -> aiohttp.ClientSession:
        if self._http is None or getattr(self._http, "closed", False):
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
        return self._http

    async def start(self) -> None:
        """Connect to the OneBot forward WebSocket endpoint."""
        import websockets

        headers = self._auth_headers() or None
        self._running = True
        await self._ensure_http()

        while self._running:
            try:
                async with websockets.connect(
                    self.config.ws_url,
                    additional_headers=headers,
                    max_size=20 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    logger.info("QQ personal bridge connected: {}", self.config.ws_url)

                    async for raw in ws:
                        try:
                            await self._handle_ws_message(raw)
                        except Exception:
                            logger.exception("QQ personal message handling failed")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._connected = False
                self._ws = None
                logger.warning("QQ personal bridge connection error: {}", exc)
                if self._running:
                    await asyncio.sleep(max(1, int(self.config.reconnect_delay_s)))

    async def stop(self) -> None:
        """Stop the bridge connection and cleanup resources."""
        self._running = False
        self._connected = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._http is not None:
            try:
                await self._http.close()
            except Exception:
                pass
            self._http = None

    def _resolve_chat_type(self, msg: OutboundMessage) -> str:
        metadata = msg.metadata or {}
        if metadata.get("chat_type") == "group" or metadata.get("is_group") is True:
            return "group"
        return self._chat_type_cache.get(str(msg.chat_id), "private")

    async def send(self, msg: OutboundMessage) -> None:
        """Send text and media to a private contact or group chat."""
        chat_type = self._resolve_chat_type(msg)
        target_key = "group_id" if chat_type == "group" else "user_id"
        target_id = _maybe_numeric_id(msg.chat_id)
        action = "send_group_msg" if chat_type == "group" else "send_private_msg"
        for media_ref in msg.media or []:
            await self._send_media(target_id, media_ref, action=action, target_key=target_key)
        if msg.content and msg.content.strip():
            await self._call_action(
                action,
                {target_key: target_id, "message": msg.content.strip()},
            )

    async def _send_media(
        self,
        target_id: int | str,
        media_ref: str,
        *,
        action: str,
        target_key: str,
    ) -> None:
        ref, filename = self._normalize_outbound_ref(media_ref)
        segment_type = _guess_segment_type(ref)
        segment: dict[str, Any] = {"type": segment_type, "data": {"file": ref}}
        if segment_type == "file" and filename:
            segment["data"]["name"] = filename
        await self._call_action(
            action,
            {target_key: target_id, "message": [segment]},
        )

    def _normalize_outbound_ref(self, media_ref: str) -> tuple[str, str]:
        media_ref = (media_ref or "").strip()
        if not media_ref:
            raise ValueError("Empty media reference")

        if _is_url(media_ref):
            filename = os.path.basename(urlparse(media_ref).path) or "file"
            return media_ref, filename

        if media_ref.startswith("file://"):
            parsed = urlparse(media_ref)
            raw = parsed.path or parsed.netloc
            local_path = Path(unquote(raw))
        else:
            local_path = Path(os.path.expanduser(media_ref))

        if not local_path.is_file():
            raise FileNotFoundError(f"Outbound media file not found: {local_path}")

        return str(local_path.resolve()), local_path.name

    async def _call_action(self, action: str, params: dict[str, Any]) -> Any:
        session = await self._ensure_http()
        url = self.config.http_url.rstrip("/") + f"/{action}"
        headers = self._auth_headers() or None

        response = await session.post(url, json=params, headers=headers)
        try:
            status = getattr(response, "status", getattr(response, "status_code", 0))
            if status and status >= 400:
                body = await self._maybe_response_text(response)
                raise RuntimeError(f"{action} failed with HTTP {status}: {body}")

            payload = await self._maybe_response_json(response)
            if isinstance(payload, dict):
                if payload.get("status") not in (None, "ok"):
                    raise RuntimeError(
                        f"{action} failed: retcode={payload.get('retcode')} msg={payload.get('msg')}"
                    )
                return payload.get("data", payload)
            return payload
        finally:
            release = getattr(response, "release", None)
            if callable(release):
                release()

    async def _handle_ws_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("QQ personal bridge sent invalid JSON: {}", raw[:120])
            return

        # Ignore action responses on the forward WebSocket.
        if isinstance(data, dict) and {"status", "retcode"} <= data.keys():
            return
        if not isinstance(data, dict):
            return
        if data.get("post_type") != "message":
            return
        message_type = str(data.get("message_type") or "")
        if message_type not in {"private", "group"}:
            return

        message_id = str(data.get("message_id") or "")
        if message_id:
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

        user_id = str(data.get("user_id") or "")
        if not user_id:
            return

        if message_type == "group":
            if not self._should_respond_in_group(data):
                return
            chat_id = str(data.get("group_id") or "")
            if not chat_id:
                return
            self._chat_type_cache[chat_id] = "group"
        else:
            chat_id = user_id
            self._chat_type_cache[chat_id] = "private"

        content, media_paths, attachments = await self._parse_message_content(
            data.get("message"),
            user_id=user_id,
            group_id=chat_id if message_type == "group" else None,
        )
        if not content:
            content = str(data.get("raw_message") or "").strip()
        if not content and not media_paths:
            return

        # Defer to BaseChannel._handle_message: it enforces allow_from /
        # pairing permission and merges the owner's group messages into the
        # shared cross-channel session when merge_owner_in_group is set.
        # Group access is scoped to the group entity (OneBot ``group_id``)
        # under the ``group_policy`` gate already applied above, mirroring
        # upstream's ``authorization_id`` contract; allow_from scopes DMs.
        await self._handle_message(
            sender_id=user_id,
            chat_id=chat_id,
            content=content,
            media=media_paths if media_paths else None,
            metadata={
                "message_id": message_id,
                "sub_type": data.get("sub_type"),
                "chat_type": "group" if message_type == "group" else "private",
                "is_group": message_type == "group",
                "group_id": str(data.get("group_id") or "") if message_type == "group" else None,
                "sender_name": self._extract_sender_name(data),
                "attachments": attachments,
            },
            is_dm=message_type == "private",
            authorization_id=_GROUP_AUTH_PREFIX + chat_id if message_type == "group" else None,
        )

    def is_allowed(self, sender_id: str) -> bool:
        """Permission check with group-entity authorization.

        A ``group:<id>`` subject is authorized by ``group_policy``: ``open``
        admits any group, ``allowlist`` admits ``group_allow_from`` members,
        and ``mention`` relies on the at-bot gate already enforced in
        ``_should_respond_in_group`` before dispatch. Bare sender ids keep
        upstream semantics (star > allowlist > pairing > deny).
        """
        subject = str(sender_id)
        if subject.startswith(_GROUP_AUTH_PREFIX):
            group_id = subject[len(_GROUP_AUTH_PREFIX):]
            policy = self.config.group_policy
            if policy == "allowlist":
                return group_id in {str(item) for item in self.config.group_allow_from}
            return policy in {"open", "mention"}
        return super().is_allowed(subject)

    def _should_respond_in_group(self, data: dict[str, Any]) -> bool:
        policy = self.config.group_policy
        group_id = str(data.get("group_id") or "")
        if policy == "open":
            return True
        if policy == "allowlist":
            return group_id in {str(item) for item in self.config.group_allow_from}

        if data.get("to_me") is True:
            return True

        self_id = str(data.get("self_id") or "")
        for seg in data.get("message") or []:
            if not isinstance(seg, dict) or str(seg.get("type") or "") != "at":
                continue
            qq = str((seg.get("data") or {}).get("qq") or "")
            if qq and qq == self_id:
                return True
        return False

    @staticmethod
    def _extract_sender_name(data: dict[str, Any]) -> str | None:
        sender = data.get("sender") or {}
        if not isinstance(sender, dict):
            return None
        for key in ("card", "nickname", "remark"):
            value = sender.get(key)
            if value:
                return str(value)
        return None

    async def _parse_message_content(
        self,
        message: Any,
        *,
        user_id: str,
        group_id: str | None = None,
    ) -> tuple[str, list[str], list[dict[str, Any]]]:
        if isinstance(message, str):
            return message.strip(), [], []
        if not isinstance(message, list):
            return "", [], []

        rendered: list[str] = []
        media_paths: list[str] = []
        attachments: list[dict[str, Any]] = []

        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type") or "")
            data = seg.get("data") or {}
            if seg_type == "text":
                text = str(data.get("text") or "")
                if text:
                    rendered.append(text)
                continue

            if seg_type not in {"image", "video", "record", "file"}:
                continue

            saved_path, meta = await self._materialize_media_segment(
                seg_type, data, user_id, group_id=group_id,
            )
            if meta:
                attachments.append(meta)
            if saved_path:
                media_paths.append(saved_path)
                label = "image" if _is_image_name(saved_path) else "file"
                if rendered and not rendered[-1].endswith("\n"):
                    rendered.append("\n")
                rendered.append(f"[{label}: {saved_path}]")
            else:
                if rendered and not rendered[-1].endswith("\n"):
                    rendered.append("\n")
                rendered.append(f"[{seg_type}]")

        return "".join(rendered).strip(), media_paths, attachments

    async def _materialize_media_segment(
        self,
        seg_type: str,
        data: dict[str, Any],
        user_id: str,
        *,
        group_id: str | None = None,
    ) -> tuple[str | None, dict[str, Any]]:
        filename_hint = self._segment_filename_hint(seg_type, data)
        ref = await self._resolve_media_ref(seg_type, data, user_id, group_id=group_id)
        owner_id = group_id or user_id
        saved_path = await self._materialize_ref(ref, filename_hint=filename_hint, user_id=owner_id)
        return saved_path, {
            "type": seg_type,
            "data": data,
            "saved_path": saved_path,
            "resolved_ref": ref,
        }

    def _segment_filename_hint(self, seg_type: str, data: dict[str, Any]) -> str:
        for key in ("name", "file_name", "filename"):
            value = data.get(key)
            if value:
                return str(value)

        file_value = data.get("file")
        if isinstance(file_value, str):
            parsed = urlparse(file_value)
            basename = os.path.basename(parsed.path) or os.path.basename(file_value)
            if basename and "." in basename:
                return basename

        fallback_id = str(data.get("file_id") or data.get("file") or "attachment")
        ext_map = {"image": ".jpg", "video": ".mp4", "record": ".mp3", "file": ".bin"}
        return _sanitize_filename(f"{seg_type}_{fallback_id}{ext_map.get(seg_type, '.bin')}")

    async def _resolve_media_ref(
        self,
        seg_type: str,
        data: dict[str, Any],
        user_id: str,
        *,
        group_id: str | None = None,
    ) -> str | None:
        for key in ("path", "file"):
            path = data.get(key)
            if isinstance(path, str) and path and Path(path).is_file():
                return str(Path(path))

        url = data.get("url")
        if isinstance(url, str) and _is_url(url):
            return url

        file_id = data.get("file_id") or data.get("file")
        if seg_type == "image" and data.get("file"):
            result = await self._safe_call_action("get_image", {"file": data.get("file")})
            return self._pick_ref(result)

        if seg_type == "record" and data.get("file"):
            result = await self._safe_call_action(
                "get_record",
                {"file": data.get("file"), "out_format": "mp3"},
            )
            return self._pick_ref(result)

        if seg_type == "file" and file_id:
            if group_id:
                result = await self._safe_call_action(
                    "get_group_file_url",
                    {"group": str(group_id), "file_id": file_id},
                )
                if not result:
                    result = await self._safe_call_action(
                        "get_group_file_url",
                        {"group_id": _maybe_numeric_id(group_id), "file_id": file_id},
                    )
            else:
                result = await self._safe_call_action(
                    "get_private_file_url",
                    {"user_id": _maybe_numeric_id(user_id), "file_id": file_id},
                )
            ref = self._pick_ref(result)
            if ref:
                return ref

        if file_id:
            result = await self._safe_call_action(
                "get_file",
                {"file_id": file_id, "type": seg_type},
            )
            return self._pick_ref(result)

        return None

    async def _safe_call_action(self, action: str, params: dict[str, Any]) -> Any:
        try:
            return await self._call_action(action, params)
        except Exception as exc:
            logger.warning("QQ personal action {} failed: {}", action, exc)
            return None

    @staticmethod
    def _pick_ref(result: Any) -> str | None:
        if isinstance(result, str):
            return result
        if not isinstance(result, dict):
            return None
        for key in ("url", "path", "file"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    async def _materialize_ref(
        self,
        ref: str | None,
        *,
        filename_hint: str,
        user_id: str,
    ) -> str | None:
        if not ref:
            return None

        if ref.startswith("file://"):
            parsed = urlparse(ref)
            raw = parsed.path or parsed.netloc
            local_path = Path(unquote(raw))
            return await self._copy_local_file(local_path, filename_hint, user_id)

        if _is_url(ref):
            return await self._download_to_media_dir(ref, filename_hint, user_id)

        local_path = Path(ref)
        if local_path.is_file():
            return await self._copy_local_file(local_path, filename_hint, user_id)

        return None

    async def _copy_local_file(self, source: Path, filename_hint: str, user_id: str) -> str | None:
        if not source.is_file():
            return None

        user_dir = self._media_root / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _sanitize_filename(filename_hint or source.name) or source.name
        target = user_dir / safe_name
        if target.exists():
            target = user_dir / f"{target.stem}_{int(asyncio.get_running_loop().time() * 1000)}{target.suffix}"
        await asyncio.to_thread(shutil.copyfile, source, target)
        return str(target)

    async def _download_to_media_dir(self, url: str, filename_hint: str, user_id: str) -> str | None:
        session = await self._ensure_http()
        user_dir = self._media_root / user_id
        user_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _sanitize_filename(filename_hint)
        if not safe_name:
            safe_name = os.path.basename(urlparse(url).path) or "qq_personal_file.bin"

        target = user_dir / safe_name
        if target.exists():
            target = user_dir / f"{target.stem}_{int(asyncio.get_running_loop().time() * 1000)}{target.suffix}"
        tmp_path = target.with_suffix(target.suffix + ".part")

        response = await session.get(url, allow_redirects=True)
        try:
            status = getattr(response, "status", getattr(response, "status_code", 0))
            if status and status >= 400:
                logger.warning("QQ personal download failed status={} url={}", status, url)
                return None

            downloaded = 0
            chunk_size = max(1024, int(self.config.download_chunk_size))
            max_bytes = max(1024 * 1024, int(self.config.download_max_bytes))

            def _open_tmp():
                tmp_path.parent.mkdir(parents=True, exist_ok=True)
                return open(tmp_path, "wb")

            fh = await asyncio.to_thread(_open_tmp)
            try:
                if hasattr(response, "content") and hasattr(response.content, "iter_chunked"):
                    async for chunk in response.content.iter_chunked(chunk_size):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            logger.warning("QQ personal download exceeded max bytes: {}", url)
                            return None
                        await asyncio.to_thread(fh.write, chunk)
                else:
                    body = await self._maybe_response_bytes(response)
                    downloaded = len(body)
                    if downloaded > max_bytes:
                        logger.warning("QQ personal download exceeded max bytes: {}", url)
                        return None
                    await asyncio.to_thread(fh.write, body)
            finally:
                await asyncio.to_thread(fh.close)

            await asyncio.to_thread(os.replace, tmp_path, target)
            return str(target)
        finally:
            release = getattr(response, "release", None)
            if callable(release):
                release()
            if tmp_path.exists():
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    @staticmethod
    async def _maybe_response_json(response: Any) -> Any:
        json_method = getattr(response, "json", None)
        if callable(json_method):
            try:
                return await json_method()
            except TypeError:
                return json_method()

        text = await QQPersonalChannel._maybe_response_text(response)
        return json.loads(text) if text else {}

    @staticmethod
    async def _maybe_response_text(response: Any) -> str:
        text_method = getattr(response, "text", None)
        if callable(text_method):
            try:
                return await text_method()
            except TypeError:
                return text_method()
        text = getattr(response, "text", "")
        return text if isinstance(text, str) else ""

    @staticmethod
    async def _maybe_response_bytes(response: Any) -> bytes:
        read_method = getattr(response, "read", None)
        if callable(read_method):
            try:
                return await read_method()
            except TypeError:
                return read_method()
        return getattr(response, "content", b"") or b""
