#!/usr/bin/env python3
"""Create a delivery packet for PRD-driven implementation work."""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "assets" / "templates"

PACKET_FILES = [
    "00-intake.md",
    "01-code-map-findings.md",
    "02-feature-breakdown.md",
    "03-implementation-plan.md",
    "04-progress.md",
    "05-verification.md",
]


def slugify(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized or "feature"


def render_template(text: str, replacements: dict[str, str]) -> str:
    output = text
    for key, value in replacements.items():
        output = output.replace(f"{{{{{key}}}}}", value)
    return output


def write_file(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Absolute path to the target repository")
    parser.add_argument("--feature", required=True, help="Feature slug or short label")
    parser.add_argument("--title", required=True, help="Human-readable feature title")
    parser.add_argument(
        "--packet-dir",
        default=".spec-work",
        help="Packet root inside the repository (default: .spec-work)",
    )
    parser.add_argument(
        "--date-prefix",
        default=datetime.now().strftime("%Y%m%d"),
        help="Date prefix used in the packet folder name",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not repo.exists():
        raise SystemExit(f"Repository does not exist: {repo}")

    feature_slug = slugify(args.feature)
    packet_name = f"{args.date_prefix}-{feature_slug}"
    packet_dir = repo / args.packet_dir / packet_name

    replacements = {
        "FEATURE_TITLE": args.title,
        "FEATURE_SLUG": feature_slug,
        "DATE_PREFIX": args.date_prefix,
        "PACKET_NAME": packet_name,
        "PACKET_DIR": str(packet_dir),
        "REPO_ROOT": str(repo),
        "CODE_MAP_PATH": str(repo / "code-map.md"),
        "CREATED_AT": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    packet_dir.mkdir(parents=True, exist_ok=True)
    for filename in PACKET_FILES:
        template_path = TEMPLATE_DIR / filename
        content = render_template(template_path.read_text(encoding="utf-8"), replacements)
        write_file(packet_dir / filename, content, force=args.force)

    code_map_path = repo / "code-map.md"
    if not code_map_path.exists() or args.force:
        template_path = TEMPLATE_DIR / "code-map.md"
        content = render_template(template_path.read_text(encoding="utf-8"), replacements)
        write_file(code_map_path, content, force=args.force)

    print(f"Packet ready: {packet_dir}")
    for filename in PACKET_FILES:
        print(f" - {packet_dir / filename}")
    print(f"Code map: {code_map_path}")


if __name__ == "__main__":
    main()
