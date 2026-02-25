#!/usr/bin/env python3
"""Resident app daemon for arXiv subscription + Slack ping/pong + health endpoint."""

from __future__ import annotations

import argparse
import collections
import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import arxiv_rss_assistant
import paperrss_version
import paperrss_utils
import slack_cmd_toolkit
import slack_healthcheck

logger = logging.getLogger("paperrss.daemon")
APP_VERSION = paperrss_version.get_version()

setup_logging = paperrss_utils.setup_logging
load_json = paperrss_utils.load_json
parse_bool = paperrss_utils.parse_bool


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {
            "started_at": now_utc_iso(),
            "health": {
                "last_status": "never",
                "last_error": None,
            },
            "rss": {
                "last_run_at": None,
                "last_status": "never",
                "last_error": None,
            },
            "pingpong": {
                "last_poll_at": None,
                "last_reply_at": None,
                "last_status": "never",
                "last_error": None,
                "last_ts": None,
            },
        }

    def update(self, section: str, patch: dict[str, Any]) -> None:
        with self.lock:
            self.data[section].update(patch)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.data))


class HealthHandler(BaseHTTPRequestHandler):
    app_state: AppState | None = None

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/healthz", "/status"}:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return

        payload = self.app_state.snapshot() if self.app_state else {"status": "unknown"}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return


def build_rss_args(config_path: str, config: dict, force_push: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        config=config_path,
        categories=config.get("rss_categories", arxiv_rss_assistant.DEFAULT_CATEGORIES),
        max_results=int(config.get("rss_max_results", 250)),
        state=config.get("rss_state", "storage/data/state.json"),
        subscription_store=config.get("subscription_store", "storage/data/subscriptions.json"),
        push_state=config.get("push_state", "storage/data/push_state.json"),
        output_dir=config.get("rss_output_dir", "storage/reports"),
        feed_file=config.get("rss_feed_file"),
        slack_webhook_url=config.get("slack_webhook_url"),
        slack_when=config.get("slack_when"),
        force_push=force_push,
        sort_priority=config.get("sort_priority"),
        classify_workers=int(config.get("classify_workers", 8)),
        author_enrich=parse_bool(config.get("author_enrich", True), default=True),
        author_cache=config.get("author_cache", "storage/data/author_cache.json"),
        log_level="INFO",
        log_file=None,
    )


def rss_loop(
    stop_event: threading.Event,
    app_state: AppState,
    config_path: str,
    config: dict,
    rss_run_lock: threading.Lock,
) -> None:
    interval_sec = int(config.get("rss_interval_seconds", 86400))
    run_on_startup = bool(config.get("rss_run_on_startup", True))

    if not run_on_startup:
        time.sleep(interval_sec)

    while not stop_event.is_set():
        try:
            args = build_rss_args(config_path, config, force_push=False)
            logger.info("rss_loop_tick")
            with rss_run_lock:
                code = arxiv_rss_assistant.run(args)
            app_state.update(
                "rss",
                {
                    "last_run_at": now_utc_iso(),
                    "last_status": "ok" if code == 0 else "error",
                    "last_error": None if code == 0 else f"exit_code={code}",
                },
            )
            logger.info("rss_loop_done status=%s", "ok" if code == 0 else "error")
        except Exception as exc:  # noqa: BLE001
            logger.exception("rss_loop_error")
            app_state.update(
                "rss",
                {
                    "last_run_at": now_utc_iso(),
                    "last_status": "error",
                    "last_error": str(exc),
                },
            )

        if stop_event.wait(interval_sec):
            break


