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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 HealthMedicalPlus"
TIMEOUT = 15
NEW_HOURS = int(CONFIG.get("new_badge_hours", 6))
MAX_ITEMS_PER_SOURCE = int(CONFIG.get("max_items_per_source", 20))
TRANSLATE_TOP_N = int(CONFIG.get("translate_top_n", 0))
TRANSLATE_WORKERS = int(CONFIG.get("translate_workers", 4))
TRANSLATE_RETRIES = int(CONFIG.get("translate_retries", 2))
MAX_ARTICLE_AGE_DAYS = int(CONFIG.get("max_article_age_days", 120))

NAV_ITEMS = [
    ("index.html", "🏠 メイン"),
    ("health.html", "🩺 健康"),
    ("medical.html", "🏥 医療"),
    ("research.html", "🧬 研究"),
    ("prevention_longevity.html", "🧓 予防 / 長寿"),
    ("public_info.html", "🏛 公的情報"),
    
    ("analysis.html", "📊 分析"),
]

MAIN_SECTIONS = ["健康", "医療", "研究", "予防 / 長寿", "公的情報"]
CATEGORY_TO_FILE = {
    "健康": "health.html",
    "医療": "medical.html",
    "研究": "research.html",
    "予防 / 長寿": "prevention_longevity.html",
    "公的情報": "public_info.html",
    
}
CATEGORY_TO_ICON = {
    "健康": "🩺",
    "医療": "🏥",
    "研究": "🧬",
    "予防 / 長寿": "🧓",
    "公的情報": "🏛",
    
}
CATEGORY_KEYWORDS = {
    "健康": ["health", "healthy", "nutrition", "fitness", "exercise", "sleep", "wellness", "diet", "mental health", "健康", "睡眠", "運動", "食事", "栄養", "体調", "生活習慣", "ストレス", "メンタルヘルス", "こころ"],
    "医療": ["medical", "medicine", "hospital", "treatment", "disease", "patient", "clinical", "drug", "cancer", "vaccine", "infection", "医療", "治療", "疾患", "病院", "新薬", "診断", "臨床", "感染症", "がん", "ワクチン"],
    "研究": ["research", "study", "scientists", "discovery", "paper", "journal", "analysis", "survey", "研究", "研究成果", "論文", "発見", "解明", "調査", "解析", "報告書", "実証", "検証"],
    "予防 / 長寿": ["prevention", "healthy aging", "aging", "longevity", "dementia", "frailty", "preventive", "public health", "予防", "長寿", "老化", "健康寿命", "認知", "フレイル", "介護予防", "生活習慣病", "公衆衛生"],
    "公的情報": ["mhlw", "ministry", "guideline", "public health", "notice", "advisory", "press release", "厚生労働省", "報道発表", "通知", "指針", "公衆衛生", "行政", "審議会", "検討会"]
}

OFFICIAL_SECONDARY_HINTS = {
    "健康": ["健康", "睡眠", "運動", "食事", "栄養", "生活習慣", "メンタル", "こころ", "ストレス", "受動喫煙"],
    "医療": ["医療", "治療", "疾患", "診療", "病院", "患者", "新薬", "感染症", "がん", "ワクチン", "インフル", "新型コロナ"],
    "研究": ["研究", "調査", "解析", "報告書", "検証", "実証", "データ", "統計"],
    "予防 / 長寿": ["予防", "健康寿命", "認知", "認知症", "老化", "長寿", "フレイル", "介護予防", "生活習慣病", "公衆衛生"]
}
TOPIC_KEYWORDS = {
    "睡眠": ["sleep", "insomnia", "sleep medicine", "睡眠", "休養"],
    "運動": ["fitness", "exercise", "physical activity", "training", "運動", "筋力", "歩数"],
    "食事": ["nutrition", "diet", "eating", "food", "食事", "栄養", "肥満", "減量"],
    "疾患・治療": ["disease", "treatment", "therapy", "drug", "patient", "clinical", "治療", "疾患", "患者", "新薬", "診断"],
    "研究成果": ["research", "study", "scientists", "trial", "journal", "研究", "論文", "研究成果", "発見"],
    "認知機能": ["brain", "cognitive", "alzheimer", "dementia", "認知", "脳", "アルツハイマー"],
    "感染症・公衆衛生": ["infection", "virus", "vaccine", "influenza", "public health", "感染", "ウイルス", "ワクチン", "公衆衛生"],
    "医療制度・行政": ["guideline", "notice", "ministry", "policy", "報道発表", "通知", "制度", "行政", "審議会"]
}
POSITIVE_WORDS = ["breakthrough", "approval", "承認", "新薬", "治験成功", "有効", "改善", "回復", "予防効果", "best"]
CAUTION_WORDS = ["delay", "中止", "延期", "cancel", "controversy", "lawsuit", "problem", "不具合", "障害", "炎上"]
TREND_KEYWORDS = ["breaking", "速報", "announcement", "発表", "warning", "alert", "緊急", "注意", "outbreak", "感染拡大", "recall", "回収", "approval", "承認", "guideline", "指針"]


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


