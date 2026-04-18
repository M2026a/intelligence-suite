from __future__ import annotations
import subprocess
import sys

def ensure_package(import_name: str, package_name: str | None = None) -> None:
    package = package_name or import_name
    try:
        __import__(import_name)
    except ImportError:
        print(f"{package} が無いためインストールします...", flush=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

ensure_package("feedparser")
ensure_package("requests")
ensure_package("deep_translator", "deep-translator")

import hashlib
import html
import json
import re
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

JST = ZoneInfo("Asia/Tokyo")
CONFIG = json.loads((SHARED / "config.json").read_text(encoding="utf-8"))
SOURCES = json.loads((SHARED / "sources.json").read_text(encoding="utf-8"))
THEMES = json.loads((SHARED / "themes.json").read_text(encoding="utf-8"))["themes"]
APP_NAME = CONFIG["app_name"]
DB_FILE = OUTPUT_DIR / CONFIG["db_name"]
TRANSLATE_CACHE_FILE = OUTPUT_DIR / "translate_cache.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 WatchHubPlus"
TIMEOUT = 15
NEW_HOURS = int(CONFIG.get("new_badge_hours", 6))
MAX_ITEMS_PER_SOURCE = int(CONFIG.get("max_items_per_source", 20))
TRANSLATE_TOP_N = int(CONFIG.get("translate_top_n", 30))
TRANSLATE_WORKERS = int(CONFIG.get("translate_workers", 4))
TRANSLATE_RETRIES = int(CONFIG.get("translate_retries", 2))

NAV_ITEMS = [
    ("index.html", "📋 メイン"),
    ("movie.html", "🎬 映画"),
    ("streaming.html", "📺 配信"),
    ("anime.html", "🎌 アニメ"),
    ("game.html", "🎮 ゲーム"),
    ("entertainment.html", "⭐ 芸能"),
    ("trend.html", "📈 トレンド"),
    ("analysis.html", "📊 分析"),
]

MAIN_SECTIONS = ["映画", "配信", "アニメ", "ゲーム", "芸能", "トレンド"]
CATEGORY_TO_FILE = {
    "映画": "movie.html",
    "配信": "streaming.html",
    "アニメ": "anime.html",
    "ゲーム": "game.html",
    "芸能": "entertainment.html",
    "トレンド": "trend.html",
}
CATEGORY_TO_ICON = {
    "映画": "🎬",
    "配信": "📺",
    "アニメ": "🎌",
    "ゲーム": "🎮",
    "芸能": "⭐",
    "トレンド": "📈",
}
PLATFORM_LABELS = {"all": "全部", "netflix": "Netflix", "prime": "Prime", "disney": "Disney+"}
CATEGORY_KEYWORDS = {
    "アニメ": ["anime", "アニメ", "声優", "manga adaptation", "tv anime", "anime film"],
    "ゲーム": ["game", "gaming", "ゲーム", "playstation", "nintendo", "xbox", "steam", "esports", "dlc", "アップデート"],
    "配信": ["netflix", "prime video", "amazon prime", "disney+", "disney plus", "streaming", "vod", "配信", "サブスク", "tudum"],
    "映画": ["movie", "film", "映画", "洋画", "邦画", "box office", "cinema", "theater", "劇場公開", "興行"],
}
ENTERTAINMENT_KEYWORDS = [
    "俳優", "女優", "声優", "タレント", "歌手", "モデル", "結婚", "離婚", "交際", "出演", "コメント", "会見",
    "actor", "actress", "celebrity", "star", "married", "divorce", "relationship", "interview"
]
TREND_KEYWORDS = [
    "ランキング", "急上昇", "話題", "注目", "トレンド", "sns", "バズ", "1位", "首位", "top 10", "top10",
    "ranking", "viral", "trending", "buzz"
]
POSITIVE_WORDS = ["top 10", "award", "record", "ヒット", "話題", "人気", "注目", "新作", "公開", "配信開始", "renewed", "ランキング"]
CAUTION_WORDS = ["delay", "中止", "延期", "cancel", "controversy", "lawsuit", "problem", "不具合", "障害", "炎上"]


def has_japanese(text: str) -> bool:
    return bool(re.search(r'[\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef]', text or ""))


def has_hangul(text: str) -> bool:
    return bool(re.search(r'[\uac00-\ud7af]', text or ""))


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def text_lower(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def normalize_title_for_dedupe(title: str) -> str:
    t = text_lower(title)
    t = re.sub(r"（[^）]*）|\([^)]*\)|\[[^\]]*\]|【[^】]*】", " ", t)
    t = re.sub(r"(news|review|reviews|trailer|teaser|速報|特集|最新|公式)", " ", t)
    t = re.sub(r"[|｜／/・:：\-–—]+", " ", t)
    t = re.sub(r"[^\w぀-ヿ一-鿿]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_allowed_domain(link: str, allowed_domains: list[str]) -> bool:
    if not link:
        return False
    parsed = urlparse(link)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not allowed_domains:
        return True
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for domain in allowed_domains:
        d = domain.lower().strip()
        if d.startswith("www."):
            d = d[4:]
        if host == d or host.endswith("." + d):
            return True
    return False


def parse_dt(entry) -> tuple[str, str]:
    for attr in ("published_parsed", "updated_parsed"):
        value = getattr(entry, attr, None)
        if value:
            dt = datetime(*value[:6], tzinfo=ZoneInfo("UTC")).astimezone(JST)
            return dt.strftime("%Y-%m-%d %H:%M"), dt.isoformat()
    return "", ""


def is_new(pub_dt_iso: str) -> bool:
    if not pub_dt_iso:
        return False
    try:
        pub_dt = datetime.fromisoformat(pub_dt_iso)
        return datetime.now(JST) - pub_dt.astimezone(JST) <= timedelta(hours=NEW_HOURS)
    except Exception:
        return False


def detect_sentiment(title: str, summary: str) -> str:
    combined = text_lower(f"{title} {summary}")
    positive = sum(1 for w in POSITIVE_WORDS if w in combined)
    caution = sum(1 for w in CAUTION_WORDS if w in combined)
    if caution > positive:
        return "caution"
    if positive > 0:
        return "hot"
    return "neutral"


def load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default if default is not None else {}


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def translate_text(text: str, cache: dict) -> str:
    text = (text or "").strip()
    if not text or has_japanese(text):
        return text
    cached = cache.get(text)
    if cached is not None:
        return cached if has_japanese(cached) else ""
    for _ in range(max(1, TRANSLATE_RETRIES + 1)):
        try:
            translated = GoogleTranslator(source="auto", target="ja").translate(text)
            translated = (translated or "").strip()
            if translated and has_japanese(translated) and not has_hangul(translated):
                cache[text] = translated
                return translated
        except Exception:
            pass
        time.sleep(0.2)
    return ""


def detect_platform(title: str, summary: str, source_name: str, source_platform: str = "") -> str:
    if source_platform:
        return source_platform
    combined = text_lower(f"{title} {summary} {source_name}")
    if any(k in combined for k in ["netflix", "ネットフリックス", "ネトフリ", "tudum"]):
        return "netflix"
    if any(k in combined for k in ["prime video", "amazon prime", "prime", "プライムビデオ", "アマプラ"]):
        return "prime"
    if any(k in combined for k in ["disney+", "disney plus", "ディズニープラス", "ディズニー+"]):
        return "disney"
    return "other"


def detect_category(title: str, summary: str, source_name: str, source_category: str = "") -> str:
    combined = text_lower(f"{title} {summary} {source_name}")
    for category in ["アニメ", "ゲーム", "配信", "映画"]:
        if any(k in combined for k in CATEGORY_KEYWORDS[category]):
            return category
    # キーワード不一致時はソース定義のカテゴリを優先、なければ映画
    return source_category if source_category in CATEGORY_KEYWORDS else "映画"


def detect_entertainment(title: str, summary: str, source_name: str) -> bool:
    combined = text_lower(f"{title} {summary} {source_name}")
    return any(k in combined for k in ENTERTAINMENT_KEYWORDS)


def calc_score(item: dict) -> int:
    combined = text_lower(f"{item.get('title','')} {item.get('summary','')}")
    base = 1
    if item.get("official"):
        base = 3
    elif item.get("is_new"):
        base = 2
    bonus = 0
    if item.get("is_trend"):
        bonus = max(bonus, 2)
    if item.get("is_entertainment"):
        bonus = max(bonus, 1)
    if item.get("platform") in {"netflix", "prime", "disney"}:
        bonus = max(bonus, 1)
    for points, keywords in {
        3: ["top 10", "ランキング", "首位", "record", "受賞", "award", "box office", "興行収入", "配信開始", "新作", "release date", "トレーラー", "trailer"],
        2: ["announcement", "発表", "公開", "season", "シリーズ", "cast", "director", "監督", "主演", "声優"],
        1: ["review", "interview", "イベント", "feature", "特集"],
    }.items():
        if any(text_lower(k) in combined for k in keywords):
            bonus = max(bonus, points)
    return min(base + bonus, 5)


def detect_trend(title: str, summary: str, score_seed: int = 0) -> bool:
    combined = text_lower(f"{title} {summary}")
    if any(k in combined for k in TREND_KEYWORDS):
        return True
    return score_seed >= 4


def fetch_source(source: dict) -> tuple[list[dict], bool]:
    name = source["name"]
    log(f"  取得中: {name}")
    try:
        resp = requests.get(source["url"], headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log(f"  [WARN] {name}: {e}")
        return [], False
    items: list[dict] = []
    for entry in feed.entries[:MAX_ITEMS_PER_SOURCE]:
        title = clean_text(getattr(entry, "title", ""))
        summary = clean_text(getattr(entry, "summary", "") or getattr(entry, "description", ""))
        link = getattr(entry, "link", "")
        if not title or not link:
            continue
        if not is_allowed_domain(link, source.get("allowed_domains", [])):
            continue
        published, pub_dt_iso = parse_dt(entry)
        lang = source.get("lang", "en")
        category = detect_category(title, summary, name, source.get("category", ""))
        is_ent = detect_entertainment(title, summary, name) or source.get("category") == "芸能"
        platform = detect_platform(title, summary, name, source.get("platform", ""))
        item = {
            "id": hashlib.md5(f"{title}|{link}".encode("utf-8")).hexdigest(),
            "source": name,
            "lang": lang,
            "region": source.get("region", "Global"),
            "official": bool(source.get("official", False)),
            "title": title,
            "summary": summary,
            "title_ja": title if lang == "ja" else "",
            "summary_ja": summary if lang == "ja" else "",
            "link": link,
            "published": published,
            "pub_dt": pub_dt_iso,
            "category": category,
            "platform": platform,
            "is_entertainment": is_ent,
            "is_new": is_new(pub_dt_iso),
        }
        item["is_trend"] = detect_trend(title, summary, 0)
        item["score"] = calc_score(item)
        if not item["is_trend"]:
            item["is_trend"] = detect_trend(title, summary, item["score"])
            item["score"] = calc_score(item)
        themes = [category]
        if is_ent:
            themes.append("芸能")
        if item["is_trend"]:
            themes.append("トレンド")
        if category == "配信":
            if platform == "netflix":
                themes.append("Netflix")
            elif platform == "prime":
                themes.append("Prime")
            elif platform == "disney":
                themes.append("Disney+")
        item["themes"] = list(dict.fromkeys(themes))
        items.append(item)
    log(f"  ✓ {name}: {len(items)}件")
    return items, True


def dedupe_items(items: list[dict]) -> list[dict]:
    seen_links = set()
    seen_titles = set()
    result = []
    for item in sorted(items, key=lambda x: (-(x.get("score", 1)), x.get("published", "")), reverse=False):
        link = item.get("link", "")
        title_key = normalize_title_for_dedupe(item.get("title", ""))
        if link in seen_links or title_key in seen_titles:
            continue
        seen_links.add(link)
        seen_titles.add(title_key)
        result.append(item)
    return sorted(result, key=lambda x: (x.get("region") != "JP", -x.get("score", 1), x.get("published", "")))


def translate_items(items: list[dict], cache: dict) -> None:
    targets = [x for x in items if x.get("lang") != "ja"][:TRANSLATE_TOP_N]
    if not targets:
        return
    log(f"\n🌐 翻訳中... ({len(targets)}件 全件)")
    sep = "\n\n---\n\n"

    def do_translate(item: dict) -> None:
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        can_combine = (
            title and summary
            and not has_japanese(title)
            and not has_japanese(summary)
        )
        if can_combine:
            result = translate_text(title + sep + summary, cache)
            if result:
                parts = result.split(sep, 1)
                t = parts[0].strip() if len(parts) > 0 else ""
                s = parts[1].strip() if len(parts) > 1 else ""
                if t and s:
                    item["title_ja"] = t
                    item["summary_ja"] = s
                    return
        t = translate_text(title, cache)
        s = translate_text(summary, cache)
        item["title_ja"] = t if t else title
        item["summary_ja"] = s if s else summary

    done = 0
    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as ex:
        futures = {ex.submit(do_translate, item): item for item in targets}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception:
                pass
            done += 1
            if done % 5 == 0 or done == len(targets):
                log(f"  翻訳進捗: {done}/{len(targets)}")
    if len(cache) > 5000:
        cache = dict(list(cache.items())[-5000:])
    save_json(TRANSLATE_CACHE_FILE, cache)


def db_init() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY,
            source TEXT,
            lang TEXT,
            region TEXT,
            official INTEGER,
            title TEXT,
            title_ja TEXT,
            summary TEXT,
            summary_ja TEXT,
            link TEXT,
            published TEXT,
            pub_dt TEXT,
            themes TEXT,
            category TEXT,
            platform TEXT,
            sentiment TEXT,
            score INTEGER,
            is_new INTEGER,
            is_entertainment INTEGER,
            is_trend INTEGER,
            fetched_at TEXT
        )
    """)
    history_days = int(CONFIG.get("history_days", 7))
    cutoff = (datetime.now(JST) - timedelta(days=history_days)).isoformat()
    conn.execute("DELETE FROM articles WHERE fetched_at < ?", (cutoff,))
    conn.commit()
    return conn


def db_save_items(conn: sqlite3.Connection, items: list[dict]) -> None:
    fetched_at = datetime.now(JST).isoformat()
    conn.executemany(
        """
        INSERT OR REPLACE INTO articles (
            id, source, lang, region, official, title, title_ja, summary, summary_ja, link,
            published, pub_dt, themes, category, platform, sentiment, score, is_new,
            is_entertainment, is_trend, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(
            it.get("id"), it.get("source"), it.get("lang"), it.get("region"), 1 if it.get("official") else 0,
            it.get("title"), it.get("title_ja"), it.get("summary"), it.get("summary_ja"), it.get("link"),
            it.get("published"), it.get("pub_dt"), json.dumps(it.get("themes", []), ensure_ascii=False),
            it.get("category"), it.get("platform"), detect_sentiment(it.get("title",""), it.get("summary","")),
            it.get("score"), 1 if it.get("is_new") else 0, 1 if it.get("is_entertainment") else 0,
            1 if it.get("is_trend") else 0, fetched_at
        ) for it in items]
    )
    conn.commit()


def collect_all() -> list[dict]:
    collected = []
    ok = 0
    err = 0
    max_workers = min(8, len(SOURCES))
    log(f"🚀 並列取得: {len(SOURCES)} ソース / {max_workers} workers 同時実行")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_source, src): src for src in SOURCES}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                items, _ = fut.result()
                collected.extend(items)
                ok += 1
            except Exception as exc:
                err += 1
                log(f"  [WARN] {src['name']}: {exc}")
    deduped = dedupe_items(collected)
    log(f"✅ 合計 {len(collected)} 件収集 → 重複除去後 {len(deduped)} 件 (成功:{ok} / エラー:{err})")
    return deduped


