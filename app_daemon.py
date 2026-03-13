#!/usr/bin/env python3
"""Resident app daemon for arXiv subscription + Slack ping/pong + health endpoint."""

from __future__ import annotations

import argparse
import collections
import json
import logging
import re
import sys
import threading
import time
from datetime import datetime, time as dt_time, timedelta, timezone
import urllib.request
from urllib.error import HTTPError
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

try:
    from zoneinfo import ZoneInfo
    SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    SHANGHAI_TZ = timezone(timedelta(hours=8))


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_time_snapshot() -> dict[str, str]:
    now_utc = datetime.now(timezone.utc)
    now_bjt = now_utc.astimezone(SHANGHAI_TZ)
    now_local = datetime.now().astimezone()
    return {
        "server_now_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "server_now_bjt": now_bjt.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "server_now_local": now_local.isoformat(),
        "server_local_tz": str(now_local.tzinfo or "unknown"),
        "scheduler_timezone": "Asia/Shanghai",
    }


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
                "next_run_at": None,
                "last_status": "never",
                "last_error": None,
            },
            "weekly": {
                "last_run_at": None,
                "next_run_at": None,
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
            payload = json.loads(json.dumps(self.data))
        payload.update(current_time_snapshot())
        return payload


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
        llm_brief_enabled=parse_bool(config.get("llm_brief_enabled", False), default=False),
        log_level="INFO",
        log_file=None,
    )


def parse_clock_hhmm(value: str, default_h: int, default_m: int) -> tuple[int, int]:
    raw = str(value or "").strip()
    match = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if not match:
        return default_h, default_m
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default_h, default_m
    return hour, minute


def normalize_report_modes(value: Any) -> set[str]:
    if isinstance(value, list):
        raw = [str(v).strip().lower() for v in value]
    else:
        raw = [part.strip().lower() for part in str(value or "daily,weekly").split(",")]
    modes = {mode for mode in raw if mode in {"daily", "weekly"}}
    return modes or {"daily", "weekly"}


def load_schedule_state(path: Path) -> dict[str, Any]:
    return load_json(path)


def save_schedule_state(path: Path, state: dict[str, Any]) -> None:
    paperrss_utils.save_json(path, state)


def upsert_schedule_state_key(path: Path, key: str, value: str) -> None:
    state = load_schedule_state(path)
    state[key] = value
    save_schedule_state(path, state)


def next_daily_due(now_local: datetime, hour: int, minute: int, last_daily_key: str | None) -> tuple[datetime, str, bool]:
    today_due = datetime.combine(now_local.date(), dt_time(hour=hour, minute=minute), tzinfo=SHANGHAI_TZ)
    today_key = now_local.date().isoformat()
    if now_local >= today_due and last_daily_key != today_key:
        return today_due, today_key, True
    next_date = now_local.date() if now_local < today_due else now_local.date() + timedelta(days=1)
    next_due = datetime.combine(next_date, dt_time(hour=hour, minute=minute), tzinfo=SHANGHAI_TZ)
    return next_due, next_date.isoformat(), False


def next_weekly_due(now_local: datetime, hour: int, minute: int, last_weekly_key: str | None) -> tuple[datetime, str, bool]:
    days_since_sunday = (now_local.weekday() + 1) % 7
    last_sunday = now_local.date() - timedelta(days=days_since_sunday)
    last_due = datetime.combine(last_sunday, dt_time(hour=hour, minute=minute), tzinfo=SHANGHAI_TZ)
    last_key = last_sunday.isoformat()
    if now_local >= last_due and last_weekly_key != last_key:
        return last_due, last_key, True
    next_sunday = last_sunday + timedelta(days=7)
    next_due = datetime.combine(next_sunday, dt_time(hour=hour, minute=minute), tzinfo=SHANGHAI_TZ)
    return next_due, next_sunday.isoformat(), False