def is_recent_article(pub_dt_iso: str) -> bool:
    if not pub_dt_iso:
        return True
    try:
        pub_dt = datetime.fromisoformat(pub_dt_iso).astimezone(JST)
        return pub_dt >= datetime.now(JST) - timedelta(days=MAX_ARTICLE_AGE_DAYS)
    except Exception:
        return True


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


def detect_category(title: str, summary: str, source_name: str, source_category: str = "") -> str:
    combined = text_lower(f"{title} {summary} {source_name}")
    best = None
    best_score = 0
    for category, words in CATEGORY_KEYWORDS.items():
        score = sum(1 for k in words if k in combined)
        if score > best_score:
            best_score = score
            best = category
    if best:
        return best
    return source_category if source_category in CATEGORY_KEYWORDS else "研究"


def detect_topic_tags(title: str, summary: str) -> list[str]:
    combined = text_lower(f"{title} {summary}")
    tags = [name for name, words in TOPIC_KEYWORDS.items() if any(w in combined for w in words)]
    return tags or ["研究成果"]


def official_secondary_categories(item: dict) -> list[str]:
    if not (item.get("official") and item.get("region") == "JP"):
        return []
    combined = text_lower(f"{item.get('title','')} {item.get('summary','')} {item.get('source','')}")
    matched = []
    for category, words in OFFICIAL_SECONDARY_HINTS.items():
        if any(text_lower(w) in combined for w in words):
            matched.append(category)
    return matched


def item_matches_category(item: dict, category: str) -> bool:
    if item.get("category") == category:
        return True
    return category in official_secondary_categories(item)


