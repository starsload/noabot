"""The unified CLI loguru sink must not interpolate variable values.

noabot: loguru's ``diagnose=True`` default renders each traceback frame's
locals (``│ └ <value>``), which leaked a live Discord bot token when the
channel's ``start()`` failed against an unreachable gateway. Pin the fix:
tracebacks stay, values go.
"""

import re
import subprocess
import sys
import textwrap

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_PROBE = textwrap.dedent(
    """
    import nanobot.cli.commands  # installs the unified sink at import
    from loguru import logger

    def _handler():
        secret_token = "s3cr3t-discord-bot-token"
        try:
            raise RuntimeError("client startup failed")
        except RuntimeError:
            logger.exception("channel start failed")

    _handler()
    """
)


def test_exception_traceback_hides_local_variable_values(tmp_path) -> None:
    script = tmp_path / "sink_probe.py"
    script.write_text(_PROBE, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    stderr = _ANSI.sub("", result.stderr)
    # The traceback itself remains useful...
    assert "RuntimeError: client startup failed" in stderr
    assert "_handler" in stderr
    # ...but loguru must not render the frame's variable values.
    assert "s3cr3t-discord-bot-token" not in stderr
    assert "secret_token" not in stderr
