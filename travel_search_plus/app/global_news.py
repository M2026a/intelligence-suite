from __future__ import annotations

import html
import json
import re
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

try:
    from deep_translator import GoogleTranslator
    _TRANSLATOR_AVAILABLE = True
except ImportError:
    _TRANSLATOR_AVAILABLE = False

ROOT = Path(__file__).resolve().parent.parent
SHARED_DIR = ROOT / "shared"
CONFIG_PATH = SHARED_DIR / "global_config.json"
SOURCES_PATH = SHARED_DIR / "global_sources.json"
THEMES_PATH = SHARED_DIR / "global_themes.json"
JST = ZoneInfo("Asia/Tokyo")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)
with open(SOURCES_PATH, "r", encoding="utf-8") as f:
    SOURCES = json.load(f)
with open(THEMES_PATH, "r", encoding="utf-8") as f:
    THEMES = json.load(f)

DB_PATH = ROOT / CONFIG.get("db_path", "output/global_travel.db")
MAX_ITEMS_PER_SOURCE = int(CONFIG.get("max_items_per_source", 40))
HISTORY_DAYS = int(CONFIG.get("history_days", 45))
REQUEST_TIMEOUT = int(CONFIG.get("request_timeout_sec", 10))
NEW_BADGE_HOURS = int(CONFIG.get("new_badge_hours", 18))
TRANSLATE_WORKERS = int(CONFIG.get("translate_workers", 4))
TRANSLATE_RETRIES = int(CONFIG.get("translate_retries", 2))
CATEGORY_RULES = THEMES["categories"]
COUNTRY_REGION_MAP = THEMES["country_region_map"]
TAG_RULES = THEMES["tag_rules"]
REGIONS = THEMES["regions"]


