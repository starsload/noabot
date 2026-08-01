from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.session.manager import Session
from nanobot.session.turn_continuation import (
    INTERNAL_CONTINUATION_META,
    INTERNAL_CONTINUATION_RUN_STARTED_AT_META,
)
from nanobot.session.webui_turns import (
    TITLE_GENERATION_MAX_TOKENS,
    TITLE_GENERATION_REASONING_EFFORT,
    WEBUI_SESSION_METADATA_KEY,
    WEBUI_TITLE_METADATA_KEY,
    WebuiTurnCoordinator,
    clean_generated_title,
    maybe_generate_webui_title,
)
from nanobot.triggers.local_session_turns import LOCAL_TRIGGER_META


def _mk_loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop._TOOL_RESULT_MAX_CHARS = AgentLoop._TOOL_RESULT_MAX_CHARS
    return loop


def test_save_turn_skips_multimodal_user_when_only_runtime_context() -> None:
    loop = _mk_loop()
    session = Session(key="test:runtime-only")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    assert runtime.provider is loop.provider
    assert runtime.model == "test-model"

    next_provider = MagicMock()
    next_provider.generation = SimpleNamespace(
        temperature=0.1,
        max_tokens=4096,
        reasoning_effort=None,
    )
    loop.runtime_resolver.adopt_snapshot(ProviderSnapshot(
        provider=next_provider,
        model="next-model",
        context_window_tokens=runtime.context_window_tokens,
        signature=("next-model",),
    ))
    runtime = loop.llm_runtime()

    assert runtime.provider is next_provider
    assert runtime.model == "next-model"