def _fmt_utc(dt_obj: datetime | None) -> str | None:
    if dt_obj is None:
        return None
    return dt_obj.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _normalize_text(text)
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


def load_weekly_llm_cache(path: Path) -> dict[str, Any]:
    return load_json(path)


def save_weekly_llm_cache(path: Path, payload: dict[str, Any]) -> None:
    paperrss_utils.save_json(path, payload)


def parse_daily_report(report_path: Path) -> dict[str, Any]:
    try:
        text = report_path.read_text(encoding="utf-8")
    except OSError:
        return {"path": report_path, "date": report_path.stem, "new_scanned": 0, "relevant": 0, "entries": []}
    m_new = re.search(r"- New papers scanned: (\d+)", text)
    m_rel = re.search(r"- Relevant papers .*: (\d+)", text)
    new_scanned = int(m_new.group(1)) if m_new else 0
    relevant = int(m_rel.group(1)) if m_rel else 0
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_full_list = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line in {"## Full Ranked Papers", "## Ranked Papers (Full List)"}:
            in_full_list = True
            continue
        title_match = re.match(r"^### (\d+)\.\s+(.*?)\s+\[(relevant|background)\]$", line)
        if in_full_list and title_match:
            if current:
                entries.append(current)
            current = {
                "rank": int(title_match.group(1)),
                "title": title_match.group(2),
                "focus": title_match.group(3),
                "score": 0,
                "tags": "",
                "interest_matches": "",
                "brief": "",
            }
            continue
        if not current:
            continue
        if line.startswith("- Link: "):
            current["link"] = line[len("- Link: "):].strip()
        elif line.startswith("- Score: "):
            try:
                current["score"] = int(line[len("- Score: "):].strip())
            except ValueError:
                current["score"] = 0
        elif line.startswith("- Recommendation score: "):
            try:
                current["score"] = int(line[len("- Recommendation score: "):].strip())
            except ValueError:
                current["score"] = 0
        elif line.startswith("- Ranking score: "):
            continue
        elif line.startswith("- Tags: "):
            current["tags"] = line[len("- Tags: "):].strip()
        elif line.startswith("- Interest matches: "):
            current["interest_matches"] = line[len("- Interest matches: "):].strip()
        elif line.startswith("- Brief: "):
            current["brief"] = line[len("- Brief: "):].strip()
    if current:
        entries.append(current)
    return {
        "path": report_path,
        "date": report_path.stem,
        "new_scanned": new_scanned,
        "relevant": relevant,
        "entries": entries,
    }


def _split_report_values(raw: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"\s*/\s*|,\s*", str(raw or ""))
        if item.strip() and item.strip().lower() != "n/a"
    ]


def _pick_cluster_key(entry: dict[str, Any]) -> str:
    generic = {
        "training",
        "inference",
        "infrastructure",
        "relevant",
        "background",
    }
    for tag in _split_report_values(entry.get("tags", "")):
        tag_norm = tag.lower()
        if tag_norm not in generic:
            return tag
    interests = _split_report_values(entry.get("interest_matches", ""))
    if interests:
        return interests[0]
    return "misc"


def collect_weekly_reports(report_dir: Path, week_end_local: datetime) -> list[dict[str, Any]]:
    week_end_date = week_end_local.date()
    week_start_date = week_end_date - timedelta(days=6)
    reports: list[dict[str, Any]] = []
    for offset in range(7):
        day = week_start_date + timedelta(days=offset)
        path = report_dir / f"{day.isoformat()}.md"
        if not path.exists():
            continue
        try:
            reports.append(parse_daily_report(path))
        except OSError:
            continue
    return reports