def calc_score(item: dict) -> int:
    combined = text_lower(f"{item.get('title','')} {item.get('summary','')}")
    base = 3 if item.get("official") else (2 if item.get("is_new") else 1)
    bonus = 0
    if item.get("is_trend"):
        bonus = max(bonus, 2)
    for points, keywords in {
        3: ["breakthrough", "approval", "承認", "緊急", "outbreak", "感染拡大", "recall", "回収"],
        2: ["announcement", "発表", "guideline", "指針", "warning", "注意", "新薬", "治験"],
        1: ["研究", "study", "report", "報告", "feature", "特集"],
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
        if not is_recent_article(pub_dt_iso):
            continue
        lang = source.get("lang", "en")
        category = detect_category(title, summary, name, source.get("category", ""))
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
            "is_new": is_new(pub_dt_iso),
        }
        item["is_trend"] = detect_trend(title, summary, 0)
        item["score"] = calc_score(item)
        if not item["is_trend"]:
            item["is_trend"] = detect_trend(title, summary, item["score"])
            item["score"] = calc_score(item)
        themes = [category] + detect_topic_tags(title, summary)
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
    non_ja_items = [x for x in items if x.get("lang") != "ja"]
    for item in non_ja_items:
        item["title_ja"] = (item.get("title_ja") or item.get("title") or "").strip()
        item["summary_ja"] = (item.get("summary_ja") or item.get("summary") or "").strip()

    if not non_ja_items:
        log("翻訳対象なし (日本語記事のみ)")
        return

    title_targets = list(non_ja_items)
    target_limit = int(CONFIG.get("translate_top_n", 0) or 0)

    summary_targets: list[dict]
    if target_limit <= 0:
        summary_targets = list(non_ja_items)
    else:
        summary_targets = []
        seen_ids = set()

        def add_targets(seq: list[dict], limit: int | None = None) -> None:
            nonlocal summary_targets
            for it in seq:
                iid = it.get("id") or ""
                if not iid or iid in seen_ids:
                    continue
                seen_ids.add(iid)
                summary_targets.append(it)
                if limit is not None and len(summary_targets) >= limit:
                    return

        add_targets(sort_items(items)[:max(target_limit, 24)])
        for cat in MAIN_SECTIONS:
            cat_items = sort_items([x for x in items if x.get("category") == cat])
            add_targets(cat_items[:target_limit])

    log(f"翻訳中... タイトル {len(title_targets)}件 / 要約 {len(summary_targets)}件")
    sep = "\n\n---\n\n"

    def do_translate_title(item: dict) -> None:
        title = (item.get("title") or "").strip()
        t = translate_text(title, cache)
        if t:
            item["title_ja"] = t

    def do_translate_summary(item: dict) -> None:
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
                if t:
                    item["title_ja"] = t
                if s:
                    item["summary_ja"] = s
                if t and s:
                    return
        t = translate_text(title, cache)
        s = translate_text(summary, cache)
        if t:
            item["title_ja"] = t
        if s:
            item["summary_ja"] = s

    title_done = 0
    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as ex:
        futures = {ex.submit(do_translate_title, item): item for item in title_targets}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception:
                pass
            title_done += 1
            if title_done % 20 == 0 or title_done == len(title_targets):
                log(f"  タイトル翻訳進捗: {title_done}/{len(title_targets)}")

    summary_done = 0
    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as ex:
        futures = {ex.submit(do_translate_summary, item): item for item in summary_targets}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception:
                pass
            summary_done += 1
            if summary_done % 10 == 0 or summary_done == len(summary_targets):
                log(f"  要約翻訳進捗: {summary_done}/{len(summary_targets)}")
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
            published, pub_dt, themes, category, sentiment, score, is_new,
            is_trend, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(
            it.get("id"), it.get("source"), it.get("lang"), it.get("region"), 1 if it.get("official") else 0,
            it.get("title"), it.get("title_ja"), it.get("summary"), it.get("summary_ja"), it.get("link"),
            it.get("published"), it.get("pub_dt"), json.dumps(it.get("themes", []), ensure_ascii=False),
            it.get("category"), detect_sentiment(it.get("title",""), it.get("summary","")),
            it.get("score"), 1 if it.get("is_new") else 0,
            1 if it.get("is_trend") else 0, fetched_at
        ) for it in items]
    )
    conn.commit()


def collect_all() -> list[dict]:
    collected = []
    ok = 0
    err = 0
    max_workers = min(8, len(SOURCES))
    log(f"並列取得: {len(SOURCES)} ソース / {max_workers} workers 同時実行")
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
    log(f"合計 {len(collected)} 件収集 → 重複除去後 {len(deduped)} 件 (成功:{ok} / エラー:{err})")
    return deduped


