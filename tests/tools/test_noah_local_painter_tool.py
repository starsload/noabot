from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.agent.tools.noah_local_painter import NoahLocalPainterTool
from nanobot.bus.events import OutboundMessage


def _install_fake_workspace(workspace: Path, image_path: Path) -> None:
    libs_dir = workspace / "libs" / "noah_local_painter"
    libs_dir.mkdir(parents=True)
    (libs_dir / "__init__.py").write_text(
        f"""
class _Result:
    ok = True
    mode = "generate"
    message = "mock-ok"
    current_model = "mock-model"
    run_dir = r"{str((workspace / 'runtime' / 'run').resolve())}"
    metadata_path = r"{str((workspace / 'runtime' / 'run' / 'meta.json').resolve())}"
    image_paths = [r"{str(image_path.resolve())}"]
    payload = {{"preset": "presets/noah_halfbody.json"}}

class NoahLocalPainter:
    def health_check(self, **kwargs):
        result = _Result()
        result.mode = "health_check"
        result.image_paths = []
        return result

    def generate(self, **kwargs):
        return _Result()
""".strip(),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_generate_can_deliver_images_to_current_chat(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"png")
    _install_fake_workspace(tmp_path, image_path)

    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = NoahLocalPainterTool(workspace=tmp_path, send_callback=_send)
    tool.set_context("telegram", "room1")

    raw = await tool.execute(
        action="generate",
        prompt="fox girl",
        deliver=True,
        delivery_text="给你画好了。",
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["delivered"] is True
    assert payload["image_paths"] == [str(image_path.resolve())]
    assert len(sent) == 1
    assert sent[0].channel == "telegram"
    assert sent[0].chat_id == "room1"
    assert sent[0].content == "给你画好了。"
    assert sent[0].media == [str(image_path.resolve())]
    assert tool._sent_in_turn is True


@pytest.mark.asyncio
async def test_health_check_returns_status_without_delivery(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"png")
    _install_fake_workspace(tmp_path, image_path)

    tool = NoahLocalPainterTool(workspace=tmp_path)
    raw = await tool.execute(action="health_check")
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["mode"] == "health_check"
    assert payload["delivered"] is False
    assert "image_paths" not in payload


@pytest.mark.asyncio
async def test_missing_workspace_wrapper_returns_error(tmp_path: Path) -> None:
    tool = NoahLocalPainterTool(workspace=tmp_path)

    with pytest.raises(FileNotFoundError):
        await tool.execute(action="health_check")


def test_is_available_checks_workspace_wrapper(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"png")
    assert NoahLocalPainterTool.is_available(tmp_path) is False

    _install_fake_workspace(tmp_path, image_path)
    assert NoahLocalPainterTool.is_available(tmp_path) is True
