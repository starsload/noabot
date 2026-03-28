from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage


def _mk_loop(owner_ids: set[str] | None = None) -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop.owner_ids = owner_ids or set()
    return loop


def test_speaker_context_marks_owner_and_group_metadata() -> None:
    loop = _mk_loop({"telegram:user1", "owner2"})
    msg = InboundMessage(
        channel="telegram",
        sender_id="user1",
        chat_id="group123",
        content="hi",
        metadata={"chat_type": "group", "sender_name": "Alice", "sender_username": "alice"},
    )

    context = loop._speaker_context_kwargs(msg)

    assert context == {
        "sender_id": "user1",
        "sender_name": "Alice",
        "sender_username": "alice",
        "conversation_type": "group",
        "is_owner": True,
    }


def test_speaker_context_infers_direct_for_matching_sender_and_chat() -> None:
    loop = _mk_loop({"telegram:owner"})
    msg = InboundMessage(
        channel="qq_personal",
        sender_id="123456",
        chat_id="123456",
        content="hi",
    )

    context = loop._speaker_context_kwargs(msg)

    assert context["conversation_type"] == "direct"
    assert context["is_owner"] is False