def ensure_dirs() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            subtitle TEXT NOT NULL,
            summary TEXT,
            link TEXT NOT NULL UNIQUE,
            source_name TEXT NOT NULL,
            category TEXT NOT NULL,
            country TEXT NOT NULL,
            region TEXT NOT NULL,
            lang TEXT NOT NULL DEFAULT 'ja',
            tags TEXT,
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 1,
            title_ja TEXT NOT NULL DEFAULT '',
            summary_ja TEXT NOT NULL DEFAULT ''
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_fetched_at ON items(fetched_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_category ON items(category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_country ON items(country)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_region ON items(region)")
    for col_def in ["title_ja TEXT NOT NULL DEFAULT ''", "summary_ja TEXT NOT NULL DEFAULT ''"]:
        col_name = col_def.split()[0]
        try:
            cur.execute(f"ALTER TABLE items ADD COLUMN {col_def}")
        except Exception:
            pass
    conn.commit(); conn.close()


def cleanup_old_data() -> None:
    cutoff = datetime.now(JST).replace(tzinfo=None) - timedelta(days=HISTORY_DAYS)
    conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
    cur.execute("DELETE FROM items WHERE fetched_at < ?", (cutoff.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit(); conn.close()


def normalize_text(value: str) -> str:
    if not value: return ""
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_datetime(value: str) -> str:
    if not value: return ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is not None: dt = dt.astimezone(JST).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception: pass
    for fmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d"]:
        try: return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception: continue
    return ""


def infer_category(title: str, summary: str, default_category: str) -> str:
    joined = f"{title} {summary}".lower()
    for rule in CATEGORY_RULES:
        for kw in rule["keywords"]:
            if kw.lower() in joined: return rule["name"]
    return default_category or "お知らせ"


def infer_country(title: str, summary: str) -> str:
    text = f"{title} {summary}"
    for country in COUNTRY_REGION_MAP.keys():
        if country in text: return country
    return "全世界"


def infer_region(country: str) -> str:
    return COUNTRY_REGION_MAP.get(country, "全世界")


def infer_tags(title: str, summary: str) -> list[str]:
    text = f"{title} {summary}"
    tags = [tag for tag, keywords in TAG_RULES.items() if any(kw in text for kw in keywords)]
    return tags[:4]


def build_subtitle(country: str, category: str, tags: list[str], source_name: str) -> str:
    tag_text = " / ".join(tags[:2]) if tags else source_name
    return f"{country} / {category} / {tag_text or source_name}"


def _find_text(node, names):
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return found.text
    return ""


def fetch_rss_source(source: dict) -> list[dict]:
    req = Request(source["url"], headers={"User-Agent": "Mozilla/5.0 TravelSearchPlus/1.0"})
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    items = []
    nodes = root.findall('.//item') + root.findall('.//{http://www.w3.org/2005/Atom}entry')
    for node in nodes[:MAX_ITEMS_PER_SOURCE]:
        title = normalize_text(_find_text(node, ['title', '{http://www.w3.org/2005/Atom}title']))
        link = _find_text(node, ['link'])
        if not link:
            link_node = node.find('{http://www.w3.org/2005/Atom}link')
            if link_node is not None:
                link = link_node.attrib.get('href', '')
        summary = normalize_text(_find_text(node, ['description', 'summary', '{http://www.w3.org/2005/Atom}summary']))
        published_raw = _find_text(node, ['pubDate', 'published', 'updated', '{http://www.w3.org/2005/Atom}updated'])
        if title and link:
            items.append({"title": title, "summary": summary, "link": link, "published_at": parse_datetime(published_raw), "source_name": source["name"], "lang": source.get("lang", "ja"), "default_category": source.get("category", "お知らせ")})
    return items


def _translate_text(text: str) -> str:
    if not text or not _TRANSLATOR_AVAILABLE: return ""
    for attempt in range(TRANSLATE_RETRIES + 1):
        try:
            result = GoogleTranslator(source="auto", target="ja").translate(text[:4500])
            return result or ""
        except Exception:
            if attempt < TRANSLATE_RETRIES: time.sleep(1)
    return ""


def translate_pending_items() -> int:
    if not _TRANSLATOR_AVAILABLE:
        print("    Global: deep_translator が未インストールのため翻訳をスキップ", flush=True)
        return 0
    conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
    cur.execute("SELECT id, title, summary FROM items WHERE lang='en' AND title_ja=''")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        print("    Global: 翻訳待ちアイテムなし", flush=True)
        return 0
    print(f"    Global: {len(rows)} 件の英語記事を翻訳中...", flush=True)

    def _translate_row(row):
        rid, title, summary = row
        return rid, _translate_text(title), _translate_text(summary or "")

    results: list[tuple] = []
    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as executor:
        futs = {executor.submit(_translate_row, row): row[0] for row in rows}
        for fut in as_completed(futs):
            try: results.append(fut.result())
            except Exception as e: print(f"    [WARN] 翻訳エラー: {e}", flush=True)

    conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
    cur.executemany("UPDATE items SET title_ja=?, summary_ja=? WHERE id=?", [(r[1], r[2], r[0]) for r in results])
    conn.commit(); conn.close()
    print(f"    Global: 翻訳完了 ({len(results)} 件)", flush=True)
    return len(results)


def refresh_global_data() -> tuple[int, int, list[str]]:
    ensure_dirs(); init_db(); cleanup_old_data()
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    fetched_count = 0; inserted_count = 0; errors: list[str] = []
    conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
    cur.execute("SELECT link FROM items")
    existing_links: set[str] = {r[0] for r in cur.fetchall()}
    total_sources = len(SOURCES)
    max_workers = min(8, total_sources) if total_sources else 1
    print(f"    Global: refreshing feeds... ({total_sources} sources / {max_workers} workers)", flush=True)

    def _fetch(idx, source):
        return idx, source.get("name", f"source{idx}"), fetch_rss_source(source)

    future_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for idx, source in enumerate(SOURCES, start=1):
            future_map[executor.submit(_fetch, idx, source)] = (idx, source.get("name", f"source{idx}"))
        completed = 0
        for future in as_completed(future_map):
            completed += 1
            idx, source_name = future_map[future]
            try:
                _idx, _name, rows = future.result()
                fetched_count += len(rows)
                new_rows = [r for r in rows if r["link"] not in existing_links]
                insert_batch: list[tuple] = []
                for row in new_rows:
                    existing_links.add(row["link"])
                    category = infer_category(row["title"], row["summary"], row["default_category"])
                    country = infer_country(row["title"], row["summary"])
                    region = infer_region(country)
                    tags = infer_tags(row["title"], row["summary"])
                    subtitle = build_subtitle(country, category, tags, row["source_name"])
                    published_at = row["published_at"] or now_str
                    try:
                        age = datetime.now(JST).replace(tzinfo=None) - datetime.strptime(published_at, "%Y-%m-%d %H:%M:%S")
                        is_new = 1 if age.total_seconds() <= NEW_BADGE_HOURS * 3600 else 0
                    except Exception:
                        is_new = 0
                    insert_batch.append((row["title"], subtitle, row["summary"], row["link"], row["source_name"], category, country, region, row.get("lang", "ja"), ", ".join(tags), published_at, now_str, is_new, "", ""))
                if insert_batch:
                    cur.executemany("INSERT OR IGNORE INTO items (title, subtitle, summary, link, source_name, category, country, region, lang, tags, published_at, fetched_at, is_new, title_ja, summary_ja) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", insert_batch)
                    inserted_count += len(insert_batch)
                print(f"    Global: done {completed}/{total_sources} - {source_name}", flush=True)
            except Exception as exc:
                msg = f"{source_name}: {exc}"
                errors.append(msg)
                print(f"    [WARN] {completed}/{total_sources} {msg}", flush=True)

    conn.commit(); conn.close()
    print(f"    Global: refresh complete (fetched {fetched_count}, inserted {inserted_count}, warnings {len(errors)})", flush=True)
    translate_pending_items()
    return fetched_count, inserted_count, errors


def read_items() -> list[dict]:
    ensure_dirs(); init_db()
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("""
        SELECT id, title, subtitle, summary, link, source_name, category,
               country, region, lang, tags, published_at, fetched_at, is_new,
               title_ja, summary_ja
        FROM items
        ORDER BY datetime(COALESCE(published_at, fetched_at)) DESC, id DESC
        LIMIT 600
    """)
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def summarize(items: list[dict]) -> dict:
    return {
        "total": len(items),
        "new_count": sum(1 for item in items if item.get("is_new")),
        "latest_time": items[0]["published_at"] if items else datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "categories": Counter(item["category"] for item in items),
        "countries": Counter(item["country"] for item in items),
        "regions": Counter(item["region"] for item in items),
        "sources": Counter(item["source_name"] for item in items),
    }


def fetch_and_get_data(refresh: bool = True) -> tuple[list[dict], dict, int, int, list[str]]:
    """Main entry point for main.py. Returns (items, summary, fetched, inserted, errors)."""
    if refresh:
        try:
            fetched_count, inserted_count, errors = refresh_global_data()
        except Exception as exc:
            fetched_count, inserted_count, errors = 0, 0, [str(exc)]
    else:
        fetched_count, inserted_count, errors = 0, 0, []
    items = read_items()
    summary = summarize(items)
    return items, summary, fetched_count, inserted_count, errors
