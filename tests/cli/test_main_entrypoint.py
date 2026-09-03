import os
import subprocess
import sys
from pathlib import Path


def test_module_entrypoint_supports_direct_python_invocation():
    # noabot: ``python nanobot`` (bare directory invocation) no longer works on
    # the upstream layout because nanobot/nanobot.py (the SDK module) shadows
    # the package once runpy prepends the package dir to sys.path.  The
    # supported direct invocation is ``python -m nanobot`` (see __main__.py).
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "nanobot", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "gateway" in result.stdout