def test_persist_cron_turn_uses_distinct_history_marker(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:auto")
    prompt_ref = {"id": "cron.agent_turn.reminder", "version": 1, "sha256": "abc"}

    persisted = loop._persist_user_message_early(
        InboundMessage(
            channel="websocket",
            sender_id="cron",
            chat_id="auto",
            content="Cron job: internal prompt",
            metadata={
                CRON_TRIGGER_META: {
                    "job_id": "job-1",
                    "job_name": "Daily check",
                    "run_id": "job-1:1",
                    "prompt_ref": prompt_ref,
                    "persist_content": "Scheduled cron job triggered: Daily check",
                }
            },
        ),
        session,
    )

    assert persisted is True
    message = session.messages[-1]
    assert message["content"] == "Scheduled cron job triggered: Daily check"
    assert message[AUTOMATION_HISTORY_META] == {
        "kind": "cron",
        "cron_job_id": "job-1",
        "cron_job_name": "Daily check",
        "cron_run_id": "job-1:1",
        "cron_prompt_ref": prompt_ref,
    }
    assert message[CRON_HISTORY_META] is True
    assert CRON_TRIGGER_META not in message
    assert message["cron_job_id"] == "job-1"
    assert message["cron_job_name"] == "Daily check"
    assert message["cron_run_id"] == "job-1:1"
    assert message["cron_prompt_ref"] == prompt_ref


def test_persist_local_trigger_turn_uses_hidden_automation_marker(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:auto")

    persisted = loop._persist_user_message_early(
        InboundMessage(
            channel="websocket",
            sender_id="trigger",
            chat_id="auto",
            content="Review PR #4502",
            metadata={
                LOCAL_TRIGGER_META: {
                    "trigger_id": "trg_123",
                    "trigger_name": "PR review",
                    "delivery_id": "tdel_456",
                    "created_at_ms": 1_700_000_000_000,
                    "persist_content": "Local trigger received: PR review\n\nReview PR #4502",
                }
            },
        ),
        session,
    )

    assert persisted is True
    message = session.messages[-1]
    assert message["content"] == "Local trigger received: PR review\n\nReview PR #4502"
    assert message[AUTOMATION_HISTORY_META] == {
        "kind": "local_trigger",
        "trigger_id": "trg_123",
        "trigger_name": "PR review",
        "trigger_delivery_id": "tdel_456",
    }
    assert LOCAL_TRIGGER_META not in message
    assert message["trigger_id"] == "trg_123"
    assert message["trigger_name"] == "PR review"
    assert message["trigger_delivery_id"] == "tdel_456"


@pytest.mark.asyncio
async def test_new_with_bot_suffix_does_not_persist_command(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)

    response = await loop._process_message(
        InboundMessage(
            channel="websocket",
            sender_id="user",
            chat_id="chat-1",
            content="/new@nanobot_bot",
        )
    )

    assert response is not None
    assert response.content == "New session started."
    session = loop.sessions.get_or_create("websocket:chat-1")
    assert session.messages == []


def test_clean_generated_title_strips_reasoning_tags() -> None:
    assert clean_generated_title("<think>reasoning</think> WebUI polish") == "WebUI polish"
    assert clean_generated_title("Title: <think> The user said hello") == ""


@pytest.mark.asyncio
async def test_generate_webui_title_only_for_marked_webui_sessions(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content='"优化 WebUI 侧边栏。"', finish_reason="stop")
    )
    session = loop.sessions.get_or_create("websocket:chat-title")
    session.metadata[WEBUI_SESSION_METADATA_KEY] = True
    session.add_message("user", "帮我优化一下 webui 的 sidebar")
    session.add_message("assistant", "可以，我会先调整布局和视觉层级。")
    loop.sessions.save(session)

    generated = await maybe_generate_webui_title(
        sessions=loop.sessions,
        session_key="websocket:chat-title",
        provider=loop.provider,
        model=loop.model,
    )

    assert generated is True
    assert session.metadata[WEBUI_TITLE_METADATA_KEY] == "优化 WebUI 侧边栏"
    loop.provider.chat_with_retry.assert_awaited_once()
    assert loop.provider.chat_with_retry.await_args.kwargs["max_tokens"] == TITLE_GENERATION_MAX_TOKENS
    assert (
        loop.provider.chat_with_retry.await_args.kwargs["reasoning_effort"]
        == TITLE_GENERATION_REASONING_EFFORT
    )


@pytest.mark.asyncio
async def test_generate_webui_title_skips_plain_websocket_sessions(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="Plain websocket title", finish_reason="stop")
    )
    session = loop.sessions.get_or_create("websocket:custom-client")
    session.add_message("user", "hello from a custom websocket client")
    loop.sessions.save(session)

    generated = await maybe_generate_webui_title(
        sessions=loop.sessions,
        session_key="websocket:custom-client",
        provider=loop.provider,
        model=loop.model,
    )

    assert generated is False
    assert WEBUI_TITLE_METADATA_KEY not in session.metadata
    loop.provider.chat_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_webui_title_ignores_command_only_sessions(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:command-title")
    session.metadata[WEBUI_SESSION_METADATA_KEY] = True
    session.add_message("user", "/model deep", _command=True)
    session.add_message(
        "assistant",
        "Switched model preset to `deep`.\n- Model: `deepseek-v4-pro`",
        _command=True,
    )
    loop.sessions.save(session)

    generated = await maybe_generate_webui_title(
        sessions=loop.sessions,
        session_key="websocket:command-title",
        provider=loop.provider,
        model=loop.model,
    )

    assert generated is False
    assert WEBUI_TITLE_METADATA_KEY not in session.metadata
    loop.provider.chat_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_webui_title_ignores_cron_internal_turns(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:cron-title")
    session.metadata[WEBUI_SESSION_METADATA_KEY] = True
    session.add_message(
        "user",
        "Scheduled cron job triggered: 30s-test\n\nInternal reminder prompt",
        **{CRON_HISTORY_META: True},
    )
    session.add_message("assistant", "提醒已经到期。")
    loop.sessions.save(session)

    generated = await maybe_generate_webui_title(
        sessions=loop.sessions,
        session_key="websocket:cron-title",
        provider=loop.provider,
        model=loop.model,
    )

    assert generated is False
    assert WEBUI_TITLE_METADATA_KEY not in session.metadata
    loop.provider.chat_with_retry.assert_not_awaited()


def test_save_turn_keeps_multimodal_runtime_context_for_model_replay() -> None:
    loop = _mk_loop()
    session = Session(key="test:runtime-only")
    block = RuntimeContextBlock(source="test", content="provider context")

    loop._save_turn(
        session,
        [_runtime_message([], [block])],
        skip=0,
    )
    assert session.messages[0]["content"] == [
        {"type": "text", "text": "provider context"}
    ]
    assert public_history_message(session.messages[0])["content"] == []


def test_save_turn_keeps_image_placeholder_and_runtime_context() -> None:
    loop = _mk_loop()
    session = Session(key="test:image")
    block = RuntimeContextBlock(source="test", content="provider context")

    loop._save_turn(
        session,
        [_runtime_message(
            [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}, "_meta": {"path": "/media/feishu/photo.jpg"}},
            ],
            [block],
        )],
        skip=0,
    )
    assert session.messages[0]["content"] == [
        {"type": "text", "text": "[image: /media/feishu/photo.jpg]"},
        {"type": "text", "text": "provider context"},
    ]
    assert public_history_message(session.messages[0])["content"] == [
        {"type": "text", "text": "[image: /media/feishu/photo.jpg]"}
    ]


