#!/usr/bin/env python3
"""Scaffold a feature-card documentation pack under docs/.

Creates:
- docs/cards/<CARD_ID>.md
- docs/e2e/<CARD_ID>.md
- docs/acceptance/<CARD_ID>.md
Optionally appends a changelog row to docs/changelog.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path


REQUIRED_TEMPLATE_FILES = {
    "card": "docs/templates/card-template.md",
    "e2e": "docs/templates/e2e-template.md",
    "acceptance": "docs/templates/acceptance-template.md",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(f"target exists: {path}")
    path.write_text(content, encoding="utf-8")


def _normalize_card_template(content: str, card_id: str, title: str, owner: str, date_text: str) -> str:
    out = content
    out = out.replace("# <Card ID>: <功能标题>", f"# {card_id}: {title}")
    out = out.replace("`FC-YYYYMMDD-NN`", f"`{card_id}`")
    out = out.replace("- Owner:", f"- Owner: {owner}" if owner else "- Owner:")
    out = out.replace("- Created At:", f"- Created At: {date_text}")
    out = out.replace("- Updated At:", f"- Updated At: {date_text}")
    return out


def _normalize_e2e_template(content: str, card_id: str, date_text: str) -> str:
    out = content
    out = out.replace("# E2E Report: <Card ID>", f"# E2E Report: {card_id}")
    out = out.replace("`FC-YYYYMMDD-NN`", f"`{card_id}`")
    out = out.replace("- Executed At:", f"- Executed At: {date_text}")
    return out


def _normalize_acceptance_template(content: str, card_id: str, reviewer: str, date_text: str) -> str:
    out = content
    out = out.replace("# Acceptance Record: <Card ID>", f"# Acceptance Record: {card_id}")
    out = out.replace("`FC-YYYYMMDD-NN`", f"`{card_id}`")
    out = out.replace("- Reviewer (Signer):", f"- Reviewer (Signer): {reviewer}" if reviewer else "- Reviewer (Signer):")
    out = out.replace("- Reviewed At:", f"- Reviewed At: {date_text}")
    return out


def _append_changelog(repo_root: Path, card_id: str, summary: str, decision: str) -> None:
    path = repo_root / "docs/changelog.md"
    if not path.exists():
        return

    content = _read(path)
    if card_id in content:
        return

    today = dt.date.today().isoformat()
    row = (
        f"| {today} | `{card_id}` | {summary} | {decision} | TBD | TBD | "
        f"`cards/{card_id}.md` / `e2e/{card_id}.md` / `acceptance/{card_id}.md` |\n"
    )

    marker = "| --- | --- | --- | --- | --- | --- | --- |\n"
    idx = content.find(marker)
    if idx == -1:
        return
    insert_at = idx + len(marker)
    new_content = content[:insert_at] + row + content[insert_at:]
    path.write_text(new_content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a docs feature-card pack")
    parser.add_argument("--repo-root", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--card-id", required=True, help="Card ID, e.g. FC-20260313-03")
    parser.add_argument("--title", required=True, help="Card title")
    parser.add_argument("--owner", default="", help="Card owner")
    parser.add_argument("--reviewer", default="", help="Acceptance reviewer")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="Date text for metadata")
    parser.add_argument("--force", action="store_true", help="Overwrite target files if they exist")
    parser.add_argument(
        "--add-changelog",
        action="store_true",
        help="Insert a changelog row if card id does not already exist",
    )
    parser.add_argument(
        "--changelog-summary",
        default="TODO: summarize this feature",
        help="Changelog summary text (used with --add-changelog)",
    )
    parser.add_argument(
        "--changelog-decision",
        default="Accepted",
        choices=["Accepted", "Rejected"],
        help="Changelog decision value",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()

    for rel in REQUIRED_TEMPLATE_FILES.values():
        if not (repo_root / rel).exists():
            raise FileNotFoundError(f"missing template: {rel}")

    card_tpl = _read(repo_root / REQUIRED_TEMPLATE_FILES["card"])
    e2e_tpl = _read(repo_root / REQUIRED_TEMPLATE_FILES["e2e"])
    acc_tpl = _read(repo_root / REQUIRED_TEMPLATE_FILES["acceptance"])

    card_content = _normalize_card_template(card_tpl, args.card_id, args.title, args.owner, args.date)
    e2e_content = _normalize_e2e_template(e2e_tpl, args.card_id, args.date)
    acceptance_content = _normalize_acceptance_template(acc_tpl, args.card_id, args.reviewer, args.date)

    card_path = repo_root / f"docs/cards/{args.card_id}.md"
    e2e_path = repo_root / f"docs/e2e/{args.card_id}.md"
    acceptance_path = repo_root / f"docs/acceptance/{args.card_id}.md"

    _write(card_path, card_content, force=args.force)
    _write(e2e_path, e2e_content, force=args.force)
    _write(acceptance_path, acceptance_content, force=args.force)

    if args.add_changelog:
        _append_changelog(repo_root, args.card_id, args.changelog_summary, args.changelog_decision)

    print(f"created: {card_path}")
    print(f"created: {e2e_path}")
    print(f"created: {acceptance_path}")
    if args.add_changelog:
        print("updated: docs/changelog.md (if present and card id was new)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
