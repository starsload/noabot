import base64
import io

from PIL import Image

from nanobot.utils.helpers import (
    estimate_message_tokens,
    estimate_prompt_tokens,
    image_block_placeholder,
    normalize_message_image_blocks_for_llm,
    prepare_image_for_llm,
    replace_image_blocks_with_placeholders,
)


def _make_png(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_prepare_image_for_llm_downscales_large_edge() -> None:
    raw = _make_png((1600, 900), (255, 0, 0))

    normalized_raw, normalized_mime = prepare_image_for_llm(
        raw,
        "image/png",
        max_bytes=200_000,
        max_edge=512,
    )

    assert normalized_mime.startswith("image/")
    with Image.open(io.BytesIO(normalized_raw)) as image:
        assert max(image.size) <= 512


def test_prepare_image_for_llm_reencodes_when_byte_budget_is_tight() -> None:
    raw = _make_png((900, 900), (12, 34, 56))

    normalized_raw, normalized_mime = prepare_image_for_llm(
        raw,
        "image/png",
        max_bytes=5_000,
        max_edge=256,
    )

    assert len(normalized_raw) <= len(raw)
    assert normalized_mime in {"image/png", "image/jpeg"}


def test_normalize_message_image_blocks_for_llm_preserves_metadata() -> None:
    raw = _make_png((1200, 800), (0, 128, 255))
    encoded = base64.b64encode(raw).decode()
    messages = [
        {
            "role": "tool",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    "_meta": {"path": "D:/tmp/screen.png"},
                },
                {"type": "text", "text": "(Image file: D:/tmp/screen.png)"},
            ],
        }
    ]

    normalized = normalize_message_image_blocks_for_llm(
        messages,
        max_bytes=5_000,
        max_edge=256,
    )

    assert normalized is not messages
    block = normalized[0]["content"][0]
    assert block["_meta"]["path"] == "D:/tmp/screen.png"
    url = block["image_url"]["url"]
    assert url.startswith("data:image/")

    payload = url.split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(payload))) as image:
        assert max(image.size) <= 256


def test_replace_image_blocks_with_placeholders_keeps_path() -> None:
    content = [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,abc"},
            "_meta": {"path": "D:/tmp/screen.png"},
        },
        {"type": "text", "text": "after"},
    ]

    replaced, changed = replace_image_blocks_with_placeholders(content)

    assert changed is True
    assert replaced == [
        {"type": "text", "text": "[image: D:/tmp/screen.png]"},
        {"type": "text", "text": "after"},
    ]


def test_estimate_prompt_tokens_counts_inline_images() -> None:
    image_message = [{
        "role": "tool",
        "content": [
            {"type": "text", "text": "before"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,abc"},
                "_meta": {"path": "D:/tmp/screen.png"},
            },
        ],
    }]
    text_only_message = [{"role": "tool", "content": [{"type": "text", "text": "before"}]}]

    image_tokens = estimate_prompt_tokens(image_message)
    text_tokens = estimate_prompt_tokens(text_only_message)

    assert image_tokens > text_tokens
    assert image_block_placeholder(image_message[0]["content"][1]) == "[image: D:/tmp/screen.png]"


def test_estimate_message_tokens_counts_inline_images() -> None:
    image_message = {
        "role": "tool",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,abc"},
            }
        ],
    }

    assert estimate_message_tokens(image_message) >= 1024