def base_css() -> str:
    return """
:root{--bg:#08111e;--panel:#101a2a;--panel2:#0c1523;--line:#1f2b40;--text:#eef4ff;--muted:#93a4bf;--accent:#4ea1ff;--accent2:#15314f}
*{box-sizing:border-box}html,body{margin:0;padding:0;background:linear-gradient(180deg,#08111e 0%,#0c1627 100%);color:var(--text);font-family:Inter,'Hiragino Sans','Yu Gothic UI',sans-serif}
a{color:inherit;text-decoration:none}body{padding-top:0}
.wrap,.main{width:min(1480px,94vw);margin:0 auto}
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
.main-grid,.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;align-items:stretch}
.grid.list{grid-template-columns:1fr}
.card,.mini-item,.category-panel{min-width:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:14px;display:flex;flex-direction:column;gap:10px;overflow:hidden}
.card.score-5{border-left:4px solid #ffd700}
.card.score-4{border-left:4px solid #4ea1ff}
.card.is-read{opacity:1}
.card-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;min-width:0;flex-wrap:wrap}
.card-meta{display:flex;gap:6px;flex-wrap:wrap;font-size:11px;color:var(--muted);min-width:0;overflow-wrap:anywhere;word-break:break-word}
.lang-badge,.tag{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--line);padding:3px 8px;border-radius:999px;font-size:11px;font-weight:700}
.lang-badge.ja{background:#14375d;border-color:#4ea1ff}
.lang-badge.en{background:#24301f;border-color:#15c39a}
.tag-new{background:#17391e;border-color:#15c39a;color:#93ffd8}.tag-hot{background:#14375d;border-color:#4ea1ff}.tag-caution{background:#3f1b1d;border-color:#ff7b7b}.tag-neutral{background:#1d2635;border-color:#4b5b78}
.tags{display:flex;gap:6px;flex-wrap:wrap;min-width:0;overflow-wrap:anywhere;word-break:break-word}.lang-pane{display:flex;flex-direction:column;gap:10px;min-width:0}.summary{font-size:13px;line-height:1.7;color:#d6e0f0;overflow-wrap:anywhere;word-break:break-word;display:-webkit-box;-webkit-line-clamp:6;-webkit-box-orient:vertical;overflow:hidden;min-height:8.2em}.hidden{display:none!important}
.card h3{margin:0;font-size:17px;line-height:1.45;overflow-wrap:anywhere;word-break:break-word;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;min-height:4.35em}.open-btn{margin-top:auto;display:inline-flex;justify-content:center;background:var(--accent2);border-color:var(--accent);max-width:100%;white-space:normal;text-align:center}
.section-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:4px 0 12px;min-width:0;flex-wrap:wrap}.section-head h2{margin:0;font-size:20px;overflow-wrap:anywhere;word-break:break-word}.count-badge{color:var(--muted);font-size:12px;overflow-wrap:anywhere;word-break:break-word}
.category-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:0}
.category-grid.single{grid-template-columns:repeat(3,minmax(0,1fr))}
.category-panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:14px}.mini-list{display:grid;gap:8px}.mini-item{padding:10px;border-radius:12px;background:#0c1422;border:1px solid #1d2a40}.mini-item a{font-size:13px;line-height:1.5}.mini-meta{font-size:11px;color:var(--muted);margin-top:4px}
footer{width:min(1480px,94vw);margin:20px auto 36px;color:var(--muted);font-size:12px}
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
      <h1>{html.escape(CONFIG.get('app_icon', '🏥'))} {html.escape(APP_NAME)} <span style='font-weight:400;color:var(--muted);font-size:16px'>｜ {html.escape(CONFIG.get('subtitle', ''))}</span></h1>
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
        lang_toggle = f"<div class='card-lang-toggle'><button class='read-btn' onclick=\"markRead(this, '{card_id}')\">既読</button></div>"
    else:
        panes = f"<div class='lang-pane lang-en'><h3>{title_orig}</h3><div class='summary'>{summary_orig}</div></div><div class='lang-pane lang-ja hidden'><h3>{title_ja}</h3><div class='summary'>{summary_ja}</div></div>"
        lang_toggle = f"<div class='card-lang-toggle'><button class='card-lang-btn active' data-lang='en' onclick=\"switchCardLang(this, 'en')\">EN</button><button class='card-lang-btn' data-lang='ja' onclick=\"switchCardLang(this, 'ja')\">JA</button><button class='read-btn' onclick=\"markRead(this, '{card_id}')\">既読</button></div>"
    score_class = "score-5" if score >= 5 else ("score-4" if score >= 4 else "")
    return f"""
<article class='card item-card {score_class}' data-cardid='{card_id}' data-region='{html.escape(item.get('region', 'Global'))}' data-score='{score}'>
  <div class='card-top'>
    <div class='card-meta'>{new_tag}<span class='lang-badge {'ja' if item.get('region')=='JP' else 'en'}'>{'🇯🇵 国内' if item.get('region')=='JP' else '🌐 海外'}</span><span>{source}</span><span>{published}</span></div>
    {lang_toggle}
  </div>
  {panes}
  <div class='tags'>{theme_tags}<span class='tag {sent_label[1]}'>{sent_label[0]}</span><span class='tag tag-neutral'>★{score}</span></div>
  <a class='open-btn' href='{link}' target='_blank' rel='noopener noreferrer'>記事を開く →</a>
</article>"""


def filter_bar(default_region: str = "all") -> tuple[str, str]:
    active_all = "active" if default_region == "all" else ""
    active_jp = "active" if default_region == "JP" else ""
    active_en = "active" if default_region == "Global" else ""
    html_text = f"""