def base_css() -> str:
    return """
:root{--bg:#08111e;--panel:#101a2a;--panel2:#0c1523;--line:#1f2b40;--text:#eef4ff;--muted:#93a4bf;--accent:#4ea1ff;--accent2:#15314f}
*{box-sizing:border-box}html,body{margin:0;padding:0;background:linear-gradient(180deg,#08111e 0%,#0c1627 100%);color:var(--text);font-family:Inter,'Hiragino Sans','Yu Gothic UI',sans-serif}
a{color:inherit;text-decoration:none}body{padding-top:0}
.wrap,.main{width:min(1280px,94vw);margin:0 auto}
header{position:sticky;top:0;z-index:50;background:rgba(9,17,29,.97);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;padding:18px 0 12px}
.title h1{margin:0;font-size:28px;line-height:1.2}.sub{margin-top:6px;color:var(--muted);font-size:13px}
.nav{display:flex;gap:8px;flex-wrap:wrap;padding:8px 0 12px}.nav a{padding:10px 14px;border-radius:10px;background:var(--panel2);border:1px solid var(--line);font-size:14px;font-weight:700}.nav a.active{background:var(--accent2);border-color:var(--accent);color:#eef6ff}.filters-panel{margin-bottom:18px}
.page-lang-btn,.card-lang-btn,.read-btn,.flt-btn,.open-btn{border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:10px;padding:8px 12px;font-size:12px;font-weight:700;cursor:pointer}
.page-lang-btn.active,.card-lang-btn.active,.flt-btn.active{background:var(--accent2);border-color:var(--accent)}
.right-controls{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:18px}
.main > .panel:first-child{position:static}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:0}
.filter-label{font-size:12px;color:var(--muted);font-weight:700}
.search{min-width:240px;background:#0b1320;border:1px solid var(--line);color:var(--text);padding:9px 12px;border-radius:10px}
.main-grid,.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.grid.list{grid-template-columns:1fr}
.card,.mini-item,.category-panel{min-width:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:14px;display:flex;flex-direction:column;gap:10px;overflow:hidden}
.card.score-5{border-left:4px solid #ffd700}
.card.score-4{border-left:4px solid #4ea1ff}
.card.is-read{opacity:.62}
.card-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;min-width:0;flex-wrap:wrap}
.card-meta{display:flex;gap:6px;flex-wrap:wrap;font-size:11px;color:var(--muted);min-width:0;overflow-wrap:anywhere;word-break:break-word}
.lang-badge,.tag{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--line);padding:3px 8px;border-radius:999px;font-size:11px;font-weight:700}
.lang-badge.ja{background:#14375d;border-color:#4ea1ff}
.lang-badge.en{background:#24301f;border-color:#15c39a}
.tag-new{background:#17391e;border-color:#15c39a;color:#93ffd8}.tag-hot{background:#14375d;border-color:#4ea1ff}.tag-caution{background:#3f1b1d;border-color:#ff7b7b}.tag-neutral{background:#1d2635;border-color:#4b5b78}
.tags{display:flex;gap:6px;flex-wrap:wrap;min-width:0;overflow-wrap:anywhere;word-break:break-word}.summary{font-size:13px;line-height:1.7;color:#d6e0f0;overflow-wrap:anywhere;word-break:break-word}.hidden{display:none!important}
.card h3{margin:0;font-size:17px;line-height:1.45;overflow-wrap:anywhere;word-break:break-word}.open-btn{margin-top:auto;display:inline-flex;justify-content:center;background:var(--accent2);border-color:var(--accent);max-width:100%;white-space:normal;text-align:center}
.section-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:4px 0 12px;min-width:0;flex-wrap:wrap}.section-head h2{margin:0;font-size:20px;overflow-wrap:anywhere;word-break:break-word}.count-badge{color:var(--muted);font-size:12px;overflow-wrap:anywhere;word-break:break-word}
.category-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:0}
.category-grid.single{grid-template-columns:repeat(3,minmax(0,1fr))}
.category-panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:14px}.mini-list{display:grid;gap:8px}.mini-item{padding:10px;border-radius:12px;background:#0c1422;border:1px solid #1d2a40}.mini-item a{font-size:13px;line-height:1.5}.mini-meta{font-size:11px;color:var(--muted);margin-top:4px}
footer{width:min(1280px,94vw);margin:20px auto 36px;color:var(--muted);font-size:12px}
@media(max-width:980px){.category-grid.single{grid-template-columns:1fr}}
@media(max-width:760px){body{padding-top:0}.top{flex-direction:column}.title h1{font-size:24px}.right-controls{justify-content:flex-start}.search{min-width:0;width:100%}.nav{padding-bottom:8px}.nav a{font-size:13px;padding:9px 12px}.grid,.main-grid,.category-grid{grid-template-columns:1fr}.card,.category-panel,.mini-item{width:100%;max-width:100%}.card-top,.card-meta,.tags,.section-head{width:100%}.card h3{font-size:16px;line-height:1.4}.summary{font-size:13px;line-height:1.65}.open-btn{width:100%}}
"""


