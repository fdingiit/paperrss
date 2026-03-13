import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app_daemon


class StopAfterFirstWait:
    def __init__(self) -> None:
        self.wait_calls: list[float] = []

    def is_set(self) -> bool:
        return False

    def wait(self, seconds: float) -> bool:
        self.wait_calls.append(float(seconds))
        return True


class DailySchedulerTests(unittest.TestCase):
    def test_daily_trigger_sends_start_notification(self) -> None:
        stop_event = StopAfterFirstWait()
        app_state = app_daemon.AppState()
        due_time = datetime(2026, 3, 13, 9, 0, tzinfo=app_daemon.SHANGHAI_TZ)
        config = {
            "report_modes": ["daily"],
            "daily_retry_seconds": 456,
            "schedule_state": "storage/data/schedule_state.json",
            "slack_webhook_url": "https://example.test/webhook",
        }

        with (
            patch("app_daemon.load_schedule_state", return_value={}),
            patch("app_daemon.next_daily_due", return_value=(due_time, "2026-03-13", True)),
            patch("app_daemon.build_rss_args", return_value=SimpleNamespace()),
            patch("app_daemon.arxiv_rss_assistant.run", return_value=0),
            patch("app_daemon.arxiv_rss_assistant.post_to_slack", return_value=(1, 0, None)) as slack_mock,
            patch("app_daemon.now_utc_iso", return_value="2026-03-13T01:00:00Z"),
            patch("app_daemon.upsert_schedule_state_key"),
        ):
            app_daemon.daily_rss_loop(stop_event, app_state, "storage/config.json", config, threading.Lock())

        slack_mock.assert_called_once()
        args, kwargs = slack_mock.call_args
        self.assertEqual(args[0], "https://example.test/webhook")
        self.assertEqual(kwargs["send_interval_seconds"], 0.0)
        self.assertEqual(kwargs["max_retries"], 4)
        self.assertEqual(len(args[1]), 1)
        self.assertIn("RSS daily run started (2026-03-13)", args[1][0]["text"])

    def test_daily_failure_does_not_mark_due_key(self) -> None:
        stop_event = StopAfterFirstWait()
        app_state = app_daemon.AppState()
        due_time = datetime(2026, 3, 13, 9, 0, tzinfo=app_daemon.SHANGHAI_TZ)
        retry_seconds = 123
        config = {
            "report_modes": ["daily"],
            "daily_retry_seconds": retry_seconds,
            "schedule_state": "storage/data/schedule_state.json",
        }

        with (
            patch("app_daemon.load_schedule_state", return_value={}),
            patch("app_daemon.next_daily_due", return_value=(due_time, "2026-03-13", True)),
            patch("app_daemon.build_rss_args", return_value=SimpleNamespace()),
            patch("app_daemon.arxiv_rss_assistant.run", return_value=2),
            patch("app_daemon.now_utc_iso", return_value="2026-03-13T01:00:00Z"),
            patch("app_daemon.upsert_schedule_state_key") as upsert_mock,
        ):
            app_daemon.daily_rss_loop(stop_event, app_state, "storage/config.json", config, threading.Lock())

        upsert_mock.assert_not_called()
        self.assertEqual(stop_event.wait_calls, [float(retry_seconds)])

    def test_daily_success_marks_due_key(self) -> None:
        stop_event = StopAfterFirstWait()
        app_state = app_daemon.AppState()
        due_time = datetime(2026, 3, 13, 9, 0, tzinfo=app_daemon.SHANGHAI_TZ)
        config = {
            "report_modes": ["daily"],
            "daily_retry_seconds": 456,
            "schedule_state": "storage/data/schedule_state.json",
        }

        with (
            patch("app_daemon.load_schedule_state", return_value={}),
            patch("app_daemon.next_daily_due", return_value=(due_time, "2026-03-13", True)),
            patch("app_daemon.build_rss_args", return_value=SimpleNamespace()),
            patch("app_daemon.arxiv_rss_assistant.run", return_value=0),
            patch("app_daemon.now_utc_iso", return_value="2026-03-13T01:00:00Z"),
            patch("app_daemon.upsert_schedule_state_key") as upsert_mock,
        ):
            app_daemon.daily_rss_loop(stop_event, app_state, "storage/config.json", config, threading.Lock())

        upsert_mock.assert_called_once_with(
            Path("storage/data/schedule_state.json"),
            "last_daily_key",
            "2026-03-13",
        )
        self.assertEqual(stop_event.wait_calls, [1.0])


if __name__ == "__main__":
    unittest.main()
