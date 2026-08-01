"""OpenPets desktop pet integration hook for nanobot.

Non-blocking: silently skips if OpenPets is unavailable.
Uses periodic refresh to maintain persistent state.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext


# IPC configuration
IPC_VERSION = 1
IPC_PROTOCOL = "openpets-ipc"

# Refresh intervals (must be less than 4 seconds)
# success/error have shorter animation duration, need more frequent refresh
REFRESH_INTERVAL_SECONDS = 3
SUCCESS_ERROR_REFRESH_INTERVAL = 1.5  # More frequent for success/error

# Idle delay after completion
IDLE_DELAY_SECONDS = 10

# Allowed reactions
ALLOWED_REACTIONS = [
    "idle", "thinking", "working", "editing", "running",
    "testing", "waiting", "waving", "success", "error", "celebrating",
]

# States that need periodic refresh to maintain
PERSISTENT_STATES = frozenset({
    "thinking", "working", "editing", "running",
    "testing", "waiting", "success", "error",
})

# States with shorter refresh interval
SHORT_REFRESH_STATES = frozenset({"success", "error"})

# Node.js script for IPC (single request per connection)
NODE_IPC_SCRIPT = r'''
const net = require('net');
const endpoint = process.argv[2];
const request = process.argv[3];

const socket = net.createConnection(endpoint);
socket.write(request + '\n');
socket.on('data', (data) => {
    console.log(data.toString());
    socket.end();
});
socket.on('error', (err) => {
    console.error('Error:', err.message);
    process.exit(1);
});
socket.setTimeout(5000, () => {
    console.error('Timeout');
    process.exit(1);
});
'''


class OpenPetsIpcClient:
    """IPC client for OpenPets using Node.js subprocess."""

    def __init__(self) -> None:
        self._discovery_path = self._default_discovery_path()
        self._endpoint: str | None = None
        self._token: str | None = None
        self._script_path: str | None = None
        self._available: bool | None = None

    def _default_discovery_path(self) -> Path:
        import platform
        if platform.system() == "Windows":
            base = Path.home() / "AppData" / "Roaming"
        else:
            base = Path.home() / ".config"
        return base / "OpenPets" / "runtime" / "ipc.json"

    def _load_discovery(self) -> bool:
        """Load IPC discovery file. Returns True if successful."""
        try:
            content = self._discovery_path.read_text()
            data = json.loads(content)
            if data.get("protocol") != IPC_PROTOCOL:
                return False
            self._endpoint = data.get("endpoint")
            self._token = data.get("token")
            return True
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    def _ensure_script(self) -> str:
        """Ensure Node.js IPC script exists."""
        if self._script_path is None:
            self._script_path = os.path.join(tempfile.gettempdir(), "openpets_ipc.js")
            with open(self._script_path, "w") as f:
                f.write(NODE_IPC_SCRIPT)
        return self._script_path

    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send IPC request via Node.js subprocess."""
        if not self._load_discovery():
            return None

        request = {
            "id": str(uuid.uuid4()),
            "version": IPC_VERSION,
            "token": self._token,
            "method": method,
            "params": params or {},
        }

        script_path = self._ensure_script()

        try:
            proc = await asyncio.create_subprocess_exec(
                "node", script_path, self._endpoint, json.dumps(request),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if stdout:
                response = json.loads(stdout.decode().strip())
                if response.get("ok"):
                    return response.get("result")
                return None
            return None
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
            return None

    def is_available(self) -> bool:
        """Quick check if OpenPets might be available."""
        if self._available is not None:
            return self._available
        return self._load_discovery()

    async def check_available(self) -> bool:
        """Async check if OpenPets is responding."""
        if self._available is not None:
            return self._available
        result = await self._send_request("hello", {})
        self._available = result is not None
        return self._available

    # Public API

    async def react(self, reaction: str) -> bool:
        """Set pet reaction. Returns True if successful."""
        if reaction not in ALLOWED_REACTIONS:
            return False
        result = await self._send_request("pet.react", {"reaction": reaction})
        return result is not None

    async def say(self, message: str, reaction: str | None = None) -> bool:
        """Make pet say a message. Returns True if successful."""
        params = {"message": message}
        if reaction and reaction in ALLOWED_REACTIONS:
            params["reaction"] = reaction
        result = await self._send_request("pet.say", params)
        return result is not None


class OpenPetsHook(AgentHook):
    """Hook that mirrors nanobot's agent state to an OpenPets desktop pet.

    Non-blocking: if OpenPets is unavailable, silently skips all operations.
    Uses periodic refresh with shorter intervals for success/error states.
    """

    def __init__(
        self,
        pet_id: str | None = None,
        say_max_length: int = 60,
    ) -> None:
        super().__init__()
        self._pet_id = pet_id
        self._say_max_length = say_max_length
        self._client = OpenPetsIpcClient()
        self._refresh_task: asyncio.Task | None = None
        self._idle_timer: asyncio.Task | None = None
        self._current_reaction: str | None = None
        self._pending_say: str | None = None
        self._streaming_started: bool = False
        self._iteration_done: bool = False
        self._enabled: bool = True

    async def _ensure_enabled(self) -> bool:
        """Check if OpenPets is available."""
        if not self._enabled:
            return False
        if self._client.is_available():
            return True
        if await self._client.check_available():
            return True
        self._enabled = False
        logger.warning("OpenPets: not available, hook disabled")
        return False

    async def _react(self, reaction: str) -> None:
        """Set pet reaction and start appropriate refresh loop."""
        if not await self._ensure_enabled():
            return

        self._cancel_idle_timer()

        if reaction in PERSISTENT_STATES:
            self._current_reaction = reaction
            self._start_refresh_loop(reaction)
        else:
            self._stop_refresh_loop()
            self._current_reaction = reaction

        success = await self._client.react(reaction)
        if success:
            logger.info("OpenPets: reaction {}", reaction)

    async def _refresh_reaction(self) -> None:
        """Refresh current reaction."""
        if not self._enabled or not self._current_reaction:
            return
        success = await self._client.react(self._current_reaction)
        if success:
            logger.debug("OpenPets: refreshed {}", self._current_reaction)

    def _start_refresh_loop(self, reaction: str) -> None:
        """Start periodic refresh loop with appropriate interval."""
        self._stop_refresh_loop()

        # Use shorter interval for success/error
        interval = SUCCESS_ERROR_REFRESH_INTERVAL if reaction in SHORT_REFRESH_STATES else REFRESH_INTERVAL_SECONDS

        async def refresh_loop():
            while self._enabled and self._current_reaction in PERSISTENT_STATES:
                try:
                    await asyncio.sleep(interval)
                    await self._refresh_reaction()
                except asyncio.CancelledError:
                    break

        self._refresh_task = asyncio.create_task(refresh_loop())

    def _stop_refresh_loop(self) -> None:
        """Stop refresh loop."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            self._refresh_task = None

    async def _say(self, text: str, reaction: str | None = None) -> None:
        """Make pet say text."""
        if not await self._ensure_enabled():
            return
        if not text:
            return
        if len(text) > self._say_max_length:
            text = text[:self._say_max_length - 3] + "..."
        text = text.replace('"', '\\"').replace("\n", " ")

        success = await self._client.say(text, reaction=reaction)
        if success:
            logger.info("OpenPets: said '{}...'", text[:30])

    def _cancel_idle_timer(self) -> None:
        """Cancel idle timer."""
        if self._idle_timer and not self._idle_timer.done():
            self._idle_timer.cancel()
            self._idle_timer = None

    async def _schedule_idle(self) -> None:
        """Wait then switch to idle."""
        try:
            await asyncio.sleep(IDLE_DELAY_SECONDS)
            await self._react("idle")
        except asyncio.CancelledError:
            pass

    def _start_idle_timer(self) -> None:
        """Start idle timer after completion."""
        self._cancel_idle_timer()
        self._idle_timer = asyncio.create_task(self._schedule_idle())

    # -- Hook callbacks --

    def wants_streaming(self) -> bool:
        return True

    async def before_iteration(self, context: AgentHookContext) -> None:
        """Start thinking at iteration start."""
        self._streaming_started = False
        self._iteration_done = False

        if context.iteration == 0:
            await self._react("thinking")

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        """Trigger thinking at streaming start."""
        if not self._streaming_started:
            self._streaming_started = True
            await self._react("thinking")

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        """Switch to working when executing tools."""
        if context.tool_calls:
            await self._react("working")

    async def after_iteration(self, context: AgentHookContext) -> None:
        """Handle iteration end."""
        if self._iteration_done:
            return

        if context.error:
            await self._react("error")
            self._start_idle_timer()
        elif context.final_content:
            text = self._pending_say
            self._pending_say = None
            if text:
                await self._say(text)
            await self._react("success")
            self._start_idle_timer()

        self._iteration_done = True

    def finalize_content(
        self, context: AgentHookContext, content: str | None
    ) -> str | None:
        """Queue content to say."""
        if content and content.strip():
            self._pending_say = content.strip()
        return content

    async def on_stream_end(
        self, context: AgentHookContext, *, resuming: bool
    ) -> None:
        """Handle stream end."""
        if resuming:
            await self._react("thinking")
        else:
            text = self._pending_say
            self._pending_say = None
            if text:
                await self._say(text)

    async def startup_notification(self, message: str = "诺亚上线啦！") -> None:
        """Show startup notification."""
        await self._react("waiting")
        await self._say(message)
        self._start_idle_timer()