def header_html(active: str, stamp: str) -> str:
    nav = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{label}</a>'
        for href, label in NAV_ITEMS
    )
    return f"""<!doctype html>
<html lang='ja'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{html.escape(APP_NAME)}</title>
<style>{base_css()}</style>
</head>
<body>
<header>
<div class='wrap'>
  <div class='top'>
    <div class='title'>
      <h1>🎭 {html.escape(APP_NAME)} <span style='font-weight:400;color:var(--muted);font-size:16px'>｜ {html.escape(CONFIG.get('subtitle', ''))}</span></h1>
      <div class='sub'>更新日時：{stamp}</div>
    </div>
    <div class='right-controls'>
      <button class='page-lang-btn active' id='btnPageEN' onclick="setPageLang('en')">🌐 English</button>
      <button class='page-lang-btn' id='btnPageJA' onclick="setPageLang('ja')">🇯🇵 日本語表示</button>
    </div>
  </div>
  <nav class='nav'>{nav}</nav>
</div>
</header>"""


def js_common(extra: str = "") -> str:
    return f"""
<script>
function setPageLang(lang) {{
  localStorage.setItem('page_lang_pref', lang);
  document.querySelectorAll('.page-lang-btn').forEach(function(btn){{ btn.classList.remove('active'); }});
  var enBtn = document.getElementById('btnPageEN');
  var jaBtn = document.getElementById('btnPageJA');
  document.querySelectorAll('.item-card').forEach(function(card){{
    var enPane = card.querySelector('.lang-en');
    var jaPane = card.querySelector('.lang-ja');
    if(!enPane || !jaPane) return;
    if(lang === 'ja') {{
      enPane.classList.add('hidden');
      jaPane.classList.remove('hidden');
      if(jaBtn) jaBtn.classList.add('active');
      if(enBtn) enBtn.classList.remove('active');
    }} else {{
      enPane.classList.remove('hidden');
      jaPane.classList.add('hidden');
      if(enBtn) enBtn.classList.add('active');
      if(jaBtn) jaBtn.classList.remove('active');
    }}
  }});
}}
function switchCardLang(btn, lang) {{
  var card = btn.closest('.item-card');
  if (!card) return;
  card.querySelectorAll('.card-lang-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  btn.classList.add('active');
  var enPane = card.querySelector('.lang-en');
  var jaPane = card.querySelector('.lang-ja');
  if (lang === 'ja') {{ if(enPane) enPane.classList.add('hidden'); if(jaPane) jaPane.classList.remove('hidden'); }}
  else {{ if(enPane) enPane.classList.remove('hidden'); if(jaPane) jaPane.classList.add('hidden'); }}
}}
function markRead(btn, id) {{
  localStorage.setItem('watch_hub_plus_read_' + id, '1');
  var card = btn.closest('.item-card');
  if (card) card.classList.add('is-read');
}}
(function(){{
  document.querySelectorAll('.item-card').forEach(function(card){{
    var id = card.dataset.cardid;
    if(localStorage.getItem('watch_hub_plus_read_' + id) === '1') card.classList.add('is-read');
  }});
  var current = localStorage.getItem('page_lang_pref') || 'en';
  setPageLang(current);
}})();
{extra}
</script>
</body></html>"""