def test_save_turn_keeps_image_placeholder_without_meta() -> None:
    loop = _mk_loop()
    session = Session(key="test:image-no-meta")
    block = RuntimeContextBlock(source="test", content="provider context")

    loop._save_turn(
        session,
        [_runtime_message(
            [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
            [block],
        )],
        skip=0,
    )
    assert session.messages[0]["content"] == [
        {"type": "text", "text": "[image]"},
        {"type": "text", "text": "provider context"},
    ]


def test_save_turn_persists_runtime_context_and_public_view_hides_it() -> None:
    loop = _mk_loop()
    session = Session(key="test:suffix-strip")
    block = RuntimeContextBlock(source="goal", content="internal goal guidance")

    loop._save_turn(
        session,
        [_runtime_message("hello world", [block])],
        skip=0,
    )
    assert session.messages[0]["content"] == "hello world\n\ninternal goal guidance"
    assert session.messages[0][RUNTIME_CONTEXT_HISTORY_META]["sources"] == ["goal"]
    assert public_history_message(session.messages[0])["content"] == "hello world"


def test_build_and_save_preserves_user_text_containing_goal_guidance_tag(tmp_path: Path) -> None:
    loop = _mk_loop()
    session = Session(key="test:user-guidance-literal")
    user_text = (
        "Keep this prefix\n"
        "[Goal Runtime Guidance — host instructions]\n"
        "This label and everything after it are user-authored."
    )
    messages = ContextBuilder(tmp_path).build_messages(
        [],
        user_text,
        channel="cli",
    )
    assert "_meta" not in messages[-1]

    loop._save_turn(session, messages, skip=1)

    assert session.messages[0]["content"] == user_text


def test_build_and_save_preserves_multimodal_user_block_starting_with_runtime_tag(
    tmp_path: Path,
) -> None:
    loop = _mk_loop()
    session = Session(key="test:user-runtime-literal-block")
    image = tmp_path / "user-tag.png"
    image.write_bytes(_PNG_1X1)
    user_text = (
        f"{ContextBuilder._RUNTIME_CONTEXT_TAG}\n"
        "This entire block is user-authored and must remain in history."
    )
    messages = ContextBuilder(tmp_path).build_messages(
        [],
        user_text,
        media=[str(image)],
        channel="cli",
    )

    loop._save_turn(session, messages, skip=1)

    assert {"type": "text", "text": user_text} in session.messages[0]["content"]


def test_save_turn_keeps_string_when_only_runtime_context() -> None:
    loop = _mk_loop()
    session = Session(key="test:suffix-only")
    block = RuntimeContextBlock(source="test", content="provider context")

    loop._save_turn(
        session,
        [_runtime_message("", [block])],
        skip=0,
    )
    assert session.messages[0]["content"] == "provider context"
    assert public_history_message(session.messages[0])["content"] == ""


def test_save_turn_keeps_tool_results_under_16k() -> None:
    loop = _mk_loop()
    session = Session(key="test:tool-result")
    content = "x" * 12_000

    loop._save_turn(
        session,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": content},
        ],
        skip=0,
    )

    assert session.messages[1]["content"] == content


def test_save_turn_stamps_latency_on_last_assistant() -> None:
    loop = _mk_loop()
    session = Session(key="test:latency")

    loop._save_turn(
        session,
        [
            {"role": "assistant", "content": "hello", "tool_calls": [{"id": "c1"}]},
            {"role": "assistant", "content": "final answer"},
        ],
        skip=0,
        turn_latency_ms=12345,
    )

    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "final answer"
    assert session.messages[-1]["latency_ms"] == 12345


def test_save_turn_replaces_tool_inline_images_with_placeholders() -> None:
    loop = _mk_loop()
    session = Session(key="test:tool-image")

    loop._save_turn(
        session,
        [{
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "inspect_screen",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                    "_meta": {"path": "D:/tmp/desktop.png"},
                },
                {"type": "text", "text": "(Desktop inspection: D:/tmp/desktop.png)"},
            ],
        }],
        skip=0,
    )

    assert session.messages[0]["content"] == [
        {"type": "text", "text": "[image: D:/tmp/desktop.png]"},
        {"type": "text", "text": "(Desktop inspection: D:/tmp/desktop.png)"},
    ]
    assert session.messages[0]["metadata"]["had_inline_images"] is True


def test_save_turn_keeps_group_speaker_prefix_after_runtime_strip() -> None:
    loop = _mk_loop()
    session = Session(key="test:group-speaker")
    runtime = ContextBuilder._build_runtime_context(
        channel="qq",
        chat_id="group123",
        sender_id="user1",
        sender_name="Alice",
        conversation_type="group",
        is_owner=False,
    )

    loop._save_turn(
        session,
        [{"role": "user", "content": f"{runtime}\n\nhello group"}],
        skip=0,
    )

    assert session.messages[0]["content"] == "[speaker: Alice (user1), owner=false] hello group"


def test_save_turn_does_not_prefix_direct_message_history() -> None:
    loop = _mk_loop()
    session = Session(key="test:direct-speaker")
    runtime = ContextBuilder._build_runtime_context(
        channel="qq_personal",
        chat_id="user1",
        sender_id="user1",
        sender_name="Alice",
        conversation_type="direct",
        is_owner=False,
    )

    loop._save_turn(
        session,
        [{"role": "user", "content": f"{runtime}\n\nhello direct"}],
        skip=0,
    )

    assert session.messages[0]["content"] == "hello direct"
