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
import time
from html import unescape
import urllib.parse
import urllib.request
from urllib.error import HTTPError
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("paperrss.rss")


def setup_logging(log_level: str = "INFO", log_file: str | None = None) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
    )


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

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

    relevance_score = (
        domain_scores[primary_domain] * 4
        + llm_hint_score * 3
        + sum(domain_scores.values())
    )
    is_inference_accel = primary_domain == "inference" and inference_accel_score > 0

    return {
        "relevant": relevant,
        "primary_domain": primary_domain,
        "relevance_score": relevance_score,
        "inference_accel_score": inference_accel_score,
        "inference_accel_hits": inference_accel_hits,
        "is_inference_accel": is_inference_accel,
        "domain_scores": domain_scores,
        "domain_hits": domain_hits,
        "llm_hits": llm_hits,
    }


def ranking_tuple(paper: Paper, meta: dict, sort_priority: str) -> tuple:
    if sort_priority == "recent":
        return (paper.published.timestamp(), meta["relevance_score"])

    if sort_priority == "balanced":
        return (
            1 if meta["relevant"] else 0,
            meta["relevance_score"],
            paper.published.timestamp(),
        )

    # Highest-priority mode: inference acceleration papers first.
    return (
        1 if meta["is_inference_accel"] else 0,
        1 if meta["primary_domain"] == "inference" else 0,
        meta["inference_accel_score"],
        1 if meta["relevant"] else 0,
        meta["relevance_score"],
        paper.published.timestamp(),
    )


def build_paper_brief(paper: Paper, meta: dict) -> dict[str, str]:
    domain = meta["primary_domain"]
    hits = meta["domain_hits"][domain] + meta["llm_hits"]
    hits = list(dict.fromkeys(hits))[:6]
    focus_label = "relevant" if meta["relevant"] else "background"
    tags = list(dict.fromkeys([domain, focus_label] + hits[:4]))
    # Keep the full arXiv abstract text (no summarization/truncation).
    abstract_text = normalize(paper.summary)
    if not abstract_text:
        abstract_text = "N/A"
    return {
        "abstract": abstract_text,
        "tags": " / ".join(tags),
    }


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


def attach_author_profiles(
    ranked_rows: list[tuple[Paper, dict]],
    enabled: bool,
    cache_path: Path,
    max_papers: int,
    timeout: int,
    workers: int,
) -> None:
    if not enabled or not ranked_rows:
        return

    cache = load_author_cache(cache_path)
    updated = False
    pending: list[tuple[int, Paper, dict]] = []
    for idx, (paper, meta) in enumerate(ranked_rows, start=1):
        if idx > max_papers:
            meta["author_profile"] = {
                "authors": paper.authors,
                "emails": [],
                "source": None,
                "error": f"skipped_by_limit(max_papers={max_papers})",
            }
            logger.info("author_profile_skipped id=%s reason=limit rank=%s", paper.paper_id, idx)
            continue
        cached = cache.get(paper.paper_id)
        if cached:
            meta["author_profile"] = cached
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
                    profile = {
                        "authors": paper.authors,
                        "emails": [],
                        "source": None,
                        "error": str(exc),
                    }
                meta["author_profile"] = profile
                cache[paper.paper_id] = profile
                updated = True
                logger.info(
                    "author_profile_fetched id=%s rank=%s emails=%s source=%s err=%s",
                    paper.paper_id,
                    idx,
                    profile.get("emails", []),
                    profile.get("source"),
                    profile.get("error"),
                )
        logger.info("author_profile_stage_done pending=%s", len(pending))

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


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def strip_version(arxiv_id: str) -> str:
    return re.sub(r"v\\d+$", "", arxiv_id)


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
        return {
            "authors": paper.authors,
            "emails": [],
            "source": None,
            "error": last_err,
        }

    author_meta_pattern = re.compile(
        r'<meta\s+name=["\']citation_author["\']\s+content=["\'](.*?)["\']',
        re.IGNORECASE,
    )
    html_authors = [unescape(x.strip()) for x in author_meta_pattern.findall(page) if x.strip()]
    emails = extract_emails(page)
    return {
        "authors": html_authors or paper.authors,
        "emails": emails[:12],
        "source": source_url,
        "error": None,
    }


