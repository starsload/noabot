"""
Entry point for running nanobot as a module: python -m nanobot
"""

import sys
from pathlib import Path

# Support running the package directory directly, e.g. `python nanobot gateway`.
# In that mode Python only adds `.../nanobot` to sys.path, so absolute imports
# like `nanobot.cli.commands` cannot resolve until we add the project root.
if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

from nanobot.cli.commands import app

if __name__ == "__main__":
    app()
