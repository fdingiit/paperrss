import re
import unittest
from datetime import datetime, timezone

import arxiv_rss_assistant


class QwenLanguageTests(unittest.TestCase):
    def test_ensure_chinese_llm_brief_result_fallbacks_to_chinese(self) -> None:
        meta = {
            "primary_domain": "inference",
            "heuristic_score": 11,
            "is_inference_accel": True,
            "domain_hits": {"inference": ["kv cache", "decoding"]},
            "llm_hits": ["serving"],
        }
        result = {
            "brief": "This paper improves long-context decoding efficiency.",
            "tags": ["inference", "kv cache"],
            "score": 87,
            "interest_matches": ["inference acceleration"],
            "model": "qwen-long",
            "source": "qwen",
            "error": None,
        }

        normalized = arxiv_rss_assistant.ensure_chinese_llm_brief_result(result, meta, fallback_score=11)

        self.assertRegex(normalized["brief"], r"[\u4e00-\u9fff]")
        self.assertTrue(normalized["tags"])
        self.assertTrue(all(re.search(r"[\u4e00-\u9fff]", tag) for tag in normalized["tags"]))
        self.assertEqual(normalized["score"], 87)

    def test_build_paper_brief_without_llm_uses_chinese_fallback(self) -> None:
        paper = arxiv_rss_assistant.Paper(
            paper_id="2603.00001v1",
            title="KV Cache Compression",
            summary="A practical inference acceleration method.",
            published=datetime.now(timezone.utc),
            authors=["A", "B"],
            categories=["cs.LG"],
            link="https://arxiv.org/abs/2603.00001",
        )
        meta = {
            "primary_domain": "inference",
            "relevant": True,
            "heuristic_score": 23,
            "score": 23,
            "is_inference_accel": True,
            "domain_hits": {"inference": ["kv cache", "decoding"]},
            "llm_hits": ["serving"],
            "llm_brief": {},
        }

        brief = arxiv_rss_assistant.build_paper_brief(paper, meta)
        self.assertRegex(brief["brief"], r"[\u4e00-\u9fff]")
        self.assertIn("无", brief["interest_matches"])


if __name__ == "__main__":
    unittest.main()
