#!/usr/bin/env python3
"""Slack healthcheck bot: reply 'pong' when someone sends 'ping' in a channel."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import paperrss_version
import paperrss_utils

logger = logging.getLogger("paperrss.healthcheck")
APP_VERSION = paperrss_version.get_version()

setup_logging = paperrss_utils.setup_logging
load_json = paperrss_utils.load_json
save_json = paperrss_utils.save_json


def slack_api_call(token: str, method: str, params: dict | None = None) -> dict:
    encoded_params: dict[str, str] = {}
    for k, v in (params or {}).items():
        if isinstance(v, (dict, list)):
            encoded_params[k] = json.dumps(v, ensure_ascii=False)
        else:
            encoded_params[k] = str(v)
    data = urllib.parse.urlencode(encoded_params).encode("utf-8")
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Slack API {method} failed: {payload.get('error', 'unknown_error')}")
    return payload


def fetch_messages(token: str, channel_id: str, oldest: str | None, limit: int = 200) -> list[dict]:
    params = {"channel": channel_id, "limit": str(limit)}
    if oldest:
        params["oldest"] = oldest
        params["inclusive"] = "false"
    payload = slack_api_call(token, "conversations.history", params)
    messages = payload.get("messages", [])
    # Slack returns newest first; process oldest -> newest.
    messages.sort(key=lambda m: float(m.get("ts", "0")))
    return messages


def send_message(token: str, channel_id: str, text: str) -> None:
    slack_api_call(token, "chat.postMessage", {"channel": channel_id, "text": text})


def should_reply_ping(msg: dict, ping_text: str) -> bool:
    # Ignore bot/system messages.
    if msg.get("subtype"):
        return False
    text = (msg.get("text") or "").strip().lower()
    return text == ping_text.lower()


def run(args: argparse.Namespace) -> int:
    logger.info("app_version=%s", APP_VERSION)
    config = load_json(Path(args.config))
    token = args.bot_token or config.get("slack_bot_token")
    channel_id = args.channel_id or config.get("slack_channel_id")
    ping_text = args.ping_text
    pong_text = args.pong_text

    if not token or not channel_id:
        logger.error("missing slack_bot_token or slack_channel_id")
        return 2

    state_path = Path(args.state)
    state = load_json(state_path)
    last_ts = state.get("last_ts")

    logger.info("healthcheck_started channel=%s ping=%s pong=%s", channel_id, ping_text, pong_text)
    while True:
        try:
            messages = fetch_messages(token, channel_id, last_ts)
            replied = 0
            for msg in messages:
                ts = msg.get("ts")
                if not ts:
                    continue
                if should_reply_ping(msg, ping_text):
                    send_message(token, channel_id, pong_text)
                    replied += 1
                last_ts = ts

            if messages:
                save_json(state_path, {"last_ts": last_ts})

            logger.info("healthcheck_polled polled=%s replied=%s last_ts=%s", len(messages), replied, last_ts)
        except Exception as exc:  # noqa: BLE001
            logger.exception("healthcheck_loop_error")

        if args.once:
            break
        time.sleep(args.interval)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Slack ping/pong healthcheck bot")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--config", default="config.json", help="Path to JSON config")
    parser.add_argument("--bot-token", default=None, help="Slack Bot User OAuth Token (xoxb-...) ")
    parser.add_argument("--channel-id", default=None, help="Slack channel ID (e.g., C0123456789)")
    parser.add_argument("--state", default="data/healthcheck_state.json", help="State file path")
    parser.add_argument("--interval", type=int, default=10, help="Polling interval in seconds")
    parser.add_argument("--ping-text", default="ping", help="Incoming text to match")
    parser.add_argument("--pong-text", default="pong", help="Reply text")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG/INFO/WARN/ERROR")
    parser.add_argument("--log-file", default=None, help="Optional log file path")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    setup_logging(args.log_level, args.log_file)
    raise SystemExit(run(args))
