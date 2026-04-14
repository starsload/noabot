import pytest

from nanobot.avatar.base import AvatarIntent
from nanobot.avatar.null_runtime import NullAvatarRuntime


@pytest.mark.asyncio
async def test_null_avatar_runtime_tracks_connection_and_last_intent() -> None:
    runtime = NullAvatarRuntime()

    assert runtime.name == "none"
    assert runtime.is_connected is False

    await runtime.connect()
    assert runtime.is_connected is True

    intent = AvatarIntent(expression="smile", motion="wave", speaking=True)
    await runtime.apply_intent(intent)
    assert runtime.last_intent == intent

    await runtime.disconnect()
    assert runtime.is_connected is False

