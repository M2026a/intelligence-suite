from __future__ import annotations

import html
import json
import re
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SHARED_DIR = ROOT / "shared"
CONFIG_PATH = SHARED_DIR / "domestic_config.json"
SOURCES_PATH = SHARED_DIR / "domestic_sources.json"
THEMES_PATH = SHARED_DIR / "domestic_themes.json"
JST = ZoneInfo("Asia/Tokyo")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)
with open(SOURCES_PATH, "r", encoding="utf-8") as f:
    SOURCES = json.load(f)
with open(THEMES_PATH, "r", encoding="utf-8") as f:
    THEMES = json.load(f)

DB_PATH = ROOT / CONFIG.get("db_path", "output/domestic_travel.db")
MAX_ITEMS_PER_SOURCE = int(CONFIG.get("max_items_per_source", 40))
HISTORY_DAYS = int(CONFIG.get("history_days", 45))
REQUEST_TIMEOUT = int(CONFIG.get("request_timeout_sec", 20))
NEW_BADGE_HOURS = int(CONFIG.get("new_badge_hours", 18))
USER_AGENT = "Mozilla/5.0 (compatible; TravelSearchPlus/0.0.3; +https://github.com/)"
CATEGORY_RULES = THEMES["categories"]
PREFECTURES = THEMES["prefectures"]
REGION_MAP = {"北海道": ["北海道"], "東北": ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"], "関東": ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"], "中部": ["新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県"], "近畿": ["滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"], "中国": ["鳥取県", "島根県", "岡山県", "広島県", "山口県"], "四国": ["徳島県", "香川県", "愛媛県", "高知県"], "九州": ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県"], "沖縄": ["沖縄県"]}
AREA_HINTS = {"東京都": ["東京", "渋谷", "新宿", "池袋", "上野", "浅草", "銀座", "六本木", "お台場"], "神奈川県": ["横浜", "みなとみらい", "鎌倉", "箱根", "江の島", "湘南"], "千葉県": ["舞浜", "幕張", "成田", "房総", "鴨川"], "埼玉県": ["川越", "秩父", "大宮", "所沢"], "愛知県": ["名古屋", "犬山", "常滑", "ジブリパーク"], "静岡県": ["熱海", "伊豆", "浜松", "富士宮"], "京都府": ["京都", "嵐山", "祇園", "清水寺", "宇治"], "大阪府": ["大阪", "梅田", "難波", "心斎橋", "天王寺", "USJ"], "兵庫県": ["神戸", "姫路", "有馬", "淡路"], "奈良県": ["奈良", "吉野"], "北海道": ["札幌", "小樽", "函館", "富良野", "旭川"], "沖縄県": ["沖縄", "那覇", "石垣", "宮古島", "美ら海"]}
TAG_RULES = {"屋内": ["美術館", "博物館", "水族館", "カフェ", "ホテル", "展覧会", "企画展", "屋内"], "屋外": ["花火", "公園", "庭園", "祭", "フェス", "海", "山", "屋外"], "今日向け": ["本日", "今日", "当日", "きょう", "本日から"], "週末向け": ["週末", "土日", "連休", "今週末"], "雨注意": ["大雨", "警報", "注意報", "雷", "台風", "強風"], "予約": ["予約", "抽選", "販売", "発売", "前売"]}


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
            prefecture TEXT NOT NULL,
            region TEXT NOT NULL,
            tags TEXT,
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 1
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_fetched_at ON items(fetched_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_category ON items(category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_prefecture ON items(prefecture)")
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


def infer_prefecture(title: str, summary: str) -> str:
    text = f"{title} {summary}"
    for pref in PREFECTURES:
        if pref in text: return pref
    for pref, hints in AREA_HINTS.items():
        for hint in hints:
            if hint in text: return pref
    return "全国"


def infer_region(prefecture: str) -> str:
    for region, prefs in REGION_MAP.items():
        if prefecture in prefs: return region
    return "全国"


def infer_tags(title: str, summary: str, category: str) -> list[str]:
    text = f"{title} {summary}"
    tags = [tag for tag, keywords in TAG_RULES.items() if any(kw in text for kw in keywords)]
    if category in {"観光スポット", "イベント"} and "屋内" not in tags and "屋外" not in tags:
        tags.append("要確認")
    return tags[:4]


def build_subtitle(prefecture: str, category: str, tags: list[str], source_name: str) -> str:
    tag_text = " / ".join(tags[:2]) if tags else source_name
    return f"{prefecture} / {category} / {tag_text or source_name}"


def _find_text(node, names):
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return found.text
    return ""


def fetch_rss_source(source: dict) -> list[dict]:
    req = Request(source["url"], headers={"User-Agent": USER_AGENT})
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
            items.append({"title": title, "summary": summary, "link": link, "published_at": parse_datetime(published_raw), "source_name": source["name"], "default_category": source.get("category", "お知らせ")})
    return items


def refresh_domestic_data() -> tuple[int, int, list[str]]:
    ensure_dirs(); init_db(); cleanup_old_data()
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    fetched_count = 0; inserted_count = 0; errors: list[str] = []
    conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
    cur.execute("SELECT link FROM items")
    existing_links: set[str] = {r[0] for r in cur.fetchall()}
    total_sources = len(SOURCES)
    max_workers = min(8, total_sources) if total_sources else 1
    print(f"    Domestic: refreshing feeds... ({total_sources} sources / {max_workers} workers)", flush=True)

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
                    prefecture = infer_prefecture(row["title"], row["summary"])
                    region = infer_region(prefecture)
                    tags = infer_tags(row["title"], row["summary"], category)
                    subtitle = build_subtitle(prefecture, category, tags, row["source_name"])
                    published_at = row["published_at"] or now_str
                    try:
                        age = datetime.now(JST).replace(tzinfo=None) - datetime.strptime(published_at, "%Y-%m-%d %H:%M:%S")
                        is_new = 1 if age.total_seconds() <= NEW_BADGE_HOURS * 3600 else 0
                    except Exception:
                        is_new = 0
                    insert_batch.append((row["title"], subtitle, row["summary"], row["link"], row["source_name"], category, prefecture, region, ", ".join(tags), published_at, now_str, is_new))
                if insert_batch:
                    cur.executemany("INSERT OR IGNORE INTO items (title, subtitle, summary, link, source_name, category, prefecture, region, tags, published_at, fetched_at, is_new) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", insert_batch)
                    inserted_count += len(insert_batch)
                print(f"    Domestic: done {completed}/{total_sources} - {source_name}", flush=True)
            except Exception as exc:
                msg = f"{source_name}: {exc}"
                errors.append(msg)
                print(f"    [WARN] {completed}/{total_sources} {msg}", flush=True)

    conn.commit(); conn.close()
    print(f"    Domestic: refresh complete (fetched {fetched_count}, inserted {inserted_count}, warnings {len(errors)})", flush=True)
    return fetched_count, inserted_count, errors


def read_items() -> list[dict]:
    ensure_dirs(); init_db()
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("SELECT id, title, subtitle, summary, link, source_name, category, prefecture, region, tags, published_at, fetched_at, is_new FROM items ORDER BY datetime(COALESCE(published_at, fetched_at)) DESC, id DESC LIMIT 600")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def summarize(items: list[dict]) -> dict:
    return {
        "total": len(items),
        "new_count": sum(1 for item in items if item.get("is_new")),
        "latest_time": items[0]["published_at"] if items else datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "categories": Counter(item["category"] for item in items),
        "prefectures": Counter(item["prefecture"] for item in items),
        "sources": Counter(item["source_name"] for item in items),
    }


def fetch_and_get_data(refresh: bool = True) -> tuple[list[dict], dict, int, int, list[str]]:
    """Main entry point for main.py. Returns (items, summary, fetched, inserted, errors)."""
    if refresh:
        try:
            fetched_count, inserted_count, errors = refresh_domestic_data()
        except Exception as exc:
            fetched_count, inserted_count, errors = 0, 0, [str(exc)]
    else:
        fetched_count, inserted_count, errors = 0, 0, []
    items = read_items()
    summary = summarize(items)
    return items, summary, fetched_count, inserted_count, errors
