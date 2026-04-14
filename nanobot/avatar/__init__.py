"""Avatar runtime abstractions."""

from nanobot.avatar.base import AvatarIntent, BaseAvatarRuntime
from nanobot.avatar.null_runtime import NullAvatarRuntime
from nanobot.avatar.orchestrator import DesktopVoiceAvatarOrchestrator
from nanobot.avatar.state_machine import AvatarTurnState, AvatarTurnStateMachine
from nanobot.avatar.vts_runtime import VTubeStudioRuntime

__all__ = [
    "AvatarIntent",
    "BaseAvatarRuntime",
    "NullAvatarRuntime",
    "DesktopVoiceAvatarOrchestrator",
    "AvatarTurnState",
    "AvatarTurnStateMachine",
    "VTubeStudioRuntime",
]
