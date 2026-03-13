import unittest
from datetime import datetime, timezone

import arxiv_rss_assistant


def make_paper(paper_id: str, published: datetime, title: str) -> arxiv_rss_assistant.Paper:
    return arxiv_rss_assistant.Paper(
        paper_id=paper_id,
        title=title,
        summary="summary",
        published=published,
        authors=["Alice"],
        categories=["cs.LG"],
        link=f"https://arxiv.org/abs/{paper_id}",
    )


def make_meta(score: int) -> dict:
    return {
        "relevant": True,
        "primary_domain": "inference",
        "heuristic_score": score,
        "score": score,
        "inference_accel_score": 0,
        "domain_scores": {"training": 0, "inference": 1, "infrastructure": 0},
        "domain_hits": {"training": [], "inference": ["inference"], "infrastructure": []},
        "llm_hits": ["llm"],
        "llm_brief": {},
        "author_profile": {},
    }


class ReportBucketTests(unittest.TestCase):
    def test_no_split_when_window_not_exceed_one_day(self) -> None:
        now = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)
        last_run = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
        p1 = make_paper("2603.00001v1", datetime(2026, 3, 13, 1, 0, tzinfo=timezone.utc), "A")
        p2 = make_paper("2603.00002v1", datetime(2026, 3, 13, 2, 0, tzinfo=timezone.utc), "B")
        ranked_rows = [(p1, make_meta(10)), (p2, make_meta(20))]

        buckets = arxiv_rss_assistant.build_report_buckets(
            now=now,
            last_run=last_run,
            new_rows=[p1, p2],
            ranked_rows=ranked_rows,
            sort_priority="balanced",
        )

        self.assertEqual(len(buckets), 1)
        report_date, new_rows_bucket, ranked_rows_bucket = buckets[0]
        self.assertEqual(report_date, "2026-03-13")
        self.assertEqual(new_rows_bucket, [p1, p2])
        self.assertEqual(ranked_rows_bucket, ranked_rows)

    def test_split_by_report_day_when_window_exceeds_one_day(self) -> None:
        now = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)
        last_run = datetime(2026, 3, 11, 11, 59, tzinfo=timezone.utc)

        # These two map to 2026-03-11 in UTC+8.
        p1 = make_paper("2603.00003v1", datetime(2026, 3, 10, 17, 30, tzinfo=timezone.utc), "low score")
        p2 = make_paper("2603.00004v1", datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc), "high score")
        # This maps to 2026-03-12 in UTC+8.
        p3 = make_paper("2603.00005v1", datetime(2026, 3, 12, 1, 0, tzinfo=timezone.utc), "next day")

        ranked_rows = [
            (p1, make_meta(10)),
            (p2, make_meta(30)),
            (p3, make_meta(20)),
        ]

        buckets = arxiv_rss_assistant.build_report_buckets(
            now=now,
            last_run=last_run,
            new_rows=[p1, p2, p3],
            ranked_rows=ranked_rows,
            sort_priority="balanced",
        )

        self.assertEqual([bucket[0] for bucket in buckets], ["2026-03-11", "2026-03-12"])

        first_day_papers = [paper.paper_id for paper in buckets[0][1]]
        first_day_ranked = [paper.paper_id for paper, _ in buckets[0][2]]
        second_day_papers = [paper.paper_id for paper in buckets[1][1]]

        self.assertEqual(first_day_papers, ["2603.00004v1", "2603.00003v1"])
        self.assertEqual(first_day_ranked, ["2603.00004v1", "2603.00003v1"])
        self.assertEqual(second_day_papers, ["2603.00005v1"])


if __name__ == "__main__":
    unittest.main()