def socket_mode_loop(
    stop_event: threading.Event,
    app_state: AppState,
    config_path: str,
    config: dict,
    rss_run_lock: threading.Lock,
) -> None:
    bot_token = config.get("slack_bot_token")
    app_token = config.get("slack_app_token")
    health_url = f"http://{config.get('health_host', '127.0.0.1')}:{int(config.get('health_port', 8080))}/healthz"
    reply_in_thread = parse_bool(config.get("cmd_reply_in_thread", True), default=True)
    report_dir = str(config.get("rss_output_dir", "storage/reports"))

    if not bot_token or not app_token:
        logger.warning("socket_mode_disabled missing slack_bot_token or slack_app_token")
        app_state.update(
            "pingpong",
            {
                "last_poll_at": now_utc_iso(),
                "last_status": "disabled",
                "last_error": "missing slack_bot_token or slack_app_token",
            },
        )
        return

    try:
        import websocket  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        logger.error("socket_mode_unavailable missing dependency websocket-client")
        app_state.update(
            "pingpong",
            {
                "last_poll_at": now_utc_iso(),
                "last_status": "error",
                "last_error": "missing dependency: websocket-client",
            },
        )
        return

    seen_event_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
    reconnect_backoff = 1

    while not stop_event.is_set():
        ws = None
        try:
            opened = slack_healthcheck.slack_api_call(app_token, "apps.connections.open", {})
            ws_url = opened.get("url")
            if not ws_url:
                raise RuntimeError("apps.connections.open returned no url")

            logger.info("socket_mode_connecting")
            ws = websocket.create_connection(ws_url, timeout=30)
            ws.settimeout(1.0)
            reconnect_backoff = 1
            logger.info("socket_mode_connected")

            app_state.update(
                "pingpong",
                {
                    "last_poll_at": now_utc_iso(),
                    "last_status": "ok",
                    "last_error": None,
                },
            )

            while not stop_event.is_set():
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue

                if not raw:
                    continue
                envelope = json.loads(raw)
                envelope_id = envelope.get("envelope_id")
                if envelope_id:
                    ws.send(json.dumps({"envelope_id": envelope_id}))

                if envelope.get("type") != "events_api":
                    continue
                payload = envelope.get("payload") or {}
                event = payload.get("event") or {}
                if event.get("type") != "app_mention":
                    continue

                event_id = str(payload.get("event_id") or envelope.get("event_id") or "")
                if event_id and event_id in seen_event_ids:
                    logger.info("cmd_socket_dedup event_id=%s", event_id)
                    continue
                if event_id:
                    seen_event_ids[event_id] = None
                    seen_event_ids.move_to_end(event_id)
                    if len(seen_event_ids) > 4000:
                        seen_event_ids.popitem(last=False)

                text = str(event.get("text") or "")
                command = slack_cmd_toolkit.parse_command(text)
                if not command:
                    logger.info("cmd_socket_mention_ignored text=%s", text[:120])
                    continue
                if command == "-force":
                    channel = event.get("channel")
                    ts = event.get("ts")
                    if not channel:
                        logger.warning("cmd_socket_missing_channel event=%s", event)
                        continue

                    if rss_run_lock.locked():
                        response = {
                            "text": "force rerun skipped: RSS task already running",
                            "blocks": [
                                {"type": "header", "text": {"type": "plain_text", "text": "Force Rerun"}},
                                {"type": "section", "text": {"type": "mrkdwn", "text": "RSS task is running. Try again shortly."}},
                            ],
                        }
                    else:
                        state_path = Path(config.get("rss_state", "storage/data/state.json"))
                        report_path = Path(config.get("rss_output_dir", "storage/reports")) / (
                            datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".md"
                        )
                        reply_thread_ts = event.get("thread_ts") or ts

                        def run_force_task() -> None:
                            removed_state = False
                            removed_report = False
                            try:
                                with rss_run_lock:
                                    if state_path.exists():
                                        state_path.unlink()
                                        removed_state = True
                                    if report_path.exists():
                                        report_path.unlink()
                                        removed_report = True
                                    logger.info(
                                        "cmd_force_cleanup_done removed_state=%s removed_report=%s state=%s report=%s",
                                        removed_state,
                                        removed_report,
                                        state_path,
                                        report_path,
                                    )
                                    args = build_rss_args(config_path, config, force_push=True)
                                    code = arxiv_rss_assistant.run(args)

                                app_state.update(
                                    "rss",
                                    {
                                        "last_run_at": now_utc_iso(),
                                        "last_status": "ok" if code == 0 else "error",
                                        "last_error": None if code == 0 else f"exit_code={code}",
                                    },
                                )
                                status = "ok" if code == 0 else f"error(exit_code={code})"
                                done_payload = {
                                    "channel": channel,
                                    "text": f"force rerun completed: {status}",
                                    "blocks": [
                                        {"type": "header", "text": {"type": "plain_text", "text": "Force Rerun Done"}},
                                        {
                                            "type": "section",
                                            "fields": [
                                                {"type": "mrkdwn", "text": f"*Status*\n{status}"},
                                                {"type": "mrkdwn", "text": f"*Report*\n`{report_path}`"},
                                                {"type": "mrkdwn", "text": f"*State reset*\n{removed_state}"},
                                                {"type": "mrkdwn", "text": f"*Report reset*\n{removed_report}"},
                                            ],
                                        },
                                    ],
                                }
                            except Exception as exc:  # noqa: BLE001
                                logger.exception("cmd_force_run_error")
                                done_payload = {
                                    "channel": channel,
                                    "text": f"force rerun failed: {exc}",
                                }
                            if reply_in_thread and reply_thread_ts:
                                done_payload["thread_ts"] = reply_thread_ts
                            try:
                                slack_healthcheck.slack_api_call(bot_token, "chat.postMessage", done_payload)
                            except Exception:  # noqa: BLE001
                                logger.exception("cmd_force_done_notify_error")

                        threading.Thread(target=run_force_task, daemon=True).start()
                        response = {
                            "text": "force rerun started",
                            "blocks": [
                                {"type": "header", "text": {"type": "plain_text", "text": "Force Rerun"}},
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": "Task started in background. I will post a completion message when done.",
                                    },
                                },
                            ],
                        }
                else:
                    response = slack_cmd_toolkit.build_command_response(command, health_url, report_dir=report_dir)
                channel = event.get("channel")
                ts = event.get("ts")
                if not channel:
                    logger.warning("cmd_socket_missing_channel event=%s", event)
                    continue
                params = {"channel": channel, "text": response.get("text", "")}
                if response.get("blocks"):
                    params["blocks"] = response["blocks"]
                if reply_in_thread and ts:
                    params["thread_ts"] = event.get("thread_ts") or ts
                slack_healthcheck.slack_api_call(bot_token, "chat.postMessage", params)
                logger.info("cmd_replied_socket cmd=%s channel=%s ts=%s", command, channel, ts)
                app_state.update(
                    "pingpong",
                    {
                        "last_poll_at": now_utc_iso(),
                        "last_reply_at": now_utc_iso(),
                        "last_status": "ok",
                        "last_error": None,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("socket_mode_loop_error")
            app_state.update(
                "pingpong",
                {
                    "last_poll_at": now_utc_iso(),
                    "last_status": "error",
                    "last_error": str(exc),
                },
            )
            if stop_event.wait(min(reconnect_backoff, 30)):
                break
            reconnect_backoff = min(reconnect_backoff * 2, 30)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass


def health_server_loop(stop_event: threading.Event, app_state: AppState, host: str, port: int) -> None:
    HealthHandler.app_state = app_state
    try:
        server = ThreadingHTTPServer((host, port), HealthHandler)
    except Exception as exc:  # noqa: BLE001
        logger.exception("health_server_bind_failed host=%s port=%s", host, port)
        app_state.update("health", {"last_status": "error", "last_error": str(exc)})
        return

    logger.info("health_server_started url=http://%s:%s/healthz", host, port)
    app_state.update("health", {"last_status": "ok", "last_error": None})
    server.timeout = 1
    while not stop_event.is_set():
        server.handle_request()
    server.server_close()


def run(args: argparse.Namespace) -> int:
    config_path = args.config
    config = load_json(Path(config_path))
    log_level = str(config.get("log_level", args.log_level))
    log_file = config.get("log_file", args.log_file)
    setup_logging(log_level, log_file)
    logger.info("app_version=%s", APP_VERSION)

    app_state = AppState()
    stop_event = threading.Event()
    rss_run_lock = threading.Lock()

    host = config.get("health_host", "127.0.0.1")
    port = int(config.get("health_port", 8080))

    logger.info("cmd_mode mode=socket")

    threads = [
        threading.Thread(
            target=rss_loop,
            args=(stop_event, app_state, config_path, config, rss_run_lock),
            daemon=True,
        ),
        threading.Thread(
            target=socket_mode_loop,
            args=(stop_event, app_state, config_path, config, rss_run_lock),
            daemon=True,
        ),
        threading.Thread(target=health_server_loop, args=(stop_event, app_state, host, port), daemon=True),
    ]

    for t in threads:
        t.start()

    logger.info("daemon_started health=http://%s:%s/healthz", host, port)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("daemon_shutting_down")
        stop_event.set()
        for t in threads:
            t.join(timeout=3)
        logger.info("daemon_stopped")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="paperrss resident daemon")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--config", default="config.json", help="Path to JSON config")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG/INFO/WARN/ERROR")
    parser.add_argument("--log-file", default=None, help="Optional log file path")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
