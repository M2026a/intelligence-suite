from __future__ import annotations

import html
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_JST = ZoneInfo("Asia/Tokyo")
from email.utils import parsedate_to_datetime
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36 ExecutiveSignal"
TIMEOUT = 15
MAX_ITEMS_PER_SOURCE = 20
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def strip_html_tags(text: str) -> str:
    if not text:
        return ""
    text = str(text)
    if "<" not in text and ">" not in text:
        return normalize_whitespace(text)
    return BeautifulSoup(text, "lxml").get_text(separator=" ")


@dataclass
class Article:
    module: str
    source: str
    source_url: str
    title: str
    url: str
    summary: str
    published: str
    lang: str
    source_weight: int
    source_type: str
    region: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def normalize_for_match(text: str) -> str:
    text = normalize_whitespace(text).lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^\w\s一-龠ぁ-んァ-ヶー]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_date(value: str | None) -> str:
    if not value:
        return datetime.now(_JST).strftime("%Y-%m-%d %H:%M")
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_JST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return datetime.now(_JST).strftime("%Y-%m-%d %H:%M")


def _request_text(url: str) -> str:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = response.encoding or response.apparent_encoding or "utf-8"
    return response.text


def _parse_feed_candidate(url: str) -> Any:
    last_exc: Exception | None = None
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        if getattr(parsed, "entries", None):
            return parsed
    except Exception as exc:
        last_exc = exc
    if last_exc is not None:
        raise last_exc
    return feedparser.parse(b"")


def fetch_rss(source: dict[str, Any], module: str) -> list[Article]:
    candidate_urls = [source["url"], *source.get("fallback_urls", [])]
    last_exc: Exception | None = None
    for candidate_url in candidate_urls:
        try:
            feed = _parse_feed_candidate(candidate_url)
            seen: set[str] = set()
            items: list[Article] = []
            for entry in getattr(feed, "entries", [])[:MAX_ITEMS_PER_SOURCE * 2]:
                title = normalize_whitespace(getattr(entry, "title", ""))
                url = normalize_whitespace(getattr(entry, "link", ""))
                summary = normalize_whitespace(strip_html_tags(getattr(entry, "summary", getattr(entry, "description", ""))))
                published = parse_date(getattr(entry, "published", getattr(entry, "updated", None)))
                if not title or not url:
                    continue
                key = url.lower()
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    Article(
                        module=module,
                        source=source["name"],
                        source_url=candidate_url,
                        title=title,
                        url=url,
                        summary=summary,
                        published=published,
                        lang=source.get("lang", "en"),
                        source_weight=source.get("weight", 3),
                        source_type="rss",
                        region=source.get("region", "global"),
                    )
                )
                if len(items) >= MAX_ITEMS_PER_SOURCE:
                    break
            if items:
                return items
        except Exception as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    return []


def _allowed_by_patterns(url: str, include_patterns: list[str], exclude_patterns: list[str]) -> bool:
    low = url.lower()
    if include_patterns and not any(p.lower() in low for p in include_patterns):
        return False
    if exclude_patterns and any(p.lower() in low for p in exclude_patterns):
        return False
    return True


def fetch_html(source: dict[str, Any], module: str) -> list[Article]:
    candidate_urls = [source["url"], *source.get("fallback_urls", [])]
    last_exc: Exception | None = None
    include_patterns = source.get("include_url_patterns", [])
    exclude_patterns = source.get("exclude_url_patterns", [])

    for candidate_url in candidate_urls:
        try:
            html_text = _request_text(candidate_url)
            soup = BeautifulSoup(html_text, "lxml")
            nodes = soup.select(source["item_selector"])
            seen: set[str] = set()
            items: list[Article] = []
            for node in nodes:
                href = (node.get("href") or "").strip()
                if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
                    continue
                url = urljoin(source.get("base_url") or candidate_url, href)
                if not _allowed_by_patterns(url, include_patterns, exclude_patterns):
                    continue
                text = normalize_whitespace(node.get_text(" ", strip=True))
                if len(text) < 12:
                    continue
                norm_title = normalize_for_match(text)
                if len(norm_title) < 8:
                    continue
                url_key = url.lower()
                if url_key in seen:
                    continue
                seen.add(url_key)
                items.append(
                    Article(
                        module=module,
                        source=source["name"],
                        source_url=candidate_url,
                        title=text,
                        url=url,
                        summary="",
                        published=datetime.now(_JST).strftime("%Y-%m-%d %H:%M"),
                        lang=source.get("lang", "en"),
                        source_weight=source.get("weight", 2),
                        source_type="html",
                        region=source.get("region", "global"),
                    )
                )
                if len(items) >= MAX_ITEMS_PER_SOURCE:
                    break
            if items:
                return items
        except Exception as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    return []


def fetch_module_articles(module: str, sources: list[dict[str, Any]], verbose: bool = False) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    health: list[dict[str, Any]] = []
    if not sources:
        return results, warnings, health

    source_total = len(sources)
    max_workers = min(8, source_total)

    if verbose:
        print(f"    🚀 parallel fetch: {source_total} sources / {max_workers} workers")

    def run_one(idx: int, source: dict[str, Any]) -> tuple[int, dict[str, Any], list[dict[str, Any]], list[str], dict[str, Any]]:
        local_warnings: list[str] = []
        if source["type"] == "rss":
            items = fetch_rss(source, module)
        else:
            items = fetch_html(source, module)
        count = len(items)
        status = "ok" if count > 0 else "zero"
        if count == 0:
            local_warnings.append(f"{module} / {source['name']}: no items fetched")
        health_row = {
            "module": module,
            "source": source["name"],
            "type": source["type"],
            "count": count,
            "status": status,
        }
        return idx, source, [item.to_dict() for item in items], local_warnings, health_row

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {}
        for idx, source in enumerate(sources, start=1):
            if verbose:
                print(f"    > [{idx}/{source_total}] {source['name']} ({source['type']}) queued")
            futures[ex.submit(run_one, idx, source)] = (idx, source)

        for fut in as_completed(futures):
            idx, source = futures[fut]
            try:
                idx, source, item_dicts, local_warnings, health_row = fut.result()
                results.extend(item_dicts)
                warnings.extend(local_warnings)
                health.append(health_row)
                if verbose:
                    print(f"      ✓ [{idx}/{source_total}] {source['name']}: {health_row['count']} items")
            except Exception as exc:
                warnings.append(f"{module} / {source['name']}: {exc}")
                health.append({
                    "module": module,
                    "source": source["name"],
                    "type": source["type"],
                    "count": 0,
                    "status": "error",
                })
                if verbose:
                    print(f"      ! [{idx}/{source_total}] {source['name']}: {exc}")

    health.sort(key=lambda x: x["source"])
    return results, warnings, health
