import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import arxiv_rss_assistant


class AuthorOrganizationTests(unittest.TestCase):
    def test_infer_organization_domains_from_emails(self) -> None:
        emails = [
            "alice@cs.stanford.edu",
            "bob@openai.com",
            "charlie@gmail.com",
            "dora@foo.edu.cn",
            "erin@foo.edu.cn",
        ]
        domains = arxiv_rss_assistant.infer_organization_domains_from_emails(emails)
        self.assertEqual(
            domains,
            [
                "stanford.edu",
                "openai.com",
                "个人邮箱域名(gmail.com)",
                "foo.edu.cn",
            ],
        )

    def test_normalize_author_profile_backfills_organizations(self) -> None:
        profile = {
            "authors": [" Alice "],
            "emails": ["Alice@cs.stanford.edu"],
            "source": "https://arxiv.org/abs/2603.00001",
            "error": None,
        }
        normalized = arxiv_rss_assistant.normalize_author_profile(profile, fallback_authors=["Fallback"])

        self.assertEqual(normalized["authors"], ["Alice"])
        self.assertEqual(normalized["emails"], ["alice@cs.stanford.edu"])
        self.assertIn("stanford.edu", normalized["organizations"])
        self.assertIn("stanford.edu", normalized["organization_hints"]["email_domains"])

    def test_normalize_qwen_organization_result_accepts_string(self) -> None:
        normalized = arxiv_rss_assistant.normalize_qwen_organization_result(
            {
                "organizations": "MIT; Stanford University",
                "evidence": "email domain, affiliation field",
                "reason": "规则一致",
            }
        )
        self.assertEqual(normalized["organizations"], ["MIT", "Stanford University"])
        self.assertEqual(normalized["evidence"], ["email domain", "affiliation field"])

    def test_enrich_author_profile_extracts_affiliation_and_email_org(self) -> None:
        paper = arxiv_rss_assistant.Paper(
            paper_id="2603.00001v1",
            title="Test",
            summary="Summary",
            published=datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc),
            authors=["Fallback Author"],
            categories=["cs.LG"],
            link="https://arxiv.org/abs/2603.00001",
        )
        html = """
        <html><head>
        <meta name=\"citation_author\" content=\"Jane Doe\" />
        <meta name=\"citation_author_institution\" content=\"Tsinghua University\" />
        <meta name=\"citation_author_institution\" content=\"Ant Research\" />
        </head><body>
        Contact: jane@tsinghua.edu.cn
        </body></html>
        """

        with patch("arxiv_rss_assistant.fetch_url_text", return_value=html):
            profile = arxiv_rss_assistant.enrich_author_profile(paper, timeout=1)

        self.assertEqual(profile["authors"], ["Jane Doe"])
        self.assertIn("jane@tsinghua.edu.cn", profile["emails"])
        self.assertIn("Tsinghua University", profile["organizations"])
        self.assertIn("Ant Research", profile["organizations"])
        self.assertIn("tsinghua.edu.cn", profile["organizations"])

    def test_slack_detail_includes_organizations_section(self) -> None:
        paper = arxiv_rss_assistant.Paper(
            paper_id="2603.00002v1",
            title="Org Test Paper",
            summary="Summary",
            published=datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc),
            authors=["Alice"],
            categories=["cs.LG"],
            link="https://arxiv.org/abs/2603.00002",
        )
        meta = {
            "relevant": True,
            "primary_domain": "inference",
            "heuristic_score": 80,
            "score": 80,
            "inference_accel_score": 1,
            "is_inference_accel": True,
            "domain_scores": {"training": 0, "inference": 2, "infrastructure": 0},
            "domain_hits": {"training": [], "inference": ["inference"], "infrastructure": []},
            "llm_hits": ["llm"],
            "llm_brief": {
                "brief": "这是中文摘要。",
                "tags": ["推理"],
                "score": 80,
                "interest_matches": ["大模型推理"],
            },
            "author_profile": {
                "authors": ["Alice"],
                "emails": ["alice@mit.edu"],
                "organizations": ["MIT", "mit.edu"],
            },
        }

        messages, _ = arxiv_rss_assistant.build_slack_messages(
            run_at=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
            new_total=1,
            ranked_rows=[(paper, meta)],
            report_path=Path("/tmp/demo.md"),
            report_date="2026-03-13",
            detail_limit=10,
        )
        detail_texts = [
            block.get("text", {}).get("text", "")
            for block in messages[1]["blocks"]
            if isinstance(block, dict) and block.get("type") == "section"
        ]
        merged = "\n".join(detail_texts)
        self.assertIn("Organizations", merged)
        self.assertIn("MIT", merged)

    def test_attach_author_profiles_uses_qwen_for_organization_name(self) -> None:
        paper = arxiv_rss_assistant.Paper(
            paper_id="2603.00003v1",
            title="Org LLM Test",
            summary="Summary",
            published=datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc),
            authors=["Alice"],
            categories=["cs.LG"],
            link="https://arxiv.org/abs/2603.00003",
        )
        ranked_rows = [(paper, {})]
        heur_profile = arxiv_rss_assistant.normalize_author_profile(
            {
                "authors": ["Alice"],
                "emails": ["alice@cs.stanford.edu"],
                "organizations": ["stanford.edu"],
                "organization_hints": {
                    "affiliations": [],
                    "email_domains": ["stanford.edu"],
                    "llm_inferred": False,
                },
                "organization_source": "heuristic",
                "source": "https://arxiv.org/abs/2603.00003",
                "error": None,
            },
            fallback_authors=paper.authors,
        )
        llm_result = {
            "organizations": ["斯坦福大学"],
            "evidence": ["邮箱域名 stanford.edu"],
            "reason": "根据邮箱域名与作者上下文判断为 Stanford University。",
            "source": "qwen",
            "error": None,
        }

        with (
            patch("arxiv_rss_assistant.load_author_cache", return_value={}),
            patch("arxiv_rss_assistant.save_author_cache"),
            patch("arxiv_rss_assistant.enrich_author_profile", return_value=heur_profile),
            patch("arxiv_rss_assistant.call_qwen_organization_analysis", return_value=llm_result) as llm_mock,
        ):
            arxiv_rss_assistant.attach_author_profiles(
                ranked_rows=ranked_rows,
                enabled=True,
                cache_path=Path("/tmp/author_cache_test.json"),
                max_papers=10,
                timeout=1,
                workers=1,
                organization_llm_enabled=True,
                organization_llm_api_key="sk-test",
                organization_llm_base_url="https://example.test/v1",
                organization_llm_model="qwen-long",
                organization_llm_timeout=1,
                organization_llm_workers=1,
            )

        profile = ranked_rows[0][1]["author_profile"]
        self.assertEqual(profile["organizations"], ["斯坦福大学"])
        self.assertEqual(profile["organization_source"], "qwen")
        self.assertTrue(profile["organization_hints"]["llm_inferred"])
        llm_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