def render_report(
    report_path: Path,
    run_at: datetime,
    new_total: int,
    ranked_rows: list[tuple[Paper, dict]],
) -> None:
    relevant_rows = [row for row in ranked_rows if row[1]["relevant"]]
    domain_counter = Counter(row[1]["primary_domain"] for row in relevant_rows)
    keyword_counter = Counter()
    for _, meta in relevant_rows:
        for hits in meta["domain_hits"].values():
            keyword_counter.update(hits)
        keyword_counter.update(meta["llm_hits"])

    lines: list[str] = []
    lines.append(f"# arXiv Daily LLM Radar - {run_at.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(f"- Run time (UTC): {run_at.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append(f"- New papers scanned: {new_total}")
    lines.append(f"- Relevant papers (training/inference/infrastructure): {len(relevant_rows)}")
    lines.append("")

    if ranked_rows:
        lines.append("## Overview")
        lines.append("")
        lines.append(
            "- Domain distribution: "
            + ", ".join(f"{k}={domain_counter.get(k, 0)}" for k in ["training", "inference", "infrastructure"])
        )
        top_keywords = ", ".join(f"{k}({v})" for k, v in keyword_counter.most_common(10))
        lines.append(f"- Top keywords: {top_keywords or 'N/A'}")
        lines.append("")

        lines.append("## Ranked Papers (Full List)")
        lines.append("")
        lines.append("> Briefs are generated from arXiv abstracts (not full PDF reading).")
        lines.append("")
        for i, (paper, meta) in enumerate(ranked_rows, start=1):
            brief = build_paper_brief(paper, meta)
            tag = "relevant" if meta["relevant"] else "background"
            author_profile = meta.get("author_profile", {})
            authors = author_profile.get("authors") or paper.authors
            emails = author_profile.get("emails") or []
            authors_text = ", ".join(authors[:6]) if authors else "N/A"
            if len(authors) > 6:
                authors_text += ", et al."

            lines.append(f"### {i}. {paper.title} [{tag}]")
            lines.append("")
            lines.append(f"- Link: {paper.link}")
            lines.append(f"- Published: {paper.published.strftime('%Y-%m-%d %H:%M UTC')}")
            lines.append(f"- Ranking score: {meta['relevance_score']}")
            lines.append(f"- Authors: {authors_text}")
            if emails:
                lines.append(f"- Author emails (from HTML): {', '.join(emails[:5])}")
            lines.append(f"- Tags: {brief['tags']}")
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


def build_slack_messages(
    run_at: datetime,
    new_total: int,
    ranked_rows: list[tuple[Paper, dict]],
    report_path: Path,
) -> list[dict[str, Any]]:
    relevant_rows = [row for row in ranked_rows if row[1]["relevant"]]
    domain_counter = Counter(row[1]["primary_domain"] for row in relevant_rows)
    header_text = (
        f"arXiv Daily LLM Radar ({run_at.strftime('%Y-%m-%d')}) | "
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
        }]

    # One Slack message per paper, plus a single overview message.
    messages: list[dict[str, Any]] = [{
        "text": header_text,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "arXiv Daily LLM Radar"}},
            {"type": "section", "fields": overview_fields},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Delivery mode: one paper per message"}]},
        ],
    }]
    total = len(ranked_rows)
    for i, (paper, meta) in enumerate(ranked_rows, start=1):
        brief = build_paper_brief(paper, meta)
        author_profile = meta.get("author_profile", {})
        authors = author_profile.get("authors") or paper.authors
        emails = author_profile.get("emails") or []
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
                        {"type": "mrkdwn", "text": f"*Score*\n{meta['relevance_score']}"},
                        {"type": "mrkdwn", "text": f"*Published*\n{paper.published.strftime('%Y-%m-%d %H:%M UTC')}"},
                        {"type": "mrkdwn", "text": f"*Tags*\n{brief['tags']}"},
                        {"type": "mrkdwn", "text": f"*{author_field_label}*\n{author_field_value}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Abstract (snippet)*\n{truncate_for_slack(brief['abstract'])}",
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
    return messages


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
    workers: int = 4,
    preserve_order: bool = True,
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
        else:
            failed += 1
            if first_error is None:
                first_error = f"msg={idx}: {err}"
        if send_interval_seconds > 0 and idx < len(messages):
            time.sleep(send_interval_seconds)
    logger.info("slack_push_done sent=%s failed=%s", sent, failed)
    return sent, failed, first_error


def run(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    config = load_config(Path(args.config))
    state_path = Path(args.state)
    output_dir = Path(args.output_dir)

    state = load_state(state_path)
    last_run_raw = state.get("last_run")
    last_run = parse_dt(last_run_raw) if last_run_raw else None
    seen_ids: set[str] = set(state.get("seen_ids", []))

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
            meta["relevance_score"],
            meta.get("is_inference_accel", False),
        )
    ranked_rows.sort(key=lambda row: ranking_tuple(row[0], row[1], sort_priority), reverse=True)
    author_enrich = (
        args.author_enrich
        if args.author_enrich is not None
        else parse_bool(config.get("author_enrich", True), default=True)
    )
    author_cache_path = Path(args.author_cache or config.get("author_cache", "data/author_cache.json"))
    author_enrich_max_papers = int(config.get("author_enrich_max_papers", 60))
    author_enrich_timeout = int(config.get("author_enrich_timeout_seconds", 8))
    author_enrich_workers = int(config.get("author_enrich_workers", 8))
    attach_author_profiles(
        ranked_rows,
        author_enrich,
        author_cache_path,
        max_papers=author_enrich_max_papers,
        timeout=author_enrich_timeout,
        workers=author_enrich_workers,
    )
    if author_enrich and ranked_rows:
        with_email = sum(1 for _, meta in ranked_rows if (meta.get("author_profile", {}).get("emails") or []))
        logger.info(
            "author_email_coverage papers=%s with_email=%s ratio=%.3f",
            len(ranked_rows),
            with_email,
            with_email / max(len(ranked_rows), 1),
        )
    relevant_rows = [row for row in ranked_rows if row[1]["relevant"]]
    for idx, (paper, meta) in enumerate(ranked_rows, start=1):
        logger.info(
            "ranking_result rank=%s id=%s domain=%s relevant=%s score=%s title=%s",
            idx,
            paper.paper_id,
            meta["primary_domain"],
            meta["relevant"],
            meta["relevance_score"],
            paper.title,
        )

    webhook_url = args.slack_webhook_url or config.get("slack_webhook_url") or os.getenv("SLACK_WEBHOOK_URL")
    slack_when = args.slack_when or config.get("slack_when", "any")
    force_push_date = str(config.get("force_push_date", "")).strip()
    force_push_today = force_push_date == today_str
    force_push = bool(args.force_push) or force_push_today

    report_path = output_dir / f"{now.strftime('%Y-%m-%d')}.md"
    report_exists = report_path.exists()
    # Preserve today's existing report when there is no new incremental data.
    preserve_existing_report = len(new_rows) == 0 and report_exists
    force_push_existing_report = force_push and preserve_existing_report
    logger.info(
        "report_write_decision path=%s exists=%s new_rows=%s preserve_existing=%s force_push_existing=%s",
        report_path,
        report_exists,
        len(new_rows),
        preserve_existing_report,
        force_push_existing_report,
    )
    if not preserve_existing_report:
        render_report(report_path, now, len(new_rows), ranked_rows)

    should_send_slack = bool(webhook_url) and (
        force_push
        or slack_when == "any"
        or (slack_when == "relevant" and len(relevant_rows) > 0)
    )
    if should_send_slack:
        if force_push_existing_report:
            slack_messages = build_slack_messages_from_report(report_path)
        else:
            slack_messages = build_slack_messages(now, len(new_rows), ranked_rows, report_path)
        try:
            sent_count, fail_count, first_error = post_to_slack(
                webhook_url,
                slack_messages,
                send_interval_seconds=float(config.get("slack_send_interval_seconds", 1.1)),
                max_retries=int(config.get("slack_max_retries", 4)),
                workers=int(config.get("slack_push_workers", 4)),
                preserve_order=parse_bool(config.get("slack_preserve_order", True), default=True),
            )
            if fail_count == 0:
                slack_status = f"sent({sent_count})"
            else:
                slack_status = f"partial(sent={sent_count}, failed={fail_count}, first_error={first_error})"
        except Exception as exc:  # noqa: BLE001
            slack_status = f"failed ({exc})"
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

    force_push_mode = (
        "existing_report" if force_push_existing_report else
        "on" if force_push else
        "off"
    )
    logger.info(
        "scan_completed new_scanned=%s relevant=%s report=%s state=%s slack=%s force_push_mode=%s sort_priority=%s author_enrich=%s max_author_papers=%s",
        len(new_rows),
        len(relevant_rows),
        report_path,
        state_path,
        slack_status,
        force_push_mode,
        sort_priority,
        author_enrich,
        author_enrich_max_papers,
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incremental arXiv RSS assistant")
    parser.add_argument("--config", default="config.json", help="Path to JSON config file")
    parser.add_argument(
        "--categories",
        nargs="+",
        default=DEFAULT_CATEGORIES,
        help="arXiv categories to query (default: cs.LG cs.AI cs.CL cs.DC stat.ML)",
    )
    parser.add_argument("--max-results", type=int, default=250, help="Max results fetched from arXiv API")
    parser.add_argument("--state", default="data/state.json", help="Path to local state JSON")
    parser.add_argument("--output-dir", default="reports", help="Directory for generated markdown reports")
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
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG/INFO/WARN/ERROR")
    parser.add_argument("--log-file", default=None, help="Optional log file path")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    setup_logging(args.log_level, args.log_file)
    raise SystemExit(run(args))