def footer_html(stamp: str, extra_js: str = "") -> str:
    return f"<footer>{html.escape(APP_NAME)} — Generated at {stamp}</footer>{js_common(extra_js)}"


def theme_tag(name: str) -> str:
    meta = THEMES.get(name)
    if not meta:
        return ""
    return f"<span class='tag' style='background:{meta['color']}22;border-color:{meta['color']};color:{meta['color']}'>{meta['icon']} {html.escape(name)}</span>"


def card_html(item: dict) -> str:
    card_id = html.escape(item.get("id", ""))
    title_orig = html.escape(item.get("title", ""))
    title_ja = html.escape(item.get("title_ja", "") or item.get("title", ""))
    summary_orig = html.escape(item.get("summary", ""))
    summary_ja = html.escape(item.get("summary_ja", "") or item.get("summary", ""))
    source = html.escape(item.get("source", ""))
    published = html.escape(item.get("published", ""))
    link = html.escape(item.get("link", "#"))
    score = item.get("score", 1)
    lang = item.get("lang", "en")
    sentiment = detect_sentiment(item.get("title", ""), item.get("summary", ""))
    theme_tags = "".join(theme_tag(t) for t in item.get("themes", []))
    new_tag = "<span class='tag tag-new'>🆕 NEW</span>" if item.get("is_new") else ""
    sent_label = {"hot": ("🔥 Hot", "tag-hot"), "caution": ("⚠ 注意", "tag-caution"), "neutral": ("➖ 通常", "tag-neutral")}[sentiment]
    if lang == "ja":
        panes = f"<div class='lang-pane lang-ja'><h3>{title_orig}</h3><div class='summary'>{summary_orig}</div></div>"
        lang_toggle = f"<div class='card-lang-toggle'><button class='read-btn' onclick=\"markRead(this,'{card_id}')\">既読</button></div>"
    else:
        panes = f"<div class='lang-pane lang-en'><h3>{title_orig}</h3><div class='summary'>{summary_orig}</div></div><div class='lang-pane lang-ja hidden'><h3>{title_ja}</h3><div class='summary'>{summary_ja}</div></div>"
        lang_toggle = f"<div class='card-lang-toggle'><button class='card-lang-btn active' onclick=\"switchCardLang(this,'en')\">EN</button><button class='card-lang-btn' onclick=\"switchCardLang(this,'ja')\">JA</button><button class='read-btn' onclick=\"markRead(this,'{card_id}')\">既読</button></div>"
    platform = item.get("platform", "other")
    platform_label = {"netflix": "Netflix", "prime": "Prime", "disney": "Disney+"}.get(platform, "")
    platform_tag = f"<span class='tag'>{platform_label}</span>" if platform_label else ""
    score_class = "score-5" if score >= 5 else ("score-4" if score >= 4 else "")
    return f"""
<article class='card item-card {score_class}' data-cardid='{card_id}' data-region='{html.escape(item.get('region', 'Global'))}' data-score='{score}' data-platform='{html.escape(platform)}'>
  <div class='card-top'>
    <div class='card-meta'>{new_tag}<span class='lang-badge {'ja' if item.get('region')=='JP' else 'en'}'>{'🇯🇵 国内' if item.get('region')=='JP' else '🌐 海外'}</span><span>{source}</span><span>{published}</span></div>
    {lang_toggle}
  </div>
  {panes}
  <div class='tags'>{theme_tags}{platform_tag}<span class='tag {sent_label[1]}'>{sent_label[0]}</span><span class='tag tag-neutral'>★{score}</span></div>
  <a class='open-btn' href='{link}' target='_blank' rel='noopener noreferrer'>記事を開く →</a>
</article>"""


