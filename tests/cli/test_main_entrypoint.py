import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_ENV,
    )


def test_module_entrypoint_supports_direct_python_invocation():
    result = _run("-m", "nanobot", "--help")

    assert result.returncode == 0, result.stderr
    assert "gateway" in result.stdout


def test_directory_entrypoint_supports_direct_python_invocation():
    # noabot: upstream's nanobot/nanobot.py (SDK module) shadows the package
    # once runpy prepends the package dir to sys.path; __main__.py swaps that
    # entry for the repo root so ``python nanobot`` keeps working.
    result = _run("nanobot", "--help")

    assert result.returncode == 0, result.stderr
    assert "gateway" in result.stdout