<div class='panel'>
  <div class='filters'>
    <span class='filter-label'>地域:</span>
    <button type='button' class='flt-btn flt-region {active_all}' data-region='all' onclick="setRegionFilter('all', this)">🌍 全部</button>
    <button type='button' class='flt-btn flt-region {active_jp}' data-region='JP' onclick="setRegionFilter('JP', this)">🇯🇵 国内</button>
    <button type='button' class='flt-btn flt-region {active_en}' data-region='Global' onclick="setRegionFilter('Global', this)">🌐 海外</button>
    <span class='filter-label'>スコア:</span>
    <button type='button' class='flt-btn flt-score active' data-score='all' onclick="setScoreFilter('all', this)">全部</button>
    <button type='button' class='flt-btn flt-score' data-score='4' onclick="setScoreFilter('4', this)">★4以上</button>
    <button type='button' class='flt-btn flt-score' data-score='3' onclick="setScoreFilter('3', this)">★3以上</button>
    <input id='searchBox' class='search' placeholder='タイトル・本文を検索...' oninput="setSearchFilter(this.value)">
  </div>
</div>
"""
    return html_text, ""


def js_common(extra: str = "") -> str:
    script = """
<script>
function safeStorageGet(key) {
  try { return window.localStorage ? localStorage.getItem(key) : null; } catch (e) { return null; }
}
function safeStorageSet(key, value) {
  try { if (window.localStorage) localStorage.setItem(key, value); } catch (e) {}
}
function setPageLang(lang) {
  document.querySelectorAll('.page-lang-btn').forEach(function(btn){ btn.classList.remove('active'); });
  var enBtn = document.getElementById('btnPageEN');
  var jaBtn = document.getElementById('btnPageJA');
  if(lang === 'ja') {
    if(jaBtn) jaBtn.classList.add('active');
    if(enBtn) enBtn.classList.remove('active');
  } else {
    if(enBtn) enBtn.classList.add('active');
    if(jaBtn) jaBtn.classList.remove('active');
  }
  document.querySelectorAll('.item-card, .mini-item').forEach(function(block){
    var enPane = block.querySelector('.lang-en');
    var jaPane = block.querySelector('.lang-ja');
    if(!enPane || !jaPane) return;
    var cardEnBtn = block.querySelector('.card-lang-btn[data-lang="en"]');
    var cardJaBtn = block.querySelector('.card-lang-btn[data-lang="ja"]');
    if(lang === 'ja') {
      enPane.classList.add('hidden');
      jaPane.classList.remove('hidden');
      if(cardJaBtn) cardJaBtn.classList.add('active');
      if(cardEnBtn) cardEnBtn.classList.remove('active');
    } else {
      enPane.classList.remove('hidden');
      jaPane.classList.add('hidden');
      if(cardEnBtn) cardEnBtn.classList.add('active');
      if(cardJaBtn) cardJaBtn.classList.remove('active');
    }
  });
}
function switchCardLang(btn, lang) {
  var card = btn.closest('.item-card');
  if (!card) return;
  card.querySelectorAll('.card-lang-btn').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  var enPane = card.querySelector('.lang-en');
  var jaPane = card.querySelector('.lang-ja');
  if (lang === 'ja') { if(enPane) enPane.classList.add('hidden'); if(jaPane) jaPane.classList.remove('hidden'); }
  else { if(enPane) enPane.classList.remove('hidden'); if(jaPane) jaPane.classList.add('hidden'); }
  if(btn.blur) btn.blur();
}
function markRead(btn, id) {
  safeStorageSet('health_medical_plus_read_' + id, '1');
  var card = btn.closest('.item-card');
  if (card) card.classList.add('is-read');
  if (btn) {
    btn.textContent = '既読済み';
    btn.classList.add('active');
    if(btn.blur) btn.blur();
  }
}
function getVisibleCount(scope, selector) {
  var root = scope || document;
  return Array.prototype.filter.call(root.querySelectorAll(selector), function(el) {
    return el.style.display !== 'none';
  }).length;
}
function refreshVisibleCounts() {
  document.querySelectorAll('.js-visible-count').forEach(function(el){
    var selector = el.dataset.target || '.item-card';
    var section = el.closest('section') || document;
    el.textContent = getVisibleCount(section, selector) + '件';
  });
  document.querySelectorAll('section').forEach(function(sec){
    var empty = sec.querySelector('.no-results');
    if(!empty) return;
    var visibleCards = getVisibleCount(sec, '.item-card');
    empty.classList.toggle('hidden', visibleCards > 0);
  });
}
function syncFilterButtons() {
  document.querySelectorAll('.flt-region').forEach(function(btn){
    btn.classList.toggle('active', (btn.dataset.region || 'all') === window.__healthRegion);
  });
  document.querySelectorAll('.flt-score').forEach(function(btn){
    btn.classList.toggle('active', (btn.dataset.score || 'all') === window.__healthScore);
  });
}
function applyHealthFilters() {
  var region = window.__healthRegion || 'all';
  var score = window.__healthScore || 'all';
  var search = (window.__healthSearch || '').toLowerCase();
  document.querySelectorAll('.item-card').forEach(function(card){
    var show = true;
    if(region !== 'all' && (card.dataset.region || '') !== region) show = false;
    if(score !== 'all' && parseInt(card.dataset.score || '1', 10) < parseInt(score, 10)) show = false;
    if(search && (card.textContent || '').toLowerCase().indexOf(search) === -1) show = false;
    card.style.display = show ? '' : 'none';
  });
  document.querySelectorAll('.mini-item[data-region]').forEach(function(item){
    var show = true;
    if(region !== 'all' && (item.dataset.region || '') !== region) show = false;
    if(search && (item.textContent || '').toLowerCase().indexOf(search) === -1) show = false;
    item.style.display = show ? '' : 'none';
  });
  syncFilterButtons();
  refreshVisibleCounts();
}
function setRegionFilter(region, btn) {
  window.__healthRegion = region || 'all';
  applyHealthFilters();
  if(btn && btn.blur) btn.blur();
}
function setScoreFilter(score, btn) {
  window.__healthScore = score || 'all';
  applyHealthFilters();
  if(btn && btn.blur) btn.blur();
}
function setSearchFilter(value) {
  window.__healthSearch = value || '';
  applyHealthFilters();
}
(function(){
  document.querySelectorAll('.item-card').forEach(function(card){
    var id = card.dataset.cardid;
    if(safeStorageGet('health_medical_plus_read_' + id) === '1') {
      card.classList.add('is-read');
      var btn = card.querySelector('.read-btn');
      if (btn) {
        btn.textContent = '既読済み';
        btn.classList.add('active');
      }
    }
  });
  setPageLang('en');
  var activeRegion = document.querySelector('.flt-region.active');
  var activeScore = document.querySelector('.flt-score.active');
  var sb = document.getElementById('searchBox');
  window.__healthRegion = activeRegion ? (activeRegion.dataset.region || 'all') : 'all';
  window.__healthScore = activeScore ? (activeScore.dataset.score || 'all') : 'all';
  window.__healthSearch = sb ? (sb.value || '') : '';
  refreshVisibleCounts();
  applyHealthFilters();
})();
</script>
"""
    return script + extra



def sort_items(items: Iterable[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda x: (
            x.get("region") != "JP",
            0 if x.get("is_new") else 1,
            -x.get("score", 1),
            -(datetime.fromisoformat(x.get("pub_dt")).timestamp()) if x.get("pub_dt") else 0,
        ),
        reverse=False,
    )


def mini_item(it: dict) -> str:
    title_orig = html.escape(it.get('title', ''))
    title_ja = html.escape(it.get('title_ja') or it.get('title', ''))
    region = '🇯🇵 国内' if it.get('region') == 'JP' else '🌐 海外'
    if it.get('lang') == 'ja':
        title_html = f"<span class='lang-en'>{title_orig}</span>"
    else:
        title_html = f"<span class='lang-en'>{title_orig}</span><span class='lang-ja hidden'>{title_ja}</span>"
    return f"<div class='mini-item' data-region='{html.escape(it.get('region','Global'))}'><a href='{html.escape(it['link'])}' target='_blank' rel='noopener noreferrer'>{title_html}</a><div class='mini-meta'>{region} / ★{it.get('score',1)} / {html.escape(it.get('published',''))}</div></div>"

def render_empty_mini(category: str) -> str:
    return f"<div class='mini-item no-results-mini'>{html.escape(category)} の該当記事はありません</div>"


def render_main(items: list[dict]) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    filter_html, filter_js = filter_bar(default_region='all')
    sections = {cat: sort_items([x for x in items if item_matches_category(x, cat)])[:3] for cat in MAIN_SECTIONS}
    blocks = []
    for cat in MAIN_SECTIONS:
        block_items = sections.get(cat, [])
        mini = "".join(mini_item(it) for it in block_items) if block_items else render_empty_mini(cat)
        blocks.append(f"<section class='category-panel'><div class='section-head'><h2>{CATEGORY_TO_ICON[cat]} {cat}</h2><a href='{CATEGORY_TO_FILE[cat]}' class='count-badge'>もっと見る →</a></div><div class='mini-list'>{mini}</div></section>")
    latest_items = sort_items(items)[:24]
    latest = "".join(card_html(it) for it in latest_items)
    body = f"""