def filter_bar(default_region: str = "JP", include_platform: bool = False) -> tuple[str, str]:
    active_all = "active" if default_region == "all" else ""
    active_jp = "active" if default_region == "JP" else ""
    active_en = "active" if default_region == "Global" else ""
    platform_html = """
    <span class='filter-label'>配信元:</span>
    <button class='flt-btn flt-platform active' data-platform='all'>全部</button>
    <button class='flt-btn flt-platform' data-platform='netflix'>Netflix</button>
    <button class='flt-btn flt-platform' data-platform='prime'>Prime</button>
    <button class='flt-btn flt-platform' data-platform='disney'>Disney+</button>
    """ if include_platform else ""
    html_text = f"""
<div class='panel'>
  <div class='filters'>
    <span class='filter-label'>地域:</span>
    <button class='flt-btn flt-region {active_all}' data-region='all'>🌍 全部</button>
    <button class='flt-btn flt-region {active_jp}' data-region='JP'>🇯🇵 国内</button>
    <button class='flt-btn flt-region {active_en}' data-region='Global'>🌐 海外</button>
    <span class='filter-label'>スコア:</span>
    <button class='flt-btn flt-score active' data-score='all'>全部</button>
    <button class='flt-btn flt-score' data-score='4'>★4以上</button>
    <button class='flt-btn flt-score' data-score='3'>★3以上</button>
    {platform_html}
    <input id='searchBox' class='search' placeholder='タイトル・本文を検索...'>
  </div>
</div>
"""
    extra = """
(function(){
  var fRegion = '%s';
  var fScore = 'all';
  var fPlatform = 'all';
  var search = '';
  function applyFilters(){
    document.querySelectorAll('.item-card').forEach(function(card){
      var show = true;
      if(fRegion !== 'all' && (card.dataset.region || '') !== fRegion) show = false;
      if(fScore !== 'all' && parseInt(card.dataset.score || '1', 10) < parseInt(fScore, 10)) show = false;
      if(fPlatform !== 'all' && (card.dataset.platform || '') !== fPlatform) show = false;
      if(search && !(card.textContent || '').toLowerCase().includes(search.toLowerCase())) show = false;
      card.style.display = show ? '' : 'none';
    });
    document.querySelectorAll('.mini-item[data-region]').forEach(function(item){
      var show = true;
      if(fRegion !== 'all' && (item.dataset.region || '') !== fRegion) show = false;
      if(fPlatform !== 'all' && (item.dataset.platform || '') !== fPlatform) show = false;
      item.style.display = show ? '' : 'none';
    });
  }
  document.querySelectorAll('.flt-region').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('.flt-region').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active');
      fRegion = btn.dataset.region;
      applyFilters();
    });
  });
  document.querySelectorAll('.flt-score').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('.flt-score').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active');
      fScore = btn.dataset.score;
      applyFilters();
    });
  });
  document.querySelectorAll('.flt-platform').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('.flt-platform').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active');
      fPlatform = btn.dataset.platform;
      applyFilters();
    });
  });
  var sb = document.getElementById('searchBox');
  if(sb){ sb.addEventListener('input', function(){ search = this.value; applyFilters(); }); }
  applyFilters();
})();
""" % default_region
    return html_text, extra


