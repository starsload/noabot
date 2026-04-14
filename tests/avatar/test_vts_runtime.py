import pytest

from nanobot.avatar.base import AvatarIntent
from nanobot.avatar.vts_runtime import VTubeStudioRuntime


class _FakeVTSRequest:
    """Mimics pyvts.vts_request - returns BaseRequest format with data wrapper."""

    def __init__(self) -> None:
        self.triggered_hotkeys: list[str] = []
        self.set_parameters: list[tuple[str, float]] = []

    def requestTriggerHotKey(self, hotkey_id: str, item_instance_id=None) -> dict:  # noqa: ANN001, N802
        return {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "HotkeyTriggerRequest",
            "data": {"hotkeyID": hotkey_id},
        }

    def requestSetParameterValue(self, parameter: str, value: float, **kwargs) -> dict:  # noqa: ANN001, N802
        return {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "SomeID",
            "messageType": "InjectParameterDataRequest",
            "data": {
                "faceFound": False,
                "mode": "set",
                "parameterValues": [{"id": parameter, "weight": 1, "value": value}],
            },
        }


class _FakeVTSClient:
    """Mimics pyvts.vts with its actual API surface."""

    def __init__(self) -> None:
        self.connected = False
        self.token_requested = False
        self.auth_requested = False
        self.closed = False
        self.vts_request = _FakeVTSRequest()
        self.requested_messages: list[dict] = []

    async def connect(self) -> None:
        self.connected = True

    async def request_authenticate_token(self) -> None:
        self.token_requested = True

    async def request_authenticate(self) -> None:
        self.auth_requested = True

    async def close(self) -> None:
        self.closed = True
        self.connected = False

    async def request(self, msg: dict) -> dict:
        """Mimics pyvts.vts.request() which sends via websocket."""
        self.requested_messages.append(msg)
        # pyvts request methods return BaseRequest format: {"apiName": ..., "data": {...}, ...}
        data = msg.get("data", {})
        # Hotkey: data = {"hotkeyID": "..."}
        if "hotkeyID" in data:
            self.vts_request.triggered_hotkeys.append(data["hotkeyID"])
        # Parameter: data = {"parameterValues": [{"id": ..., "value": ...}, ...]}
        for pv in data.get("parameterValues", []):
            self.vts_request.set_parameters.append((pv["id"], pv["value"]))
        return {"data": {"success": True}}


@pytest.mark.asyncio
async def test_vts_runtime_connect_apply_disconnect() -> None:
    holder: dict[str, _FakeVTSClient] = {}

    def _factory(**kwargs):  # noqa: ANN003
        _ = kwargs
        client = _FakeVTSClient()
        holder["client"] = client
        return client

    runtime = VTubeStudioRuntime(client_factory=_factory)
    assert runtime.is_connected is False
    await runtime.connect()

    client = holder["client"]
    assert runtime.is_connected is True
    assert client.token_requested is True
    assert client.auth_requested is True

    await runtime.apply_intent(AvatarIntent(expression="smile", motion="wave", speaking=True))
    await runtime.apply_intent(AvatarIntent(speaking=False))
    assert client.vts_request.triggered_hotkeys == ["smile", "wave"]
    assert client.vts_request.set_parameters == [("MouthOpen", 1.0), ("MouthOpen", 0.0)]

    await runtime.disconnect()
    assert runtime.is_connected is False
    assert client.closed is True


@pytest.mark.asyncio
async def test_vts_runtime_uses_configured_hotkeys_and_parameter_mapping() -> None:
    holder: dict[str, _FakeVTSClient] = {}

    def _factory(**kwargs):  # noqa: ANN003
        assert kwargs["token_path"] == "./custom-token.txt"
        client = _FakeVTSClient()
        holder["client"] = client
        return client

    runtime = VTubeStudioRuntime(
        client_factory=_factory,
        token_path="./custom-token.txt",
        speaking_parameter="VoiceOpen",
        speaking_value_on=0.75,
        speaking_value_off=0.15,
        expression_hotkeys={"smile": "ExpSmile"},
        motion_hotkeys={"wave": "MotionWave"},
    )
    await runtime.connect()

    client = holder["client"]
    await runtime.apply_intent(AvatarIntent(expression="smile", motion="wave", speaking=True))
    await runtime.apply_intent(AvatarIntent(speaking=False))

    assert client.vts_request.triggered_hotkeys == ["ExpSmile", "MotionWave"]
    assert client.vts_request.set_parameters == [("VoiceOpen", 0.75), ("VoiceOpen", 0.15)]


def test_vts_runtime_dependency_error_message() -> None:
    with pytest.raises(RuntimeError) as exc:
        VTubeStudioRuntime(module_loader=lambda _: (_ for _ in ()).throw(ModuleNotFoundError("pyvts")))
    assert "pip install pyvts" in str(exc.value)