<div class='main'>
{filter_html}
<section class='panel'>
  <div class='section-head'><h2>🆕 NEW優先 注目3件 × 5カテゴリ</h2><span class='count-badge js-visible-count' data-target='.js-mini-scope .mini-item'>0件</span></div>
  <div class='category-grid js-mini-scope'>{''.join(blocks)}</div>
</section>
<section>
  <div class='section-head'><h2>📰 最新記事</h2><span class='count-badge js-visible-count' data-target='.item-card'>0件</span></div>
  <div class='grid'>{latest}</div>
  <div class='summary no-results hidden'>条件に一致する記事がありません。</div>
</section>
</div>"""
    return header_html("index.html", stamp) + body + footer_html(stamp, filter_js)

def render_category_page(items: list[dict], category: str, filename: str) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    default_region = 'JP' if category == '公的情報' else 'all'
    filter_html, filter_js = filter_bar(default_region=default_region)
    cat_items = sort_items([x for x in items if item_matches_category(x, category)])
    focus_items = cat_items[:3]
    cards = "".join(card_html(it) for it in cat_items)
    body = f"""
<div class='main'>
{filter_html}
<section class='panel'>
  <div class='section-head'><h2>{CATEGORY_TO_ICON[category]} {category} 今日の注目 3件</h2><span class='count-badge js-visible-count' data-target='.js-mini-scope .mini-item'>0件</span></div>
  <div class='category-grid single js-mini-scope'>{''.join(mini_item(it) for it in focus_items) if focus_items else render_empty_mini(category)}</div>
