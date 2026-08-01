import asyncio

import pytest

from nanobot.providers.base import (
    RETRY_AFTER_BUFFER,
    GenerationSettings,
    LLMProvider,
    LLMResponse,
    ProviderCallContext,
    ProviderConversationState,
)


class ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.calls = 0
        self.last_kwargs: dict = {}

    async def chat(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        self.last_kwargs = kwargs
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def chat_stream(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        self.last_kwargs = kwargs
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        delta = getattr(response, "_test_stream_delta", None)
        if delta and kwargs.get("on_content_delta"):
            await kwargs["on_content_delta"](delta)
        return response

    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.asyncio
async def test_chat_with_retry_retries_transient_error_then_succeeds(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(content="429 rate limit", finish_reason="error"),
        LLMResponse(content="ok"),
    ])
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.finish_reason == "stop"
    assert response.content == "ok"
    assert provider.calls == 2
    assert delays == [1]


@pytest.mark.asyncio
async def test_chat_with_retry_does_not_retry_non_transient_error(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(content="401 unauthorized", finish_reason="error"),
    ])
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "401 unauthorized"
    assert provider.calls == 1
    assert delays == []


@pytest.mark.asyncio
async def test_chat_with_retry_does_not_retry_missing_thought_signature(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content=(
                "Error: error code: 429 - {'error': {'message': "
                "'function call is missing a thought_signature in functioncall parts'}}"
            ),
            finish_reason="error",
        ),
    ])
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert "thought_signature" in (response.content or "")
    assert provider.calls == 1
    assert delays == []


@pytest.mark.asyncio
async def test_chat_with_retry_returns_final_error_after_retries(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(content="429 rate limit a", finish_reason="error"),
        LLMResponse(content="429 rate limit b", finish_reason="error"),
        LLMResponse(content="429 rate limit c", finish_reason="error"),
        LLMResponse(content="503 final server error", finish_reason="error"),
    ])
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "503 final server error"
    assert provider.calls == 4
    assert delays == [1, 2, 4]


@pytest.mark.asyncio
async def test_chat_with_retry_preserves_cancelled_error() -> None:
    provider = ScriptedProvider([asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_chat_stream_with_retry_does_not_retry_after_emitting_content(monkeypatch) -> None:
    first = LLMResponse(content="stream stalled", finish_reason="error")
    first._test_stream_delta = "partial"  # type: ignore[attr-defined]
    provider = ScriptedProvider([
        first,
        LLMResponse(content="ok"),
    ])
    deltas: list[str] = []
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    async def _on_delta(delta: str) -> None:
        deltas.append(delta)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_content_delta=_on_delta,
    )

    assert response.content == "stream stalled"
    assert provider.calls == 1
    assert deltas == ["partial"]
    assert delays == []


@pytest.mark.asyncio
async def test_chat_stream_with_retry_retries_timeout_after_emitting_content(monkeypatch) -> None:
    first = LLMResponse(
        content="Error calling LLM: stream stalled for more than 30 seconds",
        finish_reason="error",
        error_kind="timeout",
    )
    first._test_stream_delta = "partial"  # type: ignore[attr-defined]
    provider = ScriptedProvider([
        first,
        LLMResponse(content="full retry response"),
    ])
    deltas: list[str] = []
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    async def _on_delta(delta: str) -> None:
        deltas.append(delta)

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_content_delta=_on_delta,
    )

    assert response.content == "full retry response"
    assert response.finish_reason == "stop"
    assert provider.calls == 2
    assert deltas == ["partial"]
    assert delays == [1]
    assert provider.last_kwargs.get("on_content_delta") is None


@pytest.mark.asyncio
async def test_chat_stream_with_retry_retries_timeout_in_new_stream_segment(
    monkeypatch,
) -> None:
    first = LLMResponse(
        content="Error calling LLM: stream stalled for more than 30 seconds",
        finish_reason="error",
        error_kind="timeout",
    )
    first._test_stream_delta = "partial"  # type: ignore[attr-defined]
    second = LLMResponse(content="full retry response")
    second._test_stream_delta = "full retry response"  # type: ignore[attr-defined]
    provider = ScriptedProvider([first, second])
    deltas: list[str] = []
    recoveries: list[str] = []
    delays: list[int] = []

    async def _fake_sleep(delay: int) -> None:
        delays.append(delay)

    async def _on_delta(delta: str) -> None:
        deltas.append(delta)

    async def _on_stream_recover() -> None:
        recoveries.append("recover")

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        on_content_delta=_on_delta,
        on_stream_recover=_on_stream_recover,
    )

    assert response.content == "full retry response"
    assert response.finish_reason == "stop"
    assert provider.calls == 2
    assert deltas == ["partial", "full retry response"]
    assert recoveries == ["recover"]
    assert delays == [1]
    assert provider.last_kwargs.get("on_content_delta") is not None