def build_weekly_synthesis_prompt(summary: dict[str, Any], interest_topics: list[str]) -> str:
    interest_text = "; ".join(interest_topics) if interest_topics else "大模型训练; 大模型推理; 大模型基础设施"
    top_entries = summary.get("top_entries", [])[:8]
    clusters = summary.get("clusters", [])[:5]
    lines = [
        "You are writing a weekly engineering digest for LLM training/inference/infrastructure work.",
        "Return strict JSON only with this schema:",
        '{"takeaways":["3-5 concise Chinese bullets focused on engineering conclusions"],'
        '"theme_summary":"2-3 concise Chinese sentences summarizing the week"}',
        "Requirements:",
        "- Focus on actionable engineering trends, not generic literature summary.",
        "- Mention concrete system/training/inference implications when possible.",
        "- No markdown, no code fence, no extra keys.",
        f"- User interests: {interest_text}",
        f"- Window: {summary.get('week_start')} to {summary.get('week_end')}",
        f"- Top tags: {', '.join(summary.get('top_tags', [])[:10]) or 'N/A'}",
        f"- Strongest interest matches: {', '.join(summary.get('top_interests', [])[:8]) or 'N/A'}",
        "",
        "Top entries:",
    ]
    for idx, entry in enumerate(top_entries, start=1):
        lines.append(
            f"{idx}. title={entry.get('title', 'N/A')}; score={entry.get('score', 0)}; "
            f"tags={entry.get('tags', 'N/A')}; interests={entry.get('interest_matches', 'N/A')}; "
            f"brief={entry.get('brief', 'N/A')}"
        )
    lines.append("")
    lines.append("Theme clusters:")
    for idx, cluster in enumerate(clusters, start=1):
        lines.append(
            f"{idx}. theme={cluster.get('theme', 'N/A')}; count={cluster.get('count', 0)}; "
            f"best_score={cluster.get('best_score', 0)}; samples={', '.join(cluster.get('sample_titles', [])[:3])}"
        )
    return "\n".join(lines)


def call_weekly_qwen_synthesis(
    summary: dict[str, Any],
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
                    "You are an expert research program manager for large-model systems. "
                    "You summarize weekly paper streams into concise engineering takeaways."
                ),
            },
            {"role": "user", "content": build_weekly_synthesis_prompt(summary, interest_topics)},
        ],
        "temperature": 0.2,
        "stream": False,
        "max_tokens": 500,
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
    takeaways_raw = parsed.get("takeaways", [])
    if isinstance(takeaways_raw, list):
        takeaways = [_normalize_text(item) for item in takeaways_raw if _normalize_text(item)]
    else:
        takeaways = [_normalize_text(item) for item in re.split(r"[\n;]+", str(takeaways_raw or "")) if _normalize_text(item)]
    return {
        "theme_summary": _normalize_text(parsed.get("theme_summary", ""))[:600],
        "takeaways": takeaways[:5],
        "model": model,
        "source": "qwen",
        "error": None,
    }


