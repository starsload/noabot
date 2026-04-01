import os
import subprocess
import sys
from pathlib import Path


def test_directory_entrypoint_supports_direct_python_invocation():
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    result = subprocess.run(
        [sys.executable, "nanobot", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "gateway" in result.stdout