@pytest.mark.asyncio
async def test_chat_with_retry_uses_provider_generation_defaults() -> None:
    """When callers omit generation params, provider.generation defaults are used."""
    provider = ScriptedProvider([LLMResponse(content="ok")])
    provider.generation = GenerationSettings(temperature=0.2, max_tokens=321, reasoning_effort="high")

    await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert provider.last_kwargs["temperature"] == 0.2
    assert provider.last_kwargs["max_tokens"] == 321
    assert provider.last_kwargs["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_chat_with_retry_explicit_override_beats_defaults() -> None:
    """Explicit kwargs should override provider.generation defaults."""
    provider = ScriptedProvider([LLMResponse(content="ok")])
    provider.generation = GenerationSettings(temperature=0.2, max_tokens=321, reasoning_effort="high")

    await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.9,
        max_tokens=9999,
        reasoning_effort="low",
    )

    assert provider.last_kwargs["temperature"] == 0.9
    assert provider.last_kwargs["max_tokens"] == 9999
    assert provider.last_kwargs["reasoning_effort"] == "low"


# ---------------------------------------------------------------------------
# Image fallback tests
# ---------------------------------------------------------------------------

_IMAGE_MSG = [
    {"role": "user", "content": [
        {"type": "text", "text": "describe this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}, "_meta": {"path": "/media/test.png"}},
    ]},
]

_IMAGE_MSG_NO_META = [
    {"role": "user", "content": [
        {"type": "text", "text": "describe this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]},
]


@pytest.mark.asyncio
async def test_non_transient_error_with_images_retries_without_images() -> None:
    """Any non-transient error retries once with images stripped when images are present."""
    provider = ScriptedProvider([
        LLMResponse(content="API调用参数有误,请检查文档", finish_reason="error"),
        LLMResponse(content="ok, no image"),
    ])

    response = await provider.chat_with_retry(messages=_IMAGE_MSG)

    assert response.content == "ok, no image"
    assert provider.calls == 2
    msgs_on_retry = provider.last_kwargs["messages"]
    for msg in msgs_on_retry:
        content = msg.get("content")
        if isinstance(content, list):
            assert all(b.get("type") != "image_url" for b in content)
            assert any("not delivered" in (b.get("text") or "").lower() for b in content)


@pytest.mark.asyncio
async def test_non_transient_error_without_images_no_retry() -> None:
    """Non-transient errors without image content are returned immediately."""
    provider = ScriptedProvider([
        LLMResponse(content="401 unauthorized", finish_reason="error"),
    ])

    response = await provider.chat_with_retry(
        messages=[{"role": "user", "content": "hello"}],
    )

    assert provider.calls == 1
    assert response.finish_reason == "error"


@pytest.mark.asyncio
async def test_image_fallback_returns_error_on_second_failure() -> None:
    """If the image-stripped retry also fails, return that error."""
    provider = ScriptedProvider([
        LLMResponse(content="some model error", finish_reason="error"),
        LLMResponse(content="still failing", finish_reason="error"),
    ])

    response = await provider.chat_with_retry(messages=_IMAGE_MSG)

    assert provider.calls == 2
    assert response.content == "still failing"
    assert response.finish_reason == "error"


@pytest.mark.asyncio
async def test_image_fallback_without_meta_uses_default_placeholder() -> None:
    """When _meta is absent, fallback placeholder is non-descriptive."""
    provider = ScriptedProvider([
        LLMResponse(content="error", finish_reason="error"),
        LLMResponse(content="ok"),
    ])

    response = await provider.chat_with_retry(messages=_IMAGE_MSG_NO_META)

    assert response.content == "ok"
    assert provider.calls == 2
    msgs_on_retry = provider.last_kwargs["messages"]
    for msg in msgs_on_retry:
        content = msg.get("content")
        if isinstance(content, list):
            assert any("not delivered" in (b.get("text") or "").lower() for b in content)


@pytest.mark.asyncio
async def test_image_payload_error_retries_with_normalized_images(monkeypatch) -> None:
    provider = ScriptedProvider([
        LLMResponse(
            content=(
                "Error: Exceeded limit on max bytes per data-uri item : 10485760"
            ),
            finish_reason="error",
        ),
        LLMResponse(content="ok"),
    ])
    normalized = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,xyz"},
                    "_meta": {"path": "/media/test.png"},
                },
                {"type": "text", "text": "describe this"},
            ],
        }
    ]

    monkeypatch.setattr(
        "nanobot.providers.base.normalize_message_image_blocks_for_llm",
        lambda messages: normalized,
    )

    response = await provider.chat_with_retry(messages=_IMAGE_MSG)

    assert response.content == "ok"
    assert provider.calls == 2
    assert provider.last_kwargs["messages"] == normalized
    retried_content = provider.last_kwargs["messages"][0]["content"]
    assert any(block.get("type") == "image_url" for block in retried_content)
    assert all("[image" not in (block.get("text") or "") for block in retried_content)
