"""
Memory Market +
メモリー・SSD・HBMの需給と価格動向
"""

from __future__ import annotations
import subprocess, sys

def ensure_package(import_name: str, package_name: str | None = None) -> None:
    package = package_name or import_name
    try:
        __import__(import_name)
    except ImportError:
        print(f"{package} が無いためインストールします...", flush=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

ensure_package("feedparser")
ensure_package("requests")
ensure_package("bs4", "beautifulsoup4")
ensure_package("deep_translator", "deep-translator")

import hashlib, html, json, re, sqlite3, time, unicodedata
from difflib import SequenceMatcher
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, urljoin

import feedparser, requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

ROOT    = Path(__file__).resolve().parent.parent
SHARED  = ROOT / "shared"
OUTPUT  = ROOT / "output"
LOG_DIR = ROOT / "logs"
OUTPUT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
JST = ZoneInfo("Asia/Tokyo")

CONFIG           = json.loads((SHARED / "config.json").read_text(encoding="utf-8"))
APP_NAME         = CONFIG["app_name"]
TRANSLATE_WORKERS = int(CONFIG.get("translate_workers", 4))
TRANSLATE_RETRIES = int(CONFIG.get("translate_retries", 2))
NEW_HOURS        = int(CONFIG.get("new_badge_hours", 3))
HISTORY_DAYS     = int(CONFIG.get("history_days", 7))

TRANSLATE_CACHE_FILE = OUTPUT / "translate_cache.json"
DB_FILE              = OUTPUT / CONFIG.get("db_name", "memory_market_plus.db")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 MemoryMarketPlus"
TIMEOUT    = 20


# ─── 重要タグキーワード ───────────────────────────────────────

PRICE_UP = [
    "price increase", "higher asp", "pricing improvement", "prices rise", "prices higher", "spot price", "price recovery", "pricing recovery", "better pricing", "improved pricing", "firm pricing", "price environment",
    "contract price", "higher contract", "higher pricing", "pushing overall prices higher",
    "価格上昇", "値上がり", "値上げ", "単価上昇", "asp改善", "市況改善", "上げ基調"
]
PRICE_DOWN = [
    "pricing pressure", "price decline", "weaker pricing", "price drop", "prices down", "oversupply", "soft demand", "inventory overhang",
    "inventory correction", "price erosion", "soft pricing",
    "価格下落", "値下がり", "下落", "価格圧力", "需給悪化", "在庫調整", "供給過剰"
]
DEMAND_UP = [
    "strong demand", "robust demand", "ai demand", "server demand", "data center demand", "cloud demand",
    "recovery", "inventory normalization", "demand growth", "record revenue", "year-over-year increase",
    "sales growth", "monthly sales", "order momentum", "increased demand", "healthy demand",
    "需要増", "需要堅調", "ai需要", "サーバー需要", "需要回復", "在庫正常化", "引き合い", "受注増", "販売増"
]
SUPPLY_DOWN = [
    "production cut", "reduce production", "output reduction", "utilization cut", "delay", "slowdown", "capex reduction", "capex cut", "cut wafer starts", "lower utilization",
    "supply tightness", "tight supply", "allocation", "shortage", "constrained supply",
    "tighter availability", "supply constraint",
    "減産", "生産調整", "稼働率低下", "供給逼迫", "品薄", "逼迫", "割当", "供給不足"
]
CAPEX_SIGNALS = [
    "capital expenditures", "capex", "investment", "facility", "plant", "fab", "complex",
    "r&d complex", "mass production", "new fab", "new line", "capacity expansion", "expand production",
    "増産", "量産", "新工場", "新ライン", "設備投資", "投資", "稼働開始", "生産能力"
]

REFERENCE_WORDS = [
    "record revenue", "strong demand", "robust demand", "ai demand", "server demand", "data center demand",
    "pricing improvement", "higher asp", "tight supply", "inventory normalization",
    "増収", "需要増", "需要堅調", "価格上昇", "ai需要", "在庫正常化", "受注増"
]
WARNING_WORDS = [
    "pricing pressure", "price decline", "weaker pricing", "weak demand", "inventory correction", "oversupply",
    "production cut", "reduce production", "delay", "slowdown",
    "価格下落", "価格圧力", "需要減", "在庫調整", "供給過剰", "減産", "遅延"
]

DEMAND_STRONG = [
    "strong demand", "robust demand", "healthy demand", "ai demand", "server demand", "data center demand",
    "cloud demand", "customer demand", "demand growth", "increased demand", "order momentum",
    "inventory normalization", "inventory normalisation", "bit demand", "bit growth",
    "需要増", "需要堅調", "需要回復", "ai需要", "サーバー需要", "在庫正常化", "受注増", "引き合い"
]

DEMAND_CONTEXT = [
    "ai", "server", "data center", "datacenter", "cloud", "hbm", "gpu", "dram", "nand", "ssd",
    "memory", "storage", "ddr5", "lpddr", "module"
]

DEMAND_GROWTH = [
    "demand", "growth", "growing", "increase", "increased", "recovery", "normalize", "normalization",
    "revenue", "sales", "shipment", "shipments", "orders", "momentum", "year-over-year increase",
    "前年比", "増収", "売上", "販売", "出荷", "受注"
]

SUPPLY_TIGHT_STRONG = [
    "tight supply", "supply tightness", "allocation", "shortage", "constrained supply", "tighter availability",
    "supply constraint", "供給逼迫", "供給不足", "品薄", "逼迫"
]

PRODUCT_LAUNCH_HINTS = [
    "launch", "announces", "introduces", "unveils", "availability", "shipping", "release", "showcases",
    "new ssd", "new memory", "memory module", "gaming memory", "portable ssd",
    "発売", "発表", "投入", "提供開始", "ラインアップ", "新製品"
]

IR_WORDS = ["earnings", "quarterly", "results", "guidance", "investor", "ir", "fiscal", "決算", "業績"]
SALES_WORDS = ["monthly sales", "sales growth", "record revenue", "year-over-year increase", "売上高", "月次売上", "増収"]

EVENT_KEYWORDS = {
    3: ["price increase", "price decline", "tight supply", "oversupply", "価格上昇", "価格下落", "逼迫", "供給過剰"],
    2: ["ai demand", "hbm", "capex", "mass production", "AI需要", "HBM", "設備投資", "量産"],
    1: ["ssd", "dram", "nand", "ddr5", "lpddr", "SSD", "DRAM", "NAND"],
}


# ─── ユーティリティ ───────────────────────────────────────────────


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def load_json(path: Path, default=None):
    if path.exists():
        try: return json.loads(path.read_text(encoding="utf-8"))
        except: pass
    return default if default is not None else {}

def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def normalize_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().strip()
    return host[4:] if host.startswith("www.") else host

def is_allowed_domain(link: str, allowed_domains: list[str]) -> bool:
    if not link: return False
    parsed = urlparse(link)
    if parsed.scheme not in {"http", "https"}: return False
    host = (parsed.hostname or "").lower()
    if host.startswith("www."): host = host[4:]
    for d in allowed_domains:
        d = d.lower().strip()
        if d.startswith("www."): d = d[4:]
        if host == d or host.endswith("." + d): return True
    return False

def has_japanese(text: str) -> bool:
    return bool(re.search(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]', text))

def has_hangul(text: str) -> bool:
    return bool(re.search(r'[\uac00-\ud7af]', text))

def detect_lang_from_text(*parts: str) -> str:
    text = " ".join([p for p in parts if p])
    return "ja" if has_japanese(text) else "en"

def text_lower(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()

def normalize_title_for_dedupe(title: str) -> str:
    t = text_lower(title)
    t = re.sub(r'（[^）]*）|\([^)]*\)|\[[^\]]*\]|【[^】]*】', ' ', t)
    t = re.sub(r'(写真特集|フォト特集|画像特集|photo\s*special|gallery|ギャラリー)', ' ', t)
    t = re.sub(r'\b(photo|photos|image|images|画像|写真|動画|ビデオ|news|ニュース)\b', ' ', t)
    t = re.sub(r'\b(公式サイト|公式|公開|掲載|配信|速報|特集|全文)\b', ' ', t)
    t = re.sub(r'\b\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?\b', ' ', t)
    t = re.sub(r'\b\d{1,2}:\d{2}\b', ' ', t)
    t = re.sub(r'\b\d{4}年最新版\b|\b\d{4}年版\b|\b最新版\b|\b最新\b', ' ', t)
    t = re.sub(r'\btop\d+\b', ' ', t)
    t = re.sub(r'\b\d+/\d+\b', ' ', t)
    t = re.sub(r'\b\(?[123]/[123]\)?\b', ' ', t)
    t = re.sub(r'\b(part|page)\s*\d+\b', ' ', t)
    t = re.sub(r'\b(excite|エキサイト|ニコニコニュース|ニコニコ|prtimes|pr times|yahoo!ニュース|yahooニュース|yahoo|ライブドアニュース|livedoor|msn|毎日新聞|朝日新聞|読売新聞)\b', ' ', t)
    t = re.sub(r'[|｜／/・:：\-–—]+', ' ', t)
    t = re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff]+', ' ', t)
    t = re.sub(r'\b(jp|en|us|uk|com|net|org)\b', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def is_image_or_gallery_item(title: str, summary: str = '', link: str = '') -> bool:
    combined = text_lower(f"{title} {summary} {link}")
    patterns = [
        r'画像\s*\d+\s*/\s*\d+',
        r'写真\s*\d+\s*/\s*\d+',
        r'フォト\s*\d+\s*/\s*\d+',
        r'photo\s*\d+\s*/\s*\d+',
        r'gallery',
        r'フォト特集',
        r'写真特集',
        r'画像特集',
        r'スライドショー',
        r'photo\s*special',
    ]
    if any(re.search(p, combined) for p in patterns):
        return True
    if re.search(r'(?:^|\W)(?:photo|photos|image|images|gallery)(?:\W|$)', combined) and re.search(r'\d+\s*/\s*\d+', combined):
        return True
    if any(token in combined for token in ['/photo/', '/photos/', '/gallery/', 'photo/', 'gallery/']):
        return True
    return False

def make_soft_title_key(title_key: str) -> str:
    if not title_key:
        return ''
    words = title_key.split()
    if len(words) >= 6:
        return ' '.join(words[:6])
    if len(words) >= 4:
        return ' '.join(words[:4])
    return title_key[:40]

def make_core_title_key(title_key: str) -> str:
    if not title_key:
        return ''
    words = [w for w in title_key.split() if len(w) > 1]
    if len(words) >= 8:
        return ' '.join(words[:8])
    if len(words) >= 5:
        return ' '.join(words[:5])
    return title_key[:56]

def extract_story_source(text: str, link: str = '') -> str:
    combined = text_lower(f"{text} {link}")
    patterns = [
        (r'\bedutail\b', 'edutail'),
        (r'\bpr\s?times\b', 'edutail'),
        (r'\bexcite\b|\bエキサイト\b', 'edutail'),
        (r'\bニコニコニュース\b|\bニコニコ\b', 'edutail'),
        (r'\byahoo!?\s*ニュース\b|\byahoo!?\b', 'yahoo'),
        (r'\blivedoor\b|\bライブドア\b', 'livedoor'),
        (r'\bmsn\b', 'msn'),
        (r'\bprtimes\.jp\b', 'edutail'),
    ]
    for pat, label in patterns:
        if re.search(pat, combined):
            return label
    host = normalize_host(link)
    if host:
        host = re.sub(r'\.(com|jp|net|org|co\.jp)$', '', host)
        host = re.sub(r'[^a-z0-9]+', '', host)
    return host or ''

def item_rank(x: dict):
    return (
        x.get('score', 1),
        1 if x.get('official') else 0,
        len(x.get('summary', '')),
        x.get('pub_dt', '')
    )

def is_probable_duplicate_item(current: dict, previous: dict) -> bool:
    cur_title = normalize_title_for_dedupe(current.get('title', ''))
    prev_title = normalize_title_for_dedupe(previous.get('title', ''))
    if not cur_title or not prev_title:
        return False

    if cur_title == prev_title:
        return True

    cur_host = normalize_host(current.get('link', ''))
    prev_host = normalize_host(previous.get('link', ''))
    same_host = bool(cur_host and prev_host and cur_host == prev_host)

    cur_story = extract_story_source(current.get('title', '') + ' ' + current.get('summary', ''), current.get('link', ''))
    prev_story = extract_story_source(previous.get('title', '') + ' ' + previous.get('summary', ''), previous.get('link', ''))
    same_story = bool(cur_story and prev_story and cur_story == prev_story)

    similarity = title_similarity(cur_title, prev_title)
    soft_cur = make_soft_title_key(cur_title)
    soft_prev = make_soft_title_key(prev_title)
    core_cur = make_core_title_key(cur_title)
    core_prev = make_core_title_key(prev_title)

    # same underlying distributor / story feed
    if same_story and core_cur and core_cur == core_prev:
        return True
    if same_story and similarity >= 0.80:
        return True

    # same host with slightly different wrappers such as photo pages or paginated pages
    if same_host and core_cur and core_cur == core_prev:
        return True
    if same_host and similarity >= 0.80:
        return True

    # cross-source near identical stories
    if core_cur and core_cur == core_prev and similarity >= 0.76:
        return True
    if soft_cur and soft_cur == soft_prev and similarity >= 0.84:
        return True
    if similarity >= 0.91:
        return True
    return False

def source_group(name: str) -> str:
    src = text_lower(name)
    if 'google news jp' in src:
        return 'google_news_jp'
    if 'google news en' in src or 'google news -' in src:
        return 'google_news_en'
    return src

def is_new(pub_dt_iso: str) -> bool:
    if not pub_dt_iso: return False
    try:
        pub_dt = datetime.fromisoformat(pub_dt_iso)
        return datetime.now(JST) - pub_dt.astimezone(JST) <= timedelta(hours=NEW_HOURS)
    except: return False


# ─── 判定ロジック ─────────────────────────────────────────────────

def detect_sentiment(title: str, summary: str, fx_impact: str = "neutral", full_text: str = "") -> str:
    combined = text_lower(f"{title} {summary} {full_text}")
    ref_score = sum(1 for w in REFERENCE_WORDS if text_lower(w) in combined)
    warn_score = sum(1 for w in WARNING_WORDS if text_lower(w) in combined)

    if fx_impact in {"price_up", "demand_up"}:
        ref_score += 2
    elif fx_impact in {"price_down", "supply_down"}:
        warn_score += 2

    if warn_score > ref_score:
        return "bearish"
    if ref_score > warn_score and ref_score > 0:
        return "bullish"
    return "neutral"


def detect_fx_impact(title: str, summary: str, source_name: str = "", full_text: str = "") -> str:
    """主要需給タグを判定: price_up / price_down / demand_up / supply_down / neutral。"""
    combined = text_lower(f"{title} {summary} {full_text}")
    title_l = text_lower(title)
    summary_l = text_lower(summary)
    title_summary = f"{title_l} {summary_l}"
    src = text_lower(source_name)

    def count_hits(words: list[str]) -> int:
        return sum(1 for w in words if text_lower(w) in combined)

    def has_any(words: list[str]) -> bool:
        return any(text_lower(w) in combined for w in words)

    demand_strong_hits = count_hits(DEMAND_STRONG)
    demand_context_hit = has_any(DEMAND_CONTEXT)
    product_launchish = has_any(PRODUCT_LAUNCH_HINTS) and not has_any([
        "demand", "tight supply", "pricing", "inventory normalization", "record revenue", "sales growth"
    ])

    price_context_hit = has_any(["price", "pricing", "asp", "contract", "spot", "市況", "価格"])
    price_up_phrase_hit = has_any(PRICE_UP)
    price_down_phrase_hit = has_any(PRICE_DOWN)

    supply_strong_words = list(dict.fromkeys(SUPPLY_TIGHT_STRONG + [
        "production cut", "reduce production", "output reduction", "utilization cut", "lower utilization",
        "cut wafer starts", "capex reduction", "capex cut", "delay mass production",
        "減産", "生産調整", "稼働率低下", "投資抑制", "設備投資削減"
    ]))
    supply_strong_hits = count_hits(supply_strong_words)
    supply_title_hit = any(text_lower(w) in title_summary for w in supply_strong_words)
    capex_cut_hit = has_any(["capex reduction", "capex cut", "investment discipline", "delay mass production", "設備投資削減", "投資抑制"])

    weak_supply_context = False
    if has_any(["inventory normalization", "inventory normalisation", "inventory adjustment", "在庫正常化", "在庫調整"]) and has_any([
        "tight", "tightness", "disciplined", "discipline", "constraint", "constrained", "cautious", "controlled",
        "調整", "正常化", "逼迫", "慎重", "抑制"
    ]):
        weak_supply_context = True
    if has_any(["capex", "capital expenditures", "設備投資", "投資"]) and has_any([
        "discipline", "disciplined", "reduction", "reduce", "cut", "cautious", "slow", "slower", "moderate",
        "抑制", "削減", "慎重", "抑える"
    ]):
        weak_supply_context = True
    if has_any(["utilization", "capacity", "wafer starts", "稼働率", "生産能力"]) and has_any([
        "lower", "reduction", "reduce", "cut", "adjustment", "control", "controlled", "discipline",
        "低下", "調整", "抑制", "絞る"
    ]):
        weak_supply_context = True

    revenue_signal_hit = has_any(SALES_WORDS)

    scores = {"price_up": 0, "price_down": 0, "demand_up": 0, "supply_down": 0}

    scores["price_up"] += count_hits(PRICE_UP)
    scores["price_down"] += count_hits(PRICE_DOWN)
    if any(text_lower(w) in title_summary for w in PRICE_UP):
        scores["price_up"] += 2
    if any(text_lower(w) in title_summary for w in PRICE_DOWN):
        scores["price_down"] += 2
    if price_context_hit and has_any(["improvement", "recover", "recovery", "higher", "firm", "increase", "improving", "stabilize", "stabilizing", "normalize", "normalization", "上昇", "改善", "持ち直し", "正常化"]):
        scores["price_up"] += 1
    if price_context_hit and has_any(["pressure", "weak", "decline", "drop", "erosion", "oversupply", "soft", "correction", "下落", "圧力", "軟化"]):
        scores["price_down"] += 1
    if price_context_hit and any(k in title_summary for k in ["price", "pricing", "asp", "contract", "spot", "市場", "市況", "価格"]):
        scores["price_up"] += 1
    if price_context_hit and has_any(["contract price", "spot price", "pricing environment", "asp", "価格", "市況"]):
        if scores["price_up"] == 0 and scores["price_down"] == 0:
            scores["price_up"] += 1

    if supply_strong_hits:
        scores["supply_down"] += supply_strong_hits * 2
    if supply_title_hit:
        scores["supply_down"] += 2
    if capex_cut_hit:
        scores["supply_down"] += 1
    if weak_supply_context:
        scores["supply_down"] += 2

    if demand_strong_hits:
        scores["demand_up"] += demand_strong_hits * 2
        if any(text_lower(w) in title_summary for w in DEMAND_STRONG):
            scores["demand_up"] += 1
    if demand_context_hit and has_any(["demand", "orders", "shipments", "revenue", "sales", "bit growth", "inventory normalization", "受注", "出荷", "売上"]):
        scores["demand_up"] += 2
    if revenue_signal_hit and demand_context_hit:
        scores["demand_up"] += 1
    if has_any(IR_WORDS) and has_any(["demand", "ai", "server", "data center", "inventory normalization", "hbm", "bit demand", "shipments"]):
        scores["demand_up"] += 1
    if has_any(["inventory normalization", "inventory normalisation", "在庫正常化"]) and has_any(["demand", "sales", "revenue", "shipments", "orders", "ai", "server", "hbm"]):
        scores["demand_up"] += 1
    if has_any(["growth", "recovery", "increase", "increased"]) and not (
        demand_context_hit and has_any(["demand", "sales", "revenue", "shipments", "orders", "inventory normalization"])
    ):
        scores["demand_up"] = max(scores["demand_up"] - 1, 0)
    if product_launchish and not revenue_signal_hit:
        scores["demand_up"] = max(scores["demand_up"] - 2, 0)

    if scores["price_down"] >= max(scores["price_up"], scores["supply_down"], scores["demand_up"], 2):
        return "price_down"
    if scores["price_up"] >= max(scores["price_down"], scores["supply_down"], scores["demand_up"], 2):
        return "price_up"
    if price_context_hit and (scores["price_up"] >= 1 or scores["price_down"] >= 1):
        return "price_up" if scores["price_up"] >= scores["price_down"] else "price_down"

    if scores["supply_down"] >= max(scores["price_up"], scores["price_down"], scores["demand_up"] + 1, 3):
        return "supply_down"
    if (supply_title_hit or weak_supply_context) and scores["supply_down"] >= 2 and scores["price_up"] == 0 and scores["price_down"] == 0:
        return "supply_down"

    if scores["demand_up"] >= 3 and (demand_context_hit or demand_strong_hits > 0):
        return "demand_up"
    if demand_context_hit and revenue_signal_hit and scores["demand_up"] >= 2:
        return "demand_up"

    best = max(scores, key=scores.get)
    if scores[best] > 0 and best in {"price_up", "price_down"}:
        return best

    if any(k in src for k in ["market", "pricing", "price", "spot", "contract"]):
        return "price_up"
    return "neutral"


def infer_theme_from_source(source_name: str) -> list[str]:
    src = text_lower(source_name)
    mapping = [
        (("dram", "ddr", "lpddr"), "DRAM・DDR"),
        (("nand", "ssd", "flash", "storage"), "NAND・SSD"),
        (("hbm", "ai memory", "ai", "gpu", "server"), "HBM・AI"),
        (("price", "pricing", "market", "spot", "contract", "価格", "市況"), "価格・市況"),
        (("demand", "supply", "inventory", "需給", "在庫", "allocation", "shortage"), "需給・在庫"),
        (("fab", "plant", "line", "capex", "investment", "工場", "設備投資", "量産"), "設備投資・工場"),
        (("earnings", "quarterly", "investor", "ir", "決算", "業績", "ir"), "決算・IR"),
        (("kingston", "adata", "g.skill", "cfd", "module", "consumer", "channel"), "モジュール・チャネル"),
    ]
    matched = [label for keys, label in mapping if any(k in src for k in keys)]
    return matched or ["その他"]


def infer_importance_from_source(source_name: str) -> str:
    src = text_lower(source_name)
    if any(k in src for k in ["market", "pricing", "price", "spot", "contract", "価格", "市況"]):
        return "price_up"
    return "neutral"


def calc_score(item: dict) -> int:
    """スコア計算: テーマ数・公式フラグ＋イベントキーワードボーナス"""
    combined = text_lower(f"{item.get('title','')} {item.get('summary','')}")
    themes = item.get("themes", [])

    base = 1
    if "その他" not in themes or len(themes) > 1:
        base = 2
    if item.get("official"):
        base = max(base, 3)
    if item.get("sentiment") == "bearish":
        base = max(base, 3)
    if len(themes) >= 2:
        base = max(base, 3)

    bonus = 0
    for points, keywords in EVENT_KEYWORDS.items():
        for kw in keywords:
            if text_lower(kw) in combined:
                bonus = max(bonus, points)
                break

    return min(base + bonus, 5)


def classify_themes(title: str, summary: str, lang: str, themes_cfg: dict, full_text: str = "", source_name: str = "") -> list[str]:
    primary = text_lower(f"{title} {summary}")
    extended = text_lower(f"{title} {summary} {full_text[:1200]}")

    def collect_matches(text_blob: str) -> list[str]:
        hits = []
        for theme_key, theme_data in themes_cfg["themes"].items():
            kw_key = "keywords_ja" if lang == "ja" else "keywords_en"
            for kw in theme_data.get(kw_key, []):
                if text_lower(kw) in text_blob:
                    hits.append(theme_key)
                    break
        return hits

    matched = collect_matches(primary)
    if not matched:
        matched = collect_matches(extended)

    fallback = infer_theme_from_source(source_name) if source_name else ["その他"]
    for label in [x for x in fallback if x in {"決算・IR", "モジュール・チャネル"}]:
        if label not in matched:
            matched.append(label)

    if not matched:
        return fallback
    if "その他" in matched and len(matched) > 1:
        matched = [x for x in matched if x != "その他"]

    ordered = []
    for x in matched:
        if x not in ordered:
            ordered.append(x)
    return ordered[:4] if ordered else ["その他"]



# ─── SQLite ──────────────────────────────────────────────────────

def db_init() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id          TEXT PRIMARY KEY,
            source      TEXT,
            lang        TEXT,
            region      TEXT,
            official    INTEGER,
            title       TEXT,
            title_ja    TEXT,
            summary     TEXT,
            summary_ja  TEXT,
            link        TEXT,
            published   TEXT,
            pub_dt      TEXT,
            themes      TEXT,
            sentiment   TEXT,
            fx_impact   TEXT,
            score       INTEGER,
            is_new      INTEGER,
            fetched_at  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_runs (
            run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at      TEXT,
            total_items INTEGER,
            sources_ok  INTEGER,
            sources_err INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            stat_date    TEXT PRIMARY KEY,
            total_items  INTEGER,
            price_up     INTEGER,
            price_down   INTEGER,
            demand_up    INTEGER,
            supply_down  INTEGER,
            neutral      INTEGER,
            recorded_at  TEXT
        )
    """)
    conn.commit()
    return conn


def db_save_items(conn: sqlite3.Connection, items: list[dict]) -> None:
    now = datetime.now(JST).isoformat()
    rows = [(
        item["id"], item["source"], item["lang"], item["region"],
        1 if item.get("official") else 0,
        item["title"], item.get("title_ja",""), item["summary"], item.get("summary_ja",""),
        item["link"], item["published"], item["pub_dt"],
        "|".join(item.get("themes",[])),
        item.get("sentiment","neutral"), item.get("fx_impact","neutral"),
        item.get("score",1), 1 if item.get("is_new") else 0,
        now
    ) for item in items]
    conn.executemany("""
        INSERT OR REPLACE INTO articles
        (id, source, lang, region, official, title, title_ja, summary, summary_ja,
         link, published, pub_dt, themes, sentiment, fx_impact, score, is_new, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    log(f"  ✓ SQLite保存: {len(items)}件 → {DB_FILE.name}")


def db_save_run(conn: sqlite3.Connection, total: int, ok: int, err: int) -> None:
    conn.execute(
        "INSERT INTO fetch_runs (run_at, total_items, sources_ok, sources_err) VALUES (?,?,?,?)",
        (datetime.now(JST).isoformat(), total, ok, err)
    )
    conn.commit()


def db_save_daily_stats(conn: sqlite3.Connection, items: list[dict]) -> None:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    counts = Counter(x.get("fx_impact", "neutral") for x in items)
    conn.execute(
        """INSERT OR REPLACE INTO daily_stats
        (stat_date, total_items, price_up, price_down, demand_up, supply_down, neutral, recorded_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            today,
            len(items),
            counts.get("price_up", 0),
            counts.get("price_down", 0),
            counts.get("demand_up", 0),
            counts.get("supply_down", 0),
            counts.get("neutral", 0),
            datetime.now(JST).isoformat(),
        ),
    )
    conn.commit()


def load_7day_stats() -> list[dict]:
    if not DB_FILE.exists():
        return []
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT stat_date, total_items, price_up, price_down, demand_up, supply_down, neutral FROM daily_stats ORDER BY stat_date DESC LIMIT 7"
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def trend_arrow(delta: float) -> str:
    if delta >= 2:
        return "↑↑"
    if delta > 0:
        return "↑"
    if delta <= -2:
        return "↓↓"
    if delta < 0:
        return "↓"
    return "→"


def render_7day_trend_panel() -> str:
    stats = load_7day_stats()
    if not stats:
        return ""

    def calc(metric: str) -> tuple[float, int, str]:
        values = [int(x.get(metric, 0) or 0) for x in stats]
        avg = round(sum(values) / len(values), 1) if values else 0.0
        today = values[-1] if values else 0
        delta = today - avg
        return avg, int(round(delta)), trend_arrow(delta)

    demand_avg, demand_delta, demand_arrow = calc("demand_up")
    supply_avg, supply_delta, supply_arrow = calc("supply_down")
    price_values = [int(x.get("price_up", 0) or 0) + int(x.get("price_down", 0) or 0) for x in stats]
    price_avg = round(sum(price_values) / len(price_values), 1) if price_values else 0.0
    price_today = price_values[-1] if price_values else 0
    price_delta = int(round(price_today - price_avg))
    price_arrow = trend_arrow(price_today - price_avg)

    def cls(delta: int) -> str:
        if delta > 0:
            return "#2ecc71"
        if delta < 0:
            return "#e74c3c"
        return "var(--muted)"

    def row(label: str, avg: float, delta: int, arrow: str) -> str:
        return (
            f"<div class='trend-row'>"
            f"<div class='trend-label'>{label}</div>"
            f"<div class='trend-metric'>平均 <span style='font-weight:800'>{avg}</span></div>"
            f"<div class='trend-metric'>今日 <span style='font-weight:800;color:{cls(delta)}'>{delta:+d}</span></div>"
            f"<div class='trend-arrow'>{arrow}</div>"
            f"</div>"
        )

    return f"""
<div class='panel trend-panel' style='margin-bottom:16px'>
  <h2 style='margin:0 0 12px;font-size:16px'>📊 7 Day Trend</h2>
  <div style='display:grid;gap:10px'>
    {row("需要", demand_avg, demand_delta, demand_arrow)}
    {row("供給", supply_avg, supply_delta, supply_arrow)}
    {row("価格", price_avg, price_delta, price_arrow)}
  </div>
</div>"""

def db_cleanup(conn: sqlite3.Connection) -> None:
    """HISTORY_DAYS日より古い記事を削除"""
    cutoff = (datetime.now(JST) - timedelta(days=HISTORY_DAYS * 2)).isoformat()
    conn.execute("DELETE FROM articles WHERE pub_dt < ? AND pub_dt != ''", (cutoff,))
    conn.commit()


# ─── 翻訳 ─────────────────────────────────────────────────────────

def translate_text(text: str, cache: dict) -> str:
    text = (text or "").strip()
    if not text or has_japanese(text): return text
    cached = cache.get(text)
    if cached: return cached if has_japanese(cached) else ""
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


def translate_items(items: list[dict], cache: dict) -> None:
    # 英語記事を全件翻訳
    en_items = [x for x in items if x.get("lang") != "ja"]
    if not en_items:
        return
    log(f"\n🌐 翻訳中... ({len(en_items)}件 全件)")

    def do_translate(item: dict) -> dict:
        title = (item.get("title", "") or "").strip()
        summary = (item.get("summary", "") or "").strip()

        item["title_ja"] = ""
        item["summary_ja"] = ""

        can_combine = (
            title
            and summary
            and not has_japanese(title)
            and not has_japanese(summary)
        )

        # まず結合翻訳を試す
        if can_combine:
            sep = "\n\n---\n\n"
            combined = title + sep + summary
            cached = cache.get(combined)
            result = cached if cached else translate_text(combined, cache)

            if result:
                parts = result.split(sep, 1)
                title_ja = parts[0].strip() if len(parts) > 0 else ""
                summary_ja = parts[1].strip() if len(parts) > 1 else ""

                if title_ja and summary_ja:
                    item["title_ja"] = title_ja
                    item["summary_ja"] = summary_ja

                    if not cached:
                        cache[combined] = result

                    return item

        # 結合翻訳失敗時は個別翻訳にフォールバック
        title_ja = translate_text(title, cache)
        summary_ja = translate_text(summary, cache)

        item["title_ja"] = title_ja if title_ja else title
        item["summary_ja"] = summary_ja if summary_ja else summary
        return item

    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as ex:
        futures = {ex.submit(do_translate, item): item for item in en_items}
        done = 0
        for f in as_completed(futures):
            try:
                f.result()
            except:
                pass
            done += 1
            if done % 10 == 0 or done == len(en_items):
                log(f"  翻訳進捗: {done}/{len(en_items)}")

    save_json(TRANSLATE_CACHE_FILE, cache)
    log("  ✓ 翻訳完了・キャッシュ保存")


# ─── データ収集 ───────────────────────────────────────────────────

def _request_text(url: str, allow_insecure_ssl_retry: bool = False) -> str:
    resp = fetch_with_optional_ssl_retry(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,ja;q=0.8"},
        timeout=TIMEOUT,
        allow_insecure_ssl_retry=allow_insecure_ssl_retry,
    )
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text

def extract_article_text(url: str, allow_insecure_ssl_retry: bool = False) -> dict:
    lower = (url or "").lower()
    if any(lower.endswith(ext) for ext in [".pdf", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx"]):
        return {"title": "", "summary": "", "text": "", "published_at": ""}
    try:
        html_text = _request_text(url, allow_insecure_ssl_retry=allow_insecure_ssl_retry)
    except Exception:
        return {"title": "", "summary": "", "text": "", "published_at": ""}
    soup = BeautifulSoup(html_text, "html.parser")

    title = ""
    for selector in ["meta[property='og:title']", "meta[name='twitter:title']"]:
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            title = clean_text(tag.get("content", ""))
            break
    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))

    summary = ""
    for selector in ["meta[property='og:description']", "meta[name='description']", "meta[name='twitter:description']"]:
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            summary = clean_text(tag.get("content", ""))
            break

    published = ""
    for selector, attr in [("meta[property='article:published_time']", "content"), ("meta[name='pubdate']", "content"), ("meta[name='publish-date']", "content"), ("time[datetime]", "datetime")]:
        tag = soup.select_one(selector)
        if tag and tag.get(attr):
            published = clean_text(tag.get(attr, ""))
            break

    body = ""
    for selector in ["article", "main", ".article-body", ".article__body", ".post-content", ".entry-content", ".content", "#content"]:
        node = soup.select_one(selector)
        if not node:
            continue
        paras = [clean_text(p.get_text(" ", strip=True)) for p in node.select("p")]
        paras = [p for p in paras if len(p) > 35]
        if paras:
            body = " ".join(paras[:20])
            break
    if not body:
        paras = [clean_text(p.get_text(" ", strip=True)) for p in soup.select("p")]
        paras = [p for p in paras if len(p) > 35]
        body = " ".join(paras[:20])

    return {"title": title[:300], "summary": summary[:700], "text": body[:8000], "published_at": published[:100]}

def source_keywords(source: dict) -> list[str]:
    kws = [text_lower(k) for k in source.get("include_keywords", []) if str(k).strip()]
    company = text_lower(source.get("company", ""))
    if company:
        kws.append(company)
    return sorted(set(kws))

def matches_source_keywords(text: str, source: dict) -> bool:
    kws = source_keywords(source)
    if not kws:
        return True
    blob = text_lower(text)
    return any(k in blob for k in kws)

def extract_page_candidate_links(source: dict, html_text: str, base_url: str) -> list[tuple[str,str]]:
    try:
        soup = BeautifulSoup(html_text, "html.parser")
    except Exception:
        soup = BeautifulSoup(str(html_text).encode("utf-8", "ignore"), "html.parser")
    preferred_selectors = ["article a[href]", "main a[href]", ".news a[href]", ".press a[href]", ".article a[href]", "a[href]"]
    if "micron" in text_lower(source.get("name", "")) or "micron" in text_lower(base_url):
        preferred_selectors = [
            "a[href*='/news-releases/news-release-details/']",
            "main a[href]",
            "article a[href]",
            "a[href]"
        ]
    seen = set()
    links = []
    for selector in preferred_selectors:
        for a in soup.select(selector):
            href = a.get("href", "").strip()
            title = clean_text(a.get_text(" ", strip=True))
            if not href:
                continue
            absolute = urljoin(base_url, href)
            if absolute in seen:
                continue
            seen.add(absolute)
            if not is_allowed_domain(absolute, source.get("allowed_domains", [normalize_host(base_url)])):
                continue
            low = absolute.lower()
            if any(x in low for x in ["/privacy", "/contact", "/support", "/careers", "/login", "/search", "javascript:", "mailto:"]):
                continue
            if any(low.endswith(ext) for ext in [".pdf", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx"]):
                continue
            if len(title) < 6 and low.count('/') < 4:
                continue
            score = 0
            if normalize_host(absolute) == normalize_host(base_url):
                score += 6
            if any(w in low for w in ["news", "article", "press", "detail", "release", "ir"]):
                score += 5
            if low.count('/') >= 4:
                score += 3
            if any(ch.isdigit() for ch in low):
                score += 2
            if len(title) >= 20:
                score += 2
            links.append((f"{score:03d}", title, absolute))
        if links:
            break
    links.sort(reverse=True)
    return [(title, absolute) for _, title, absolute in links]

def infer_lang_from_source(source: dict) -> str:
    lang = source.get("lang")
    if lang:
        return lang
    name = text_lower(source.get("name", ""))
    url = text_lower(source.get("url", ""))
    if any(k in name for k in ["決算公告", "japan", "jp"]) or "en-jp" in url or "/jp" in url:
        return "ja"
    return "en"

def parse_datetime_flexible(value: str | None):
    if not value:
        return None
    value = value.strip()
    patterns = [
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
        "%Y/%m/%d", "%Y.%m.%d", "%b %d, %Y", "%B %d, %Y"
    ]
    for fmt in patterns:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

def fetch_with_optional_ssl_retry(url: str, **kwargs):
    verify = kwargs.pop("verify", True)
    allow_retry = kwargs.pop("allow_insecure_ssl_retry", False)
    try:
        return requests.get(url, verify=verify, **kwargs)
    except requests.exceptions.SSLError:
        if not allow_retry:
            raise
        return requests.get(url, verify=False, **kwargs)

def source_item_from_parts(source: dict, title: str, summary: str, link: str, pub_dt, full_text: str = ""):
    lang = detect_lang_from_text(title, summary) or infer_lang_from_source(source)
    name = source["name"]
    if pub_dt:
        pub_dt_jst = pub_dt.astimezone(JST)
        pub_str = pub_dt_jst.strftime("%Y-%m-%d %H:%M")
        pub_iso = pub_dt_jst.isoformat()
    else:
        pub_str = ""
        pub_iso = ""
    themes = classify_themes(title, summary, lang, themes_cfg_global, full_text=full_text, source_name=name)
    if themes == ["その他"]:
        themes = infer_theme_from_source(name)
    fx_impact = detect_fx_impact(title, summary, name, full_text)
    sentiment = detect_sentiment(title, summary, fx_impact, full_text)
    if fx_impact == "neutral":
        fx_impact = infer_importance_from_source(name)
    item = {
        "id": "item-" + hashlib.md5(link.encode()).hexdigest()[:12],
        "source": name,
        "lang": lang,
        "region": source.get("region", source.get("company", "")),
        "official": bool(source.get("official", source.get("type") == "official")),
        "title": title,
        "title_ja": "",
        "summary": summary[:300],
        "summary_ja": "",
        "link": link,
        "published": pub_str,
        "pub_dt": pub_iso,
        "themes": themes,
        "sentiment": sentiment,
        "fx_impact": fx_impact,
        "is_new": is_new(pub_iso),
        "score": 1,
    }
    item["score"] = calc_score(item)
    return item

def extract_page_items(source: dict, response_text: str, base_url: str) -> list[dict]:
    article_limit = int(source.get("article_limit", CONFIG.get("max_items_per_source", 20)))
    fetch_article = bool(source.get("fetch_article", True))
    items = []
    for title, href in extract_page_candidate_links(source, response_text, base_url):
        article = {"title": title, "summary": "", "text": "", "published_at": ""}
        if fetch_article:
            article = extract_article_text(href, allow_insecure_ssl_retry=bool(source.get("allow_insecure_ssl_retry", False)))
        final_title = clean_text(article.get("title") or title)
        final_summary = clean_text(article.get("summary") or article.get("text") or "")[:700]
        combined = f"{final_title} {final_summary} {href}"
        if not matches_source_keywords(combined, source):
            continue
        if is_image_or_gallery_item(final_title, final_summary, href):
            continue
        pub_dt = parse_datetime_flexible(article.get("published_at"))
        items.append(source_item_from_parts(source, final_title, final_summary, href, pub_dt, article.get("text", "")))
        if len(items) >= article_limit:
            break
    return items

# themes_cfg is injected into helper scope during fetch
themes_cfg_global = {}

def fetch_source(source: dict, themes_cfg: dict) -> tuple[list[dict], bool]:
    global themes_cfg_global
    themes_cfg_global = themes_cfg

    name = source["name"]
    url = source["url"]
    method = source.get("method", "rss")
    allowed_domains = source.get("allowed_domains", [normalize_host(url)])
    article_limit = int(source.get("article_limit", CONFIG.get("max_items_per_source", 20)))
    fetch_article = bool(source.get("fetch_article", True if method in {"page", "rss"} else False))

    log(f"  取得中: {name}")
    try:
        if method == "links":
            items = []
            for link_info in source.get("links", []):
                title = clean_text(link_info.get("title", ""))
                link = link_info.get("url", "")
                summary = clean_text(link_info.get("summary", ""))
                pub_dt = parse_datetime_flexible(link_info.get("published_at"))
                if not title or not link or not is_allowed_domain(link, allowed_domains):
                    continue
                if fetch_article:
                    article = extract_article_text(link, allow_insecure_ssl_retry=bool(source.get("allow_insecure_ssl_retry", False)))
                    title = clean_text(article.get("title") or title)
                    article_summary = clean_text(article.get("summary") or article.get("text") or "")
                    summary = article_summary[:700] if article_summary else summary
                    pub_dt = parse_datetime_flexible(article.get("published_at")) or pub_dt
                combined = f"{title} {summary} {link}"
                if not matches_source_keywords(combined, source):
                    continue
                items.append(source_item_from_parts(source, title, summary, link, pub_dt, article.get("text", "")))
            log(f"  ✓ {name}: {len(items)}件")
            return items[:article_limit], True

        resp = fetch_with_optional_ssl_retry(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,ja;q=0.8"},
            timeout=TIMEOUT,
            allow_insecure_ssl_retry=bool(source.get("allow_insecure_ssl_retry", False)),
        )
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or resp.encoding

        if method == "rss":
            feed = feedparser.parse(resp.content)
            items = []
            for entry in feed.entries:
                title = clean_text(getattr(entry, "title", ""))
                summary = clean_text(getattr(entry, "summary", getattr(entry, "description", "")))
                link = getattr(entry, "link", "")
                if not title or not link or not is_allowed_domain(link, allowed_domains):
                    continue
                pub_dt = None
                try:
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                        pub_dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
                if fetch_article:
                    article = extract_article_text(link, allow_insecure_ssl_retry=bool(source.get("allow_insecure_ssl_retry", False)))
                    title = clean_text(article.get("title") or title)
                    article_summary = clean_text(article.get("summary") or article.get("text") or "")
                    if article_summary:
                        summary = article_summary[:700]
                    pub_dt = parse_datetime_flexible(article.get("published_at")) or pub_dt
                combined = f"{title} {summary} {link}"
                if not matches_source_keywords(combined, source):
                    continue
                if is_image_or_gallery_item(title, summary, link):
                    continue
                items.append(source_item_from_parts(source, title, summary, link, pub_dt, article.get("text", "")))
                if len(items) >= article_limit:
                    break
            log(f"  ✓ {name}: {len(items)}件")
            return items, True

        if method == "page":
            page_text = resp.text
            if "micron" in text_lower(name):
                try:
                    page_text = resp.content.decode(resp.encoding or "utf-8", errors="ignore")
                except Exception:
                    page_text = resp.text
            items = extract_page_items(source, page_text, url)
            log(f"  ✓ {name}: {len(items)}件")
            return items, True

        log(f"  ⚠ {name}: 未対応 method={method}")
        return [], False
    except Exception as e:
        log(f"  ⚠ {name}: {e}")
        return [], False

def collect_all(sources: list[dict], themes_cfg: dict) -> tuple[list[dict], int, int]:
    all_items, ok, err = [], 0, 0
    max_workers = min(8, len(sources))
    log(f"  🚀 並列取得: {len(sources)} ソース / {max_workers} workers 同時実行")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_source, src, themes_cfg): src for src in sources}
        for f in as_completed(futures):
            src = futures[f]
            try:
                items, success = f.result()
                all_items.extend(items)
                if success: ok += 1
                else: err += 1
            except Exception as e:
                log(f"[WARN] {src.get('name','?')}: {e}")
                err += 1

    deduped = []
    deduped_pos = {}
    seen_links = set()
    buckets = {}

    for item in all_items:
        link = item.get('link', '')
        if link in seen_links:
            continue
        seen_links.add(link)

        group = source_group(item.get('source', ''))
        title_key = normalize_title_for_dedupe(item.get('title', ''))
        host = normalize_host(link)
        story = extract_story_source(item.get('title', '') + ' ' + item.get('summary', ''), link)
        soft_key = make_soft_title_key(title_key)

        core_key = make_core_title_key(title_key)

        candidate_keys = [
            f"{group}::{story}::{soft_key}",
            f"{group}::{host}::{soft_key}",
            f"global_story::{story}::{core_key}",
            f"global_host::{host}::{core_key}",
            f"global_title::{core_key}",
        ]

        matched_prev = None
        matched_bucket_key = None
        for bk in candidate_keys:
            for prev in buckets.get(bk, []):
                if is_probable_duplicate_item(item, prev):
                    matched_prev = prev
                    matched_bucket_key = bk
                    break
            if matched_prev is not None:
                break

        if matched_prev is not None:
            if item_rank(item) > item_rank(matched_prev):
                idx = deduped_pos[id(matched_prev)]
                deduped[idx] = item
                deduped_pos[id(item)] = idx
                del deduped_pos[id(matched_prev)]
                # replace in all buckets containing matched_prev
                for bk, vals in buckets.items():
                    for i, v in enumerate(vals):
                        if v is matched_prev:
                            vals[i] = item
            continue

        deduped.append(item)
        deduped_pos[id(item)] = len(deduped) - 1
        for bk in candidate_keys:
            buckets.setdefault(bk, []).append(item)

    deduped.sort(key=lambda x: (x.get('pub_dt', '') or '', x.get('score', 1)), reverse=True)
    log(f"\n✅ 合計 {len(all_items)} 件収集 → 重複除去後 {len(deduped)} 件 (成功:{ok} / エラー:{err})")
    return deduped, ok, err


# ─── CSS / JS共通 ─────────────────────────────────────────────────

def base_css() -> str:
    return """
:root{
  --bg:#0d1016;--panel:#151a23;--panel2:#1b2230;--line:#2a3446;
  --text:#ebf0f8;--muted:#9ba9bc;--accent:#4ea1ff;--accent2:#1e2f49;
  --ok:#1f6f53;--warn:#8a5a17;--bad:#7c3038;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:'Segoe UI',Meiryo,sans-serif}
a{color:inherit;text-decoration:none}
header{position:sticky;top:0;z-index:50;background:rgba(13,16,22,.96);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.wrap{max-width:1480px;margin:0 auto;padding:14px 20px}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.title h1{margin:0;font-size:22px;font-weight:800}
.title .sub{margin-top:4px;color:var(--muted);font-size:12px}
.right-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.page-lang-btn{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:12px;padding:9px 14px;font-size:13px;cursor:pointer;transition:all .15s}
.page-lang-btn.active{background:var(--accent2);border-color:var(--accent);color:#eaf4ff}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.nav a{display:inline-flex;align-items:center;gap:6px;text-decoration:none;border:1px solid rgba(255,255,255,.10);color:var(--text);background:rgba(255,255,255,.03);padding:10px 18px;border-radius:999px;font-size:13px;font-weight:700;white-space:nowrap;transition:all .18s ease}
.nav a:hover{border-color:rgba(255,255,255,.18);background:rgba(255,255,255,.06)}
.nav a.active{background:linear-gradient(135deg,#4ea1ff,#5a86ff);border-color:transparent;color:#fff;box-shadow:0 4px 16px rgba(78,161,255,.3)}
.main{max-width:1480px;margin:18px auto;padding:0 20px 40px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:16px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}
.stat .num{font-size:26px;font-weight:800;margin-bottom:4px}
.stat .label{color:var(--muted);font-size:12px}
.stat .icon{font-size:22px;margin-bottom:8px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;align-items:center}
.flt-btn{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:10px;padding:7px 13px;font-size:13px;cursor:pointer;transition:all .15s}
.flt-btn:hover{border-color:var(--accent)}
.flt-btn.active{background:var(--accent2);border-color:var(--accent);color:#eaf4ff}
.search{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:10px;padding:7px 12px;font-size:13px;min-width:220px}
.search::placeholder{color:var(--muted)}
.filter-label{color:var(--muted);font-size:12px;white-space:nowrap}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.grid.list{grid-template-columns:1fr}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:10px;transition:border-color .18s,opacity .18s}
.card:hover{border-color:var(--accent)}
.card.is-read{opacity:.45}
.card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.card-meta{display:flex;gap:6px;flex-wrap:wrap;color:var(--muted);font-size:12px;align-items:center}
.card-lang-toggle{display:flex;gap:5px;flex-shrink:0;align-items:center}
.card-lang-btn{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:8px;padding:4px 9px;font-size:12px;cursor:pointer;white-space:nowrap;transition:all .15s}
.card-lang-btn.active{background:var(--accent2);border-color:var(--accent);color:#eaf4ff}
.read-btn{background:var(--panel2);border:1px solid var(--line);color:var(--muted);border-radius:8px;padding:4px 9px;font-size:12px;cursor:pointer;transition:all .15s}
.read-btn:hover{border-color:#666}
.card h3{margin:0;font-size:15px;line-height:1.5;font-weight:700}
.summary{color:#ced7e6;font-size:13px;line-height:1.6}
.lang-pane.hidden{display:none!important}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:auto}
.tag{display:inline-block;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:600;border:1px solid transparent}
.tag-official{background:#173126;border-color:#2f7254;color:#c6f1dc}
.tag-score5,.tag-score4{background:#1e1a3a;border-color:#a78bfa;color:#e0d9ff}
.tag-score3{background:#1e2f49;border-color:#4ea1ff;color:#cae2ff}
.tag-score2{background:#2d2014;border-color:#8a6a31;color:#ffe0a2}
.tag-score1{background:#1e1e1e;border-color:#444;color:#aaa}
.tag-theme{font-size:11px;padding:3px 8px;border-radius:999px;border:1px solid transparent;font-weight:600}
.tag-new{background:#1a2e1a;border-color:#2ecc71;color:#2ecc71;font-weight:800;animation:pulse 1.5s infinite}
.tag-bullish{background:#1a2e1a;border-color:#2ecc71;color:#2ecc71}
.tag-bearish{background:#2e1a1a;border-color:#e74c3c;color:#e74c3c}
.tag-neutral{background:#1e1e2e;border-color:#636e72;color:#9ba9bc}
.tag-usd-strong{background:#1a2040;border-color:#4ea1ff;color:#a8d4ff}
.tag-usd-weak{background:#2e1a1a;border-color:#e74c3c;color:#ffb3b3}
.tag-jpy-strong{background:#1a2e1a;border-color:#2ecc71;color:#a8ffcc}
.tag-jpy-weak{background:#2e2a1a;border-color:#f39c12;color:#ffe0a2}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
.lang-badge{font-size:11px;padding:2px 6px;border-radius:4px;background:#2a3446;color:var(--muted)}
.lang-badge.ja{background:#173126;color:#c6f1dc}
.lang-badge.en{background:#1e2f49;color:#cae2ff}
.open-btn{display:inline-flex;justify-content:center;align-items:center;background:var(--accent2);border:1px solid var(--accent);color:#eaf4ff;border-radius:10px;padding:9px 14px;font-size:13px;font-weight:700;margin-top:4px;transition:all .15s}
.open-btn:hover{background:#2a4a80}
.theme-section{margin-bottom:28px}
.theme-header{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.theme-header h2{margin:0;font-size:17px;font-weight:800}
.theme-badge{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:999px;font-size:13px;font-weight:700;border:1px solid transparent}
.cb-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.cb-card{background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:14px}
.cb-card .cb-name{font-weight:800;font-size:15px;margin-bottom:8px}
.cb-card .cb-count{font-size:22px;font-weight:800;color:var(--accent)}
.cb-card .cb-recent{font-size:12px;color:var(--muted);margin-top:6px}
footer{max-width:1480px;margin:0 auto;padding:0 20px 28px;color:var(--muted);font-size:12px}
@media(max-width:700px){
  .wrap,.main,footer{padding-left:12px;padding-right:12px}
  .grid{grid-template-columns:1fr}
  .search{min-width:160px;width:100%}
  .card-top{flex-direction:column}
}
"""


def page_lang_js() -> str:
    return """
<script>
var pageLang = 'en';
function setPageLang(lang) {
  pageLang = lang;
  document.getElementById('btnPageEN').classList.toggle('active', lang === 'en');
  document.getElementById('btnPageJA').classList.toggle('active', lang === 'ja');
  document.querySelectorAll('.item-card').forEach(function(card) {
    if ((card.dataset.lang || 'en') === 'ja') return;
    var enPane = card.querySelector('.lang-en');
    var jaPane = card.querySelector('.lang-ja');
    var enBtn = card.querySelector('.card-lang-btn[data-l="en"]');
    var jaBtn = card.querySelector('.card-lang-btn[data-l="ja"]');
    if (!enPane || !jaPane) return;
    if (lang === 'ja') {
      enPane.classList.add('hidden'); jaPane.classList.remove('hidden');
      if(enBtn) enBtn.classList.remove('active');
      if(jaBtn) jaBtn.classList.add('active');
    } else {
      enPane.classList.remove('hidden'); jaPane.classList.add('hidden');
      if(enBtn) enBtn.classList.add('active');
      if(jaBtn) jaBtn.classList.remove('active');
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
  if (!enPane || !jaPane) return;
  if (lang === 'ja') { enPane.classList.add('hidden'); jaPane.classList.remove('hidden'); }
  else { enPane.classList.remove('hidden'); jaPane.classList.add('hidden'); }
}
</script>
"""


def read_js() -> str:
    return """
<script>
var READ_KEY = 'memory_information_suite_read';
function getReadSet() {
  try { return new Set(JSON.parse(localStorage.getItem(READ_KEY) || '[]')); } catch(e) { return new Set(); }
}
function saveReadSet(s) { try { localStorage.setItem(READ_KEY, JSON.stringify([...s])); } catch(e) {} }
function markRead(btn, cardId) {
  var card = btn.closest('.item-card');
  var rs = getReadSet();
  if (rs.has(cardId)) { rs.delete(cardId); card.classList.remove('is-read'); btn.textContent = '既読'; }
  else { rs.add(cardId); card.classList.add('is-read'); btn.textContent = '未読に戻す'; }
  saveReadSet(rs);
}
(function applyRead() {
  var rs = getReadSet();
  document.querySelectorAll('.item-card[data-cardid]').forEach(function(card) {
    if (rs.has(card.dataset.cardid)) {
      card.classList.add('is-read');
      var btn = card.querySelector('.read-btn');
      if (btn) btn.textContent = '未読に戻す';
    }
  });
})();
</script>
"""


def header_html(active: str, stamp: str) -> str:
    nav_items = [
        ("index.html",        "📋 メイン"),
        ("pickup.html",       "⭐ 注目記事"),
        ("focus.html",    "🏷 需給タグ"),
        ("themes.html",        "🏷 テーマ別"),
        ("sources.html", "🏛 公式ソース"),
        ("analysis.html",     "📊 分析"),
    ]
    nav = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{label}</a>'
        for href, label in nav_items
    )
    return f"""<!doctype html>
<html lang='ja'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{html.escape(APP_NAME)} - {html.escape(active)}</title>
<style>{base_css()}</style>
</head>
<body>
<header>
<div class='wrap'>
  <div class='top'>
    <div class='title'>
      <h1>💾 {html.escape(APP_NAME)} <span style='font-weight:400;color:var(--muted);font-size:16px'>｜ {html.escape(CONFIG.get("subtitle", ""))}</span></h1>
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


def footer_html(stamp: str) -> str:
    return f"<footer>{html.escape(APP_NAME)} — Generated at {stamp}</footer></body></html>"



IMPORTANCE_MAP = {
    "price_up": ("📈 価格上昇", "tag-usd-strong"),
    "price_down": ("📉 価格下落", "tag-usd-weak"),
    "demand_up": ("📦 需要増", "tag-jpy-strong"),
    "supply_down": ("🛑 供給減", "tag-jpy-weak"),
    "neutral": ("➖ その他", "tag-neutral"),
}

SENT_MAP = {
    "bullish": ("✅ 追い風", "tag-bullish"),
    "bearish": ("⚠ 重し", "tag-bearish"),
    "neutral": ("➖ Neutral", "tag-neutral"),
}


def card_html(item: dict, themes_cfg: dict) -> str:
    card_id     = html.escape(item.get("id", ""))
    title_orig  = html.escape(item.get("title", ""))
    title_ja    = html.escape(item.get("title_ja", "") or item.get("title", ""))
    summary_orig= html.escape(item.get("summary", ""))
    summary_ja  = html.escape(item.get("summary_ja", "") or item.get("summary", ""))
    source      = html.escape(item.get("source", ""))
    published   = html.escape(item.get("published", ""))
    link        = html.escape(item.get("link", "#"))
    lang        = item.get("lang", "en")
    score       = item.get("score", 1)
    is_official = item.get("official", False)
    themes      = item.get("themes", [])
    sentiment   = item.get("sentiment", "neutral")
    fx_impact   = item.get("fx_impact", "neutral")
    new_flag    = item.get("is_new", False)

    theme_tags = ""
    for t in themes:
        if t == "その他": continue
        td    = themes_cfg["themes"].get(t, {})
        color = td.get("color", "#636e72")
        icon  = td.get("icon", "")
        theme_tags += f"<span class='tag tag-theme' style='background:{color}22;border-color:{color};color:{color}'>{icon} {html.escape(t)}</span>"

    sent_label, sent_cls = SENT_MAP.get(sentiment, ("➖ Neutral", "tag-neutral"))
    fx_label,   fx_cls   = IMPORTANCE_MAP.get(fx_impact, ("➖ その他", "tag-neutral"))
    score_cls = f"tag-score{min(score,5)}"
    new_tag      = "<span class='tag tag-new'>🆕 NEW</span>" if new_flag else ""
    official_tag = "<span class='tag tag-official'>🏛 公式</span>" if is_official else ""
    lang_badge   = f"<span class='lang-badge {'ja' if lang=='ja' else 'en'}'>{'🇯🇵 日本語' if lang=='ja' else '🌐 English'}</span>"

    if lang == "ja":
        panes       = f"<div class='lang-pane lang-ja'><h3>{title_orig}</h3><div class='summary'>{summary_orig}</div></div>"
        lang_toggle = f"<div class='card-lang-toggle'><button class='read-btn' onclick=\"markRead(this,'{card_id}')\">既読</button></div>"
    else:
        panes       = f"""<div class='lang-pane lang-en'><h3>{title_orig}</h3><div class='summary'>{summary_orig}</div></div>
<div class='lang-pane lang-ja hidden'><h3>{title_ja}</h3><div class='summary'>{summary_ja}</div></div>"""
        lang_toggle = f"""<div class='card-lang-toggle'>
  <button class='card-lang-btn active' data-l='en' onclick="switchCardLang(this,'en')">🌐 EN</button>
  <button class='card-lang-btn' data-l='ja' onclick="switchCardLang(this,'ja')">🇯🇵 JA</button>
  <button class='read-btn' onclick="markRead(this,'{card_id}')">既読</button>
</div>"""

    return f"""
<article class='card item-card'
  data-cardid='{card_id}'
  data-bucket='{detect_bucket(item)}'
  data-themes='{"|".join(themes)}'
  data-lang='{html.escape(lang)}'
  data-score='{score}'
  data-source='{source}'
  data-sentiment='{html.escape(sentiment)}'
  data-importance='{html.escape(fx_impact)}'
  data-isnew='{"true" if new_flag else "false"}'>
  <div class='card-top'>
    <div class='card-meta'>{new_tag}{lang_badge}{official_tag}<span>{source}</span><span>{published}</span></div>
    {lang_toggle}
  </div>
  {panes}
  <div class='tags'>{theme_tags}<span class='tag {fx_cls}'>{fx_label}</span><span class='tag {sent_cls}'>{sent_label}</span><span class='tag {score_cls}'>★{score}</span></div>
  <a class='open-btn' href='{link}' target='_blank' rel='noopener noreferrer'>記事を開く →</a>
</article>"""


# ─── 重要タグページ ───────────────────────────────────────────────

def render_focus(items: list[dict], themes_cfg: dict) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

    cats = {
        "price_up": [x for x in items if x.get("fx_impact") == "price_up"],
        "price_down":   [x for x in items if x.get("fx_impact") == "price_down"],
        "demand_up": [x for x in items if x.get("fx_impact") == "demand_up"],
        "supply_down":   [x for x in items if x.get("fx_impact") == "supply_down"],
    }
    neutral_count = sum(1 for x in items if x.get("fx_impact") == "neutral")

    tab_cfg = [
        ("price_up", "📈 価格上昇", "#4ea1ff", "#0d1e35"),
        ("price_down",   "📉 価格下落", "#e74c3c", "#2e0d0d"),
        ("demand_up", "📦 需要増",   "#2ecc71", "#0d2e1a"),
        ("supply_down",   "🛑 供給減",   "#f39c12", "#2e2400"),
    ]
    tab_meta = {k: {"color": c, "bg": bg} for k, _, c, bg in tab_cfg}

    fx_css = """
<style>
.fx-summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:24px}
.fx-summary-card{border-radius:16px;padding:18px;text-align:center;border:1px solid transparent}
.fx-summary-card .fx-num{font-size:36px;font-weight:900;margin-bottom:6px}
.fx-summary-card .fx-label{font-size:14px;font-weight:700}
.fx-summary-card .fx-desc{font-size:11px;color:var(--muted);margin-top:4px}
.fx-usd-strong{background:#0d1e35;border-color:#4ea1ff}
.fx-usd-weak{background:#2e0d0d;border-color:#e74c3c}
.fx-jpy-strong{background:#0d2e1a;border-color:#2ecc71}
.fx-jpy-weak{background:#2e2400;border-color:#f39c12}
.fx-tabs{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.fx-tab{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:12px;padding:10px 18px;font-size:14px;font-weight:700;cursor:pointer;transition:all .18s}
.fx-tab.active{border-color:transparent;color:#fff;box-shadow:0 4px 16px rgba(0,0,0,.3)}
.fx-panel{display:none}.fx-panel.active{display:block}
</style>"""

    summary_cards = f"""
<div class='fx-summary-grid'>
  <div class='fx-summary-card fx-usd-strong'>
    <div class='fx-num' style='color:#4ea1ff'>{len(cats["price_up"] )}</div>
    <div class='fx-label'>📈 価格上昇</div>
    <div class='fx-desc'>Pricing pressure / ASP</div>
  </div>
  <div class='fx-summary-card fx-usd-weak'>
    <div class='fx-num' style='color:#e74c3c'>{len(cats["price_down"] )}</div>
    <div class='fx-label'>📉 価格下落</div>
    <div class='fx-desc'>Downside / oversupply</div>
  </div>
  <div class='fx-summary-card fx-jpy-strong'>
    <div class='fx-num' style='color:#2ecc71'>{len(cats["demand_up"] )}</div>
    <div class='fx-label'>📦 需要増</div>
    <div class='fx-desc'>Demand recovery / AI</div>
  </div>
  <div class='fx-summary-card fx-jpy-weak'>
    <div class='fx-num' style='color:#f39c12'>{len(cats["supply_down"] )}</div>
    <div class='fx-label'>🛑 供給減</div>
    <div class='fx-desc'>Supply cut / tightness</div>
  </div>
  <div class='fx-summary-card' style='background:var(--panel2);border-color:var(--line)'>
    <div class='fx-num' style='color:var(--muted)'>{neutral_count}</div>
    <div class='fx-label'>➖ その他</div>
    <div class='fx-desc'>General</div>
  </div>
</div>"""

    tabs_html = ""
    panels_html = ""
    for i, (key, label, color, bg) in enumerate(tab_cfg):
        active = "active" if i == 0 else ""
        style = f"background:{bg};border-color:{color};color:#fff" if active else ""
        tabs_html += f"<button class='fx-tab {active}' data-key='{key}' style='{style}' onclick=\"switchFxTab('{key}',this)\">{label} ({len(cats[key])})</button>"
        cards = "".join(card_html(it, themes_cfg) for it in cats[key]) if cats[key] else "<div style='color:var(--muted);padding:20px'>該当記事なし</div>"
        panels_html += f"<div class='fx-panel {active}' id='fx-{key}'><div class='grid'>{cards}</div></div>"

    body = f"""
{fx_css}
<div class='main'>
  {summary_cards}
  <div class='panel'>
    <div class='filters'>
      <span class='filter-label'>言語:</span>
      <button class='flt-btn flt-imp-lang active' data-lang='all'>🌍 全部</button>
      <button class='flt-btn flt-imp-lang' data-lang='ja'>🇯🇵 日本語</button>
      <button class='flt-btn flt-imp-lang' data-lang='en'>🌐 English</button>
      <input id='searchBoxImportance' class='search' placeholder='タイトル・本文を検索...'>
    </div>
  </div>
  <div class='fx-tabs'>{tabs_html}</div>
  {panels_html}
</div>
<script>
const FX_TAB_META = {json.dumps(tab_meta, ensure_ascii=False)};
(function(){{
  var impLang = 'all', impSearch = '';
  function applyImportanceFilters(){{
    document.querySelectorAll('.fx-panel .item-card').forEach(function(card){{
      var show = true;
      if (impLang !== 'all' && (card.dataset.lang || '') !== impLang) show = false;
      if (impSearch && !(card.textContent || '').toLowerCase().includes(impSearch.toLowerCase())) show = false;
      card.style.display = show ? '' : 'none';
    }});
  }}
  document.querySelectorAll('.flt-imp-lang').forEach(function(btn){{
    btn.addEventListener('click', function(){{
      document.querySelectorAll('.flt-imp-lang').forEach(function(b){{ b.classList.remove('active'); }});
      btn.classList.add('active');
      impLang = btn.dataset.lang;
      applyImportanceFilters();
    }});
  }});
  var sb = document.getElementById('searchBoxImportance');
  if (sb) sb.addEventListener('input', function(){{ impSearch = this.value; applyImportanceFilters(); }});
  applyImportanceFilters();
}})();
function switchFxTab(key, btn) {{
  document.querySelectorAll('.fx-panel').forEach(function(p){{ p.classList.remove('active'); }});
  document.querySelectorAll('.fx-tab').forEach(function(b){{
    b.classList.remove('active');
    b.style.background='';
    b.style.borderColor='var(--line)';
    b.style.color='';
  }});
  document.getElementById('fx-' + key).classList.add('active');
  btn.classList.add('active');
  var meta = FX_TAB_META[key] || null;
  if (meta) {{
    btn.style.background = meta.bg;
    btn.style.borderColor = meta.color;
    btn.style.color = '#fff';
  }}
}}
</script>"""

    return header_html("focus.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


# ─── メインページ ─────────────────────────────────────────────────

def filter_js(sources: list[str]) -> str:
    source_btns = "".join(
        f"<button class='flt-btn flt-source' data-source='{html.escape(s)}'>{html.escape(s)}</button>"
        for s in sources
    )
    return f"""
<div class='filters' id='sourceFilterRow' style='margin-top:4px'>
  <span class='filter-label'>ソース:</span>
  <button class='flt-btn flt-source active' data-source='all'>すべて</button>
  {source_btns}
</div>
<script>
(function(){{
  var fTheme='all',fBucket='all',fLang='all',fScore='all',fView='cards',fSource='all',fSentiment='all',fFx='all',fNew=false,search='';
  function applyFilters(){{
    document.querySelectorAll('.item-card').forEach(function(card){{
      var show=true;
      if(fTheme!=='all' && !(card.dataset.themes||'').split('|').includes(fTheme)) show=false;
      if(fBucket!=='all' && (card.dataset.bucket||'')!==fBucket) show=false;
      if(fLang!=='all' && (card.dataset.lang||'')!==fLang) show=false;
      if(fScore!=='all' && parseInt(card.dataset.score||'1')<parseInt(fScore)) show=false;
      if(fSource!=='all' && (card.dataset.source||'')!==fSource) show=false;
      if(fSentiment!=='all' && (card.dataset.sentiment||'')!==fSentiment) show=false;
      if(fFx!=='all' && (card.dataset.importance||'')!==fFx) show=false;
      if(fNew && card.dataset.isnew!=='true') show=false;
      if(search && !(card.textContent||'').toLowerCase().includes(search.toLowerCase())) show=false;
      card.style.display=show?'':'none';
    }});
    var grid=document.getElementById('itemGrid');
    if(grid) grid.classList.toggle('list',fView==='list');
  }}
  function bindGroup(sel,key){{
    document.querySelectorAll(sel).forEach(function(btn){{
      btn.addEventListener('click',function(){{
        document.querySelectorAll(sel).forEach(function(b){{b.classList.remove('active');}});
        btn.classList.add('active');
        if(key==='fTheme') fTheme=btn.dataset.theme;
        else if(key==='fBucket') fBucket=btn.dataset.bucket;
        else if(key==='fLang') fLang=btn.dataset.lang;
        else if(key==='fScore') fScore=btn.dataset.score;
        else if(key==='fView') fView=btn.dataset.view;
        else if(key==='fSource') fSource=btn.dataset.source;
        else if(key==='fSentiment') fSentiment=btn.dataset.sentiment;
        else if(key==='fFx') fFx=btn.dataset.fx;
        applyFilters();
      }});
    }});
  }}
  bindGroup('.flt-theme','fTheme');
  bindGroup('.flt-bucket','fBucket');
  bindGroup('.flt-lang','fLang');
  bindGroup('.flt-score','fScore');
  bindGroup('.flt-view','fView');
  bindGroup('.flt-source','fSource');
  bindGroup('.flt-sentiment','fSentiment');
  bindGroup('.flt-fx','fFx');
  var newBtn=document.getElementById('btnNewOnly');
  if(newBtn) newBtn.addEventListener('click',function(){{
    fNew=!fNew; newBtn.classList.toggle('active',fNew); applyFilters();
  }});
  var sb=document.getElementById('searchBox');
  if(sb) sb.addEventListener('input',function(){{search=this.value;applyFilters();}});
}})();
</script>"""




def bucket_definitions() -> list[dict]:
    return [
        {
            "key": "dram",
            "label": "DRAM",
            "icon": "🧠",
            "desc": "DRAM / DDR / LPDDR / モジュール系",
            "match_themes": {"DRAM・DDR"},
            "keywords": ["dram", "ddr", "lpddr", "rdimm", "udimm", "module", "dram module"],
        },
        {
            "key": "nand_ssd",
            "label": "NAND/SSD",
            "icon": "💽",
            "desc": "NAND / SSD / Flash / Storage 系",
            "match_themes": {"NAND・SSD", "モジュール・チャネル"},
            "keywords": ["nand", "ssd", "flash", "storage", "nvme", "ufs", "pcie", "bics"],
        },
        {
            "key": "hbm",
            "label": "HBM",
            "icon": "🤖",
            "desc": "HBM / AI server / GPU / Accelerator 系",
            "match_themes": {"HBM・AI"},
            "keywords": ["hbm", "high bandwidth memory", "ai server", "gpu", "accelerator", "ai demand"],
        },
        {
            "key": "market_company",
            "label": "Market/Company",
            "icon": "🏭",
            "desc": "価格・需給・設備投資・決算など全体動向",
            "match_themes": {"価格・市況", "需給・在庫", "設備投資・工場", "決算・IR"},
            "keywords": ["price", "pricing", "market", "demand", "supply", "inventory", "capex", "investment", "earnings", "financial", "guidance"],
        },
    ]


def detect_bucket(item: dict) -> str:
    themes = set(item.get("themes", []))
    combined = text_lower(f"{item.get('title','')} {item.get('summary','')}")
    defs = bucket_definitions()
    priority = ["hbm", "dram", "nand_ssd", "market_company"]
    for key in priority:
        bd = next(x for x in defs if x["key"] == key)
        if themes & bd["match_themes"]:
            return key
        if any(k in combined for k in bd["keywords"]):
            return key
    return "market_company"


def bucket_meta_map() -> dict:
    return {x["key"]: x for x in bucket_definitions()}

def render_index(items: list[dict], themes_cfg: dict) -> str:
    stamp  = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    total  = len(items)
    new_count     = sum(1 for x in items if x.get("is_new"))
    official_count= sum(1 for x in items if x.get("official"))
    price_up = sum(1 for x in items if x.get("fx_impact") == "price_up")
    price_down   = sum(1 for x in items if x.get("fx_impact") == "price_down")
    demand_up = sum(1 for x in items if x.get("fx_impact") == "demand_up")
    supply_down   = sum(1 for x in items if x.get("fx_impact") == "supply_down")

    theme_counts = Counter()
    for item in items:
        for t in item.get("themes", []): theme_counts[t] += 1

    source_list = sorted(set(x.get("source","") for x in items))
    bucket_meta = bucket_meta_map()
    bucket_counts = Counter(detect_bucket(x) for x in items)
    bucket_btns = "<button class='flt-btn flt-bucket active' data-bucket='all'>🧩 全区分</button>"
    for key in ["dram", "nand_ssd", "hbm", "market_company"]:
        meta = bucket_meta[key]
        bucket_btns += f"<button class='flt-btn flt-bucket' data-bucket='{key}'>{meta['icon']} {meta['label']} ({bucket_counts.get(key,0)})</button>"

    theme_btns = "<button class='flt-btn flt-theme active' data-theme='all'>📋 すべて</button>"
    for tk, td in themes_cfg["themes"].items():
        cnt   = theme_counts.get(tk, 0)
        color = td.get("color", "#636e72")
        icon  = td.get("icon", "")
        theme_btns += f"<button class='flt-btn flt-theme' data-theme='{html.escape(tk)}'>{icon} {html.escape(tk)} ({cnt})</button>"

    cards_html = "".join(card_html(item, themes_cfg) for item in items)

    stat_grid = f"""
<div class='stat-grid'>
  <div class='stat'><div class='icon'>📰</div><div class='num'>{total}</div><div class='label'>総記事数</div></div>
  <div class='stat'><div class='icon'>🆕</div><div class='num' style='color:#2ecc71'>{new_count}</div><div class='label'>直近{NEW_HOURS}h 新着</div></div>
  <div class='stat'><div class='icon'>🟢</div><div class='num' style='color:#4ea1ff'>{price_up}</div><div class='label'>価格上昇</div></div>
  <div class='stat'><div class='icon'>🔴</div><div class='num' style='color:#e74c3c'>{price_down}</div><div class='label'>価格下落</div></div>
  <div class='stat'><div class='icon'>🔵</div><div class='num' style='color:#2ecc71'>{demand_up}</div><div class='label'>需要増</div></div>
  <div class='stat'><div class='icon'>🟡</div><div class='num' style='color:#f39c12'>{supply_down}</div><div class='label'>供給減</div></div>
  <div class='stat'><div class='icon'>🏛</div><div class='num'>{official_count}</div><div class='label'>公式ソース</div></div>
</div>"""

    return (
        header_html("index.html", stamp)
        + f"""
<div class='main'>
{stat_grid}
<div class='panel'>
  <div class='filters'>{bucket_btns}</div><div class='filters'>{theme_btns}</div>
  <div class='filters'>
    <span class='filter-label'>言語:</span>
    <button class='flt-btn flt-lang active' data-lang='all'>🌍 全部</button>
    <button class='flt-btn flt-lang' data-lang='ja'>🇯🇵 日本語</button>
    <button class='flt-btn flt-lang' data-lang='en'>🌐 English</button>
    <span class='filter-label'>重要タグ:</span>
    <button class='flt-btn flt-fx active' data-fx='all'>全部</button>
    <button class='flt-btn flt-fx' data-fx='price_up'>📈 上昇</button>
    <button class='flt-btn flt-fx' data-fx='price_down'>📉 下落</button>
    <button class='flt-btn flt-fx' data-fx='demand_up'>📦 需要増</button>
    <button class='flt-btn flt-fx' data-fx='supply_down'>🛑 供給減</button>
  </div>
  <div class='filters'>
    <span class='filter-label'>参考度:</span>
    <button class='flt-btn flt-sentiment active' data-sentiment='all'>全部</button>
    <button class='flt-btn flt-sentiment' data-sentiment='bullish'>✅ 参考</button>
    <button class='flt-btn flt-sentiment' data-sentiment='bearish'>⚠ 注意</button>
    <span class='filter-label'>スコア:</span>
    <button class='flt-btn flt-score active' data-score='all'>全部</button>
    <button class='flt-btn flt-score' data-score='4'>★4以上</button>
    <button class='flt-btn flt-score' data-score='3'>★3以上</button>
    <button class='flt-btn flt-view active' data-view='cards'>▦ カード</button>
    <button class='flt-btn flt-view' data-view='list'>≡ リスト</button>
    <button class='flt-btn' id='btnNewOnly'>🆕 新着のみ</button>
    <input id='searchBox' class='search' placeholder='タイトル・本文を検索...'>
  </div>
  {filter_js(source_list)}
</div>
<div class='grid' id='itemGrid'>{cards_html}</div>
</div>"""
        + footer_html(stamp)
        + page_lang_js()
        + read_js()
    )


# ─── テーマ別ページ ───────────────────────────────────────────────

def render_themes(items: list[dict], themes_cfg: dict) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    bucket_meta = bucket_meta_map()
    bucket_items = {key: [] for key in bucket_meta.keys()}
    for item in items:
        bucket_items[detect_bucket(item)].append(item)

    sections_html = ""
    bucket_btns = "<button class='flt-btn flt-bucket-sec active' data-sec='all'>📋 全区分</button>"
    order = ["dram", "nand_ssd", "hbm", "market_company"]
    for key in order:
        meta = bucket_meta[key]
        sec_items = bucket_items.get(key, [])
        if not sec_items:
            continue
        bucket_btns += f"<button class='flt-btn flt-bucket-sec' data-sec='{key}'>{meta['icon']} {html.escape(meta['label'])} ({len(sec_items)})</button>"
        cards = "".join(card_html(it, themes_cfg) for it in sec_items)
        sections_html += f"""
<div class='theme-section' data-section='{key}'>
  <div class='theme-header'>
    <span class='theme-badge'>{meta['icon']} {html.escape(meta['label'])}</span>
    <span style='color:var(--muted);font-size:13px'>{html.escape(meta['desc'])} · <span class='section-count'>{len(sec_items)}</span>件</span>
  </div>
  <div class='grid theme-grid'>{cards}</div>
</div>"""

    filter_bar = (
        "<div class='panel'>"
        "<div class='filters'><span class='filter-label'>区分:</span>"
        + bucket_btns +
        "</div>"
        "<div class='filters'>"
        "<span class='filter-label'>言語:</span>"
        "<button class='flt-btn flt-lang active' data-lang='all'>🌍 全部</button>"
        "<button class='flt-btn flt-lang' data-lang='ja'>🇯🇵 日本語</button>"
        "<button class='flt-btn flt-lang' data-lang='en'>🌐 English</button>"
        "<span class='filter-label'>重要タグ:</span>"
        "<button class='flt-btn flt-fx-t active' data-fx='all'>全部</button>"
        "<button class='flt-btn flt-fx-t' data-fx='price_up'>📈 上昇</button>"
        "<button class='flt-btn flt-fx-t' data-fx='price_down'>📉 下落</button>"
        "<button class='flt-btn flt-fx-t' data-fx='demand_up'>📦 需要増</button>"
        "<button class='flt-btn flt-fx-t' data-fx='supply_down'>🛑 供給減</button>"
        "<input id='searchBoxTheme' class='search' placeholder='タイトル・本文を検索...'>"
        "</div></div>"
        """<script>
(function(){
  var fSec='all', fLang='all', fFx='all', search='';
  function applyThemeFilters(){
    document.querySelectorAll('.theme-section').forEach(function(sec){
      var secKey = sec.getAttribute('data-section') || '';
      if(fSec !== 'all' && secKey !== fSec){ sec.style.display='none'; return; }
      var visible = 0;
      sec.querySelectorAll('.item-card').forEach(function(card){
        var show = true;
        if(fLang !== 'all' && (card.dataset.lang || '') !== fLang) show = false;
        if(fFx !== 'all' && (card.dataset.importance || '') !== fFx) show = false;
        if(search && !(card.textContent || '').toLowerCase().includes(search.toLowerCase())) show = false;
        card.style.display = show ? '' : 'none';
        if(show) visible++;
      });
      sec.style.display = visible > 0 ? '' : 'none';
      var cnt = sec.querySelector('.section-count');
      if(cnt) cnt.textContent = visible;
    });
  }
  document.querySelectorAll('.flt-bucket-sec').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('.flt-bucket-sec').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active');
      fSec = btn.dataset.sec;
      applyThemeFilters();
    });
  });
  document.querySelectorAll('.flt-lang').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('.flt-lang').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active'); fLang = btn.dataset.lang; applyThemeFilters();
    });
  });
  document.querySelectorAll('.flt-fx-t').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('.flt-fx-t').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active'); fFx = btn.dataset.fx; applyThemeFilters();
    });
  });
  var sb = document.getElementById('searchBoxTheme');
  if(sb) sb.addEventListener('input', function(){ search = this.value; applyThemeFilters(); });
})();
</script>"""
    )

    body = f"<div class='main'>{filter_bar}{sections_html}</div>"
    return header_html("themes.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


# ─── 公式ソースページ ───────────────────────────────────────────────

def render_sources(items: list[dict], themes_cfg: dict) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    source_meta = {
        "Samsung Global Newsroom": {"icon": "🏛", "desc": "Samsung公式ニュース"},
        "Micron Investor Relations - Quarterly Results": {"icon": "🏛", "desc": "Micron決算・ガイダンス"},
        "Micron Investor Relations - News": {"icon": "🏛", "desc": "Micron投資家向けニュース"},
        "Kioxia Investor Relations": {"icon": "🏛", "desc": "Kioxia IR・財務情報"},
        "Kioxia News": {"icon": "🏛", "desc": "Kioxia公式ニュース"},
        "Kingston Press Highlights": {"icon": "🏛", "desc": "Kingstonプレスリリース"},
        "ADATA Investor Financial": {"icon": "🏛", "desc": "ADATA月次・財務情報"},
        "ADATA News": {"icon": "🏛", "desc": "ADATA公式ニュース"},
        "G.SKILL Press": {"icon": "🏛", "desc": "G.SKILLプレス情報"},
        "CFD Corporate": {"icon": "🏛", "desc": "CFDコーポレート情報"},
        "CFD 決算公告": {"icon": "🏛", "desc": "CFD決算公告"},
    }

    display_sources = []
    for src_name, meta in source_meta.items():
        src_items = [x for x in items if x.get("source") == src_name]
        latest = src_items[0].get("published", "記事なし") if src_items else "記事なし"
        display_sources.append((src_name, meta, src_items, latest))

    cb_grid_html = ""
    for src_name, meta, src_items, latest in display_sources:
        cb_grid_html += f"""
<div class='cb-card'>
  <div class='cb-name'>{meta['icon']} {html.escape(src_name)}</div>
  <div style='color:var(--muted);font-size:12px;margin-bottom:8px'>{html.escape(meta['desc'])}</div>
  <div class='cb-count'>{len(src_items)}</div>
  <div style='color:var(--muted);font-size:11px'>件の記事</div>
  <div class='cb-recent'>最新: {html.escape(latest)}</div>
</div>"""

    cb_items = [x for x in items if x.get("official") or x.get("source") in source_meta]
    cards_html = "".join(card_html(it, themes_cfg) for it in cb_items)

    cb_filter = """
<div class='panel'>
  <div class='filters'>
    <span class='filter-label'>公式ソース:</span>
    <button class='flt-btn flt-cb-bank active' data-bank='all'>💾 全部</button>
    <button class='flt-btn flt-cb-bank' data-bank='Samsung Global Newsroom'>Samsung</button>
    <button class='flt-btn flt-cb-bank' data-bank='Micron Investor Relations - Quarterly Results'>Micron 決算</button>
    <button class='flt-btn flt-cb-bank' data-bank='Micron Investor Relations - News'>Micron News</button>
    <button class='flt-btn flt-cb-bank' data-bank='Kioxia Investor Relations'>Kioxia IR</button>
    <button class='flt-btn flt-cb-bank' data-bank='Kioxia News'>Kioxia News</button>
    <button class='flt-btn flt-cb-bank' data-bank='Kingston Press Highlights'>Kingston</button>
    <button class='flt-btn flt-cb-bank' data-bank='ADATA Investor Financial'>ADATA IR</button>
    <button class='flt-btn flt-cb-bank' data-bank='ADATA News'>ADATA News</button>
    <button class='flt-btn flt-cb-bank' data-bank='G.SKILL Press'>G.SKILL</button>
    <button class='flt-btn flt-cb-bank' data-bank='CFD Corporate'>CFD Corporate</button>
    <button class='flt-btn flt-cb-bank' data-bank='CFD 決算公告'>CFD 決算</button>
  </div>
  <div class='filters'>
    <span class='filter-label'>言語:</span>
    <button class='flt-btn flt-lang-cb active' data-lang='all'>🌍 全部</button>
    <button class='flt-btn flt-lang-cb' data-lang='ja'>🇯🇵 日本語</button>
    <button class='flt-btn flt-lang-cb' data-lang='en'>🌐 English</button>
    <span class='filter-label'>重要タグ:</span>
    <button class='flt-btn flt-fx-cb active' data-fx='all'>全部</button>
    <button class='flt-btn flt-fx-cb' data-fx='price_up'>📈 上昇</button>
    <button class='flt-btn flt-fx-cb' data-fx='price_down'>📉 下落</button>
    <button class='flt-btn flt-fx-cb' data-fx='demand_up'>📦 需要増</button>
    <button class='flt-btn flt-fx-cb' data-fx='supply_down'>🛑 供給減</button>
    <button class='flt-btn flt-view-cb active' data-view='cards'>▦ カード</button>
    <button class='flt-btn flt-view-cb' data-view='list'>≡ リスト</button>
    <input id='searchBoxCB' class='search' placeholder='タイトル・本文を検索...'>
  </div>
</div>
<script>
(function(){
  var fBank='all', fLang='all', fFx='all', fView='cards', search='';
  function apply(){
    var visible=0;
    document.querySelectorAll('#cbGrid .item-card').forEach(function(card){
      var show=true;
      if(fBank!=='all' && (card.dataset.source||'')!==fBank) show=false;
      if(fLang!=='all' && (card.dataset.lang||'')!==fLang) show=false;
      if(fFx!=='all' && (card.dataset.importance||'')!==fFx) show=false;
      if(search && !(card.textContent||'').toLowerCase().includes(search.toLowerCase())) show=false;
      card.style.display=show?'':'none';
      if(show) visible++;
    });
    document.getElementById('cbGrid').classList.toggle('list', fView==='list');
    var cnt=document.getElementById('cbCount');
    if(cnt) cnt.textContent=visible;
  }
  document.querySelectorAll('.flt-cb-bank').forEach(function(btn){
    btn.addEventListener('click',function(){
      document.querySelectorAll('.flt-cb-bank').forEach(function(b){b.classList.remove('active');});
      btn.classList.add('active'); fBank=btn.dataset.bank; apply();
    });
  });
  document.querySelectorAll('.flt-lang-cb').forEach(function(btn){
    btn.addEventListener('click',function(){
      document.querySelectorAll('.flt-lang-cb').forEach(function(b){b.classList.remove('active');});
      btn.classList.add('active'); fLang=btn.dataset.lang; apply();
    });
  });
  document.querySelectorAll('.flt-fx-cb').forEach(function(btn){
    btn.addEventListener('click',function(){
      document.querySelectorAll('.flt-fx-cb').forEach(function(b){b.classList.remove('active');});
      btn.classList.add('active'); fFx=btn.dataset.fx; apply();
    });
  });
  document.querySelectorAll('.flt-view-cb').forEach(function(btn){
    btn.addEventListener('click',function(){
      document.querySelectorAll('.flt-view-cb').forEach(function(b){b.classList.remove('active');});
      btn.classList.add('active'); fView=btn.dataset.view; apply();
    });
  });
  var sb=document.getElementById('searchBoxCB');
  if(sb) sb.addEventListener('input',function(){search=this.value; apply();});
  apply();
})();
</script>"""

    body = f"""
<div class='main'>
  <div class='panel' style='margin-bottom:16px'>
    <h2 style='margin:0 0 14px;font-size:16px'>💾 公式ソースモニター</h2>
    <div class='cb-grid'>{cb_grid_html}</div>
  </div>
  {cb_filter}
  <div style='margin-bottom:12px;font-weight:700;font-size:15px'>公式ソース関連記事（<span id='cbCount'>{len(cb_items)}</span>件）</div>
  <div class='grid' id='cbGrid'>{cards_html}</div>
</div>"""
    return header_html("sources.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


# ─── 分析ページ ───────────────────────────────────────────────────

def render_analysis(items: list[dict], themes_cfg: dict) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    source_counts = Counter(x.get("source", "") for x in items)
    theme_counts  = Counter()
    sent_counts   = Counter(x.get("sentiment", "neutral") for x in items)
    fx_counts     = Counter(x.get("fx_impact", "neutral") for x in items)
    bucket_counts = Counter(detect_bucket(x) for x in items)

    bucket_detail = {}
    for item in items:
        for t in item.get("themes", []):
            theme_counts[t] += 1
        bk = detect_bucket(item)
        if bk not in bucket_detail:
            bucket_detail[bk] = {"total": 0, "price_up": 0, "price_down": 0, "demand_up": 0, "supply_down": 0}
        bucket_detail[bk]["total"] += 1
        fx = item.get("fx_impact", "neutral")
        if fx in bucket_detail[bk]:
            bucket_detail[bk][fx] += 1

    src_rows = ""
    for src, cnt in source_counts.most_common():
        src_items = [x for x in items if x.get("source") == src]
        official  = "🏛 公式" if any(x.get("official") for x in src_items) else ""
        lang      = src_items[0].get("lang", "") if src_items else ""
        lang_str  = "🇯🇵 日本語" if lang == "ja" else "🌐 English"
        src_rows += f"<tr><td>{html.escape(src)}</td><td>{lang_str}</td><td>{official}</td><td style='font-weight:800'>{cnt}</td></tr>"

    theme_rows = ""
    for t, cnt in theme_counts.most_common():
        td_info = themes_cfg["themes"].get(t, {})
        icon    = td_info.get("icon", "")
        color   = td_info.get("color", "#636e72")
        theme_rows += f"<tr><td><span style='color:{color}'>{icon} {html.escape(t)}</span></td><td style='font-weight:800'>{cnt}</td></tr>"

    bull = sent_counts.get("bullish", 0)
    bear = sent_counts.get("bearish", 0)
    neu  = sent_counts.get("neutral", 0)
    total_sent = bull + bear + neu or 1

    bucket_meta = bucket_meta_map()
    bucket_rows = ""
    order = ["dram", "nand_ssd", "hbm", "market_company"]
    for key in order:
        meta = bucket_meta.get(key, {"label": key, "icon": ""})
        row = bucket_detail.get(key, {"total": 0, "price_up": 0, "price_down": 0, "demand_up": 0, "supply_down": 0})
        bucket_rows += (
            f"<tr>"
            f"<td>{meta.get('icon','')} {html.escape(meta.get('label', key))}</td>"
            f"<td style='font-weight:800'>{row['total']}</td>"
            f"<td style='font-weight:800;color:#4ea1ff'>{row['price_up']}</td>"
            f"<td style='font-weight:800;color:#e74c3c'>{row['price_down']}</td>"
            f"<td style='font-weight:800;color:#2ecc71'>{row['demand_up']}</td>"
            f"<td style='font-weight:800;color:#f39c12'>{row['supply_down']}</td>"
            f"</tr>"
        )

    table_css = ".table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}.table th,.table td{padding:10px 14px;border-bottom:1px solid var(--line);text-align:left}.table th{background:var(--panel2);font-size:13px;color:var(--muted)}.table tr:last-child td{border-bottom:none}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}.three-col{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}.summary-trend-grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(320px,1fr);gap:16px;align-items:stretch}.summary-trend-grid>.panel{margin-bottom:16px;height:100%}.trend-panel .trend-row{display:grid;grid-template-columns:56px minmax(78px,1fr) minmax(72px,1fr) 28px;gap:10px;align-items:center}.trend-panel .trend-label{font-weight:800}.trend-panel .trend-metric{font-size:14px;white-space:nowrap}.trend-panel .trend-arrow{font-weight:900;font-size:18px;text-align:right}.summary-panel .summary-split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}.summary-panel .summary-group-title{font-size:13px;color:var(--muted);margin-bottom:10px}.summary-panel .summary-stat-row{display:flex;gap:18px;flex-wrap:wrap}.summary-panel .summary-stat{text-align:center}.summary-panel .summary-stat .value{font-size:28px;font-weight:800}.summary-panel .summary-stat .label{color:var(--muted);font-size:12px;white-space:nowrap}@media(max-width:1100px){.summary-trend-grid{grid-template-columns:1fr}.summary-panel .summary-split,.two-col,.three-col{grid-template-columns:1fr}}@media(max-width:900px){.trend-panel .trend-row{grid-template-columns:52px 1fr auto 22px;gap:8px}.trend-panel .trend-metric{font-size:13px}}"
    sent_html = f"""
<div class='panel summary-panel' style='margin-bottom:16px'>
  <h2 style='margin:0 0 14px;font-size:16px'>📊 総合サマリー</h2>
  <div class='summary-split'>
    <div>
      <div class='summary-group-title'>参考度</div>
      <div class='summary-stat-row'>
        <div class='summary-stat'><div class='value' style='color:#2ecc71'>{bull}</div><div class='label'>✅ 参考 ({bull*100//total_sent}%)</div></div>
        <div class='summary-stat'><div class='value' style='color:#e74c3c'>{bear}</div><div class='label'>⚠ 注意 ({bear*100//total_sent}%)</div></div>
        <div class='summary-stat'><div class='value' style='color:#9ba9bc'>{neu}</div><div class='label'>➖ Neutral ({neu*100//total_sent}%)</div></div>
      </div>
    </div>
    <div>
      <div class='summary-group-title'>重要タグ</div>
      <div class='summary-stat-row'>
        <div class='summary-stat'><div class='value' style='font-size:24px;color:#4ea1ff'>{fx_counts.get("price_up",0)}</div><div class='label'>📈 上昇</div></div>
        <div class='summary-stat'><div class='value' style='font-size:24px;color:#e74c3c'>{fx_counts.get("price_down",0)}</div><div class='label'>📉 下落</div></div>
        <div class='summary-stat'><div class='value' style='font-size:24px;color:#2ecc71'>{fx_counts.get("demand_up",0)}</div><div class='label'>📦 需要増</div></div>
        <div class='summary-stat'><div class='value' style='font-size:24px;color:#f39c12'>{fx_counts.get("supply_down",0)}</div><div class='label'>🛑 供給減</div></div>
      </div>
    </div>
  </div>
</div>"""

    trend_html = render_7day_trend_panel()

    type_html = f"""
<div class='panel' style='margin-bottom:16px'>
  <h2 style='margin:0 0 14px;font-size:16px'>🧩 種類別分析</h2>
  <table class='table'>
    <thead><tr><th>区分</th><th>件数</th><th>価格上昇</th><th>価格下落</th><th>需要増</th><th>供給減</th></tr></thead>
    <tbody>{bucket_rows}</tbody>
  </table>
</div>"""

    body = f"""
<style>{table_css}</style>
<div class='main'>
  <div class='summary-trend-grid'>
    {sent_html}
    {trend_html}
  </div>
  {type_html}
  <div class='two-col'>
    <div class='panel'>
      <h2 style='margin:0 0 14px;font-size:16px'>📡 ソース別記事数</h2>
      <table class='table'><thead><tr><th>ソース</th><th>言語</th><th>種別</th><th>件数</th></tr></thead><tbody>{src_rows}</tbody></table>
    </div>
    <div class='panel'>
      <h2 style='margin:0 0 6px;font-size:16px'>🏷 テーマ別記事数</h2><div style='color:var(--muted);font-size:12px;margin-bottom:10px'>※ 1記事に複数テーマを付与するため、合計は総記事数を上回る場合があります。</div>
      <table class='table'><thead><tr><th>テーマ</th><th>件数</th></tr></thead><tbody>{theme_rows}</tbody></table>
    </div>
  </div>
</div>"""
    return header_html("analysis.html", stamp) + body + footer_html(stamp)



# ─── 注目記事ページ（pickup） ─────────────────────────────────────

PICKUP_CSS = """
<style>
.reading-switch{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap}
.reading-tab{padding:10px 20px;border-radius:999px;border:1px solid var(--line);background:var(--panel2);color:var(--text);font-weight:800;font-size:14px;cursor:pointer;transition:all .18s;white-space:nowrap}
.reading-tab.active{background:linear-gradient(135deg,#4ea1ff,#5a86ff);border-color:transparent;color:#fff;box-shadow:0 4px 16px rgba(78,161,255,.3)}
.reading-panel{display:none}.reading-panel.active{display:block}
.sec-hdr{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.sec-hdr h2{margin:0;font-size:18px;font-weight:800}
.count-badge{background:var(--accent2);border:1px solid var(--accent);color:#eaf4ff;border-radius:999px;padding:3px 12px;font-size:12px;font-weight:700}
.top3-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:28px}
.top3-card{background:linear-gradient(180deg,#161d2c,#111624);border:1px solid var(--line);border-radius:16px;padding:16px;display:flex;flex-direction:column;gap:10px;transition:border-color .18s;min-height:280px}
.top3-card:hover{border-color:var(--accent)}
.top3-card.rank1{border-top:3px solid #ffd700}
.top3-card.rank2{border-top:3px solid #c0c0c0}
.top3-card.rank3{border-top:3px solid #cd7f32}
.top3-rank-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.top3-rank{font-size:18px;font-weight:900;width:36px;height:36px;border-radius:50%;border:2px solid;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.top3-meta{display:flex;gap:5px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--muted);flex:1}
.top3-lang-toggle{display:flex;gap:4px;flex-shrink:0}
.top3-title{font-size:15px;font-weight:800;line-height:1.5;color:var(--text)}
.top3-summary{font-size:12px;line-height:1.65;color:#ced7e6;margin-top:4px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:4;overflow:hidden}
.ranking-section{margin-top:8px}
.ranking-hdr{display:flex;align-items:center;gap:10px;padding:10px 0;margin-bottom:10px;border-top:1px solid var(--line)}
.ranking-hdr h3{margin:0;font-size:15px;font-weight:800;color:var(--muted)}
.rank-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.rank-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:8px;transition:border-color .15s;position:relative}
.rank-card:hover{border-color:var(--accent)}
.rank-num{position:absolute;top:10px;right:12px;font-size:11px;font-weight:800;color:var(--muted)}
.rank-top-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:11px;padding-right:30px}
.rank-title{font-size:13px;font-weight:700;line-height:1.45;color:var(--text);display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
.rank-summary{font-size:12px;color:#ced7e6;line-height:1.55;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden}
.rank-lang-toggle{display:flex;gap:4px}
.rank-open{display:inline-flex;justify-content:center;align-items:center;background:var(--accent2);border:1px solid var(--accent);color:#eaf4ff;border-radius:8px;padding:6px 12px;font-size:12px;font-weight:700;margin-top:auto;transition:all .15s}
.rank-open:hover{background:#2a4a80}
.no-items{color:var(--muted);padding:20px;text-align:center;border:1px dashed var(--line);border-radius:12px}
@media(max-width:900px){.top3-grid{grid-template-columns:1fr}}
</style>"""


def _build_top3_card(rank: int, item: dict, themes_cfg: dict) -> str:
    card_id    = html.escape(item.get("id", ""))
    title_orig = html.escape(item.get("title", ""))
    title_ja   = html.escape(item.get("title_ja", "") or item.get("title", ""))
    sum_orig   = html.escape(item.get("summary", ""))
    sum_ja     = html.escape(item.get("summary_ja", "") or item.get("summary", ""))
    source     = html.escape(item.get("source", ""))
    published  = html.escape(item.get("published", "")[:16])
    link       = html.escape(item.get("link", "#"))
    lang       = item.get("lang", "en")
    score      = item.get("score", 1)
    fx_impact  = item.get("fx_impact", "neutral")
    new_flag   = item.get("is_new", False)
    is_official= item.get("official", False)

    rank_color = {1:"#ffd700",2:"#c0c0c0",3:"#cd7f32"}.get(rank,"#4ea1ff")
    rank_cls   = {1:"rank1",2:"rank2",3:"rank3"}.get(rank,"")
    fx_label, fx_cls = IMPORTANCE_MAP.get(fx_impact, ("➖","tag-neutral"))
    new_tag      = "<span class='tag tag-new'>🆕 NEW</span>" if new_flag else ""
    official_tag = "<span class='tag tag-official'>🏛</span>" if is_official else ""

    if lang == "ja":
        panes       = f"<div class='lang-pane lang-ja'><div class='top3-title'>{title_orig}</div><div class='top3-summary'>{sum_orig}</div></div>"
        lang_toggle = ""
    else:
        panes = f"""<div class='lang-pane lang-en'><div class='top3-title'>{title_orig}</div><div class='top3-summary'>{sum_orig}</div></div>
<div class='lang-pane lang-ja hidden'><div class='top3-title'>{title_ja}</div><div class='top3-summary'>{sum_ja}</div></div>"""
        lang_toggle = f"""<div class='top3-lang-toggle'>
  <button class='card-lang-btn active' data-l='en' onclick="switchCardLang(this,'en')">EN</button>
  <button class='card-lang-btn' data-l='ja' onclick="switchCardLang(this,'ja')">JA</button>
</div>"""

    return f"""
<article class='top3-card {rank_cls} item-card' data-cardid='{card_id}' data-lang='{html.escape(lang)}' data-score='{score}' data-importance='{html.escape(fx_impact)}'>
  <div class='top3-rank-row'>
    <div class='top3-rank' style='color:{rank_color};border-color:{rank_color}'>#{rank}</div>
    <div class='top3-meta'>{new_tag}{official_tag}<span class='lang-badge {"ja" if lang=="ja" else "en"}'>{"🇯🇵" if lang=="ja" else "🌐"}</span><span>{source}</span><span>{published}</span></div>
    {lang_toggle}
  </div>
  {panes}
  <div class='tags'><span class='tag {fx_cls}'>{fx_label}</span></div>
  <a class='rank-open' href='{link}' target='_blank' rel='noopener noreferrer'>記事を開く →</a>
</article>"""


def _build_rank_card(rank: int, item: dict) -> str:
    card_id   = html.escape(item.get("id", ""))
    title_orig= html.escape(item.get("title", ""))
    title_ja  = html.escape(item.get("title_ja", "") or item.get("title", ""))
    sum_orig  = html.escape(item.get("summary", ""))
    sum_ja    = html.escape(item.get("summary_ja", "") or item.get("summary", ""))
    source    = html.escape(item.get("source", ""))
    published = html.escape(item.get("published", "")[:16])
    link      = html.escape(item.get("link", "#"))
    lang      = item.get("lang", "en")
    score     = item.get("score", 1)
    fx_impact = item.get("fx_impact", "neutral")
    new_flag  = item.get("is_new", False)

    fx_label, fx_cls = IMPORTANCE_MAP.get(fx_impact, ("➖","tag-neutral"))
    new_tag = "<span class='tag tag-new' style='font-size:10px;padding:2px 5px'>NEW</span>" if new_flag else ""

    if lang == "ja":
        panes       = f"<div class='lang-pane lang-ja'><div class='rank-title'>{title_orig}</div><div class='rank-summary'>{sum_orig}</div></div>"
        lang_toggle = ""
    else:
        panes = f"""<div class='lang-pane lang-en'><div class='rank-title'>{title_orig}</div><div class='rank-summary'>{sum_orig}</div></div>
<div class='lang-pane lang-ja hidden'><div class='rank-title'>{title_ja}</div><div class='rank-summary'>{sum_ja}</div></div>"""
        lang_toggle = f"""<div class='rank-lang-toggle'>
  <button class='card-lang-btn active' data-l='en' onclick="switchCardLang(this,'en')" style='font-size:11px;padding:3px 7px'>EN</button>
  <button class='card-lang-btn' data-l='ja' onclick="switchCardLang(this,'ja')" style='font-size:11px;padding:3px 7px'>JA</button>
</div>"""

    return f"""
<article class='rank-card item-card' data-cardid='{card_id}' data-lang='{html.escape(lang)}' data-score='{score}' data-importance='{html.escape(fx_impact)}'>
  <span class='rank-num'>#{rank}</span>
  <div class='rank-top-row'>{new_tag}<span class='lang-badge {"ja" if lang=="ja" else "en"}' style='font-size:10px'>{"🇯🇵" if lang=="ja" else "🌐"}</span><span style='color:var(--muted)'>{source}</span><span style='color:var(--muted)'>{published}</span><span class='tag {fx_cls}' style='font-size:10px;padding:2px 6px'>{fx_label}</span>{lang_toggle}</div>
  {panes}
  <a class='rank-open' href='{link}' target='_blank' rel='noopener noreferrer' style='font-size:12px;padding:6px 10px'>開く →</a>
</article>"""


def _build_pickup_tab(top3: list, rest: list, themes_cfg: dict) -> str:
    if not top3 and not rest:
        return "<div class='no-items'>記事が見つかりませんでした</div>"
    top3_html = "".join(_build_top3_card(i+1, it, themes_cfg) for i, it in enumerate(top3))
    rest_html = ""
    if rest:
        rank_cards = "".join(_build_rank_card(i+4, it) for i, it in enumerate(rest))
        rest_html  = f"""<div class='ranking-section'>
  <div class='ranking-hdr'><h3>📋 ランキング #{4}〜#{3+len(rest)}</h3><span class='count-badge'>{len(rest)}件</span></div>
  <div class='rank-grid'>{rank_cards}</div>
</div>"""
    return f"<div class='top3-grid'>{top3_html}</div>{rest_html}"


def render_pickup(items: list[dict], themes_cfg: dict) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    ja_sorted  = sorted([x for x in items if x.get("lang") == "ja"],  key=lambda x: -x.get("score",1))
    en_sorted  = sorted([x for x in items if x.get("lang") != "ja"],  key=lambda x: -x.get("score",1))
    mix_sorted = sorted(items, key=lambda x: -x.get("score",1))

    ja_tab  = _build_pickup_tab(ja_sorted[:3],  ja_sorted[3:],  themes_cfg)
    en_tab  = _build_pickup_tab(en_sorted[:3],  en_sorted[3:],  themes_cfg)
    mix_tab = _build_pickup_tab(mix_sorted[:3], mix_sorted[3:], themes_cfg)

    body = f"""
{PICKUP_CSS}
<div class='main'>
  <div class='reading-switch'>
    <button class='reading-tab active' onclick="switchPickupTab('mix',this)">🌐 全部</button>
    <button class='reading-tab' onclick="switchPickupTab('ja',this)">🇯🇵 日本語</button>
    <button class='reading-tab' onclick="switchPickupTab('en',this)">🌐 English</button>
  </div>
  <div class='reading-panel active' id='pickup-tab-mix'>
    <div class='sec-hdr'><h2>🌐 全部 注目記事</h2><span class='count-badge'>{len(mix_sorted)}件</span></div>
    {mix_tab}
  </div>
  <div class='reading-panel' id='pickup-tab-ja'>
    <div class='sec-hdr'><h2>🇯🇵 日本語 注目記事</h2><span class='count-badge'>{len(ja_sorted)}件</span></div>
    {ja_tab}
  </div>
  <div class='reading-panel' id='pickup-tab-en'>
    <div class='sec-hdr'><h2>🌐 English 注目記事</h2><span class='count-badge'>{len(en_sorted)}件</span></div>
    {en_tab}
  </div>
</div>
<script>
function switchPickupTab(tab, btn) {{
  document.querySelectorAll('.reading-panel').forEach(function(p){{ p.classList.remove('active'); }});
  document.querySelectorAll('.reading-tab').forEach(function(b){{ b.classList.remove('active'); }});
  document.getElementById('pickup-tab-' + tab).classList.add('active');
  btn.classList.add('active');
}}
</script>"""
    return header_html("pickup.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


# ─── メイン ──────────────────────────────────────────────────────

def main() -> None:
    start = time.time()
    log("=" * 50)
    log(f"💾 {APP_NAME}")
    log("=" * 50)

    sources    = json.loads((SHARED / "sources.json").read_text(encoding="utf-8"))
    themes_cfg = json.loads((SHARED / "themes.json").read_text(encoding="utf-8"))

    log(f"\n📡 {len(sources)} ソースからデータ収集開始...\n")
    items, ok, err = collect_all(sources, themes_cfg)

    if not items:
        log("⚠ 記事が取得できませんでした。")
        sys.exit(1)

    # SQLite保存
    conn = db_init()
    db_save_items(conn, items)
    db_save_run(conn, len(items), ok, err)
    db_save_daily_stats(conn, items)
    db_cleanup(conn)
    conn.close()

    # 翻訳（英語記事全件）
    cache = load_json(TRANSLATE_CACHE_FILE, {})
    translate_items(items, cache)

    log("\n🖊 HTML生成中...")
    pages = [
        ("index.html",        render_index(items, themes_cfg)),
        ("pickup.html",       render_pickup(items, themes_cfg)),
        ("focus.html",    render_focus(items, themes_cfg)),
        ("themes.html",        render_themes(items, themes_cfg)),
        ("sources.html", render_sources(items, themes_cfg)),
        ("analysis.html",     render_analysis(items, themes_cfg)),
    ]
    for filename, content in pages:
        (OUTPUT / filename).write_text(content, encoding="utf-8")
        log(f"  ✓ {filename} 生成完了")

    log(f"\n⏱ 処理時間: {time.time() - start:.1f}秒")
    log(f"\n✅ 完了！")
    log(f"   {OUTPUT / 'index.html'}")


if __name__ == "__main__":
    main()
