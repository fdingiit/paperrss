#!/usr/bin/env python3
"""Slack command toolkit for channel message commands (mention + flags).

Current commands:
- @bot -ping: health check
- @bot -help: list commands
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import paperrss_version
import slack_healthcheck

logger = logging.getLogger("paperrss.cmd")
APP_VERSION = paperrss_version.get_version()


def setup_logging(log_level: str = "INFO", log_file: str | None = None) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)

    class MaxLevelFilter(logging.Filter):
        def __init__(self, max_level: int) -> None:
            super().__init__()
            self.max_level = max_level

        def filter(self, record: logging.LogRecord) -> bool:
            return record.levelno <= self.max_level

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.addFilter(MaxLevelFilter(logging.INFO))
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    handlers: list[logging.Handler] = [stdout_handler, stderr_handler]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
    )


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_bot_user_id(token: str) -> str:
    payload = slack_healthcheck.slack_api_call(token, "auth.test", {})
    user_id = payload.get("user_id")
    if not user_id:
        raise RuntimeError("auth.test missing user_id")
    return user_id


def parse_command(text: str, bot_user_id: str | None = None) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None

    # Accept any Slack mention token. This avoids strict ID coupling when users
    # mention the app entity instead of the bot user entity.
    if not re.search(r"<@[^>]+>", raw):
        return None

    # Remove all mentions then parse first token command.
    cleaned = re.sub(r"<@[^>]+>", " ", raw).strip()
    if not cleaned:
        return None
    cmd = cleaned.split()[0].strip().lower()
    if not cmd.startswith("-"):
        return None
    return cmd


def ping_payload(health_url: str | None) -> str:
    if not health_url:
        return "pong"
    try:
        with urllib.request.urlopen(health_url, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        snippet = body[:600]
        return f"pong\nhealth: {health_url}\n```{snippet}```"
    except Exception as exc:  # noqa: BLE001
        return f"pong (health check failed: {exc})"


def _extract_report_stats(text: str) -> tuple[int, int, list[dict[str, str]]]:
    m_new = re.search(r"- New papers scanned: (\d+)", text)
    m_rel = re.search(r"- Relevant papers .*: (\d+)", text)
    new_scanned = int(m_new.group(1)) if m_new else 0
    relevant = int(m_rel.group(1)) if m_rel else 0

    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        m_title = re.match(r"^### (\d+)\.\s+(.*?)\s+\[(relevant|background)\]$", line)
        if m_title:
            if current:
                entries.append(current)
            current = {"rank": m_title.group(1), "title": m_title.group(2), "tag": m_title.group(3)}
            continue
        if current and line.startswith("- Link: "):
            current["link"] = line[len("- Link: ") :].strip()
    if current:
        entries.append(current)
    return new_scanned, relevant, entries


def build_daily_brief_payload(report_dir: str) -> dict:
    report_root = Path(report_dir)
    files = sorted(report_root.glob("*.md"))
    if not files:
        return {"text": "No report found yet. Run RSS scan first."}

    today_name = datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".md"
    report_path = report_root / today_name
    if not report_path.exists():
        report_path = files[-1]

    try:
        text = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"text": f"Failed to read report: {exc}"}

    new_scanned, relevant, entries = _extract_report_stats(text)
    source_note = ""
    if not entries:
        for older in reversed(files):
            if older == report_path:
                continue
            try:
                old_text = older.read_text(encoding="utf-8")
            except OSError:
                continue
            old_new, old_rel, old_entries = _extract_report_stats(old_text)
            if old_entries:
                report_path = older
                new_scanned, relevant, entries = old_new, old_rel, old_entries
                source_note = " (fallback: latest non-empty report)"
                break

    top = entries[:8]
    lines = []
    for e in top:
        title = e.get("title", "N/A")
        link = e.get("link", "")
        rank = e.get("rank", "?")
        tag = e.get("tag", "unknown")
        if link:
            lines.append(f"{rank}. <{link}|{title}> [{tag}]")
        else:
            lines.append(f"{rank}. {title} [{tag}]")
    top_text = "\n".join(lines) if lines else "No ranked entries."

    return {
        "text": f"Daily brief ({report_path.name})",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Daily RSS Brief"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Report*\n`{report_path.name}`{source_note}"},
                    {"type": "mrkdwn", "text": f"*New scanned*\n{new_scanned}"},
                    {"type": "mrkdwn", "text": f"*Relevant*\n{relevant}"},
                    {"type": "mrkdwn", "text": f"*Total ranked*\n{len(entries)}"},
                ],
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Top Papers*\n{top_text}"}},
            *(
                [
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": "No non-empty report found yet. Run RSS scan once to generate rankings.",
                            }
                        ],
                    }
                ]
                if not entries
                else []
            ),
        ],
    }


def build_command_response(command: str, health_url: str | None, report_dir: str = "reports") -> dict:
    if command == "-ping":
        if not health_url:
            return {"text": "pong"}
        try:
            with urllib.request.urlopen(health_url, timeout=5) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            payload = json.loads(body)
            rss = payload.get("rss", {})
            cmd = payload.get("pingpong", {})
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": "Pong"}},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Now*\n{now}"},
                        {"type": "mrkdwn", "text": f"*Health URL*\n{health_url}"},
                        {"type": "mrkdwn", "text": f"*RSS Status*\n{rss.get('last_status', 'unknown')}"},
                        {"type": "mrkdwn", "text": f"*CMD Status*\n{cmd.get('last_status', 'unknown')}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*Last RSS Run*\n"
                            f"`{rss.get('last_run_at', 'N/A')}`\n"
                            "*Last CMD Reply*\n"
                            f"`{cmd.get('last_reply_at', 'N/A')}`"
                        ),
                    },
                },
            ]
            return {"text": "pong", "blocks": blocks}
        except Exception as exc:  # noqa: BLE001
            return {"text": f"pong (health check failed: {exc})"}
    if command == "-help":
        return {
            "text": f"LLMRssBot v{APP_VERSION} | commands: -ping -brief -force -help",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "LLMRssBot Commands"}},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*Capabilities*\n"
                            "I monitor arXiv daily for LLM training/inference/infrastructure papers, "
                            "push ranked rich-format updates to Slack, and support ops/debug commands."
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "`-ping` health check\n"
                            "`-brief` today's RSS brief\n"
                            "`-force` reset today's state/report and rerun full scan\n"
                            "`-help` show commands"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                "Usage: `@LLMRssBot -ping` / `@LLMRssBot -brief` / `@LLMRssBot -force`"
                                f"  |  version: `v{APP_VERSION}`"
                            ),
                        }
                    ],
                },
            ],
        }
    if command == "-brief":
        return build_daily_brief_payload(report_dir)
    return {"text": f"Unknown command: {command}. Try -help"}


def execute_command(command: str, health_url: str | None, report_dir: str = "reports") -> str:
    return build_command_response(command, health_url, report_dir=report_dir).get("text", "")


def process_batch(
    token: str,
    channel_id: str,
    bot_user_id: str,
    last_ts: str | None,
    health_url: str | None,
    reply_in_thread: bool,
    report_dir: str = "reports",
) -> tuple[str | None, int, int]:
    messages = slack_healthcheck.fetch_messages(token, channel_id, last_ts)
    replied = 0
    matched = 0

    for msg in messages:
        ts = msg.get("ts")
        if not ts:
            continue
        if msg.get("subtype"):
            last_ts = ts
            continue

        command = parse_command(msg.get("text", ""), bot_user_id)
        if command:
            matched += 1
            payload = build_command_response(command, health_url, report_dir=report_dir)
            params = {"channel": channel_id, "text": payload.get("text", "")}
            if payload.get("blocks"):
                params["blocks"] = payload["blocks"]
            if reply_in_thread:
                params["thread_ts"] = ts
            slack_healthcheck.slack_api_call(token, "chat.postMessage", params)
            replied += 1
            logger.info("cmd_replied ts=%s cmd=%s", ts, command)

        last_ts = ts

    return last_ts, matched, replied


def run(args: argparse.Namespace) -> int:
    logger.info("app_version=%s", APP_VERSION)
    config = load_json(Path(args.config))
    token = args.bot_token or config.get("slack_bot_token")
    channel_id = args.channel_id or config.get("slack_channel_id")
    health_url = args.health_url or config.get("health_url", "http://127.0.0.1:8080/healthz")
    report_dir = config.get("rss_output_dir", "reports")
    reply_in_thread = bool(config.get("cmd_reply_in_thread", True))

    if not token or not channel_id:
        logger.error("missing slack_bot_token or slack_channel_id")
        return 2

    bot_user_id = get_bot_user_id(token)
    state_path = Path(args.state)
    state = load_json(state_path)
    last_ts = state.get("last_ts")

    logger.info("cmd_toolkit_started channel=%s bot_user_id=%s", channel_id, bot_user_id)
    while True:
        try:
            last_ts, matched, replied = process_batch(
                token=token,
                channel_id=channel_id,
                bot_user_id=bot_user_id,
                last_ts=last_ts,
                health_url=health_url,
                reply_in_thread=reply_in_thread,
                report_dir=report_dir,
            )
            if last_ts:
                save_json(state_path, {"last_ts": last_ts})
            logger.info("cmd_toolkit_polled matched=%s replied=%s last_ts=%s", matched, replied, last_ts)
        except Exception:  # noqa: BLE001
            logger.exception("cmd_toolkit_loop_error")

        if args.once:
            break
        import time

        time.sleep(args.interval)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Slack command toolkit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--config", default="config.json", help="Path to JSON config")
    parser.add_argument("--bot-token", default=None, help="Slack bot token xoxb-...")
    parser.add_argument("--channel-id", default=None, help="Slack channel ID")
    parser.add_argument("--health-url", default=None, help="Health endpoint URL for -ping")
    parser.add_argument("--state", default="data/cmd_state.json", help="State path")
    parser.add_argument("--interval", type=int, default=8, help="Polling interval seconds")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    parser.add_argument("--log-file", default=None, help="Optional log file")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    setup_logging(args.log_level, args.log_file)
    raise SystemExit(run(args))