def attach_weekly_synthesis(
    summary: dict[str, Any],
    config: dict[str, Any],
    weekly_key: str,
) -> dict[str, Any]:
    enabled = parse_bool(config.get("weekly_llm_enabled", config.get("llm_brief_enabled", False)), default=False)
    api_key = str(config.get("llm_brief_api_key", "")).strip()
    if not enabled or not api_key:
        logger.info("weekly_llm_synthesis_skipped enabled=%s has_key=%s week=%s", enabled, bool(api_key), weekly_key)
        return {
            "theme_summary": "",
            "takeaways": [],
            "model": str(config.get("llm_brief_model", "qwen-long")).strip() or "qwen-long",
            "source": "disabled",
            "error": "disabled_or_missing_key",
        }

    cache_path = Path(config.get("weekly_llm_cache", "storage/data/weekly_llm_cache.json"))
    cache = load_weekly_llm_cache(cache_path)
    cache_key = f"{weekly_key}:{str(config.get('llm_brief_model', 'qwen-long')).strip() or 'qwen-long'}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict) and cached.get("takeaways"):
        logger.info("weekly_llm_synthesis_cache_hit week=%s", weekly_key)
        return cached

    base_url = str(config.get("llm_brief_base_url", arxiv_rss_assistant.QWEN_COMPAT_BASE_URL)).strip() or arxiv_rss_assistant.QWEN_COMPAT_BASE_URL
    model = str(config.get("weekly_llm_model", config.get("llm_brief_model", "qwen-long"))).strip() or "qwen-long"
    timeout = int(config.get("weekly_llm_timeout_seconds", config.get("llm_brief_timeout_seconds", 20)))
    interest_topics = list(config.get("interest_topics", []))
    logger.info("weekly_llm_synthesis_start week=%s model=%s", weekly_key, model)
    try:
        result = call_weekly_qwen_synthesis(
            summary=summary,
            interest_topics=interest_topics,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
        )
    except HTTPError as exc:
        result = {"theme_summary": "", "takeaways": [], "model": model, "source": "qwen", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        result = {"theme_summary": "", "takeaways": [], "model": model, "source": "qwen", "error": str(exc)}
    cache[cache_key] = result
    save_weekly_llm_cache(cache_path, cache)
    logger.info(
        "weekly_llm_synthesis_done week=%s takeaways=%s err=%s",
        weekly_key,
        len(result.get("takeaways", [])),
        result.get("error"),
    )
    return result


def build_weekly_report_markdown(
    report_dir: Path,
    weekly_report_path: Path,
    week_end_local: datetime,
    weekly_synthesis: dict[str, Any] | None = None,
    *,
    write: bool = True,
) -> dict[str, Any] | None:
    reports = collect_weekly_reports(report_dir, week_end_local)
    if not reports:
        return None

    total_new = sum(item["new_scanned"] for item in reports)
    total_relevant = sum(item["relevant"] for item in reports)
    all_entries: list[dict[str, Any]] = []
    tag_counter: collections.Counter[str] = collections.Counter()
    interest_counter: collections.Counter[str] = collections.Counter()
    cluster_map: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        for entry in report["entries"]:
            enriched = dict(entry)
            enriched["report_date"] = report["date"]
            all_entries.append(enriched)
            tag_counter.update(_split_report_values(entry.get("tags", "")))
            interest_counter.update(_split_report_values(entry.get("interest_matches", "")))
            cluster_key = _pick_cluster_key(enriched)
            cluster_map.setdefault(cluster_key, []).append(enriched)
    all_entries.sort(
        key=lambda row: (
            int(row.get("score", 0)),
            row.get("report_date", ""),
            -int(row.get("rank", 9999)),
        ),
        reverse=True,
    )
    top_entries = all_entries[:10]
    cluster_items = sorted(
        cluster_map.items(),
        key=lambda item: (
            len(item[1]),
            max(int(row.get("score", 0)) for row in item[1]),
        ),
        reverse=True,
    )[:4]
    top_tags = [
        tag
        for tag, _ in tag_counter.most_common(20)
        if tag.lower() not in {"relevant", "background"}
    ][:8]
    top_interests = [item for item, _ in interest_counter.most_common(6)]
    week_end_date = week_end_local.date()
    week_start_date = week_end_date - timedelta(days=6)

    lines = [
        f"# arXiv Weekly LLM Radar - Week Ending {week_end_date.isoformat()}",
        "",
        f"- Run time (BJT): {week_end_local.strftime('%Y-%m-%d %H:%M:%S')} BJT",
        f"- Window (BJT): {week_start_date.isoformat()} to {week_end_date.isoformat()}",
        f"- Daily reports included: {len(reports)}",
        f"- Total new papers scanned: {total_new}",
        f"- Total relevant papers: {total_relevant}",
        "",
        "## Weekly Overview",
        "",
        "- Weekly mode: summarize themes first, then list the papers worth reading in depth.",
        "- Dominant themes: " + (" / ".join(top_tags[:5]) if top_tags else "N/A"),
        "- Strongest interest matches: " + (" / ".join(top_interests[:4]) if top_interests else "N/A"),
        "",
        "## Theme Summary",
        "",
    ]
    if weekly_synthesis:
        theme_summary = _normalize_text(weekly_synthesis.get("theme_summary", ""))
        takeaways = weekly_synthesis.get("takeaways", []) or []
        if theme_summary or takeaways:
            lines.append("## Engineering Takeaways")
            lines.append("")
            if theme_summary:
                lines.append(f"- Weekly synthesis: {theme_summary}")
                lines.append("")
            for item in takeaways:
                lines.append(f"- {item}")
            lines.append("")
    for idx, (theme, items) in enumerate(cluster_items, start=1):
        best = max(items, key=lambda row: int(row.get("score", 0)))
        sample_titles = ", ".join(row.get("title", "N/A") for row in items[:3])
        lines.append(f"### Theme {idx}. {theme}")
        lines.append("")
        lines.append(f"- Papers in cluster: {len(items)}")
        lines.append(f"- Highest score: {best.get('score', 0)}")
        lines.append(f"- Representative papers: {sample_titles}")
        if best.get("brief"):
            lines.append(f"- What matters: {best.get('brief')}")
        lines.append("")

    lines.append("## Best Of Week")
    lines.append("")
    for idx, entry in enumerate(top_entries, start=1):
        lines.append(f"### {idx}. {entry.get('title', 'N/A')}")
        lines.append("")
        lines.append(f"- Report date: {entry.get('report_date', 'N/A')}")
        lines.append(f"- Score: {entry.get('score', 0)}")
        if entry.get("interest_matches"):
            lines.append(f"- Interest matches: {entry.get('interest_matches')}")
        if entry.get("tags"):
            lines.append(f"- Tags: {entry.get('tags')}")
        if entry.get("brief"):
            lines.append(f"- Why it made the weekly cut: {entry.get('brief')}")
        if entry.get("link"):
            lines.append(f"- Link: {entry.get('link')}")
        lines.append("")
    if write:
        weekly_report_path.parent.mkdir(parents=True, exist_ok=True)
        weekly_report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {
        "week_start": week_start_date.isoformat(),
        "week_end": week_end_date.isoformat(),
        "report_count": len(reports),
        "new_scanned": total_new,
        "relevant": total_relevant,
        "top_entries": top_entries,
        "top_tags": top_tags,
        "top_interests": top_interests,
        "weekly_synthesis": weekly_synthesis or {"theme_summary": "", "takeaways": []},
        "clusters": [
            {
                "theme": theme,
                "count": len(items),
                "best_score": max(int(row.get("score", 0)) for row in items),
                "sample_titles": [row.get("title", "N/A") for row in items[:3]],
            }
            for theme, items in cluster_items
        ],
        "report_path": str(weekly_report_path),
    }


def build_weekly_slack_payload(summary: dict[str, Any]) -> dict[str, Any]:
    top_lines: list[str] = []
    for idx, entry in enumerate(summary.get("top_entries", [])[:8], start=1):
        title = entry.get("title", "N/A")
        link = entry.get("link", "")
        score = entry.get("score", 0)
        report_date = entry.get("report_date", "N/A")
        if link:
            top_lines.append(f"{idx}. [{score}] <{link}|{title}> ({report_date})")
        else:
            top_lines.append(f"{idx}. [{score}] {title} ({report_date})")
    top_text = "\n".join(top_lines) if top_lines else "No ranked papers."
    theme_lines = [
        f"• {theme['theme']} ({theme['count']} papers, best={theme['best_score']})"
        for theme in summary.get("clusters", [])[:4]
    ]
    theme_text = "\n".join(theme_lines) if theme_lines else "No clear weekly clusters."
    interest_text = " / ".join(summary.get("top_interests", [])[:4]) or "N/A"
    weekly_synthesis = summary.get("weekly_synthesis", {}) or {}
    takeaways = weekly_synthesis.get("takeaways", []) or []
    theme_summary = _normalize_text(weekly_synthesis.get("theme_summary", ""))
    takeaway_text = "\n".join(f"• {item}" for item in takeaways[:5]) if takeaways else "No weekly synthesis yet."
    return {
        "text": f"Weekly brief ({summary.get('week_start')} to {summary.get('week_end')})",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Weekly LLM Radar"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Window*\n{summary.get('week_start')} to {summary.get('week_end')}"},
                    {"type": "mrkdwn", "text": f"*Reports included*\n{summary.get('report_count', 0)}"},
                    {"type": "mrkdwn", "text": f"*New scanned*\n{summary.get('new_scanned', 0)}"},
                    {"type": "mrkdwn", "text": f"*Relevant*\n{summary.get('relevant', 0)}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*This Week's Themes*\n"
                        f"{theme_text}\n\n"
                        f"*Strongest Interest Matches*\n{interest_text}"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Engineering Takeaways*\n"
                        + (f"{theme_summary}\n\n" if theme_summary else "")
                        + takeaway_text
                    ),
                },
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Best Of Week*\n{top_text}"}},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Report: `{summary.get('report_path', 'N/A')}`"}
                ],
            },
        ],
    }