def sort_items(items: Iterable[dict]) -> list[dict]:
    return sorted(items, key=lambda x: (x.get("region") != "JP", -x.get("score", 1), x.get("published", "")))


def sort_pickup(items: Iterable[dict]) -> list[dict]:
    """今日の注目用: NEW優先 → スコア降順 → 新着日時降順"""
    return sorted(
        items,
        key=lambda x: (
            x.get("region") != "JP",
            0 if x.get("is_new") else 1,
            -x.get("score", 1),
            x.get("pub_dt", ""),
        ),
        reverse=False
    )


def mini_item(it: dict) -> str:
    title = html.escape(it['title_ja'] if it.get('lang') != 'ja' and it.get('title_ja') else it['title'])
    region = '🇯🇵 国内' if it.get('region') == 'JP' else '🌐 海外'
    platform = it.get("platform", "other")
    return f"<div class='mini-item' data-region='{html.escape(it.get('region','Global'))}' data-platform='{html.escape(platform)}'><a href='{html.escape(it['link'])}' target='_blank' rel='noopener noreferrer'>{title}</a><div class='mini-meta'>{region} / ★{it.get('score',1)} / {html.escape(it.get('published',''))}</div></div>"


def ensure_minimum(items: list[dict], title: str) -> list[dict]:
    if items:
        return items
    now = datetime.now(JST)
    return [{
        "id": hashlib.md5(title.encode()).hexdigest(),
        "source": "Fallback",
        "lang": "ja",
        "region": "JP",
        "official": False,
        "title": f"{title} の取得結果がまだありません",
        "summary": "この版は起動確認を優先し、記事が0件でもHTMLを生成します。",
        "title_ja": f"{title} の取得結果がまだありません",
        "summary_ja": "この版は起動確認を優先し、記事が0件でもHTMLを生成します。",
        "link": "#",
        "published": now.strftime("%Y-%m-%d %H:%M"),
        "pub_dt": now.isoformat(),
        "themes": [title],
        "category": "映画",
        "platform": "other",
        "sentiment": "neutral",
        "is_new": True,
        "is_entertainment": False,
        "is_trend": False,
        "score": 1,
    }]


def render_main(items: list[dict]) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    filter_html, filter_js = filter_bar(default_region='JP')
    sections = {
        "映画": sort_pickup([x for x in items if x.get("category") == "映画"])[:3],
        "配信": sort_pickup([x for x in items if x.get("category") == "配信"])[:3],
        "アニメ": sort_pickup([x for x in items if x.get("category") == "アニメ"])[:3],
        "ゲーム": sort_pickup([x for x in items if x.get("category") == "ゲーム"])[:3],
        "芸能": sort_pickup([x for x in items if x.get("is_entertainment")])[:3],
        "トレンド": sort_pickup([x for x in items if x.get("is_trend")])[:3],
    }
    blocks = []
    for cat in MAIN_SECTIONS:
        block_items = ensure_minimum(sections.get(cat, []), cat)
        mini = "".join(mini_item(it) for it in block_items)
        blocks.append(f"<section class='category-panel'><div class='section-head'><h2>{CATEGORY_TO_ICON[cat]} {cat}</h2><a href='{CATEGORY_TO_FILE[cat]}' class='count-badge'>もっと見る →</a></div><div class='mini-list'>{mini}</div></section>")
    latest = "".join(card_html(it) for it in sort_items(items)[:24])
    body = f"""
<div class='main'>
{filter_html}
<section class='panel'>
  <div class='section-head'><h2>🆕 NEW優先 注目3件 × 6カテゴリ</h2><span class='count-badge'>初期表示は国内</span></div>
  <div class='category-grid'>{''.join(blocks)}</div>
</section>
<section>
  <div class='section-head'><h2>📰 最新記事</h2><span class='count-badge'>全カテゴリ混在</span></div>
  <div class='grid'>{latest}</div>
</section>
</div>"""
    return header_html("index.html", stamp) + body + footer_html(stamp, filter_js)


def render_category_page(items: list[dict], category: str, filename: str) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    filter_html, filter_js = filter_bar(default_region='JP')
    cat_items = sort_items([x for x in items if x.get("category") == category])
    focus_items = ensure_minimum(cat_items[:3], category)
    cards = "".join(card_html(it) for it in cat_items) or "".join(card_html(it) for it in focus_items)
    body = f"""
<div class='main'>
{filter_html}
<section class='panel'>
  <div class='section-head'><h2>{CATEGORY_TO_ICON[category]} {category} 今日の注目 3件</h2><span class='count-badge'>初期表示は国内</span></div>
  <div class='category-grid single'>{''.join(mini_item(it) for it in focus_items)}</div>
</section>
<section>
  <div class='section-head'><h2>📰 最新記事</h2><span class='count-badge'>{len(cat_items)}件</span></div>
  <div class='grid'>{cards}</div>
</section>
</div>"""
    return header_html(filename, stamp) + body + footer_html(stamp, filter_js)


