#!/usr/bin/env python3
"""arXiv incremental subscription assistant for LLM training/inference/infrastructure.

Features:
- Fetches recent papers from arXiv API (Atom feed)
- Keeps local state for incremental daily updates
- Scores relevance for three domains: training / inference / infrastructure
- Generates a markdown daily report
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import sys
import time
from html import unescape
import urllib.parse
import urllib.request
from urllib.error import HTTPError
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import paperrss_version
import paperrss_utils

logger = logging.getLogger("paperrss.rss")
APP_VERSION = paperrss_version.get_version()

setup_logging = paperrss_utils.setup_logging


ARXIV_API = "https://export.arxiv.org/api/query"
QWEN_COMPAT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
REPORT_DAY_TZ = timezone(timedelta(hours=8))
PERSONAL_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "yahoo.co.jp",
    "qq.com",
    "foxmail.com",
    "163.com",
    "126.com",
    "proton.me",
    "protonmail.com",
    "icloud.com",
}
MULTI_PART_PUBLIC_SUFFIXES = {
    "ac.uk",
    "co.uk",
    "org.uk",
    "gov.uk",
    "ac.jp",
    "co.jp",
    "or.jp",
    "go.jp",
    "ac.kr",
    "co.kr",
    "com.cn",
    "edu.cn",
    "org.cn",
    "gov.cn",
    "ac.cn",
    "com.au",
    "edu.au",
    "org.au",
    "net.au",
    "com.hk",
    "edu.hk",
    "org.hk",
}

DEFAULT_CATEGORIES = ["cs.LG", "cs.AI", "cs.CL", "cs.DC", "stat.ML"]

DOMAIN_KEYWORDS = {
    "training": [
        "pretrain",
        "pre-training",
        "fine-tune",
        "finetune",
        "alignment",
        "rlhf",
        "dpo",
        "curriculum",
        "optimizer",
        "sgd",
        "adam",
        "data mixture",
        "scaling law",
        "distillation",
        "continual learning",
        "instruction tuning",
        "sft",
        "loss function",
    ],
    "inference": [
        "inference",
        "decoding",
        "speculative",
        "kv cache",
        "attention",
        "quantization",
        "low-bit",
        "sparsity",
        "serving",
        "latency",
        "throughput",
        "vllm",
        "prefix cache",
        "batching",
        "beam search",
    ],
    "infrastructure": [
        "system",
        "systems",
        "distributed",
        "cluster",
        "scheduling",
        "compiler",
        "kernel",
        "parallelism",
        "pipeline parallel",
        "tensor parallel",
        "communication",
        "allreduce",
        "rdma",
        "nvlink",
        "profiling",
        "deployment",
        "runtime",
    ],
}

LLM_HINTS = [
    "llm",
    "language model",
    "foundation model",
    "transformer",
    "gpt",
    "mixture of experts",
    "moe",
]

INFERENCE_ACCEL_KEYWORDS = [
    "latency",
    "throughput",
    "speculative",
    "decoding",
    "kv cache",
    "quantization",
    "low-bit",
    "vllm",
    "serving",
    "batching",
    "prefix cache",
]


@dataclass
class Paper:
    paper_id: str
    title: str
    summary: str
    published: datetime
    authors: list[str]
    categories: list[str]
    link: str


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def extract_id(id_url: str) -> str:
    # id_url is usually like: http://arxiv.org/abs/2501.12345v1
    return id_url.rstrip("/").split("/")[-1]


def build_query(categories: Iterable[str]) -> str:
    cat_query = " OR ".join(f"cat:{c}" for c in categories)
    return f"({cat_query})"


def parse_atom_payload(payload: bytes) -> list[Paper]:
    root = ET.fromstring(payload)
    items: list[Paper] = []

    for entry in root.findall("atom:entry", ATOM_NS):
        id_url = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
        title = normalize(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
        summary = normalize(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
        published_raw = entry.findtext("atom:published", default="", namespaces=ATOM_NS)
        authors = [
            normalize(a.findtext("atom:name", default="", namespaces=ATOM_NS))
            for a in entry.findall("atom:author", ATOM_NS)
        ]
        categories = [c.attrib.get("term", "") for c in entry.findall("atom:category", ATOM_NS)]

        link = ""
        for l in entry.findall("atom:link", ATOM_NS):
            if l.attrib.get("rel") == "alternate":
                link = l.attrib.get("href", "")
                break
        if not link:
            link = id_url

        if not (id_url and title and published_raw):
            continue

        items.append(
            Paper(
                paper_id=extract_id(id_url),
                title=title,
                summary=summary,
                published=parse_dt(published_raw),
                authors=[a for a in authors if a],
                categories=[c for c in categories if c],
                link=link,
            )
        )

    return items


def fetch_papers(categories: list[str], max_results: int, feed_file: str | None = None) -> list[Paper]:
    if feed_file:
        payload = Path(feed_file).read_bytes()
        return parse_atom_payload(payload)

    params = {
        "search_query": build_query(categories),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": "0",
        "max_results": str(max_results),
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "paperrss-assistant/1.0"})

    with urllib.request.urlopen(req, timeout=45) as response:
        payload = response.read()
    return parse_atom_payload(payload)


def score_domain(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    hits: list[str] = []
    for kw in keywords:
        if kw in text:
            hits.append(kw)
    return len(hits), hits


def classify_paper(paper: Paper) -> dict:
    text = f"{paper.title} {paper.summary}".lower()
    llm_hint_score, llm_hits = score_domain(text, LLM_HINTS)

    domain_scores: dict[str, int] = {}
    domain_hits: dict[str, list[str]] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        score, hits = score_domain(text, keywords)
        domain_scores[domain] = score
        domain_hits[domain] = hits
    inference_accel_score, inference_accel_hits = score_domain(text, INFERENCE_ACCEL_KEYWORDS)

    primary_domain = max(domain_scores, key=domain_scores.get)
    relevant = domain_scores[primary_domain] > 0 and llm_hint_score > 0

    # Keep some infra papers even if LLM hints are sparse, but strongly systems-oriented.
    if not relevant and primary_domain == "infrastructure" and domain_scores[primary_domain] >= 2:
        relevant = True

    heuristic_score = (
        domain_scores[primary_domain] * 4
        + llm_hint_score * 3
        + sum(domain_scores.values())
    )
    is_inference_accel = primary_domain == "inference" and inference_accel_score > 0

    return {
        "relevant": relevant,
        "primary_domain": primary_domain,
        "heuristic_score": heuristic_score,
        "score": heuristic_score,
        "inference_accel_score": inference_accel_score,
        "inference_accel_hits": inference_accel_hits,
        "is_inference_accel": is_inference_accel,
        "domain_scores": domain_scores,
        "domain_hits": domain_hits,
        "llm_hits": llm_hits,
    }


def ranking_tuple(paper: Paper, meta: dict, sort_priority: str) -> tuple:
    unified_score = int(meta.get("score", 0) or 0)
    if sort_priority == "recent":
        return (paper.published.timestamp(), unified_score)

    if sort_priority == "balanced":
        return (
            1 if meta["relevant"] else 0,
            unified_score,
            paper.published.timestamp(),
        )

    return (
        1 if meta["relevant"] else 0,
        unified_score,
        paper.published.timestamp(),
    )


def report_day_key(published: datetime) -> str:
    return published.astimezone(REPORT_DAY_TZ).date().isoformat()


def build_report_buckets(
    now: datetime,
    last_run: datetime | None,
    new_rows: list[Paper],
    ranked_rows: list[tuple[Paper, dict]],
    sort_priority: str,
) -> list[tuple[str, list[Paper], list[tuple[Paper, dict]]]]:
    should_split = bool(last_run and (now - last_run) > timedelta(days=1))
    if not should_split or not ranked_rows:
        return [(now.strftime("%Y-%m-%d"), new_rows, ranked_rows)]

    grouped: dict[str, list[tuple[Paper, dict]]] = {}
    for row in ranked_rows:
        paper, _ = row
        grouped.setdefault(report_day_key(paper.published), []).append(row)

    buckets: list[tuple[str, list[Paper], list[tuple[Paper, dict]]]] = []
    for day_key in sorted(grouped):
        rows = grouped[day_key]
        rows.sort(key=lambda row: ranking_tuple(row[0], row[1], sort_priority), reverse=True)
        buckets.append((day_key, [paper for paper, _ in rows], rows))
    return buckets


def build_paper_brief(paper: Paper, meta: dict) -> dict[str, str]:
    llm_brief = meta.get("llm_brief", {})
    llm_tags = [normalize(str(tag)).lower() for tag in llm_brief.get("tags", []) if normalize(str(tag))]
    tags = llm_tags or _fallback_chinese_tags(meta)
    # Keep the full arXiv abstract text (no summarization/truncation).
    abstract_text = normalize(paper.summary)
    if not abstract_text:
        abstract_text = "无"
    brief_text = normalize(llm_brief.get("brief", ""))
    if not brief_text:
        brief_text = _fallback_chinese_brief(meta)
    interest_matches = [
        normalize(str(item))
        for item in llm_brief.get("interest_matches", [])
        if normalize(str(item))
    ]
    return {
        "brief": brief_text,
        "abstract": abstract_text,
        "tags": " / ".join(tags),
        "score": str(int(meta.get("score", llm_brief.get("score", llm_brief.get("recommendation_score", meta.get("heuristic_score", 0)))) or 0)),
        "interest_matches": " / ".join(interest_matches) if interest_matches else "无",
    }


def select_daily_top_picks(ranked_rows: list[tuple[Paper, dict]], limit: int = 6) -> list[tuple[Paper, dict]]:
    relevant = [row for row in ranked_rows if row[1]["relevant"]]
    picks = relevant[:limit]
    if len(picks) >= limit:
        return picks
    seen = {paper.paper_id for paper, _ in picks}
    for row in ranked_rows:
        paper, _ = row
        if paper.paper_id in seen:
            continue
        picks.append(row)
        seen.add(paper.paper_id)
        if len(picks) >= limit:
            break
    return picks


def load_llm_brief_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_llm_brief_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_tags = value
    elif isinstance(value, str):
        raw_tags = re.split(r"[,/\n|]+", value)
    else:
        raw_tags = []
    tags: list[str] = []
    for item in raw_tags:
        tag = normalize(str(item)).lower()
        if tag:
            tags.append(tag)
    return list(dict.fromkeys(tags))[:6]


def _normalize_interest_matches(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = re.split(r"[,/\n|]+", value)
    else:
        raw_items = []
    items: list[str] = []
    for item in raw_items:
        match = normalize(str(item))
        if match:
            items.append(match)
    return list(dict.fromkeys(items))[:6]


def _contains_chinese(text: Any) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def _domain_label_zh(domain: str) -> str:
    mapping = {
        "training": "大模型训练",
        "inference": "大模型推理",
        "infrastructure": "大模型基础设施",
    }
    return mapping.get(str(domain or "").strip().lower(), "大模型工程")


def _fallback_chinese_tags(meta: dict) -> list[str]:
    tags = [_domain_label_zh(str(meta.get("primary_domain", ""))), "工程实践", "论文解读"]
    if bool(meta.get("is_inference_accel")):
        tags.insert(1, "推理加速")
    return list(dict.fromkeys(tags))[:6]


def _fallback_chinese_brief(meta: dict) -> str:
    domain = str(meta.get("primary_domain", ""))
    domain_hits = list((meta.get("domain_hits", {}) or {}).get(domain, []))
    llm_hits = list(meta.get("llm_hits", []))
    hints = list(dict.fromkeys(domain_hits + llm_hits))[:3]
    hint_text = f"关键线索包括：{'、'.join(hints)}。" if hints else "可从方法设计与实验设置评估其工程价值。"
    accel_text = "，并包含推理加速相关信号" if bool(meta.get("is_inference_accel")) else ""
    return (
        f"该论文主要聚焦{_domain_label_zh(domain)}方向{accel_text}。"
        f"{hint_text}"
        "建议结合你的业务场景评估其可复现性、系统收益与落地成本。"
    )


def ensure_chinese_llm_brief_result(
    result: dict[str, Any],
    meta: dict,
    fallback_score: int = 0,
) -> dict[str, Any]:
    normalized = normalize_llm_brief_result(result, fallback_score=fallback_score)
    brief = normalize(str(normalized.get("brief", "")))
    tags = _normalize_tags(normalized.get("tags"))
    interests = _normalize_interest_matches(normalized.get("interest_matches"))

    if not _contains_chinese(brief):
        normalized["brief"] = _fallback_chinese_brief(meta)
    if not tags or not all(_contains_chinese(tag) for tag in tags):
        normalized["tags"] = _fallback_chinese_tags(meta)
    if interests:
        normalized["interest_matches"] = [item for item in interests if _contains_chinese(item)]

    return normalize_llm_brief_result(normalized, fallback_score=fallback_score)


def normalize_llm_brief_result(result: dict[str, Any], fallback_score: int = 0) -> dict[str, Any]:
    normalized = dict(result or {})
    try:
        score = int(normalized.get("score", normalized.get("recommendation_score", fallback_score)) or 0)
    except (TypeError, ValueError):
        score = fallback_score
    score = max(0, min(100, score))
    normalized["score"] = score
    normalized.pop("recommendation_score", None)
    normalized["tags"] = _normalize_tags(normalized.get("tags"))
    normalized["interest_matches"] = _normalize_interest_matches(normalized.get("interest_matches"))
    normalized["brief"] = normalize(str(normalized.get("brief", "")))[:600]
    normalized["model"] = str(normalized.get("model", ""))
    normalized["source"] = str(normalized.get("source", ""))
    normalized["error"] = normalized.get("error")
    return normalized


def build_qwen_brief_prompt(paper: Paper, meta: dict, interest_topics: list[str]) -> str:
    domain = meta["primary_domain"]
    hints = list(dict.fromkeys(meta["domain_hits"][domain] + meta["llm_hits"]))[:8]
    hint_text = ", ".join(hints) if hints else "none"
    interest_text = "; ".join(interest_topics) if interest_topics else "大模型训练; 大模型推理; 大模型基础设施"
    return (
        "请阅读下面的 arXiv 论文标题与摘要，并返回严格 JSON。\n"
        "只允许返回以下 schema（不得有额外字段）：\n"
        '{"brief":"2-3句中文摘要，可保留必要英文术语缩写",'
        '"tags":["3-6个中文短标签"],'
        '"score":0,'
        '"interest_matches":["从用户兴趣列表中匹配到的中文项"]}\n'
        "要求：\n"
        "- 必须使用中文输出 brief/tags/interest_matches。\n"
        "- 重点说明方法、工程价值、对大模型训练/推理/基础设施的意义。\n"
        "- score 必须是 0 到 100 的整数。\n"
        "- score 是最终统一排序分，综合兴趣匹配度、工程实用性、创新性。\n"
        "- interest_matches 只能从用户兴趣列表中选择原词或轻微归一化表达。\n"
        "- 不要 Markdown，不要代码块，不要解释文本，只返回 JSON。\n"
        f"- 规则侧预测领域: {domain}\n"
        f"- 规则侧关键词: {hint_text}\n"
        f"- 用户兴趣列表: {interest_text}\n\n"
        f"标题: {paper.title}\n"
        f"摘要: {normalize(paper.summary) or 'N/A'}\n"
    )


def call_qwen_brief(
    paper: Paper,
    meta: dict,
    interest_topics: list[str],
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是大模型工程研究助手。必须仅返回合法 JSON，且 brief/tags/interest_matches 必须为中文。"
                ),
            },
            {"role": "user", "content": build_qwen_brief_prompt(paper, meta, interest_topics)},
        ],
        "temperature": 0.2,
        "stream": False,
        "max_tokens": 320,
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "paperrss-assistant/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8", errors="ignore"))

    content = (
        raw.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    parsed = _extract_json_object(content)
    brief = normalize(str(parsed.get("brief", "")))
    tags = _normalize_tags(parsed.get("tags"))
    interest_matches = _normalize_interest_matches(parsed.get("interest_matches"))
    if not brief:
        brief = normalize(str(content))
    result = normalize_llm_brief_result({
        "brief": brief[:600],
        "tags": tags,
        "score": parsed.get("score", parsed.get("recommendation_score", 0)),
        "interest_matches": interest_matches,
        "model": model,
        "source": "qwen",
        "error": None,
    }, fallback_score=int(meta.get("heuristic_score", 0)))
    return ensure_chinese_llm_brief_result(
        result,
        meta,
        fallback_score=int(meta.get("heuristic_score", 0)),
    )


def attach_llm_briefs(
    ranked_rows: list[tuple[Paper, dict]],
    enabled: bool,
    interest_topics: list[str],
    api_key: str,
    base_url: str,
    model: str,
    cache_path: Path,
    max_papers: int,
    timeout: int,
    workers: int,
) -> None:
    if not enabled or not api_key or not ranked_rows:
        return

    cache = load_llm_brief_cache(cache_path)
    updated = False
    pending: list[tuple[int, Paper, dict]] = []
    for idx, (paper, meta) in enumerate(ranked_rows, start=1):
        if idx > max_papers:
            meta["llm_brief"] = normalize_llm_brief_result({
                "brief": "",
                "tags": [],
                "score": 0,
                "interest_matches": [],
                "model": model,
                "source": "qwen",
                "error": f"skipped_by_limit(max_papers={max_papers})",
            }, fallback_score=int(meta.get("heuristic_score", 0)))
            logger.info("llm_brief_skipped id=%s reason=limit rank=%s", paper.paper_id, idx)
            continue
        cached = cache.get(paper.paper_id)
        if cached and ("score" in cached or "recommendation_score" in cached) and "interest_matches" in cached:
            cached_result = ensure_chinese_llm_brief_result(
                cached,
                meta,
                fallback_score=int(meta.get("heuristic_score", 0)),
            )
            meta["llm_brief"] = cached_result
            if cached_result != cached:
                cache[paper.paper_id] = cached_result
                updated = True
            logger.info("llm_brief_cache_hit id=%s", paper.paper_id)
            continue
        pending.append((idx, paper, meta))

    if not pending:
        return

    logger.info(
        "llm_brief_stage_start pending=%s workers=%s timeout=%s model=%s",
        len(pending),
        max(1, workers),
        timeout,
        model,
    )

    def _run_llm(paper: Paper, meta: dict) -> dict[str, Any]:
        return call_qwen_brief(
            paper,
            meta,
            interest_topics=interest_topics,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
        )

    if workers <= 1:
        future_results: list[tuple[int, Paper, dict, dict[str, Any]]] = []
        for idx, paper, meta in pending:
            logger.info("llm_brief_request_start id=%s rank=%s", paper.paper_id, idx)
            try:
                result = _run_llm(paper, meta)
            except Exception as exc:  # noqa: BLE001
                result = normalize_llm_brief_result({
                    "brief": "",
                    "tags": [],
                    "score": 0,
                    "interest_matches": [],
                    "model": model,
                    "source": "qwen",
                    "error": str(exc),
                }, fallback_score=int(meta.get("heuristic_score", 0)))
            future_results.append((idx, paper, meta, result))
        for idx, paper, meta, result in future_results:
            result = ensure_chinese_llm_brief_result(
                result,
                meta,
                fallback_score=int(meta.get("heuristic_score", 0)),
            )
            meta["llm_brief"] = result
            cache[paper.paper_id] = result
            updated = True
            logger.info(
                "llm_brief_done id=%s rank=%s score=%s tags=%s interests=%s err=%s",
                paper.paper_id,
                idx,
                result.get("score", 0),
                result.get("tags", []),
                result.get("interest_matches", []),
                result.get("error"),
            )
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            future_map: dict[concurrent.futures.Future[dict[str, Any]], tuple[int, Paper, dict]] = {}
            for idx, paper, meta in pending:
                logger.info("llm_brief_request_start id=%s rank=%s", paper.paper_id, idx)
                future_map[pool.submit(_run_llm, paper, meta)] = (idx, paper, meta)
            for future in concurrent.futures.as_completed(future_map):
                idx, paper, meta = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    result = normalize_llm_brief_result({
                        "brief": "",
                        "tags": [],
                        "score": 0,
                        "interest_matches": [],
                        "model": model,
                        "source": "qwen",
                        "error": str(exc),
                    }, fallback_score=int(meta.get("heuristic_score", 0)))
                result = ensure_chinese_llm_brief_result(
                    result,
                    meta,
                    fallback_score=int(meta.get("heuristic_score", 0)),
                )
                meta["llm_brief"] = result
                cache[paper.paper_id] = result
                updated = True
                logger.info(
                    "llm_brief_done id=%s rank=%s score=%s tags=%s interests=%s err=%s",
                    paper.paper_id,
                    idx,
                    result.get("score", 0),
                    result.get("tags", []),
                    result.get("interest_matches", []),
                    result.get("error"),
                )

    logger.info("llm_brief_stage_done pending=%s", len(pending))
    if updated:
        save_llm_brief_cache(cache_path, cache)


def load_author_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_author_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text_list(values: Any, max_items: int | None = None) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for raw in values:
        txt = normalize(str(raw))
        if not txt:
            continue
        out.append(txt)
    deduped = list(dict.fromkeys(out))
    if max_items is None:
        return deduped
    return deduped[:max_items]


def registrable_domain(domain: str) -> str:
    labels = [label for label in str(domain).strip().lower().split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    suffix2 = ".".join(labels[-2:])
    if suffix2 in MULTI_PART_PUBLIC_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return suffix2


def infer_organization_domains_from_emails(emails: list[str]) -> list[str]:
    domains: list[str] = []
    for email in emails:
        if "@" not in email:
            continue
        raw_domain = email.rsplit("@", 1)[-1].strip().lower()
        if not raw_domain:
            continue
        domain = registrable_domain(raw_domain)
        if not domain:
            continue
        if domain in PERSONAL_EMAIL_DOMAINS:
            domains.append(f"个人邮箱域名({domain})")
        else:
            domains.append(domain)
    return list(dict.fromkeys(domains))[:10]


def extract_author_institutions(html_text: str) -> list[str]:
    institution_meta_pattern = re.compile(
        r'<meta\s+name=["\']citation_author_institution["\']\s+content=["\'](.*?)["\']',
        re.IGNORECASE,
    )
    institutions = [normalize(unescape(x)) for x in institution_meta_pattern.findall(html_text)]
    return [item for item in list(dict.fromkeys(institutions)) if item][:10]


def build_organization_hints(affiliations: list[str], email_domains: list[str]) -> list[str]:
    return list(dict.fromkeys(affiliations + email_domains))[:12]


def normalize_author_profile(profile: dict[str, Any], fallback_authors: list[str]) -> dict[str, Any]:
    data = dict(profile or {})
    authors = normalize_text_list(data.get("authors"), max_items=20) or normalize_text_list(fallback_authors, max_items=20)
    emails = extract_emails("\n".join(normalize_text_list(data.get("emails"), max_items=20)))[:12]
    hints = data.get("organization_hints", {}) if isinstance(data.get("organization_hints"), dict) else {}
    affiliations = normalize_text_list(hints.get("affiliations"), max_items=10)
    email_domains = normalize_text_list(hints.get("email_domains"), max_items=10)
    llm_inferred = bool(hints.get("llm_inferred", False))
    if not email_domains:
        email_domains = infer_organization_domains_from_emails(emails)
    organizations = normalize_text_list(data.get("organizations"), max_items=12)
    if not organizations:
        organizations = build_organization_hints(affiliations, email_domains)
    organization_source = normalize(str(data.get("organization_source", "")))
    if not organization_source:
        organization_source = "qwen" if llm_inferred else "heuristic"
    organization_reason = normalize(str(data.get("organization_reason", "")))[:400]
    organization_error = data.get("organization_error")
    return {
        "authors": authors,
        "emails": emails,
        "organizations": organizations,
        "organization_hints": {
            "affiliations": affiliations,
            "email_domains": email_domains,
            "llm_inferred": llm_inferred,
        },
        "organization_source": organization_source,
        "organization_reason": organization_reason,
        "organization_error": organization_error,
        "source": data.get("source"),
        "error": data.get("error"),
    }


def normalize_qwen_organization_result(result: dict[str, Any]) -> dict[str, Any]:
    data = dict(result or {})
    raw_organizations = data.get("organizations")
    if isinstance(raw_organizations, str):
        raw_organizations = re.split(r"[,/\n|;]+", raw_organizations)
    raw_evidence = data.get("evidence")
    if isinstance(raw_evidence, str):
        raw_evidence = re.split(r"[,/\n|;]+", raw_evidence)
    organizations = normalize_text_list(raw_organizations, max_items=12)
    evidence = normalize_text_list(raw_evidence, max_items=10)
    reason = normalize(str(data.get("reason", "")))[:300]
    return {
        "organizations": organizations,
        "evidence": evidence,
        "reason": reason,
    }


def build_qwen_organization_prompt(
    paper: Paper,
    authors: list[str],
    emails: list[str],
    affiliations: list[str],
    email_domains: list[str],
    heuristic_organizations: list[str],
) -> str:
    authors_text = ", ".join(authors[:12]) if authors else "N/A"
    emails_text = ", ".join(emails[:12]) if emails else "N/A"
    affiliations_text = "; ".join(affiliations[:10]) if affiliations else "N/A"
    domains_text = ", ".join(email_domains[:10]) if email_domains else "N/A"
    heuristics_text = "; ".join(heuristic_organizations[:12]) if heuristic_organizations else "N/A"
    return (
        "请根据作者信息推断论文相关组织（高校、公司、研究院、实验室）。\n"
        "只返回严格 JSON，不要 markdown，不要解释文本。\n"
        "schema:\n"
        '{"organizations":["组织全称，优先中文或官方英文名"],'
        '"evidence":["证据短语"],'
        '"reason":"不超过80字的中文说明"}\n'
        "要求：\n"
        "- organizations 只保留最可信的 1-6 个组织。\n"
        "- 若只有个人邮箱且无机构证据，可返回邮箱域名对应描述。\n"
        "- 不要输出个人姓名。\n\n"
        f"论文标题: {paper.title}\n"
        f"作者: {authors_text}\n"
        f"邮箱: {emails_text}\n"
        f"页面机构字段(citation_author_institution): {affiliations_text}\n"
        f"邮箱域名候选: {domains_text}\n"
        f"规则候选组织: {heuristics_text}\n"
    )


def call_qwen_organization_analysis(
    paper: Paper,
    profile: dict[str, Any],
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    hints = profile.get("organization_hints", {}) if isinstance(profile.get("organization_hints"), dict) else {}
    prompt = build_qwen_organization_prompt(
        paper=paper,
        authors=normalize_text_list(profile.get("authors"), max_items=20),
        emails=normalize_text_list(profile.get("emails"), max_items=20),
        affiliations=normalize_text_list(hints.get("affiliations"), max_items=10),
        email_domains=normalize_text_list(hints.get("email_domains"), max_items=10),
        heuristic_organizations=normalize_text_list(profile.get("organizations"), max_items=12),
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是学术作者组织识别助手。必须仅返回合法 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "stream": False,
        "max_tokens": 260,
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "paperrss-assistant/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8", errors="ignore"))
    content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _extract_json_object(content)
    normalized = normalize_qwen_organization_result(parsed)
    if not normalized["organizations"]:
        normalized["organizations"] = normalize_text_list(profile.get("organizations"), max_items=12)
    return {
        "organizations": normalized["organizations"],
        "evidence": normalized["evidence"],
        "reason": normalized["reason"],
        "model": model,
        "source": "qwen",
        "error": None,
    }


def attach_author_profiles(
    ranked_rows: list[tuple[Paper, dict]],
    enabled: bool,
    cache_path: Path,
    max_papers: int,
    timeout: int,
    workers: int,
    organization_llm_enabled: bool = False,
    organization_llm_api_key: str = "",
    organization_llm_base_url: str = QWEN_COMPAT_BASE_URL,
    organization_llm_model: str = "qwen-long",
    organization_llm_timeout: int = 20,
    organization_llm_workers: int = 2,
) -> None:
    if not enabled or not ranked_rows:
        return

    cache = load_author_cache(cache_path)
    updated = False
    pending: list[tuple[int, Paper, dict]] = []
    for idx, (paper, meta) in enumerate(ranked_rows, start=1):
        if idx > max_papers:
            meta["author_profile"] = normalize_author_profile({
                "authors": paper.authors,
                "emails": [],
                "organizations": [],
                "organization_hints": {
                    "affiliations": [],
                    "email_domains": [],
                    "llm_inferred": False,
                },
                "organization_source": "heuristic",
                "source": None,
                "error": f"skipped_by_limit(max_papers={max_papers})",
            }, fallback_authors=paper.authors)
            logger.info("author_profile_skipped id=%s reason=limit rank=%s", paper.paper_id, idx)
            continue
        cached = cache.get(paper.paper_id)
        if cached:
            normalized_cached = normalize_author_profile(cached, fallback_authors=paper.authors)
            meta["author_profile"] = normalized_cached
            if normalized_cached != cached:
                cache[paper.paper_id] = normalized_cached
                updated = True
            logger.info("author_profile_cache_hit id=%s", paper.paper_id)
            continue
        pending.append((idx, paper, meta))

    if pending:
        logger.info(
            "author_profile_stage_start pending=%s workers=%s timeout=%s",
            len(pending),
            max(1, workers),
            timeout,
        )

        def _fetch_profile(paper: Paper) -> dict[str, Any]:
            return enrich_author_profile(paper, timeout=timeout)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            future_map: dict[concurrent.futures.Future[dict[str, Any]], tuple[int, Paper, dict]] = {}
            for idx, paper, meta in pending:
                logger.info("author_profile_fetch_start id=%s rank=%s", paper.paper_id, idx)
                future = pool.submit(_fetch_profile, paper)
                future_map[future] = (idx, paper, meta)

            for future in concurrent.futures.as_completed(future_map):
                idx, paper, meta = future_map[future]
                try:
                    profile = future.result()
                except Exception as exc:  # noqa: BLE001
                    profile = normalize_author_profile({
                        "authors": paper.authors,
                        "emails": [],
                        "organizations": [],
                        "organization_hints": {
                            "affiliations": [],
                            "email_domains": [],
                            "llm_inferred": False,
                        },
                        "organization_source": "heuristic",
                        "source": None,
                        "error": str(exc),
                    }, fallback_authors=paper.authors)
                profile = normalize_author_profile(profile, fallback_authors=paper.authors)
                meta["author_profile"] = profile
                cache[paper.paper_id] = profile
                updated = True
                logger.info(
                    "author_profile_fetched id=%s rank=%s emails=%s organizations=%s source=%s err=%s",
                    paper.paper_id,
                    idx,
                    profile.get("emails", []),
                    profile.get("organizations", []),
                    profile.get("source"),
                    profile.get("error"),
                )
        logger.info("author_profile_stage_done pending=%s", len(pending))

    if organization_llm_enabled and organization_llm_api_key:
        org_pending: list[tuple[int, Paper, dict, dict[str, Any]]] = []
        for idx, (paper, meta) in enumerate(ranked_rows, start=1):
            if idx > max_papers:
                continue
            profile = normalize_author_profile(meta.get("author_profile", {}), fallback_authors=paper.authors)
            meta["author_profile"] = profile
            if (
                profile.get("organization_source") == "qwen"
                and bool(profile.get("organization_hints", {}).get("llm_inferred"))
                and bool(profile.get("organizations"))
            ):
                continue
            org_pending.append((idx, paper, meta, profile))

        if org_pending:
            logger.info(
                "author_org_llm_stage_start pending=%s workers=%s timeout=%s model=%s",
                len(org_pending),
                max(1, organization_llm_workers),
                organization_llm_timeout,
                organization_llm_model,
            )

            def _run_org_llm(paper: Paper, profile: dict[str, Any]) -> dict[str, Any]:
                return call_qwen_organization_analysis(
                    paper=paper,
                    profile=profile,
                    api_key=organization_llm_api_key,
                    base_url=organization_llm_base_url,
                    model=organization_llm_model,
                    timeout=organization_llm_timeout,
                )

            if organization_llm_workers <= 1:
                llm_results: list[tuple[int, Paper, dict, dict[str, Any], dict[str, Any]]] = []
                for idx, paper, meta, profile in org_pending:
                    logger.info("author_org_llm_request_start id=%s rank=%s", paper.paper_id, idx)
                    try:
                        result = _run_org_llm(paper, profile)
                    except Exception as exc:  # noqa: BLE001
                        result = {
                            "organizations": profile.get("organizations", []),
                            "evidence": [],
                            "reason": "",
                            "model": organization_llm_model,
                            "source": "qwen",
                            "error": str(exc),
                        }
                    llm_results.append((idx, paper, meta, profile, result))
            else:
                llm_results = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, organization_llm_workers)) as pool:
                    future_map: dict[concurrent.futures.Future[dict[str, Any]], tuple[int, Paper, dict, dict[str, Any]]] = {}
                    for idx, paper, meta, profile in org_pending:
                        logger.info("author_org_llm_request_start id=%s rank=%s", paper.paper_id, idx)
                        future_map[pool.submit(_run_org_llm, paper, profile)] = (idx, paper, meta, profile)
                    for future in concurrent.futures.as_completed(future_map):
                        idx, paper, meta, profile = future_map[future]
                        try:
                            result = future.result()
                        except Exception as exc:  # noqa: BLE001
                            result = {
                                "organizations": profile.get("organizations", []),
                                "evidence": [],
                                "reason": "",
                                "model": organization_llm_model,
                                "source": "qwen",
                                "error": str(exc),
                            }
                        llm_results.append((idx, paper, meta, profile, result))

            for idx, paper, meta, profile, result in llm_results:
                llm_orgs = normalize_text_list(result.get("organizations"), max_items=12)
                final_orgs = llm_orgs or normalize_text_list(profile.get("organizations"), max_items=12)
                merged_hints = dict(profile.get("organization_hints", {}))
                merged_hints["llm_inferred"] = bool(llm_orgs)
                merged_profile = normalize_author_profile({
                    **profile,
                    "organizations": final_orgs,
                    "organization_hints": merged_hints,
                    "organization_source": "qwen" if llm_orgs else profile.get("organization_source", "heuristic"),
                    "organization_reason": normalize(str(result.get("reason", ""))),
                    "organization_error": result.get("error"),
                }, fallback_authors=paper.authors)
                meta["author_profile"] = merged_profile
                cache[paper.paper_id] = merged_profile
                updated = True
                logger.info(
                    "author_org_llm_done id=%s rank=%s organizations=%s llm_used=%s err=%s",
                    paper.paper_id,
                    idx,
                    merged_profile.get("organizations", []),
                    bool(llm_orgs),
                    result.get("error"),
                )
            logger.info("author_org_llm_stage_done pending=%s", len(org_pending))

    if updated:
        save_author_cache(cache_path, cache)


def classify_rows(new_rows: list[Paper], workers: int) -> list[tuple[Paper, dict]]:
    if not new_rows:
        return []
    if workers <= 1:
        return [(paper, classify_paper(paper)) for paper in new_rows]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        metas = list(pool.map(classify_paper, new_rows))
    return list(zip(new_rows, metas))


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"last_run": None, "seen_ids": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"last_run": None, "seen_ids": []}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_subscription_store(path: Path) -> dict:
    if not path.exists():
        return {"seen_ids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"seen_ids": []}
    if not isinstance(data, dict):
        return {"seen_ids": []}
    return {"seen_ids": list(data.get("seen_ids", []))}


def save_subscription_store(path: Path, seen_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen_ids": seen_ids}, ensure_ascii=False, indent=2), encoding="utf-8")


def load_push_state(path: Path) -> dict:
    if not path.exists():
        return {"pushed_by_date": {}, "pushed_report_dates": [], "pushed_paper_ids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"pushed_by_date": {}, "pushed_report_dates": [], "pushed_paper_ids": []}
    if not isinstance(data, dict):
        return {"pushed_by_date": {}, "pushed_report_dates": [], "pushed_paper_ids": []}
    return {
        "pushed_by_date": dict(data.get("pushed_by_date", {})),
        "pushed_paper_ids": list(data.get("pushed_paper_ids", [])),
        "pushed_report_dates": list(data.get("pushed_report_dates", [])),
    }


def save_push_state(path: Path, pushed_by_date: dict[str, list[str]], pushed_report_dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pushed_by_date": pushed_by_date,
                "pushed_report_dates": pushed_report_dates,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def prune_pushed_by_date(
    pushed_by_date: dict[str, set[str]],
    today_str: str,
    retention_days: int,
) -> dict[str, set[str]]:
    keep_after = datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=max(0, retention_days - 1))
    out: dict[str, set[str]] = {}
    for date_key, ids in pushed_by_date.items():
        try:
            d = datetime.strptime(date_key, "%Y-%m-%d")
        except ValueError:
            continue
        if d >= keep_after:
            out[date_key] = ids
    return out


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def parse_bool(value: Any, default: bool = False) -> bool:
    return paperrss_utils.parse_bool(value, default)


def strip_version(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id)


def extract_emails(text: str) -> list[str]:
    canonical = unescape(text)
    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", canonical)

    # Also parse mailto links; some pages use URL-encoded addresses.
    for raw in re.findall(r"mailto:([^\"' >]+)", canonical, flags=re.IGNORECASE):
        addr = urllib.parse.unquote(raw).split("?", 1)[0]
        addr = unescape(addr).strip()
        if re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", addr):
            emails.append(addr)

    # Keep deterministic order while deduplicating.
    return list(dict.fromkeys(e.lower() for e in emails))


def fetch_url_text(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "paperrss-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def enrich_author_profile(paper: Paper, timeout: int = 8) -> dict[str, Any]:
    base_id = strip_version(paper.paper_id)
    html_urls = [
        f"https://arxiv.org/html/{base_id}",
        f"https://arxiv.org/abs/{paper.paper_id}",
        f"https://arxiv.org/abs/{base_id}",
    ]
    last_err: str | None = None
    page = ""
    source_url = None
    for url in html_urls:
        try:
            page = fetch_url_text(url, timeout=timeout)
            source_url = url
            break
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            continue

    if not page:
        return normalize_author_profile({
            "authors": paper.authors,
            "emails": [],
            "organizations": [],
            "organization_hints": {
                "affiliations": [],
                "email_domains": [],
                "llm_inferred": False,
            },
            "organization_source": "heuristic",
            "source": None,
            "error": last_err,
        }, fallback_authors=paper.authors)

    author_meta_pattern = re.compile(
        r'<meta\s+name=["\']citation_author["\']\s+content=["\'](.*?)["\']',
        re.IGNORECASE,
    )
    html_authors = [unescape(x.strip()) for x in author_meta_pattern.findall(page) if x.strip()]
    emails = extract_emails(page)
    affiliations = extract_author_institutions(page)
    email_domains = infer_organization_domains_from_emails(emails)
    organizations = build_organization_hints(affiliations, email_domains)
    return normalize_author_profile({
        "authors": html_authors or paper.authors,
        "emails": emails[:12],
        "organizations": organizations,
        "organization_hints": {
            "affiliations": affiliations,
            "email_domains": email_domains,
            "llm_inferred": False,
        },
        "organization_source": "heuristic",
        "organization_reason": "由 arXiv affiliation 字段与邮箱域名规则推断",
        "source": source_url,
        "error": None,
    }, fallback_authors=paper.authors)


def render_report(
    report_path: Path,
    run_at: datetime,
    new_total: int,
    ranked_rows: list[tuple[Paper, dict]],
    report_date: str | None = None,
) -> None:
    relevant_rows = [row for row in ranked_rows if row[1]["relevant"]]
    top_picks = select_daily_top_picks(ranked_rows, limit=6)
    domain_counter = Counter(row[1]["primary_domain"] for row in relevant_rows)
    keyword_counter = Counter()
    for _, meta in relevant_rows:
        for hits in meta["domain_hits"].values():
            keyword_counter.update(hits)
        keyword_counter.update(meta["llm_hits"])

    lines: list[str] = []
    display_date = str(report_date or run_at.strftime("%Y-%m-%d"))
    lines.append(f"# arXiv Daily LLM Radar - {display_date}")
    lines.append("")
    lines.append(f"- Run time (UTC): {run_at.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append(f"- New papers scanned: {new_total}")
    lines.append(f"- Relevant papers (training/inference/infrastructure): {len(relevant_rows)}")
    lines.append("")

    if ranked_rows:
        lines.append("## Today At A Glance")
        lines.append("")
        lines.append(
            "- Domain distribution: "
            + ", ".join(f"{k}={domain_counter.get(k, 0)}" for k in ["training", "inference", "infrastructure"])
        )
        top_keywords = ", ".join(f"{k}({v})" for k, v in keyword_counter.most_common(10))
        lines.append(f"- Top keywords: {top_keywords or 'N/A'}")
        lines.append(f"- Reading mode: detailed top picks first, then full ranked list")
        lines.append("")

        lines.append("## Top Picks")
        lines.append("")
        if any(meta.get("llm_brief", {}).get("brief") for _, meta in ranked_rows):
            lines.append("> Briefs are generated by Qwen from arXiv abstracts (not full PDF reading).")
        else:
            lines.append("> Briefs are generated from arXiv abstracts (not full PDF reading).")
        lines.append("")
        for i, (paper, meta) in enumerate(top_picks, start=1):
            brief = build_paper_brief(paper, meta)
            lines.append(f"### Pick {i}. {paper.title}")
            lines.append("")
            lines.append(f"- Score: {brief['score']}")
            lines.append(f"- Tags: {brief['tags']}")
            lines.append(f"- Interest matches: {brief['interest_matches']}")
            lines.append(f"- Why read today: {brief['brief']}")
            lines.append(f"- Link: {paper.link}")
            lines.append("")

        lines.append("## Full Ranked Papers")
        lines.append("")
        for i, (paper, meta) in enumerate(ranked_rows, start=1):
            brief = build_paper_brief(paper, meta)
            tag = "relevant" if meta["relevant"] else "background"
            author_profile = meta.get("author_profile", {})
            authors = author_profile.get("authors") or paper.authors
            emails = author_profile.get("emails") or []
            organizations = author_profile.get("organizations") or []
            org_reason = normalize(str(author_profile.get("organization_reason", "")))
            authors_text = ", ".join(authors[:6]) if authors else "N/A"
            if len(authors) > 6:
                authors_text += ", et al."

            lines.append(f"### {i}. {paper.title} [{tag}]")
            lines.append("")
            lines.append(f"- Link: {paper.link}")
            lines.append(f"- Published: {paper.published.strftime('%Y-%m-%d %H:%M UTC')}")
            lines.append(f"- Score: {brief['score']}")
            lines.append(f"- Authors: {authors_text}")
            if emails:
                lines.append(f"- Author emails (from HTML): {', '.join(emails[:5])}")
            if organizations:
                lines.append(f"- Organizations: {', '.join(organizations[:6])}")
                if org_reason:
                    lines.append(f"- Organization analysis note: {org_reason}")
            lines.append(f"- Brief: {brief['brief']}")
            lines.append(f"- Tags: {brief['tags']}")
            lines.append(f"- Interest matches: {brief['interest_matches']}")
            lines.append(f"- Abstract: {brief['abstract']}")
            lines.append("")
    else:
        lines.append("## Overview")
        lines.append("")
        lines.append("- No new papers found in this incremental run.")
        lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _payload_text_only(text: str) -> dict[str, Any]:
    return {"text": text}


def truncate_for_slack(text: str, max_len: int = 700) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def split_slack_detail_and_tail_rows(
    ranked_rows: list[tuple[Paper, dict]],
    detail_limit: int = 10,
) -> tuple[list[tuple[Paper, dict]], list[tuple[Paper, dict]]]:
    limit = max(0, int(detail_limit))
    if len(ranked_rows) <= limit:
        return ranked_rows, []
    return ranked_rows[:limit], ranked_rows[limit:]


def build_slack_messages(
    run_at: datetime,
    new_total: int,
    ranked_rows: list[tuple[Paper, dict]],
    report_path: Path,
    report_date: str | None = None,
    detail_limit: int = 10,
) -> tuple[list[dict[str, Any]], dict[int, list[str]]]:
    relevant_rows = [row for row in ranked_rows if row[1]["relevant"]]
    top_picks = select_daily_top_picks(ranked_rows, limit=10)
    detail_rows, tail_rows = split_slack_detail_and_tail_rows(ranked_rows, detail_limit=detail_limit)
    domain_counter = Counter(row[1]["primary_domain"] for row in relevant_rows)
    display_date = str(report_date or run_at.strftime("%Y-%m-%d"))
    header_text = (
        f"arXiv Daily LLM Radar ({display_date}) | "
        f"new={new_total}, relevant={len(relevant_rows)}, total={len(ranked_rows)}"
    )
    overview_fields = [
        {"type": "mrkdwn", "text": f"*New scanned*\n{new_total}"},
        {"type": "mrkdwn", "text": f"*Relevant*\n{len(relevant_rows)}"},
        {
            "type": "mrkdwn",
            "text": (
                "*Domain split*\n"
                f"training={domain_counter.get('training', 0)}, "
                f"inference={domain_counter.get('inference', 0)}, "
                f"infrastructure={domain_counter.get('infrastructure', 0)}"
            ),
        },
        {"type": "mrkdwn", "text": f"*Report*\n`{report_path}`"},
    ]
    if not ranked_rows:
        return [{
            "text": header_text,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "arXiv Daily LLM Radar"}},
                {"type": "section", "fields": overview_fields},
                {"type": "section", "text": {"type": "mrkdwn", "text": "No new papers in this run."}},
            ],
        }], {}

    top_pick_lines: list[str] = []
    for idx, (paper, meta) in enumerate(top_picks, start=1):
        brief = build_paper_brief(paper, meta)
        top_pick_lines.append(
            f"{idx}. [{brief['score']}] <{paper.link}|{paper.title}>"
        )

    # Daily mode: one overview + top-N details + one compact remainder message.
    delivery_mode = (
        "Delivery mode: top-picks summary first, then one paper per message"
        if not tail_rows
        else "Delivery mode: top 10 detail cards first, then one compact thread-style remainder message"
    )
    messages: list[dict[str, Any]] = [{
        "text": header_text,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "arXiv Daily LLM Radar"}},
            {"type": "section", "fields": overview_fields},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Today’s Top Picks*\n" + ("\n".join(top_pick_lines) if top_pick_lines else "No picks."),
                },
            },
            {"type": "context", "elements": [{"type": "mrkdwn", "text": delivery_mode}]},
        ],
    }]
    message_paper_ids: dict[int, list[str]] = {}
    total = len(ranked_rows)
    for i, (paper, meta) in enumerate(detail_rows, start=1):
        brief = build_paper_brief(paper, meta)
        author_profile = meta.get("author_profile", {})
        authors = author_profile.get("authors") or paper.authors
        emails = author_profile.get("emails") or []
        organizations = author_profile.get("organizations") or []
        author_text = ", ".join(authors[:3]) if authors else "N/A"
        if len(authors) > 3:
            author_text += ", et al."
        email_text = ", ".join(emails[:3]) if emails else ""
        author_field_label = "Author emails" if email_text else "Authors"
        author_field_value = email_text if email_text else author_text
        messages.append({
            "text": f"[{i}/{total}] {paper.title}",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*[{i}/{total}] {paper.title}*"},
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Score*\n{brief['score']}"},
                        {"type": "mrkdwn", "text": f"*Published*\n{paper.published.strftime('%Y-%m-%d %H:%M UTC')}"},
                        {"type": "mrkdwn", "text": f"*Tags*\n{brief['tags']}"},
                        {"type": "mrkdwn", "text": f"*{author_field_label}*\n{author_field_value}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Interest matches*\n{brief['interest_matches']}",
                    },
                },
                *([{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*Organizations*\n"
                            + truncate_for_slack(", ".join(organizations[:5]), max_len=300)
                        ),
                    },
                }] if organizations else []),
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Brief*\n{truncate_for_slack(brief['brief'], max_len=300)}",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Abstract (snippet)*\n{truncate_for_slack(brief['abstract'], max_len=500)}",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Open arXiv"},
                            "url": paper.link,
                        }
                    ],
                },
            ],
        })
        message_paper_ids[len(messages)] = [paper.paper_id]

    if tail_rows:
        tail_lines: list[str] = []
        for i, (paper, meta) in enumerate(tail_rows, start=len(detail_rows) + 1):
            brief = build_paper_brief(paper, meta)
            one_line_brief = truncate_for_slack(brief["brief"].replace("\n", " "), max_len=110)
            tail_lines.append(
                f"*{i}. [{brief['score']}] <{paper.link}|{paper.title}>*\n"
                f"   摘要: {one_line_brief}"
            )
        messages.append({
            "text": f"Thread: remaining {len(tail_rows)} papers",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Thread: Remaining {len(tail_rows)} Papers (Compact)*\n"
                            + "\n\n".join(tail_lines)
                        ),
                    },
                },
            ],
        })
        message_paper_ids[len(messages)] = [paper.paper_id for paper, _ in tail_rows]

    return messages, message_paper_ids


def split_text_chunks(text: str, max_chars: int = 3500) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_slack_messages_from_report(report_path: Path) -> list[dict[str, Any]]:
    try:
        report_text = report_path.read_text(encoding="utf-8").strip()
    except OSError:
        return [_payload_text_only(f"arXiv Daily LLM Radar\nReport unavailable: {report_path}")]

    if not report_text:
        return [_payload_text_only(f"arXiv Daily LLM Radar\nReport is empty: {report_path}")]

    prefix = f"arXiv Daily LLM Radar (force push existing report)\nReport: {report_path}"
    chunks = split_text_chunks(report_text, max_chars=3200)
    if len(chunks) == 1:
        return [_payload_text_only(f"{prefix}\n\n{chunks[0]}")]
    messages = [_payload_text_only(f"[1/{len(chunks)}]\n{prefix}\n\n{chunks[0]}")]
    messages.extend(_payload_text_only(f"[{idx}/{len(chunks)}]\n{chunk}") for idx, chunk in enumerate(chunks[1:], start=2))
    return messages


def post_to_slack(
    webhook_url: str,
    messages: list[dict[str, Any]],
    timeout: int = 20,
    send_interval_seconds: float = 0.0,
    max_retries: int = 4,
    on_message_sent: Any = None,
) -> tuple[int, int, str | None]:
    def send_one(idx: int, message: dict[str, Any]) -> tuple[int, bool, str | None]:
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        msg_title = str(message.get("text", ""))[:120]
        logger.info("slack_push_message_start index=%s title=%s", idx, msg_title)
        for attempt in range(max_retries + 1):
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout):
                    logger.info("slack_push_message_ok index=%s attempt=%s", idx, attempt + 1)
                    return idx, True, None
            except HTTPError as exc:
                retry_after = exc.headers.get("Retry-After")
                logger.warning(
                    "slack_push_message_http_error index=%s attempt=%s code=%s retry_after=%s",
                    idx, attempt + 1, exc.code, retry_after
                )
                if attempt < max_retries:
                    backoff = float(retry_after) if retry_after else min(2 ** attempt, 8)
                    time.sleep(backoff)
                    continue
                logger.error("slack_push_message_failed index=%s err=%s", idx, exc)
                return idx, False, str(exc)
            except Exception as exc:  # noqa: BLE001
                err_txt = f"msg={idx} attempt={attempt + 1}: {exc}"
                logger.warning("slack_push_message_retry index=%s attempt=%s err=%s", idx, attempt + 1, exc)
                if attempt < max_retries:
                    time.sleep(min(2 ** attempt, 8))
                else:
                    logger.error("slack_push_message_failed index=%s err=%s", idx, exc)
                    return idx, False, err_txt
        return idx, False, "unknown"

    logger.info("slack_push_start total_messages=%s mode=sequential", len(messages))
    sent = 0
    failed = 0
    first_error: str | None = None

    for idx, msg in enumerate(messages, start=1):
        _, ok, err = send_one(idx, msg)
        if ok:
            sent += 1
            if callable(on_message_sent):
                try:
                    on_message_sent(idx, msg)
                except Exception:  # noqa: BLE001
                    logger.exception("slack_push_sent_callback_error index=%s", idx)
        else:
            failed += 1
            if first_error is None:
                first_error = f"msg={idx}: {err}"
        if send_interval_seconds > 0 and idx < len(messages):
            time.sleep(send_interval_seconds)
    logger.info("slack_push_done sent=%s failed=%s", sent, failed)
    return sent, failed, first_error


def run(args: argparse.Namespace) -> int:
    logger.info("app_version=%s", APP_VERSION)
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    config = load_config(Path(args.config))
    state_path = Path(args.state or config.get("rss_state", "storage/data/state.json"))
    subscription_store_path = Path(
        args.subscription_store or config.get("subscription_store", "storage/data/subscriptions.json")
    )
    push_state_path = Path(args.push_state or config.get("push_state", "storage/data/push_state.json"))
    output_dir = Path(args.output_dir or config.get("rss_output_dir", "storage/reports"))

    state = load_state(state_path)
    subscription_store = load_subscription_store(subscription_store_path)
    push_state = load_push_state(push_state_path)
    last_run_raw = state.get("last_run")
    last_run = parse_dt(last_run_raw) if last_run_raw else None
    seen_ids: set[str] = set(state.get("seen_ids", [])) | set(subscription_store.get("seen_ids", []))
    push_state_retention_days = int(config.get("push_state_retention_days", 14))
    pushed_by_date_raw = push_state.get("pushed_by_date", {}) or {}
    pushed_by_date: dict[str, set[str]] = {
        str(k): set(v if isinstance(v, list) else [])
        for k, v in pushed_by_date_raw.items()
    }
    # Backward compatibility with old flat format.
    legacy_ids = set(push_state.get("pushed_paper_ids", []))
    if legacy_ids:
        pushed_by_date.setdefault(today_str, set()).update(legacy_ids)
    pushed_by_date = prune_pushed_by_date(pushed_by_date, today_str, push_state_retention_days)
    pushed_paper_ids: set[str] = set().union(*pushed_by_date.values()) if pushed_by_date else set()
    pushed_report_dates: set[str] = set(push_state.get("pushed_report_dates", []))

    try:
        papers = fetch_papers(args.categories, args.max_results, args.feed_file)
        logger.info("fetch_done total_papers=%s categories=%s", len(papers), args.categories)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch arXiv feed")
        return 2

    new_rows: list[Paper] = []
    for paper in papers:
        if paper.paper_id in seen_ids:
            continue
        if last_run and paper.published <= last_run:
            continue
        new_rows.append(paper)
        logger.info(
            "subscription_new id=%s published=%s title=%s",
            paper.paper_id,
            paper.published.strftime("%Y-%m-%dT%H:%M:%SZ"),
            paper.title,
        )

    sort_priority = args.sort_priority or config.get("sort_priority", "inference_acceleration")
    classify_workers = int(args.classify_workers or config.get("classify_workers", 8))
    logger.info("classify_stage_start total=%s workers=%s", len(new_rows), max(1, classify_workers))
    ranked_rows = classify_rows(new_rows, max(1, classify_workers))
    logger.info("classify_stage_done total=%s", len(ranked_rows))
    for paper, meta in ranked_rows:
        logger.info(
            "subscription_classified id=%s domain=%s relevant=%s score=%s inference_accel=%s",
            paper.paper_id,
            meta["primary_domain"],
            meta["relevant"],
            meta["heuristic_score"],
            meta.get("is_inference_accel", False),
        )
    llm_brief_api_key = str(config.get("llm_brief_api_key", "")).strip()
    llm_brief_base_url = str(config.get("llm_brief_base_url", QWEN_COMPAT_BASE_URL)).strip() or QWEN_COMPAT_BASE_URL
    llm_brief_model = str(config.get("llm_brief_model", "qwen-long")).strip() or "qwen-long"
    author_enrich = (
        args.author_enrich
        if args.author_enrich is not None
        else parse_bool(config.get("author_enrich", True), default=True)
    )
    author_cache_path = Path(args.author_cache or config.get("author_cache", "storage/data/author_cache.json"))
    author_enrich_max_papers = int(config.get("author_enrich_max_papers", 60))
    author_enrich_timeout = int(config.get("author_enrich_timeout_seconds", 8))
    author_enrich_workers = int(config.get("author_enrich_workers", 8))
    author_org_llm_enabled = parse_bool(config.get("author_org_llm_enabled", True), default=True)
    author_org_llm_api_key = str(config.get("author_org_llm_api_key", llm_brief_api_key)).strip() or llm_brief_api_key
    author_org_llm_base_url = (
        str(config.get("author_org_llm_base_url", llm_brief_base_url)).strip() or llm_brief_base_url
    )
    author_org_llm_model = str(config.get("author_org_llm_model", llm_brief_model)).strip() or llm_brief_model
    author_org_llm_timeout = int(config.get("author_org_llm_timeout_seconds", 20))
    author_org_llm_workers = int(config.get("author_org_llm_workers", 2))
    attach_author_profiles(
        ranked_rows,
        author_enrich,
        author_cache_path,
        max_papers=author_enrich_max_papers,
        timeout=author_enrich_timeout,
        workers=author_enrich_workers,
        organization_llm_enabled=author_org_llm_enabled,
        organization_llm_api_key=author_org_llm_api_key,
        organization_llm_base_url=author_org_llm_base_url,
        organization_llm_model=author_org_llm_model,
        organization_llm_timeout=author_org_llm_timeout,
        organization_llm_workers=author_org_llm_workers,
    )
    if author_enrich and ranked_rows:
        with_email = sum(1 for _, meta in ranked_rows if (meta.get("author_profile", {}).get("emails") or []))
        with_org = sum(1 for _, meta in ranked_rows if (meta.get("author_profile", {}).get("organizations") or []))
        logger.info(
            "author_profile_coverage papers=%s with_email=%s email_ratio=%.3f with_org=%s org_ratio=%.3f",
            len(ranked_rows),
            with_email,
            with_email / max(len(ranked_rows), 1),
            with_org,
            with_org / max(len(ranked_rows), 1),
        )
    llm_brief_enabled = (
        getattr(args, "llm_brief_enabled", None)
        if getattr(args, "llm_brief_enabled", None) is not None
        else parse_bool(config.get("llm_brief_enabled", False), default=False)
    )
    interest_topics = list(config.get("interest_topics", []))
    if not interest_topics:
        interest_topics = [
            "大模型训练",
            "大模型推理",
            "大模型基础设施",
            "推理加速",
            "distributed training",
            "serving system",
        ]
    llm_brief_cache_path = Path(config.get("llm_brief_cache", "storage/data/llm_brief_cache.json"))
    llm_brief_max_papers = int(config.get("llm_brief_max_papers", 250))
    llm_brief_timeout = int(config.get("llm_brief_timeout_seconds", 20))
    llm_brief_workers = int(config.get("llm_brief_workers", 4))
    llm_score_threshold = int(config.get("llm_score_threshold", config.get("llm_recommendation_threshold", 60)))
    attach_llm_briefs(
        ranked_rows,
        enabled=llm_brief_enabled,
        interest_topics=interest_topics,
        api_key=llm_brief_api_key,
        base_url=llm_brief_base_url,
        model=llm_brief_model,
        cache_path=llm_brief_cache_path,
        max_papers=llm_brief_max_papers,
        timeout=llm_brief_timeout,
        workers=llm_brief_workers,
    )
    if llm_brief_enabled and ranked_rows:
        llm_ok = sum(1 for _, meta in ranked_rows if meta.get("llm_brief", {}).get("brief"))
        llm_err = sum(1 for _, meta in ranked_rows if meta.get("llm_brief", {}).get("error"))
        logger.info(
            "llm_brief_coverage papers=%s with_brief=%s failed=%s ratio=%.3f model=%s",
            len(ranked_rows),
            llm_ok,
            llm_err,
            llm_ok / max(len(ranked_rows), 1),
            llm_brief_model,
        )
        for _, meta in ranked_rows:
            llm_score = int(meta.get("llm_brief", {}).get("score", 0) or 0)
            meta["score"] = llm_score
            meta["relevant"] = llm_score >= llm_score_threshold
    else:
        for _, meta in ranked_rows:
            meta["score"] = int(meta.get("heuristic_score", 0) or 0)
    ranked_rows.sort(key=lambda row: ranking_tuple(row[0], row[1], sort_priority), reverse=True)
    relevant_rows = [row for row in ranked_rows if row[1]["relevant"]]
    for idx, (paper, meta) in enumerate(ranked_rows, start=1):
        logger.info(
            "ranking_result rank=%s id=%s domain=%s relevant=%s score=%s title=%s",
            idx,
            paper.paper_id,
            meta["primary_domain"],
            meta["relevant"],
            meta["score"],
            paper.title,
        )

    webhook_url = (
        args.slack_webhook_url
        if args.slack_webhook_url is not None
        else config.get("slack_webhook_url") or os.getenv("SLACK_WEBHOOK_URL")
    )
    slack_when = args.slack_when or config.get("slack_when", "any")
    force_push_date = str(config.get("force_push_date", "")).strip()
    force_push_today = force_push_date == today_str
    force_push = bool(args.force_push) or force_push_today

    report_buckets = build_report_buckets(
        now=now,
        last_run=last_run,
        new_rows=new_rows,
        ranked_rows=ranked_rows,
        sort_priority=sort_priority,
    )
    if len(report_buckets) > 1:
        logger.info(
            "backfill_split_enabled buckets=%s range=%s",
            len(report_buckets),
            ",".join(day_key for day_key, _, _ in report_buckets),
        )

    any_force_push_existing_report = False
    report_paths: list[Path] = []
    slack_status_by_day: list[str] = []
    for bucket_index, (report_date_key, new_rows_bucket, ranked_rows_bucket) in enumerate(report_buckets, start=1):
        report_path = output_dir / f"{report_date_key}.md"
        report_paths.append(report_path)
        report_exists = report_path.exists()
        preserve_existing_report = len(new_rows_bucket) == 0 and report_exists
        force_push_existing_report = force_push and preserve_existing_report
        any_force_push_existing_report = any_force_push_existing_report or force_push_existing_report
        logger.info(
            "report_write_decision path=%s report_date=%s bucket=%s/%s exists=%s new_rows=%s preserve_existing=%s force_push_existing=%s",
            report_path,
            report_date_key,
            bucket_index,
            len(report_buckets),
            report_exists,
            len(new_rows_bucket),
            preserve_existing_report,
            force_push_existing_report,
        )
        if not preserve_existing_report:
            render_report(
                report_path,
                now,
                len(new_rows_bucket),
                ranked_rows_bucket,
                report_date=report_date_key,
            )

        ranked_rows_for_push = ranked_rows_bucket
        if not force_push:
            ranked_rows_for_push = [row for row in ranked_rows_bucket if row[0].paper_id not in pushed_paper_ids]
        dedup_filtered = len(ranked_rows_bucket) - len(ranked_rows_for_push)
        logger.info(
            "push_dedupe report_date=%s total_ranked=%s filtered_already_pushed=%s remaining=%s",
            report_date_key,
            len(ranked_rows_bucket),
            dedup_filtered,
            len(ranked_rows_for_push),
        )

        relevant_rows_bucket = [row for row in ranked_rows_bucket if row[1]["relevant"]]
        should_send_slack = bool(webhook_url) and (
            force_push
            or slack_when == "any"
            or (slack_when == "relevant" and len(relevant_rows_bucket) > 0)
        )
        if not force_push and len(ranked_rows_for_push) == 0:
            should_send_slack = False
            logger.info("push_dedupe_skip reason=no_new_rows_after_dedupe report_date=%s", report_date_key)
        if not force_push and report_date_key in pushed_report_dates and len(ranked_rows_for_push) == 0:
            should_send_slack = False
            logger.info("push_dedupe_skip reason=report_already_pushed date=%s", report_date_key)

        day_slack_status = "skipped"
        if should_send_slack:
            slack_top_detail_limit = int(config.get("slack_top_detail_limit", 10))
            if force_push_existing_report:
                slack_messages = build_slack_messages_from_report(report_path)
                paper_ids_by_message_index: dict[int, list[str]] = {}
            else:
                run_at_for_report = datetime.strptime(report_date_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                slack_messages, paper_ids_by_message_index = build_slack_messages(
                    run_at_for_report,
                    len(new_rows_bucket),
                    ranked_rows_for_push,
                    report_path,
                    report_date=report_date_key,
                    detail_limit=slack_top_detail_limit,
                )

            def on_message_sent(idx: int, _msg: dict[str, Any], report_date_key: str = report_date_key) -> None:
                # Persist push state incrementally to survive mid-run failures/restarts.
                pushed_report_dates.add(report_date_key)
                paper_ids = paper_ids_by_message_index.get(idx, [])
                for paper_id in paper_ids:
                    pushed_by_date.setdefault(report_date_key, set()).add(paper_id)
                    pushed_paper_ids.add(paper_id)
                pruned = prune_pushed_by_date(pushed_by_date, today_str, push_state_retention_days)
                save_push_state(
                    push_state_path,
                    {k: sorted(v) for k, v in sorted(pruned.items())},
                    sorted(pushed_report_dates)[-365:],
                )
                logger.info(
                    "push_state_incremental_saved msg_index=%s paper_ids=%s report_date=%s",
                    idx,
                    ",".join(paper_ids) if paper_ids else "",
                    report_date_key,
                )

            try:
                sent_count, fail_count, first_error = post_to_slack(
                    webhook_url,
                    slack_messages,
                    send_interval_seconds=float(config.get("slack_send_interval_seconds", 1.1)),
                    max_retries=int(config.get("slack_max_retries", 4)),
                    on_message_sent=on_message_sent,
                )
                if fail_count == 0:
                    day_slack_status = f"sent({sent_count})"
                else:
                    day_slack_status = f"partial(sent={sent_count}, failed={fail_count}, first_error={first_error})"
            except Exception as exc:  # noqa: BLE001
                day_slack_status = f"failed ({exc})"
        slack_status_by_day.append(f"{report_date_key}:{day_slack_status}")

    final_report_path = report_paths[-1] if report_paths else (output_dir / f"{today_str}.md")
    if len(slack_status_by_day) == 1:
        slack_status = slack_status_by_day[0].split(":", 1)[1]
    elif slack_status_by_day:
        slack_status = "; ".join(slack_status_by_day)
    else:
        slack_status = "skipped"

    # Update state at the end so the pipeline remains fetch -> classify -> enrich -> report -> push -> state.
    seen_ids.update(p.paper_id for p in new_rows)
    # Keep only the latest 5000 IDs to limit local state file growth.
    trimmed_seen = list(seen_ids)[-5000:]
    new_state = {
        "last_run": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seen_ids": trimmed_seen,
    }
    save_state(state_path, new_state)
    save_subscription_store(subscription_store_path, trimmed_seen)
    pushed_by_date = prune_pushed_by_date(pushed_by_date, today_str, push_state_retention_days)
    save_push_state(
        push_state_path,
        {k: sorted(v) for k, v in sorted(pushed_by_date.items())},
        sorted(pushed_report_dates)[-365:],
    )

    force_push_mode = "existing_report" if any_force_push_existing_report else ("on" if force_push else "off")
    logger.info(
        "scan_completed new_scanned=%s relevant=%s report=%s state=%s subscription_store=%s push_state=%s slack=%s force_push_mode=%s sort_priority=%s author_enrich=%s max_author_papers=%s",
        len(new_rows),
        len(relevant_rows),
        final_report_path,
        state_path,
        subscription_store_path,
        push_state_path,
        slack_status,
        force_push_mode,
        sort_priority,
        author_enrich,
        author_enrich_max_papers,
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incremental arXiv RSS assistant")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--config", default="storage/config.json", help="Path to JSON config file")
    parser.add_argument(
        "--categories",
        nargs="+",
        default=DEFAULT_CATEGORIES,
        help="arXiv categories to query (default: cs.LG cs.AI cs.CL cs.DC stat.ML)",
    )
    parser.add_argument("--max-results", type=int, default=250, help="Max results fetched from arXiv API")
    parser.add_argument("--state", default=None, help="Path to local state JSON")
    parser.add_argument("--subscription-store", default=None, help="Path to subscription seen-id store JSON")
    parser.add_argument("--push-state", default=None, help="Path to push dedupe state JSON")
    parser.add_argument("--output-dir", default=None, help="Directory for generated markdown reports")
    parser.add_argument(
        "--feed-file",
        default=None,
        help="Optional local Atom XML file for offline testing (skips network fetch)",
    )
    parser.add_argument(
        "--slack-webhook-url",
        default=None,
        help="Slack Incoming Webhook URL (or set env SLACK_WEBHOOK_URL)",
    )
    parser.add_argument(
        "--slack-when",
        choices=["any", "relevant"],
        default=None,
        help="When to send Slack notification: any run or only when relevant papers exist",
    )
    parser.add_argument(
        "--force-push",
        action="store_true",
        help="Force Slack push for this run even when no new/relevant papers",
    )
    parser.add_argument(
        "--sort-priority",
        choices=["inference_acceleration", "balanced", "recent"],
        default=None,
        help="Ranking strategy (default: inference_acceleration)",
    )
    parser.add_argument(
        "--classify-workers",
        type=int,
        default=None,
        help="Worker count for classification stage (default from config or 8)",
    )
    parser.add_argument(
        "--author-enrich",
        dest="author_enrich",
        action="store_true",
        help="Fetch author emails from arXiv HTML pages",
    )
    parser.add_argument(
        "--no-author-enrich",
        dest="author_enrich",
        action="store_false",
        help="Disable author enrichment network calls",
    )
    parser.set_defaults(author_enrich=None)
    parser.add_argument("--author-cache", default=None, help="Path to author enrichment cache JSON")
    parser.add_argument(
        "--llm-brief",
        dest="llm_brief_enabled",
        action="store_true",
        help="Enable Qwen brief generation from title + abstract",
    )
    parser.add_argument(
        "--no-llm-brief",
        dest="llm_brief_enabled",
        action="store_false",
        help="Disable Qwen brief generation",
    )
    parser.set_defaults(llm_brief_enabled=None)
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG/INFO/WARN/ERROR")
    parser.add_argument("--log-file", default=None, help="Optional log file path")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    setup_logging(args.log_level, args.log_file)
    raise SystemExit(run(args))
