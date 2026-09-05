"""
Entry point for running nanobot: ``python -m nanobot`` or ``python nanobot``.

``python nanobot`` (directory invocation) used to break on the upstream
layout: runpy prepends this package dir to sys.path, where nanobot.py (the
SDK module) then shadows the ``nanobot`` package and every ``nanobot.*``
import fails.  Detect that and swap the entry for the repo root so both
styles work.
"""

import sys
from pathlib import Path

_package_dir = Path(__file__).resolve().parent
if sys.path and sys.path[0] and Path(sys.path[0]).resolve() == _package_dir:
    sys.path[0] = str(_package_dir.parent)

from nanobot.cli.commands import app  # noqa: E402 (after the sys.path fix-up)

if __name__ == "__main__":
    app()