def daily_rss_loop(
    stop_event: threading.Event,
    app_state: AppState,
    config_path: str,
    config: dict,
    rss_run_lock: threading.Lock,
) -> None:
    modes = normalize_report_modes(config.get("report_modes", ["daily", "weekly"]))
    if "daily" not in modes:
        logger.info("daily_scheduler_disabled")
        app_state.update("rss", {"last_status": "disabled", "last_error": None})
        return

    daily_hour, daily_minute = parse_clock_hhmm(config.get("daily_report_time_bjt", "09:00"), 9, 0)
    schedule_state_path = Path(config.get("schedule_state", "storage/data/schedule_state.json"))
    while not stop_event.is_set():
        now_local = datetime.now(SHANGHAI_TZ)
        schedule_state = load_schedule_state(schedule_state_path)
        next_due, due_key, should_run = next_daily_due(now_local, daily_hour, daily_minute, schedule_state.get("last_daily_key"))
        app_state.update("rss", {"next_run_at": _fmt_utc(next_due)})
        if not should_run:
            wait_seconds = max(1.0, (next_due - now_local).total_seconds())
            logger.info("daily_scheduler_wait next_run_bjt=%s wait_seconds=%.1f", next_due.isoformat(), wait_seconds)
            if stop_event.wait(wait_seconds):
                break
            continue
        try:
            args = build_rss_args(config_path, config, force_push=False)
            logger.info("daily_scheduler_tick due_key=%s", due_key)
            with rss_run_lock:
                code = arxiv_rss_assistant.run(args)
            upsert_schedule_state_key(schedule_state_path, "last_daily_key", due_key)
            app_state.update(
                "rss",
                {
                    "last_run_at": now_utc_iso(),
                    "last_status": "ok" if code == 0 else "error",
                    "last_error": None if code == 0 else f"exit_code={code}",
                },
            )
            logger.info("daily_scheduler_done status=%s due_key=%s", "ok" if code == 0 else "error", due_key)
        except Exception as exc:  # noqa: BLE001
            logger.exception("daily_scheduler_error")
            upsert_schedule_state_key(schedule_state_path, "last_daily_key", due_key)
            app_state.update(
                "rss",
                {
                    "last_run_at": now_utc_iso(),
                    "last_status": "error",
                    "last_error": str(exc),
                },
            )

        if stop_event.wait(1):
            break