def render_streaming(items: list[dict]) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    filter_html, filter_js = filter_bar(default_region='JP', include_platform=True)
    stream_items = sort_items([x for x in items if x.get("category") == "配信"])
    sections = {
        "netflix": ensure_minimum([x for x in stream_items if x.get("platform") == "netflix"][:3], "Netflix"),
        "prime": ensure_minimum([x for x in stream_items if x.get("platform") == "prime"][:3], "Prime"),
        "disney": ensure_minimum([x for x in stream_items if x.get("platform") == "disney"][:3], "Disney+"),
    }
    blocks = []
    for key in ["netflix", "prime", "disney"]:
        mini = "".join(mini_item(it) for it in sections[key])
        blocks.append(f"<section class='category-panel'><div class='section-head'><h2>📺 {PLATFORM_LABELS[key]} 注目3件</h2><span class='count-badge'>配信タブ内フィルタ対応</span></div><div class='mini-list'>{mini}</div></section>")
    cards = "".join(card_html(it) for it in stream_items) or "".join(card_html(it) for it in ensure_minimum([], "配信"))
    body = f"""
<div class='main'>
{filter_html}
<section class='panel'>
  <div class='section-head'><h2>📺 配信プラットフォーム別 注目3件</h2><span class='count-badge'>Netflix / Prime / Disney+</span></div>
  <div class='category-grid'>{''.join(blocks)}</div>
</section>
<section>
  <div class='section-head'><h2>📰 最新記事</h2><span class='count-badge'>{len(stream_items)}件</span></div>
  <div class='grid'>{cards}</div>
</section>
</div>"""
    return header_html("streaming.html", stamp) + body + footer_html(stamp, filter_js)


def render_entertainment(items: list[dict]) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    filter_html, filter_js = filter_bar(default_region='JP')
    ent_items = sort_items([x for x in items if x.get("is_entertainment")])
    focus_items = ensure_minimum(ent_items[:3], "芸能")
    cards = "".join(card_html(it) for it in ent_items) or "".join(card_html(it) for it in focus_items)
    body = f"""
<div class='main'>
{filter_html}
<section class='panel'>
  <div class='section-head'><h2>⭐ 芸能 今日の注目 3件</h2><span class='count-badge'>人物・出演・コメント系</span></div>
  <div class='category-grid single'>{''.join(mini_item(it) for it in focus_items)}</div>
</section>
<section>
  <div class='section-head'><h2>📰 最新記事</h2><span class='count-badge'>{len(ent_items)}件</span></div>
  <div class='grid'>{cards}</div>
</section>
</div>"""
    return header_html("entertainment.html", stamp) + body + footer_html(stamp, filter_js)


def render_trend(items: list[dict]) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    filter_html, filter_js = filter_bar(default_region='JP')
    trend_items = sort_items([x for x in items if x.get("is_trend")])
    focus_items = ensure_minimum(trend_items[:3], "トレンド")
    cards = "".join(card_html(it) for it in trend_items) or "".join(card_html(it) for it in focus_items)
    body = f"""
<div class='main'>
{filter_html}
<section class='panel'>
  <div class='section-head'><h2>📈 トレンド 今日の注目 3件</h2><span class='count-badge'>ランキング・急上昇・話題</span></div>
  <div class='category-grid single'>{''.join(mini_item(it) for it in focus_items)}</div>
</section>
<section>
  <div class='section-head'><h2>📰 最新記事</h2><span class='count-badge'>{len(trend_items)}件</span></div>
  <div class='grid'>{cards}</div>
</section>
</div>"""
    return header_html("trend.html", stamp) + body + footer_html(stamp, filter_js)


def extract_title_words(title: str) -> list[str]:
    """タイトルから作品名候補を抽出（カギ括弧・クォート内 or 5文字以上の塊）"""
    candidates = []
    # 「」『』【】内を優先抽出
    for m in re.finditer(r'[「『【]([^」』】]{2,20})[」』】]', title):
        candidates.append(m.group(1).strip())
    # ダブルクォート内
    for m in re.finditer(r'"([^"]{2,30})"', title):
        candidates.append(m.group(1).strip())
    # 上記で何も取れなかった場合：記号区切りの前半部分（サイト名除去）
    if not candidates:
        part = re.split(r'[｜|/・:：\-–—]', title)[0].strip()
        if len(part) >= 4:
            candidates.append(part)
    return [c for c in candidates if c]


