"""State machine for desktop voice/avatar turn lifecycle."""

from __future__ import annotations

from enum import StrEnum


class AvatarTurnState(StrEnum):
    """Conversation turn states for desktop pet voice flow."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"


class AvatarTurnStateMachine:
    """Small, explicit state machine to keep turn transitions auditable."""

    def __init__(self) -> None:
        self._state = AvatarTurnState.IDLE

    @property
    def state(self) -> AvatarTurnState:
        return self._state

    def start_listening(self) -> AvatarTurnState:
        self._state = AvatarTurnState.LISTENING
        return self._state

    def finish_listening(self) -> AvatarTurnState:
        self._state = AvatarTurnState.THINKING
        return self._state

    def start_speaking(self) -> AvatarTurnState:
        self._state = AvatarTurnState.SPEAKING
        return self._state

    def interrupt(self) -> AvatarTurnState:
        self._state = AvatarTurnState.INTERRUPTED
        return self._state

    def finish_speaking(self) -> AvatarTurnState:
        self._state = AvatarTurnState.IDLE
        return self._state

    def reset(self) -> AvatarTurnState:
        self._state = AvatarTurnState.IDLE
        return self._state