def weekly_report_loop(
    stop_event: threading.Event,
    app_state: AppState,
    config: dict,
) -> None:
    modes = normalize_report_modes(config.get("report_modes", ["daily", "weekly"]))
    if "weekly" not in modes:
        logger.info("weekly_scheduler_disabled")
        app_state.update("weekly", {"last_status": "disabled", "last_error": None})
        return

    weekly_hour, weekly_minute = parse_clock_hhmm(config.get("weekly_report_time_bjt", "18:00"), 18, 0)
    schedule_state_path = Path(config.get("schedule_state", "storage/data/schedule_state.json"))
    report_dir = Path(config.get("rss_output_dir", "storage/reports"))
    weekly_report_dir = Path(config.get("weekly_output_dir", str(report_dir)))
    webhook_url = config.get("slack_webhook_url")

    while not stop_event.is_set():
        now_local = datetime.now(SHANGHAI_TZ)
        schedule_state = load_schedule_state(schedule_state_path)
        next_due, due_key, should_run = next_weekly_due(
            now_local,
            weekly_hour,
            weekly_minute,
            schedule_state.get("last_weekly_key"),
        )
        app_state.update("weekly", {"next_run_at": _fmt_utc(next_due)})
        if not should_run:
            wait_seconds = max(1.0, (next_due - now_local).total_seconds())
            logger.info("weekly_scheduler_wait next_run_bjt=%s wait_seconds=%.1f", next_due.isoformat(), wait_seconds)
            if stop_event.wait(wait_seconds):
                break
            continue

        try:
            logger.info("weekly_scheduler_tick due_key=%s", due_key)
            weekly_report_path = weekly_report_dir / f"weekly-{due_key}.md"
            summary = build_weekly_report_markdown(report_dir, weekly_report_path, now_local, write=False)
            if summary is not None:
                weekly_synthesis = attach_weekly_synthesis(summary, config, due_key)
                summary = build_weekly_report_markdown(
                    report_dir,
                    weekly_report_path,
                    now_local,
                    weekly_synthesis=weekly_synthesis,
                )
            upsert_schedule_state_key(schedule_state_path, "last_weekly_key", due_key)
            if summary is None:
                logger.warning("weekly_scheduler_no_reports due_key=%s report_dir=%s", due_key, report_dir)
                app_state.update(
                    "weekly",
                    {
                        "last_run_at": now_utc_iso(),
                        "last_status": "ok",
                        "last_error": "no_reports_found",
                    },
                )
            elif webhook_url:
                sent, failed, first_error = arxiv_rss_assistant.post_to_slack(
                    webhook_url,
                    [build_weekly_slack_payload(summary)],
                    send_interval_seconds=0.0,
                    max_retries=int(config.get("slack_max_retries", 4)),
                )
                weekly_status = "ok" if failed == 0 else f"partial(sent={sent}, failed={failed}, err={first_error})"
                app_state.update(
                    "weekly",
                    {
                        "last_run_at": now_utc_iso(),
                        "last_status": weekly_status,
                        "last_error": None if failed == 0 else first_error,
                    },
                )
                logger.info("weekly_scheduler_done status=%s due_key=%s report=%s", weekly_status, due_key, weekly_report_path)
            else:
                app_state.update(
                    "weekly",
                    {
                        "last_run_at": now_utc_iso(),
                        "last_status": "skipped",
                        "last_error": "missing slack_webhook_url",
                    },
                )
                logger.warning("weekly_scheduler_skip due_key=%s reason=missing_webhook", due_key)
        except Exception as exc:  # noqa: BLE001
            logger.exception("weekly_scheduler_error")
            upsert_schedule_state_key(schedule_state_path, "last_weekly_key", due_key)
            app_state.update(
                "weekly",
                {
                    "last_run_at": now_utc_iso(),
                    "last_status": "error",
                    "last_error": str(exc),
                },
            )

        if stop_event.wait(1):
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
            target=daily_rss_loop,
            args=(stop_event, app_state, config_path, config, rss_run_lock),
            daemon=True,
        ),
        threading.Thread(
            target=weekly_report_loop,
            args=(stop_event, app_state, config),
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
    parser.add_argument("--config", default="storage/config.json", help="Path to JSON config")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG/INFO/WARN/ERROR")
    parser.add_argument("--log-file", default=None, help="Optional log file path")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