def render_analysis(items: list[dict]) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    filter_html, filter_js = filter_bar(default_region='JP')
    now = datetime.now(JST)
    cutoff_7d = now - timedelta(days=7)

    # ── カテゴリ別件数（芸能・トレンドはフラグで正しくカウント）──
    category_counts: dict[str, int] = {}
    for cat in ["映画", "配信", "アニメ", "ゲーム"]:
        category_counts[cat] = sum(1 for x in items if x.get("category") == cat)
    category_counts["芸能"] = sum(1 for x in items if x.get("is_entertainment"))
    category_counts["トレンド"] = sum(1 for x in items if x.get("is_trend"))

    # ── 地域・プラットフォーム別 ──
    region_counts = Counter(x.get("region", "Global") for x in items)
    platform_counts = Counter(
        x.get("platform", "other") for x in items if x.get("category") == "配信"
    )

    # ── 直近7日スコア上位カテゴリ ──
    trend_counts: Counter = Counter()
    for x in items:
        try:
            if x.get("pub_dt") and datetime.fromisoformat(x["pub_dt"]).astimezone(JST) >= cutoff_7d:
                trend_counts[x.get("category", "その他")] += 1
                if x.get("is_entertainment"):
                    trend_counts["芸能"] += 1
                if x.get("is_trend"):
                    trend_counts["トレンド"] += 1
        except Exception:
            pass

    # ── 作品名頻出ランキング（メイン機能）──
    title_counter: Counter = Counter()
    for x in items:
        display_title = x.get("title_ja") or x.get("title") or ""
        for word in extract_title_words(display_title):
            title_counter[word] += 1
    # 2件以上出現したものだけランキング化
    hot_titles = [(t, c) for t, c in title_counter.most_common(20) if c >= 2]

    # ── HTML組み立て ──
    rows1 = "".join(
        f"<div class='mini-item'>"
        f"<div style='font-weight:700'>{CATEGORY_TO_ICON[k]} {html.escape(k)}</div>"
        f"<div class='mini-meta'>{category_counts.get(k, 0)}件</div>"
        f"</div>"
        for k in MAIN_SECTIONS
    )
    rows2 = "".join(
        f"<div class='mini-item'><div style='font-weight:700'>{name}</div>"
        f"<div class='mini-meta'>{count}件</div></div>"
        for name, count in [
            ("国内", region_counts.get("JP", 0)),
            ("海外", region_counts.get("Global", 0)),
            ("Netflix", platform_counts.get("netflix", 0)),
            ("Prime", platform_counts.get("prime", 0)),
            ("Disney+", platform_counts.get("disney", 0)),
        ]
    )
    rows3 = "".join(
        f"<div class='mini-item'><div style='font-weight:700'>"
        f"{CATEGORY_TO_ICON.get(k, '•')} {html.escape(k)}</div>"
        f"<div class='mini-meta'>直近7日 {v}件</div></div>"
        for k, v in (trend_counts.most_common(6) or [("−", 0)])
    )

    if hot_titles:
        medal = ["🥇", "🥈", "🥉"]
        rows_hot = "".join(
            f"<div class='mini-item' style='display:flex;justify-content:space-between;align-items:center'>"
            f"<div style='font-weight:700'>{medal[i] if i < 3 else '　'} {html.escape(t)}</div>"
            f"<div class='mini-meta' style='white-space:nowrap'>{c}件</div>"
            f"</div>"
            for i, (t, c) in enumerate(hot_titles[:10])
        )
        hot_section = f"""
<section class='panel' style='margin-bottom:18px'>
  <div class='section-head'>
    <h2>🏆 話題の作品・人物ランキング</h2>
    <span class='count-badge'>複数記事に登場したワード TOP10</span>
  </div>
  <div class='mini-list'>{rows_hot}</div>
</section>"""
    else:
        hot_section = ""

    blocks_html = "".join([
        f"<section class='category-panel'><div class='section-head'><h2>カテゴリ別件数</h2></div><div class='mini-list'>{rows1}</div></section>",
        f"<section class='category-panel'><div class='section-head'><h2>地域 / 配信元</h2></div><div class='mini-list'>{rows2}</div></section>",
        f"<section class='category-panel'><div class='section-head'><h2>直近7日 活発なカテゴリ</h2></div><div class='mini-list'>{rows3}</div></section>",
    ])

    # スコア上位5件をハイライト表示
    top_cards = "".join(card_html(it) for it in sort_items(items)[:5])

    body = f"""
<div class='main'>
{filter_html}
{hot_section}
<section class='panel'>
  <div class='section-head'><h2>📊 分析サマリー</h2><span class='count-badge'>{len(items)}件 / 更新:{stamp}</span></div>
  <div class='category-grid'>{blocks_html}</div>
</section>
<section>
  <div class='section-head'><h2>⭐ スコア上位記事</h2><span class='count-badge'>★4以上が金・青ボーダー</span></div>
  <div class='grid'>{top_cards}</div>
</section>
</div>"""
    return header_html("analysis.html", stamp) + body + footer_html(stamp, filter_js)


def fallback_items() -> list[dict]:
    now = datetime.now(JST)
    seeds = [
        ("映画", "国内向け映画トピックの取得結果がまだありません", "この版は起動確認を優先し、記事が0件でもHTMLを生成します。", "JP", "other", False, False),
        ("配信", "配信トピックの取得結果がまだありません", "Netflix / Prime / Disney+ を配信タブでまとめて扱います。", "JP", "netflix", False, True),
        ("アニメ", "アニメ関連トピックの取得結果がまだありません", "地域フィルタは国内を初期選択にしています。", "JP", "other", False, False),
        ("ゲーム", "ゲーム関連トピックの取得結果がまだありません", "ソース一覧を後から差し替えても他ファイルはそのまま流用できます。", "JP", "other", False, True),
        ("映画", "芸能向けプレースホルダー", "人物・出演・コメント系の記事が入る予定です。", "JP", "other", True, False),
        ("映画", "トレンド向けプレースホルダー", "ランキング・急上昇・話題キーワードをここに集約します。", "JP", "other", False, True),
    ]
    items = []
    for i, (cat, title, summary, region, platform, is_ent, is_trend) in enumerate(seeds, start=1):
        themes = [cat]
        if is_ent: themes.append("芸能")
        if is_trend: themes.append("トレンド")
        items.append({
            "id": hashlib.md5(f"fallback|{cat}|{i}".encode("utf-8")).hexdigest(),
            "source": "Fallback",
            "lang": "ja",
            "region": region,
            "official": False,
            "title": title,
            "summary": summary,
            "title_ja": title,
            "summary_ja": summary,
            "link": "#",
            "published": now.strftime("%Y-%m-%d %H:%M"),
            "pub_dt": now.isoformat(),
            "themes": themes,
            "category": cat,
            "platform": platform,
            "sentiment": "neutral",
            "is_new": True,
            "is_entertainment": is_ent,
            "is_trend": is_trend,
            "score": 1,
        })
    return items


def main() -> None:
    start = time.time()
    log("=" * 46)
    log(f"  {APP_NAME}")
    log("=" * 46)
    log("[1/3] フィード収集中...")
    items = collect_all()
    if not items:
        log("記事が取得できなかったため、プレースホルダーでHTMLを生成します。")
        items = fallback_items()
    cache = load_json(TRANSLATE_CACHE_FILE, {})
    translate_items(items, cache)
    conn = db_init()
    db_save_items(conn, items)
    conn.close()

    log("\n[2/3] HTML生成中...")
    pages = {
        "index.html": render_main(items),
        "movie.html": render_category_page(items, "映画", "movie.html"),
        "streaming.html": render_streaming(items),
        "anime.html": render_category_page(items, "アニメ", "anime.html"),
        "game.html": render_category_page(items, "ゲーム", "game.html"),
        "entertainment.html": render_entertainment(items),
        "trend.html": render_trend(items),
        "analysis.html": render_analysis(items),
    }
    for filename, content in pages.items():
        (OUTPUT_DIR / filename).write_text(content, encoding="utf-8")
        log(f"  ✓ {filename}")

    log("\n[3/3] 完了")
    log(f"出力先: {OUTPUT_DIR / 'index.html'}")
    log(f"⏱ 処理時間: {time.time() - start:.1f}秒")

if __name__ == "__main__":
    main()
