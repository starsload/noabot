from nanobot.avatar.state_machine import AvatarTurnState, AvatarTurnStateMachine


def test_avatar_turn_state_machine_basic_flow() -> None:
    sm = AvatarTurnStateMachine()
    assert sm.state == AvatarTurnState.IDLE

    assert sm.start_listening() == AvatarTurnState.LISTENING
    assert sm.finish_listening() == AvatarTurnState.THINKING
    assert sm.start_speaking() == AvatarTurnState.SPEAKING
    assert sm.finish_speaking() == AvatarTurnState.IDLE


def test_avatar_turn_state_machine_interrupt_and_reset() -> None:
    sm = AvatarTurnStateMachine()
    sm.start_listening()
    assert sm.interrupt() == AvatarTurnState.INTERRUPTED
    assert sm.reset() == AvatarTurnState.IDLE