</section>
<section>
  <div class='section-head'><h2>📰 最新記事</h2><span class='count-badge js-visible-count' data-target='.item-card'>0件</span></div>
  <div class='grid'>{cards}</div>
  <div class='summary no-results hidden'>条件に一致する記事がありません。</div>
</section>
</div>"""
    return header_html(filename, stamp) + body + footer_html(stamp, filter_js)


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
    now = datetime.now(JST)
    cutoff_7d = now - timedelta(days=7)

    category_counts = {cat: sum(1 for x in items if item_matches_category(x, cat)) for cat in MAIN_SECTIONS}
    region_counts = Counter(x.get("region", "Global") for x in items)
    topic_counts = Counter()
    for x in items:
        for t in x.get("themes", []):
            if t in TOPIC_KEYWORDS:
                topic_counts[t] += 1

    trend_counts = Counter()
    for x in items:
        try:
            if x.get("pub_dt") and datetime.fromisoformat(x["pub_dt"]).astimezone(JST) >= cutoff_7d:
                trend_counts[x.get("category", "その他")] += 1
        except Exception:
            pass

    rows1 = "".join(
        f"<div class='mini-item'><div style='font-weight:700'>{CATEGORY_TO_ICON[k]} {html.escape(k)}</div><div class='mini-meta'>{category_counts.get(k, 0)}件</div></div>"
        for k in MAIN_SECTIONS
    )
    rows2 = "".join([
        f"<div class='mini-item'><div style='font-weight:700'>🇯🇵 国内</div><div class='mini-meta'>{region_counts.get('JP', 0)}件</div></div>",
        f"<div class='mini-item'><div style='font-weight:700'>🌐 海外</div><div class='mini-meta'>{region_counts.get('Global', 0)}件</div></div>",
    ])

    summary_rows = []
    zero_rows = []
    for k in MAIN_SECTIONS:
        jp_count = sum(1 for x in items if item_matches_category(x, k) and x.get("region") == "JP")
        gl_count = sum(1 for x in items if item_matches_category(x, k) and x.get("region") == "Global")
        summary_rows.append(
            f"<div class='mini-item'><div style='font-weight:700'>{CATEGORY_TO_ICON[k]} {html.escape(k)}</div><div class='mini-meta'>国内 {jp_count}件 / 海外 {gl_count}件</div></div>"
        )
        if jp_count == 0 or gl_count == 0:
            lacks = []
            if jp_count == 0:
                lacks.append("国内0件")
            if gl_count == 0:
                lacks.append("海外0件")
            zero_rows.append(
                f"<div class='mini-item'><div style='font-weight:700'>{CATEGORY_TO_ICON[k]} {html.escape(k)}</div><div class='mini-meta'>{' / '.join(lacks)}</div></div>"
            )
    rows3 = ''.join(summary_rows)
    rows4 = "".join(
        f"<div class='mini-item'><div style='font-weight:700'>{html.escape(k)}</div><div class='mini-meta'>{v}件</div></div>"
        for k, v in (topic_counts.most_common(8) or [("－", 0)])
    )
    rows5 = "".join(
        f"<div class='mini-item'><div style='font-weight:700'>{CATEGORY_TO_ICON.get(k, '•')} {html.escape(k)}</div><div class='mini-meta'>直近7日 {v}件</div></div>"
        for k, v in (trend_counts.most_common(6) or [("－", 0)])
    )
    rows6 = ''.join(zero_rows) or "<div class='mini-item'><div style='font-weight:700'>不足カテゴリなし</div><div class='mini-meta'>国内 / 海外ともに記事あり</div></div>"
    blocks_html = "".join([
        f"<section class='category-panel'><div class='section-head'><h2>カテゴリ別件数</h2></div><div class='mini-list'>{rows1}</div></section>",
        f"<section class='category-panel'><div class='section-head'><h2>地域別件数</h2></div><div class='mini-list'>{rows2}</div></section>",
        f"<section class='category-panel'><div class='section-head'><h2>カテゴリ×地域件数</h2></div><div class='mini-list'>{rows3}</div></section>",
        f"<section class='category-panel'><div class='section-head'><h2>トピック件数</h2></div><div class='mini-list'>{rows4}</div></section>",
        f"<section class='category-panel'><div class='section-head'><h2>直近7日 活発なカテゴリ</h2></div><div class='mini-list'>{rows5}</div></section>",
        f"<section class='category-panel'><div class='section-head'><h2>0件カテゴリ</h2></div><div class='mini-list'>{rows6}</div></section>",
    ])
    body = f"""
