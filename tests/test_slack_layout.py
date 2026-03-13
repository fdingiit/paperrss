import unittest
from datetime import datetime, timezone
from pathlib import Path

import arxiv_rss_assistant


def make_paper(idx: int) -> arxiv_rss_assistant.Paper:
    return arxiv_rss_assistant.Paper(
        paper_id=f"2603.{idx:05d}v1",
        title=f"Paper {idx}",
        summary="A summary",
        published=datetime(2026, 3, 13, 0, idx % 60, tzinfo=timezone.utc),
        authors=["Alice", "Bob"],
        categories=["cs.LG"],
        link=f"https://arxiv.org/abs/2603.{idx:05d}",
    )


def make_meta(score: int) -> dict:
    return {
        "relevant": True,
        "primary_domain": "inference",
        "heuristic_score": score,
        "score": score,
        "inference_accel_score": 1,
        "is_inference_accel": True,
        "domain_scores": {"training": 0, "inference": 2, "infrastructure": 0},
        "domain_hits": {"training": [], "inference": ["inference", "serving"], "infrastructure": []},
        "llm_hits": ["llm"],
        "llm_brief": {
            "brief": f"这是论文的中文摘要，score={score}",
            "tags": ["推理", "系统"],
            "score": score,
            "interest_matches": ["大模型推理"],
        },
        "author_profile": {},
    }


class SlackLayoutTests(unittest.TestCase):
    def test_fifteen_rows_layout_top10_plus_compact_tail(self) -> None:
        ranked_rows = [(make_paper(i), make_meta(100 - i)) for i in range(1, 16)]

        messages, mapping = arxiv_rss_assistant.build_slack_messages(
            run_at=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
            new_total=15,
            ranked_rows=ranked_rows,
            report_path=Path("/tmp/demo.md"),
            report_date="2026-03-13",
            detail_limit=10,
        )

        self.assertEqual(len(messages), 12)
        self.assertIn("total=15", messages[0]["text"])
        self.assertTrue(messages[1]["text"].startswith("[1/15]"))
        self.assertTrue(messages[10]["text"].startswith("[10/15]"))
        self.assertIn("remaining 5", messages[11]["text"])

        self.assertEqual(mapping[2], ["2603.00001v1"])
        self.assertEqual(mapping[11], ["2603.00010v1"])
        self.assertEqual(
            mapping[12],
            [
                "2603.00011v1",
                "2603.00012v1",
                "2603.00013v1",
                "2603.00014v1",
                "2603.00015v1",
            ],
        )
        tail_text = messages[11]["blocks"][0]["text"]["text"]
        self.assertIn("\n\n*12.", tail_text)
        self.assertIn("摘要:", tail_text)

    def test_small_batch_keeps_one_detail_per_paper(self) -> None:
        ranked_rows = [(make_paper(i), make_meta(100 - i)) for i in range(1, 4)]

        messages, mapping = arxiv_rss_assistant.build_slack_messages(
            run_at=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
            new_total=3,
            ranked_rows=ranked_rows,
            report_path=Path("/tmp/demo.md"),
            report_date="2026-03-13",
            detail_limit=10,
        )

        self.assertEqual(len(messages), 4)
        self.assertEqual(len(mapping), 3)
        self.assertEqual(mapping[2], ["2603.00001v1"])
        self.assertEqual(mapping[4], ["2603.00003v1"])


if __name__ == "__main__":
    unittest.main()
