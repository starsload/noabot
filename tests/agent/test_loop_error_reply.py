"""Outbound softening of provider error payloads (noabot).

DashScope-style failures arrive as `LLMResponse(content="Error: data: {json}",
finish_reason="error")` and used to be forwarded to the chat verbatim.  The
raw text must stay out of the user-facing reply while the friendly line
covers the common moderation refusal distinctly.
"""

from __future__ import annotations

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage

_INSPECTION_BODY = (
    'Error: data: {"error":{"code":"data_inspection_failed","param":null,'
    '"message":"Input text data may contain inappropriate content.","type":'
    '"data_inspection_failed"},"id":"chatcmpl-d6ed591d"}}'
)


class _StubLoop(AgentLoop):
    """Minimal instance for exercising _assemble_outbound without a bus."""

    def __init__(self) -> None:  # skip AgentLoop.__init__
        self.tools: dict = {}


def _inbound() -> InboundMessage:
    return InboundMessage(
        channel="qq_personal", sender_id="owner", chat_id="c1", content="hi"
    )


def test_friendly_content_maps_inspection_failure() -> None:
    out = AgentLoop._friendly_error_content(_INSPECTION_BODY)
    assert out == AgentLoop._MODEL_ERROR_FRIENDLY_INSPECTION
    assert "data_inspection_failed" in out


def test_friendly_content_maps_other_errors_generically() -> None:
    assert (
        AgentLoop._friendly_error_content("Error calling LLM: boom")
        == AgentLoop._MODEL_ERROR_FRIENDLY_GENERIC
    )


def test_friendly_content_passes_through_non_errors() -> None:
    assert AgentLoop._friendly_error_content("今天天气不错") == "今天天气不错"
    assert AgentLoop._friendly_error_content("") == ""


def test_assemble_outbound_softens_error_turn() -> None:
    loop = _StubLoop()
    outbound = loop._assemble_outbound(
        _inbound(),
        final_content=_INSPECTION_BODY,
        stop_reason="error",
        had_injections=False,
        streamed_content=False,
    )
    assert outbound is not None
    assert outbound.content == AgentLoop._MODEL_ERROR_FRIENDLY_INSPECTION
    assert "chatcmpl" not in outbound.content


def test_assemble_outbound_leaves_successful_turns_untouched() -> None:
    loop = _StubLoop()
    outbound = loop._assemble_outbound(
        _inbound(),
        final_content=_INSPECTION_BODY,
        stop_reason="final",
        had_injections=False,
        streamed_content=False,
    )
    assert outbound is not None
    assert outbound.content == _INSPECTION_BODY
