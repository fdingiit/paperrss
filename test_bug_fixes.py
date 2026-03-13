#!/usr/bin/env python3
"""Unit tests for v0.4.0 bug fixes:
- ranking_tuple: default (inference_acceleration) priority includes is_inference_accel element
- parse_daily_report: OSError guard returns safe default dict
- build_weekly_report_markdown: write=False does not write file to disk
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import arxiv_rss_assistant
import app_daemon


def _make_paper(ts: float = 1_700_000_000.0) -> arxiv_rss_assistant.Paper:
    """Return a minimal Paper instance with a fixed published timestamp."""
    return arxiv_rss_assistant.Paper(
        paper_id="2401.00001",
        title="Test Paper",
        summary="Summary text.",
        published=datetime.fromtimestamp(ts, tz=timezone.utc),
        authors=["Author One"],
        categories=["cs.LG"],
        link="https://arxiv.org/abs/2401.00001",
    )


def _make_meta(relevant: bool = True, is_inference_accel: bool = False, score: int = 5) -> dict:
    return {
        "relevant": relevant,
        "is_inference_accel": is_inference_accel,
        "score": score,
    }


class TestRankingTuple(unittest.TestCase):
    """ranking_tuple should include is_inference_accel for the default priority."""

    def test_default_priority_includes_is_inference_accel(self) -> None:
        paper = _make_paper()
        meta = _make_meta(relevant=True, is_inference_accel=True, score=8)
        t = arxiv_rss_assistant.ranking_tuple(paper, meta, "inference_acceleration")
        # Tuple must have 4 elements: (relevant, is_inference_accel, score, timestamp)
        self.assertEqual(len(t), 4)
        self.assertEqual(t[0], 1)  # relevant
        self.assertEqual(t[1], 1)  # is_inference_accel
        self.assertEqual(t[2], 8)  # unified_score
        self.assertAlmostEqual(t[3], paper.published.timestamp())

    def test_default_priority_is_inference_accel_false(self) -> None:
        paper = _make_paper()
        meta = _make_meta(relevant=True, is_inference_accel=False, score=3)
        t = arxiv_rss_assistant.ranking_tuple(paper, meta, "inference_acceleration")
        self.assertEqual(len(t), 4)
        self.assertEqual(t[1], 0)  # is_inference_accel flag is 0

    def test_default_priority_sorts_inference_accel_above_non_accel(self) -> None:
        """Papers with is_inference_accel=True should rank higher than those without."""
        paper_accel = _make_paper(ts=1_700_000_000.0)
        paper_plain = _make_paper(ts=1_700_000_001.0)  # newer but not inference-accel
        meta_accel = _make_meta(relevant=True, is_inference_accel=True, score=5)
        meta_plain = _make_meta(relevant=True, is_inference_accel=False, score=5)
        t_accel = arxiv_rss_assistant.ranking_tuple(paper_accel, meta_accel, "inference_acceleration")
        t_plain = arxiv_rss_assistant.ranking_tuple(paper_plain, meta_plain, "inference_acceleration")
        self.assertGreater(t_accel, t_plain)

    def test_balanced_priority_tuple_length(self) -> None:
        paper = _make_paper()
        meta = _make_meta(relevant=True, score=4)
        t = arxiv_rss_assistant.ranking_tuple(paper, meta, "balanced")
        self.assertEqual(len(t), 3)

    def test_recent_priority_tuple_length(self) -> None:
        paper = _make_paper()
        meta = _make_meta(relevant=True, score=4)
        t = arxiv_rss_assistant.ranking_tuple(paper, meta, "recent")
        self.assertEqual(len(t), 2)

    def test_default_and_balanced_tuples_are_comparable(self) -> None:
        """Tuples from the same priority mode must be comparable without TypeError."""
        p1 = _make_paper(ts=1_700_000_000.0)
        p2 = _make_paper(ts=1_700_000_001.0)
        meta1 = _make_meta(relevant=True, is_inference_accel=True, score=7)
        meta2 = _make_meta(relevant=False, is_inference_accel=False, score=2)
        for priority in ("inference_acceleration", "balanced", "recent"):
            t1 = arxiv_rss_assistant.ranking_tuple(p1, meta1, priority)
            t2 = arxiv_rss_assistant.ranking_tuple(p2, meta2, priority)
            # Should not raise TypeError
            _ = t1 > t2


class TestParseDailyReportOSError(unittest.TestCase):
    """parse_daily_report should return a safe default dict on OSError."""

    def test_oserror_returns_default_dict(self) -> None:
        missing = Path("/nonexistent/path/to/2024-01-15.md")
        result = app_daemon.parse_daily_report(missing)
        self.assertEqual(result["new_scanned"], 0)
        self.assertEqual(result["relevant"], 0)
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["date"], missing.stem)
        self.assertEqual(result["path"], missing)

    def test_oserror_on_permission_denied(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
                result = app_daemon.parse_daily_report(tmp_path)
            self.assertEqual(result["new_scanned"], 0)
            self.assertEqual(result["relevant"], 0)
            self.assertEqual(result["entries"], [])
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_valid_file_still_parsed(self) -> None:
        report_text = (
            "# arXiv Daily LLM Radar\n"
            "- New papers scanned: 42\n"
            "- Relevant papers (score>=5): 3\n"
            "\n"
            "## Full Ranked Papers\n"
            "\n"
            "### 1. A Great Paper [relevant]\n"
            "- Recommendation score: 7\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(report_text)
            tmp_path = Path(tmp.name)
        try:
            result = app_daemon.parse_daily_report(tmp_path)
            self.assertEqual(result["new_scanned"], 42)
            self.assertEqual(result["relevant"], 3)
            self.assertEqual(len(result["entries"]), 1)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestBuildWeeklyReportWriteFlag(unittest.TestCase):
    """build_weekly_report_markdown(write=False) must not write any file."""

    def _make_daily_report(self, directory: Path, date_str: str) -> None:
        content = (
            f"# arXiv Daily LLM Radar - {date_str}\n"
            "- New papers scanned: 10\n"
            "- Relevant papers (score>=5): 2\n"
            "\n"
            "## Full Ranked Papers\n"
            "\n"
            "### 1. Some Paper [relevant]\n"
            "- Recommendation score: 6\n"
            "- Tags: inference / kv-cache\n"
            "- Brief: Fast decoding via KV cache pruning.\n"
        )
        (directory / f"{date_str}.md").write_text(content, encoding="utf-8")

    def test_write_false_does_not_create_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            week_end = datetime(2024, 1, 21, 12, 0, 0, tzinfo=timezone.utc)
            # Create one daily report in the 7-day window (2024-01-15 to 2024-01-21)
            self._make_daily_report(report_dir, "2024-01-18")
            weekly_path = report_dir / "weekly-2024-W03.md"
            result = app_daemon.build_weekly_report_markdown(
                report_dir, weekly_path, week_end, write=False
            )
            self.assertIsNotNone(result)
            self.assertFalse(
                weekly_path.exists(),
                "weekly report file must NOT be written when write=False",
            )

    def test_write_true_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            week_end = datetime(2024, 1, 21, 12, 0, 0, tzinfo=timezone.utc)
            self._make_daily_report(report_dir, "2024-01-18")
            weekly_path = report_dir / "weekly-2024-W03.md"
            result = app_daemon.build_weekly_report_markdown(
                report_dir, weekly_path, week_end, write=True
            )
            self.assertIsNotNone(result)
            self.assertTrue(
                weekly_path.exists(),
                "weekly report file MUST be written when write=True",
            )

    def test_write_false_returns_correct_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            week_end = datetime(2024, 1, 21, 12, 0, 0, tzinfo=timezone.utc)
            self._make_daily_report(report_dir, "2024-01-18")
            weekly_path = report_dir / "weekly-2024-W03.md"
            result = app_daemon.build_weekly_report_markdown(
                report_dir, weekly_path, week_end, write=False
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["report_count"], 1)
            self.assertEqual(result["new_scanned"], 10)
            self.assertEqual(result["relevant"], 2)

    def test_no_reports_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            week_end = datetime(2024, 1, 21, 12, 0, 0, tzinfo=timezone.utc)
            weekly_path = report_dir / "weekly-2024-W03.md"
            result = app_daemon.build_weekly_report_markdown(
                report_dir, weekly_path, week_end, write=False
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