<div class='main'>
<section class='panel'>
  <div class='section-head'><h2>📊 分析サマリー</h2><span class='count-badge'>{len(items)}件 / 更新:{stamp}</span></div>
  <div class='category-grid'>{blocks_html}</div>
</section>
</div>"""
    return header_html("analysis.html", stamp) + body + footer_html(stamp, "")


def main() -> None:
    start = time.time()
    log("=" * 46)
    log(f"  {APP_NAME}")
    log("=" * 46)
    log("[1/3] フィード収集中...")
    items = collect_all()
    log("[2/3] 翻訳処理...")
    cache = load_json(TRANSLATE_CACHE_FILE, {})
    translate_items(items, cache)

    log("[3/3] HTML生成中...")
    conn = db_init()
    db_save_items(conn, items)
    conn.close()
    pages = {
        "index.html": render_main(items),
        "health.html": render_category_page(items, "健康", "health.html"),
        "medical.html": render_category_page(items, "医療", "medical.html"),
        "research.html": render_category_page(items, "研究", "research.html"),
        "prevention_longevity.html": render_category_page(items, "予防 / 長寿", "prevention_longevity.html"),
        "public_info.html": render_category_page(items, "公的情報", "public_info.html"),
        "analysis.html": render_analysis(items),
    }
    for filename, content in pages.items():
        (OUTPUT_DIR / filename).write_text(content, encoding="utf-8")
        log(f"  ✓ {filename}")

    log("完了")
    log(f"出力先: {OUTPUT_DIR / 'index.html'}")
    log(f"処理時間: {time.time() - start:.1f}秒")

if __name__ == "__main__":
    main()
