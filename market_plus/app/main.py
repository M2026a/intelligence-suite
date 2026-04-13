"""
Market +
市場・為替・経済情報を収集・一覧化
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
from urllib.parse import urlparse

import feedparser, requests
from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared"
OUTPUT = ROOT / "output"
LOG_DIR = ROOT / "logs"

OUTPUT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

JST = ZoneInfo("Asia/Tokyo")

CONFIG            = json.loads((SHARED / "config.json").read_text(encoding="utf-8"))
APP_NAME          = CONFIG["app_name"]
TRANSLATE_WORKERS = int(CONFIG.get("translate_workers", 4))
TRANSLATE_RETRIES = int(CONFIG.get("translate_retries", 2))
NEW_HOURS         = int(CONFIG.get("new_badge_hours", 6))
HISTORY_DAYS      = int(CONFIG.get("history_days", 14))

TRANSLATE_CACHE_FILE = OUTPUT / "translate_cache.json"
DB_FILE              = OUTPUT / CONFIG.get("db_name", "market_plus.db")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 MarketPlus"
TIMEOUT    = 15


# ─── USD/JPY 影響キーワード ───────────────────────────────────────

USD_STRONG = [
    "rate hike", "hawkish", "tightening", "strong jobs", "beat expectations",
    "above forecast", "upside surprise", "strong gdp", "nonfarm payroll",
    "dollar strength", "usd rally", "fed hike", "fomc hike",
    "利上げ", "タカ派", "予想上回", "強い雇用", "ドル高", "ドル買い",
]
USD_WEAK = [
    "rate cut", "dovish", "easing", "weak jobs", "miss expectations",
    "below forecast", "downside surprise", "recession", "slowdown",
    "dollar weakness", "dollar falls", "fed cut", "fomc cut",
    "利下げ", "ハト派", "予想下回", "弱い雇用", "ドル安", "ドル売り",
]
JPY_STRONG = [
    "boj hike", "boj tightening", "japan rate hike", "yen strength",
    "buy yen", "risk off", "geopolitical risk", "flight to safety",
    "日銀利上げ", "円高", "円買い", "リスクオフ", "地政学リスク", "有事の円買い",
]
JPY_WEAK = [
    "boj easing", "yield curve control", "ycc", "yen weakness", "sell yen",
    "risk on", "carry trade", "intervention sell yen",
    "日銀緩和", "円安", "円売り", "リスクオン", "キャリートレード", "円売り介入",
]

BULLISH_WORDS = USD_STRONG + ["higher inflation", "risk on"]
BEARISH_WORDS = USD_WEAK + ["景気後退", "リセッション", "risk off"]

# イベントキーワードボーナス
EVENT_KEYWORDS = {
    3: ["為替介入", "円買い介入", "intervention", "boj intervention", "mof intervention"],
    2: ["fomc", "federal open market committee", "金融政策決定会合",
        "nonfarm payroll", "non-farm payroll", "雇用統計",
        "cpi", "consumer price index", "消費者物価指数",
        "rate decision", "金利決定", "interest rate decision"],
    1: ["gdp", "recession", "景気後退", "利上げ", "利下げ", "rate hike", "rate cut",
        "inflation", "インフレ", "unemployment", "失業率", "trade war", "貿易戦争",
        "sanctions", "制裁", "tariff", "関税"],
}


# ─── ユーティリティ ───────────────────────────────────────────────

def now_jst() -> datetime:
    return datetime.now(JST)

def jst_stamp(fmt: str = "%Y-%m-%d %H:%M") -> str:
    return now_jst().strftime(fmt)

def log(msg: str) -> None:
    print(f"[{jst_stamp('%H:%M:%S')}] {msg}", flush=True)

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
    t = re.sub(r'\b(excite|エキサイト|ニコニコニュース|ニコニコ|prtimes|pr times|yahoo!ニュース|yahooニュース|yahoo|ライブドアニュース|livedoor|msn)\b', ' ', t)
    t = re.sub(r'[|｜／/・:：\-–—]+', ' ', t)
    t = re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff]+', ' ', t)
    t = re.sub(r'\b(jp|en|us|uk|com|net|org)\b', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def title_similarity(a: str, b: str) -> float:
    if not a or not b: return 0.0
    return SequenceMatcher(None, a, b).ratio()

def is_image_or_gallery_item(title: str, summary: str = '', link: str = '') -> bool:
    combined = text_lower(f"{title} {summary} {link}")
    patterns = [
        r'画像\s*\d+\s*/\s*\d+', r'写真\s*\d+\s*/\s*\d+', r'フォト\s*\d+\s*/\s*\d+',
        r'photo\s*\d+\s*/\s*\d+', r'gallery', r'フォト特集', r'写真特集', r'画像特集',
        r'スライドショー', r'photo\s*special',
    ]
    if any(re.search(p, combined) for p in patterns): return True
    if re.search(r'(?:^|\W)(?:photo|photos|image|images|gallery)(?:\W|$)', combined) and re.search(r'\d+\s*/\s*\d+', combined): return True
    if any(token in combined for token in ['/photo/', '/photos/', '/gallery/', 'photo/', 'gallery/']): return True
    return False

def make_soft_title_key(title_key: str) -> str:
    if not title_key: return ''
    words = title_key.split()
    if len(words) >= 6: return ' '.join(words[:6])
    if len(words) >= 4: return ' '.join(words[:4])
    return title_key[:40]

def make_core_title_key(title_key: str) -> str:
    if not title_key: return ''
    words = [w for w in title_key.split() if len(w) > 1]
    if len(words) >= 8: return ' '.join(words[:8])
    if len(words) >= 5: return ' '.join(words[:5])
    return title_key[:56]

def extract_story_source(text: str, link: str = '') -> str:
    combined = text_lower(f"{text} {link}")
    patterns = [
        (r'\bedutail\b', 'edutail'), (r'\bpr\s?times\b', 'edutail'),
        (r'\bexcite\b|\bエキサイト\b', 'edutail'), (r'\bニコニコニュース\b|\bニコニコ\b', 'edutail'),
        (r'\byahoo!?\s*ニュース\b|\byahoo!?\b', 'yahoo'), (r'\blivedoor\b|\bライブドア\b', 'livedoor'),
        (r'\bmsn\b', 'msn'), (r'\bprtimes\.jp\b', 'edutail'),
    ]
    for pat, label in patterns:
        if re.search(pat, combined): return label
    host = normalize_host(link)
    if host:
        host = re.sub(r'\.(com|jp|net|org|co\.jp)$', '', host)
        host = re.sub(r'[^a-z0-9]+', '', host)
    return host or ''

def item_rank(x: dict):
    return (x.get('score', 1), 1 if x.get('official') else 0, len(x.get('summary', '')), x.get('pub_dt', ''))

def is_probable_duplicate_item(current: dict, previous: dict) -> bool:
    cur_title  = normalize_title_for_dedupe(current.get('title', ''))
    prev_title = normalize_title_for_dedupe(previous.get('title', ''))
    if not cur_title or not prev_title: return False
    if cur_title == prev_title: return True
    cur_host   = normalize_host(current.get('link', ''))
    prev_host  = normalize_host(previous.get('link', ''))
    same_host  = bool(cur_host and prev_host and cur_host == prev_host)
    cur_story  = extract_story_source(current.get('title', '') + ' ' + current.get('summary', ''), current.get('link', ''))
    prev_story = extract_story_source(previous.get('title', '') + ' ' + previous.get('summary', ''), previous.get('link', ''))
    same_story = bool(cur_story and prev_story and cur_story == prev_story)
    similarity = title_similarity(cur_title, prev_title)
    soft_cur   = make_soft_title_key(cur_title)
    soft_prev  = make_soft_title_key(prev_title)
    core_cur   = make_core_title_key(cur_title)
    core_prev  = make_core_title_key(prev_title)
    if same_story and core_cur and core_cur == core_prev: return True
    if same_story and similarity >= 0.80: return True
    if same_host and core_cur and core_cur == core_prev: return True
    if same_host and similarity >= 0.80: return True
    if core_cur and core_cur == core_prev and similarity >= 0.76: return True
    if soft_cur and soft_cur == soft_prev and similarity >= 0.84: return True
    if similarity >= 0.91: return True
    return False

def source_group(name: str) -> str:
    src = text_lower(name)
    if 'google news jp' in src: return 'google_news_jp'
    if 'google news en' in src or 'google news -' in src: return 'google_news_en'
    return src

def is_new(pub_dt_iso: str) -> bool:
    if not pub_dt_iso: return False
    try:
        pub_dt = datetime.fromisoformat(pub_dt_iso)
        return now_jst() - pub_dt.astimezone(JST) <= timedelta(hours=NEW_HOURS)
    except: return False


# ─── 判定ロジック ─────────────────────────────────────────────────

def detect_sentiment(title: str, summary: str, themes: list[str] | None = None, fx_impact: str | None = None) -> str:
    combined = text_lower(f"{title} {summary}")
    themes = themes or []

    bullish_extra = [
        "strong demand", "resilient", "robust", "beat expectations", "above forecast",
        "upside surprise", "growth accelerates", "higher yields", "yield rise", "risk on",
        "soft landing", "景気堅調", "上振れ", "堅調", "改善", "株高"
    ]
    bearish_extra = [
        "miss expectations", "below forecast", "downside surprise", "recession", "slowdown",
        "contraction", "risk off", "flight to safety", "yield fall", "yield decline",
        "景気後退", "減速", "下振れ", "悪化", "混乱", "株安"
    ]

    bull = sum(1.0 for w in (BULLISH_WORDS + bullish_extra) if text_lower(w) in combined)
    bear = sum(1.0 for w in (BEARISH_WORDS + bearish_extra) if text_lower(w) in combined)

    if any(t in themes for t in ["金利・中央銀行", "インフレ・物価", "雇用・景気"]):
        if any(text_lower(w) in combined for w in ["rate hike", "higher for longer", "hawkish", "strong jobs", "hot cpi", "hot ppi", "利上げ", "タカ派", "雇用堅調", "上振れ"]):
            bull += 1.4
        if any(text_lower(w) in combined for w in ["rate cut", "dovish", "weak jobs", "recession risk", "cooling inflation", "利下げ", "ハト派", "景気後退", "下振れ"]):
            bear += 1.4
        if any(text_lower(w) in combined for w in ["fed", "fomc", "frb", "cpi", "ppi", "pce", "nfp", "payroll", "米金利", "雇用統計"]):
            bull += 0.6

    if "地政学・貿易" in themes and any(text_lower(w) in combined for w in ["war", "conflict", "missile", "attack", "sanctions", "tariff", "関税", "制裁", "紛争", "戦争"]):
        bear += 0.9

    if "要人発言" in themes and any(text_lower(w) in combined for w in ["said", "remarks", "statement", "speech", "interview", "述べた", "発言", "会見", "声明"]):
        if any(text_lower(w) in combined for w in ["hawkish", "strong dollar", "dollar strength", "ドル高", "ドル買い"]):
            bull += 0.9
        elif any(text_lower(w) in combined for w in ["dovish", "weak dollar", "yen buying", "リスクオフ", "円買い"]):
            bear += 0.9

    if fx_impact in {"usd_strong", "jpy_weak"}:
        bull += 1.2
    elif fx_impact in {"usd_weak", "jpy_strong"}:
        bear += 1.2

    if bull < 0.85 and bear < 0.85: return "neutral"
    if abs(bull - bear) < 0.28: return "neutral"
    return "bullish" if bull > bear else "bearish"


def detect_fx_impact(title: str, summary: str, themes: list[str] | None = None) -> str:
    """USD/JPY影響方向を判定: usd_strong / usd_weak / jpy_strong / jpy_weak / neutral"""
    combined = text_lower(f"{title} {summary}")
    themes = themes or []

    scores = {"usd_strong": 0.0, "usd_weak": 0.0, "jpy_strong": 0.0, "jpy_weak": 0.0}

    def has_any(words: list[str]) -> bool:
        return any(text_lower(w) in combined for w in words)

    def count_any(words: list[str]) -> int:
        return sum(1 for w in words if text_lower(w) in combined)

    usd_hawkish_words = [
        "rate hike", "higher for longer", "hawkish", "tightening", "sticky inflation",
        "hot cpi", "hot ppi", "strong payroll", "strong payrolls", "strong jobs",
        "strong labor market", "robust growth", "yield rise", "treasury yields rise",
        "inflation pressures", "price pressures", "tariff inflation",
        "利上げ", "タカ派", "引き締め", "インフレ加速", "雇用堅調", "米金利上昇", "上振れ",
        "物価上振れ", "賃金上昇", "ドル高"
    ]
    usd_dovish_words = [
        "rate cut", "rate cuts", "dovish", "easing", "disinflation", "soft data",
        "weak payroll", "weak payrolls", "weak jobs", "growth slowdown", "recession risk",
        "yield fall", "yield decline", "cooling inflation", "missed estimates",
        "利下げ", "ハト派", "緩和", "ディスインフレ", "景気減速", "景気後退", "米金利低下", "下振れ",
        "物価鈍化", "弱い指標", "ドル安"
    ]
    jpy_safe_haven_words = [
        "safe haven", "flight to safety", "yen buying", "buying yen", "risk-off",
        "market turmoil", "panic", "haven demand", "安全資産", "円買い", "円高圧力", "リスクオフ",
        "quality bid", "volatility spike"
    ]
    jpy_risk_off_context_words = [
        "war", "conflict", "missile", "attack", "escalation", "geopolitical tension",
        "middle east", "red sea", "ukraine", "russia", "taiwan strait", "shipping disruption",
        "軍事", "有事", "戦争", "紛争", "ミサイル", "攻撃", "中東"
    ]
    jpy_weak_words = [
        "risk-on", "equity rally", "stocks rise", "stocks rally", "carry trade", "carry trades",
        "global recovery", "record high", "appetite for risk", "yen selling", "sell yen",
        "リスクオン", "株高", "キャリートレード", "楽観", "円売り", "円安"
    ]
    boj_hawkish_words = [
        "boj hike", "bank of japan hike", "boj normalization", "yield curve control exit",
        "jgb yield rise", "boj raises rates", "日銀利上げ", "日銀正常化", "ycc修正", "円買い介入",
        "政策正常化", "追加利上げ"
    ]
    boj_dovish_words = [
        "boj dovish", "boj easing", "ultra-loose", "maintain stimulus", "prolonged easing",
        "日銀緩和", "金融緩和維持", "超緩和", "円売り介入", "緩和維持"
    ]

    scores["usd_strong"] += count_any(USD_STRONG + usd_hawkish_words) * 0.9
    scores["usd_weak"]   += count_any(USD_WEAK + usd_dovish_words) * 0.9
    scores["jpy_strong"] += count_any(JPY_STRONG + jpy_safe_haven_words + boj_hawkish_words) * 0.9
    scores["jpy_weak"]   += count_any(JPY_WEAK + jpy_weak_words + boj_dovish_words) * 0.9

    us_macro_words = [
        "fed", "fomc", "federal reserve", "us yields", "treasury yields", "cpi", "pce", "nfp",
        "payrolls", "jobless claims", "frb", "米金利", "米物価", "米雇用", "雇用統計", "消費者物価",
        "treasury", "米国債", "米景気", "retail sales", "gdp"
    ]
    japan_macro_words = [
        "boj", "bank of japan", "日銀", "ycc", "国債利回り", "jgb", "財務省", "鈴木財務相",
        "円安けん制", "為替介入", "口先介入", "春闘", "実質賃金"
    ]

    if "金利・中央銀行" in themes or "インフレ・物価" in themes or "雇用・景気" in themes:
        if has_any(us_macro_words):
            if has_any(usd_dovish_words + ["軟化", "鈍化", "低下", "利下げ観測", "弱い", "減速"]):
                scores["usd_weak"] += 1.2
            elif has_any(usd_hawkish_words + ["上振れ", "強い", "堅調", "加速", "上昇"]):
                scores["usd_strong"] += 1.4
            else:
                scores["usd_strong"] += 0.9
        if has_any(japan_macro_words):
            if has_any(boj_dovish_words + ["円安", "円売り", "緩和"]):
                scores["jpy_weak"] += 1.1
            elif has_any(boj_hawkish_words + ["円高", "円買い", "正常化", "介入警戒", "けん制"]):
                scores["jpy_strong"] += 1.3
            else:
                scores["jpy_strong"] += 0.75

    if "地政学・貿易" in themes:
        if has_any(jpy_safe_haven_words):
            scores["jpy_strong"] += 1.3
        elif has_any(jpy_risk_off_context_words):
            scores["jpy_strong"] += 0.95
        if has_any(["tariff", "tariffs", "trade war", "sanctions", "関税", "制裁", "報復関税", "輸出規制"]):
            scores["usd_strong"] += 0.65

    if "要人発言" in themes:
        if has_any(["powell", "fed chair", "fomc", "frb", "treasury secretary", "bessent", "パウエル", "frb議長"]):
            if has_any(usd_dovish_words):
                scores["usd_weak"] += 1.0
            else:
                scores["usd_strong"] += 0.95
        if has_any(["boj", "bank of japan", "財務省", "finance minister", "鈴木財務相", "植田", "日銀"]):
            if has_any(["円安", "weak yen", "yen weakness", "介入", "けん制"]):
                scores["jpy_strong"] += 1.0
            elif has_any(["緩和", "ultra-loose", "円売り"]):
                scores["jpy_weak"] += 0.9
            else:
                scores["jpy_strong"] += 0.65

    if has_any(["usd/jpy", "ドル円", "yen", "円", "dollar", "ドル"]):
        if has_any(["rises", "rise", "gains", "firm", "higher", "strengthens", "上昇", "続伸", "強含み"]):
            scores["usd_strong"] += 0.9
        if has_any(["falls", "fall", "drops", "weaker", "lower", "softens", "下落", "反落", "軟化"]):
            scores["usd_weak"] += 0.9
        if has_any(["yen buying", "円買い", "safe haven", "flight to safety", "risk-off", "リスクオフ"]):
            scores["jpy_strong"] += 1.0
        if has_any(["yen selling", "円売り", "carry trade", "risk-on", "リスクオン"]):
            scores["jpy_weak"] += 1.0

    if max(scores.values()) < 0.85:
        if "金利・中央銀行" in themes or "インフレ・物価" in themes:
            if has_any(usd_dovish_words + ["鈍化", "低下", "下振れ"]):
                scores["usd_weak"] += 0.95
            else:
                scores["usd_strong"] += 0.95
        elif "雇用・景気" in themes:
            if has_any(["recession", "景気悪化", "失業", "弱い雇用", "下振れ"]):
                scores["jpy_strong"] += 0.9
            else:
                scores["usd_strong"] += 0.75
        elif "地政学・貿易" in themes:
            scores["jpy_strong"] += 0.8
        elif "要人発言" in themes:
            if has_any(["円", "yen", "boj", "日銀", "財務省"]):
                scores["jpy_strong"] += 0.7
            else:
                scores["usd_strong"] += 0.7

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_key, best_score = ordered[0]
    second_score = ordered[1][1]
    if best_score < 0.38: return "neutral"
    if best_score - second_score < 0.10 and best_score < 0.95: return "neutral"
    return best_key


def infer_theme_from_source(source_name: str) -> list[str]:
    src = text_lower(source_name)
    mapping = [
        (("federal reserve", "fed", "fomc", "monetary policy", "bank of japan", "boj", "ecb", "bis", "central bank"), "金利・中央銀行"),
        (("cpi", "ppi", "inflation", "price index", "bls", "労働統計"), "インフレ・物価"),
        (("gdp", "economic", "industrial production", "employment", "unemployment", "census", "bea", "retail", "trade"), "雇用・景気"),
        (("fiscal", "budget", "tax", "subsidy", "cabinet office", "ministry of finance", "meti", "cao", "mof", "内閣府", "財務省", "経産省"), "財政"),
        (("nvidia", "intel", "semiconductor", "chip", "gpu", "memory", "hbm", "dram", "nand"), "半導体・産業"),
        (("japan", "boj", "meti", "cabinet office", "ministry of finance", "日銀", "日本", "財務省", "経産省", "内閣府", "nhk"), "日本"),
    ]
    matched = [label for keys, label in mapping if any(k in src for k in keys)]
    return matched or ["その他"]


def infer_fx_bias_from_source(source_name: str) -> str:
    src = text_lower(source_name)
    if any(k in src for k in ["bank of japan", "boj", "日銀", "財務省", "mof"]):
        return "jpy_strong"
    if any(k in src for k in ["federal reserve", "fed", "fomc", "ecb", "bis"]):
        return "usd_strong"
    return "neutral"


def detect_market_context(title: str, summary: str, themes: list[str] | None = None) -> str:
    """市場全体のセンチメントを簡易判定: risk_on / risk_off / neutral"""
    combined = text_lower(f"{title} {summary}")
    themes = themes or []

    risk_on_words = [
        "risk-on", "risk appetite", "equity rally", "stocks rise", "stocks rally",
        "record high", "optimism", "soft landing", "growth optimism",
        "リスクオン", "株高", "上昇", "最高値", "楽観", "持ち直し"
    ]
    risk_off_words = [
        "risk-off", "recession", "slowdown", "uncertainty", "market turmoil",
        "stocks fall", "selloff", "geopolitical tension", "flight to safety",
        "リスクオフ", "景気後退", "減速", "不透明", "株安", "急落", "混乱", "地政学"
    ]

    on_score = sum(1 for w in risk_on_words if text_lower(w) in combined)
    off_score = sum(1 for w in risk_off_words if text_lower(w) in combined)

    if "地政学・貿易" in themes:
        off_score += 1
    if "雇用・景気" in themes and any(text_lower(w) in combined for w in ["strong", "robust", "堅調", "改善"]):
        on_score += 1
    if "雇用・景気" in themes and any(text_lower(w) in combined for w in ["weak", "miss", "slump", "悪化", "下振れ"]):
        off_score += 1

    if max(on_score, off_score) == 0:
        return "neutral"
    if abs(on_score - off_score) <= 0:
        return "neutral"
    return "risk_on" if on_score > off_score else "risk_off"


def calc_score(item: dict) -> int:
    combined = text_lower(f"{item.get('title','')} {item.get('summary','')}")
    themes = item.get("themes", [])
    base = 1
    if "その他" not in themes or len(themes) > 1: base = 2
    if item.get("official"): base = max(base, 3)
    if item.get("sentiment") == "bearish": base = max(base, 3)
    if len(themes) >= 2: base = max(base, 3)
    bonus = 0
    for points, keywords in EVENT_KEYWORDS.items():
        for kw in keywords:
            if text_lower(kw) in combined:
                bonus = max(bonus, points)
                break
    return min(base + bonus, 5)


def classify_themes(title: str, summary: str, lang: str, themes_cfg: dict) -> list[str]:
    combined = text_lower(f"{title} {summary}")
    matched = []
    for theme_key, theme_data in themes_cfg["themes"].items():
        kw_key = "keywords_ja" if lang == "ja" else "keywords_en"
        for kw in theme_data.get(kw_key, []):
            if text_lower(kw) in combined:
                matched.append(theme_key)
                break
    return matched if matched else ["その他"]


# ─── SQLite ──────────────────────────────────────────────────────

def db_init() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id          TEXT PRIMARY KEY,
            source      TEXT,
            lang        TEXT,
            title       TEXT,
            summary     TEXT,
            link        TEXT,
            published   TEXT,
            pub_dt      TEXT,
            themes      TEXT,
            sentiment   TEXT,
            fx_impact   TEXT,
            market_context TEXT,
            score       INTEGER,
            is_new      INTEGER,
            fetched_at  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at      TEXT,
            total_items INTEGER,
            sources_ok  INTEGER,
            sources_err INTEGER
        )
    """)
    conn.commit()
    return conn

def db_save_items(conn: sqlite3.Connection, items: list[dict]) -> None:
    now = now_jst().isoformat()
    conn.executemany("""
        INSERT OR REPLACE INTO articles
        (id, source, lang, title, summary, link, published, pub_dt, themes, sentiment, fx_impact, market_context, score, is_new, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, [(
        item["id"], item.get("source",""), item.get("lang",""),
        item.get("title",""), item.get("summary",""), item.get("link",""),
        item.get("published",""), item.get("pub_dt",""),
        json.dumps(item.get("themes",[]), ensure_ascii=False),
        item.get("sentiment","neutral"), item.get("fx_impact","neutral"), item.get("market_context","neutral"),
        item.get("score",1), 1 if item.get("is_new") else 0, now
    ) for item in items])
    conn.commit()
    log(f"  ✓ SQLite保存: {len(items)}件 → {DB_FILE.name}")

def db_save_run(conn: sqlite3.Connection, total: int, ok: int, err: int) -> None:
    conn.execute(
        "INSERT INTO fetch_runs (run_at, total_items, sources_ok, sources_err) VALUES (?,?,?,?)",
        (now_jst().isoformat(), total, ok, err)
    )
    conn.commit()

def db_cleanup(conn: sqlite3.Connection) -> None:
    cutoff = (now_jst() - timedelta(days=HISTORY_DAYS * 2)).isoformat()
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

def fetch_source(source: dict, themes_cfg: dict) -> tuple[list[dict], bool]:
    name = source["name"]
    url  = source["url"]
    lang = source.get("lang", "en")
    allowed_domains = source.get("allowed_domains", [normalize_host(url)])

    log(f"  取得中: {name}")
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log(f"  ⚠ {name}: {e}")
        return [], False

    items = []
    for entry in feed.entries:
        title   = clean_text(getattr(entry, "title", ""))
        summary = clean_text(getattr(entry, "summary", getattr(entry, "description", "")))
        link    = getattr(entry, "link", "")

        if not title or not link: continue
        if not is_allowed_domain(link, allowed_domains): continue
        if is_image_or_gallery_item(title, summary, link): continue

        pub_dt = None
        try:
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                pub_dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
        except: pass

        if pub_dt:
            pub_dt = pub_dt.astimezone(JST)
            if now_jst() - pub_dt > timedelta(days=HISTORY_DAYS): continue
            pub_str = pub_dt.strftime("%Y-%m-%d %H:%M")
        else:
            pub_str = ""

        themes = classify_themes(title, summary, lang, themes_cfg)
        if themes == ["その他"]:
            themes = infer_theme_from_source(name)

        fx_impact = detect_fx_impact(title, summary, themes)
        if fx_impact == "neutral":
            fx_bias = infer_fx_bias_from_source(name)
            if fx_bias != "neutral":
                fx_impact = fx_bias

        market_context = detect_market_context(title, summary, themes)
        sentiment = detect_sentiment(title, summary, themes, fx_impact)

        item = {
            "id":        "item-" + hashlib.md5(link.encode()).hexdigest()[:12],
            "source":    name,
            "lang":      lang,
            "region":    source.get("region", ""),
            "official":  bool(source.get("official", False)),
            "title":     title,
            "title_ja":  "",
            "summary":   summary[:300],
            "summary_ja":"",
            "link":      link,
            "published": pub_str,
            "pub_dt":    pub_dt.isoformat() if pub_dt else "",
            "themes":    themes,
            "sentiment": sentiment,
            "fx_impact": fx_impact,
            "is_new":    is_new(pub_dt.isoformat() if pub_dt else ""),
            "score":     1,
        }
        item["score"] = calc_score(item)
        items.append(item)

    log(f"  ✓ {name}: {len(items)}件")
    return items, True


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
        if link in seen_links: continue
        seen_links.add(link)

        group     = source_group(item.get('source', ''))
        title_key = normalize_title_for_dedupe(item.get('title', ''))
        host      = normalize_host(link)
        story     = extract_story_source(item.get('title', '') + ' ' + item.get('summary', ''), link)
        soft_key  = make_soft_title_key(title_key)
        core_key  = make_core_title_key(title_key)

        candidate_keys = [
            f"{group}::{story}::{soft_key}",
            f"{group}::{host}::{soft_key}",
            f"global_story::{story}::{core_key}",
            f"global_host::{host}::{core_key}",
            f"global_title::{core_key}",
        ]

        matched_prev = None
        for bk in candidate_keys:
            for prev in buckets.get(bk, []):
                if is_probable_duplicate_item(item, prev):
                    matched_prev = prev
                    break
            if matched_prev is not None: break

        if matched_prev is not None:
            if item_rank(item) > item_rank(matched_prev):
                idx = deduped_pos[id(matched_prev)]
                deduped[idx] = item
                deduped_pos[id(item)] = idx
                del deduped_pos[id(matched_prev)]
                for bk, vals in buckets.items():
                    for i, v in enumerate(vals):
                        if v is matched_prev: vals[i] = item
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
.cb-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
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
var READ_KEY = 'market_plus_read';
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
        ("fx_impact.html",    "💱 為替影響"),
        ("central_bank.html", "🏦 中央銀行"),
        ("themes.html",       "🏷 テーマ別"),
        ("sources.html",      "🏛 注目ソース"),
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
      <h1>📊 {html.escape(APP_NAME)} <span style='font-weight:400;color:var(--muted);font-size:16px'>｜ {html.escape(CONFIG.get("subtitle", ""))}</span></h1>
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


# fx_impact値 → 表示ラベル・CSSクラス
FX_IMPACT_MAP = {
    "usd_strong": ("🟢 USD Bullish", "tag-usd-strong"),
    "usd_weak":   ("🔴 USD Bearish", "tag-usd-weak"),
    "jpy_strong": ("🔵 JPY Bullish", "tag-jpy-strong"),
    "jpy_weak":   ("🟡 JPY Bearish", "tag-jpy-weak"),
    "neutral":    ("➖ Neutral",      "tag-neutral"),
}

SENT_MAP = {
    "bullish": ("✅ 参考", "tag-bullish"),
    "bearish": ("⚠ 注意", "tag-bearish"),
    "neutral": ("➖ Neutral", "tag-neutral"),
}


def card_html(item: dict, themes_cfg: dict) -> str:
    card_id      = html.escape(item.get("id", ""))
    title_orig   = html.escape(item.get("title", ""))
    title_ja     = html.escape(item.get("title_ja", "") or item.get("title", ""))
    summary_orig = html.escape(item.get("summary", ""))
    summary_ja   = html.escape(item.get("summary_ja", "") or item.get("summary", ""))
    source       = html.escape(item.get("source", ""))
    published    = html.escape(item.get("published", ""))
    link         = html.escape(item.get("link", "#"))
    lang         = item.get("lang", "en")
    score        = item.get("score", 1)
    is_official  = item.get("official", False)
    themes       = item.get("themes", [])
    sentiment    = item.get("sentiment", "neutral")
    fx_impact    = item.get("fx_impact", "neutral")
    new_flag     = item.get("is_new", False)

    theme_tags = ""
    for t in themes:
        if t == "その他": continue
        td    = themes_cfg["themes"].get(t, {})
        color = td.get("color", "#636e72")
        icon  = td.get("icon", "")
        theme_tags += f"<span class='tag tag-theme' style='background:{color}22;border-color:{color};color:{color}'>{icon} {html.escape(t)}</span>"

    sent_label, sent_cls = SENT_MAP.get(sentiment, ("➖ Neutral", "tag-neutral"))
    fx_label,   fx_cls   = FX_IMPACT_MAP.get(fx_impact, ("➖ Neutral", "tag-neutral"))
    score_cls = f"tag-score{min(score,5)}"
    new_tag      = "<span class='tag tag-new'>🆕 NEW</span>" if new_flag else ""
    official_tag = "<span class='tag tag-official'>🏛 公式</span>" if is_official else ""
    lang_badge   = f"<span class='lang-badge {'ja' if lang=='ja' else 'en'}'>{'🇯🇵 国内' if lang=='ja' else '🌐 海外'}</span>"

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
  var fTheme='all',fLang='all',fScore='all',fView='cards',fSource='all',fSentiment='all',fFx='all',fNew=false,search='';
  function applyFilters(){{
    document.querySelectorAll('.item-card').forEach(function(card){{
      var show=true;
      if(fTheme!=='all' && !(card.dataset.themes||'').split('|').includes(fTheme)) show=false;
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


def render_index(items: list[dict], themes_cfg: dict) -> str:
    stamp = jst_stamp()
    total         = len(items)
    new_count     = sum(1 for x in items if x.get("is_new"))
    official_count= sum(1 for x in items if x.get("official"))
    usd_strong    = sum(1 for x in items if x.get("fx_impact") == "usd_strong")
    usd_weak      = sum(1 for x in items if x.get("fx_impact") == "usd_weak")
    jpy_strong    = sum(1 for x in items if x.get("fx_impact") == "jpy_strong")
    jpy_weak      = sum(1 for x in items if x.get("fx_impact") == "jpy_weak")

    theme_counts = Counter()
    for item in items:
        for t in item.get("themes", []): theme_counts[t] += 1

    source_list = sorted(set(x.get("source","") for x in items))

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
  <div class='stat'><div class='icon'>🟢</div><div class='num' style='color:#4ea1ff'>{usd_strong}</div><div class='label'>USD Bullish</div></div>
  <div class='stat'><div class='icon'>🔴</div><div class='num' style='color:#e74c3c'>{usd_weak}</div><div class='label'>USD Bearish</div></div>
  <div class='stat'><div class='icon'>🔵</div><div class='num' style='color:#2ecc71'>{jpy_strong}</div><div class='label'>JPY Bullish</div></div>
  <div class='stat'><div class='icon'>🟡</div><div class='num' style='color:#f39c12'>{jpy_weak}</div><div class='label'>JPY Bearish</div></div>
  <div class='stat'><div class='icon'>🏛</div><div class='num'>{official_count}</div><div class='label'>公式ソース</div></div>
</div>"""

    return (
        header_html("index.html", stamp)
        + f"""
<div class='main'>
{stat_grid}
<div class='panel'>
  <div class='filters'>{theme_btns}</div>
  <div class='filters'>
    <span class='filter-label'>地域:</span>
    <button class='flt-btn flt-lang active' data-lang='all'>🌍 全部</button>
    <button class='flt-btn flt-lang' data-lang='ja'>🇯🇵 国内</button>
    <button class='flt-btn flt-lang' data-lang='en'>🌐 海外</button>
    <span class='filter-label'>為替影響:</span>
    <button class='flt-btn flt-fx active' data-fx='all'>全部</button>
    <button class='flt-btn flt-fx' data-fx='usd_strong'>🟢 USD Bullish</button>
    <button class='flt-btn flt-fx' data-fx='usd_weak'>🔴 USD Bearish</button>
    <button class='flt-btn flt-fx' data-fx='jpy_strong'>🔵 JPY Bullish</button>
    <button class='flt-btn flt-fx' data-fx='jpy_weak'>🟡 JPY Bearish</button>
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


# ─── 為替影響ページ ───────────────────────────────────────────────

def render_fx_impact(items: list[dict], themes_cfg: dict) -> str:
    stamp = jst_stamp()

    tab_cfg = [
        ("usd_strong", "USD Bullish", "ドル高材料", "fx-usd-strong"),
        ("usd_weak",   "USD Bearish", "ドル安材料", "fx-usd-weak"),
        ("jpy_strong", "JPY Bullish", "円高材料",   "fx-jpy-strong"),
        ("jpy_weak",   "JPY Bearish", "円安材料",   "fx-jpy-weak"),
    ]
    cats = {key: [x for x in items if x.get("fx_impact") == key] for key, *_ in tab_cfg}
    neutral_count = sum(1 for x in items if x.get("fx_impact") == "neutral")

    fx_css = """
<style>
.fx-summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:18px}
.fx-summary-card{border-radius:16px;padding:18px;text-align:center;border:1px solid transparent}
.fx-summary-card .fx-num{font-size:36px;font-weight:900;margin-bottom:6px}
.fx-summary-card .fx-label{font-size:14px;font-weight:700}
.fx-summary-card .fx-desc{font-size:11px;color:var(--muted);margin-top:4px}
.fx-usd-strong{background:#0d1e35;border-color:#4ea1ff}
.fx-usd-weak{background:#2e0d0d;border-color:#e74c3c}
.fx-jpy-strong{background:#0d2e1a;border-color:#2ecc71}
.fx-jpy-weak{background:#2e2400;border-color:#f39c12}
.fx-note{margin:-4px 0 16px;color:var(--muted);font-size:12px}
.fx-tabs{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.fx-panel-note{margin:0 0 12px;color:var(--muted);font-size:12px}
.fx-empty{color:var(--muted);padding:20px;border:1px dashed var(--line);border-radius:14px;background:rgba(255,255,255,.02)}
.fx-article-filter{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 16px;align-items:center}
.fx-article-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
</style>"""

    summary_cards = f"""
<div class='fx-summary-grid'>
  <div class='fx-summary-card fx-usd-strong'>
    <div class='fx-num' style='color:#4ea1ff'>{len(cats['usd_strong'])}</div>
    <div class='fx-label'>USD Bullish</div><div class='fx-desc'>ドル高材料</div>
  </div>
  <div class='fx-summary-card fx-usd-weak'>
    <div class='fx-num' style='color:#e74c3c'>{len(cats['usd_weak'])}</div>
    <div class='fx-label'>USD Bearish</div><div class='fx-desc'>ドル安材料</div>
  </div>
  <div class='fx-summary-card fx-jpy-strong'>
    <div class='fx-num' style='color:#2ecc71'>{len(cats['jpy_strong'])}</div>
    <div class='fx-label'>JPY Bullish</div><div class='fx-desc'>円高材料</div>
  </div>
  <div class='fx-summary-card fx-jpy-weak'>
    <div class='fx-num' style='color:#f39c12'>{len(cats['jpy_weak'])}</div>
    <div class='fx-label'>JPY Bearish</div><div class='fx-desc'>円安材料</div>
  </div>
  <div class='fx-summary-card' style='background:var(--panel2);border-color:var(--line)'>
    <div class='fx-num' style='color:var(--muted)'>{neutral_count}</div>
    <div class='fx-label'>Neutral</div><div class='fx-desc'>中立</div>
  </div>
</div>"""

    note_map = {key: desc_ja for key, _label_en, desc_ja, _cls in tab_cfg}
    tabs_html = []
    for i, (key, label_en, _desc_ja, accent_cls) in enumerate(tab_cfg):
        active = 'active' if i == 0 else ''
        count_all = len(cats[key])
        tabs_html.append(
            f'<button class="flt-btn flt-fx {active}" data-key="{key}" data-accent="{accent_cls}">{html.escape(label_en)} ({count_all})</button>'
        )

    cards = []
    for key, *_rest in tab_cfg:
        for it in cats[key]:
            raw_lang = (it.get('lang', 'en') or 'en').lower()
            norm_lang = 'ja' if raw_lang in {'ja', 'jp', 'japanese', '日本語'} else 'en'
            cards.append(f"<div class='fx-article-wrap' data-impact='{key}' data-lang='{norm_lang}'>{card_html(it, themes_cfg)}</div>")
    cards_html = ''.join(cards)

    note_json = json.dumps(note_map, ensure_ascii=False)
    script = f"""
<script>
var fxActiveImpact = 'usd_strong';
var fxArticleLang = 'all';
var fxNoteMap = {note_json};
function refreshFxView() {{
  var hasVisible = false;
  document.querySelectorAll('.fx-article-wrap').forEach(function(wrap) {{
    var impact = (wrap.dataset.impact || '').toLowerCase();
    var lang = (wrap.dataset.lang || 'en').toLowerCase();
    var show = (impact === fxActiveImpact) && (fxArticleLang === 'all' || lang === fxArticleLang);
    wrap.style.display = show ? '' : 'none';
    if(show) hasVisible = true;
  }});
  var note = document.getElementById('fxPanelNote');
  if(note) note.textContent = fxNoteMap[fxActiveImpact] || '';
  var empty = document.getElementById('fxEmptyState');
  if(empty) {{
    if(hasVisible) {{ empty.style.display = 'none'; }}
    else {{
      empty.style.display = '';
      if(fxArticleLang === 'ja') empty.textContent = 'この区分の日本語記事はありません。';
      else if(fxArticleLang === 'en') empty.textContent = 'この区分の English 記事はありません。';
      else empty.textContent = 'この条件に合う記事はありません。';
    }}
  }}
}}
function switchFxTab(key, btn) {{
  fxActiveImpact = key;
  document.querySelectorAll('.flt-fx').forEach(function(b) {{ b.classList.remove('active'); }});
  if(btn) btn.classList.add('active');
  refreshFxView();
}}
function switchFxArticleLang(lang, btn) {{
  fxArticleLang = lang;
  document.querySelectorAll('.flt-fx-lang').forEach(function(b) {{ b.classList.remove('active'); }});
  if(btn) btn.classList.add('active');
  refreshFxView();
}}
document.addEventListener('DOMContentLoaded', function() {{
  document.querySelectorAll('.flt-fx').forEach(function(btn) {{
    btn.addEventListener('click', function() {{ switchFxTab(btn.dataset.key || 'usd_strong', btn); }});
  }});
  document.querySelectorAll('.flt-fx-lang').forEach(function(btn) {{
    btn.addEventListener('click', function() {{ switchFxArticleLang(btn.dataset.lang || 'all', btn); }});
  }});
  refreshFxView();
}});
</script>"""

    body = (
        fx_css
        + "<div class='main'>"
        + summary_cards
        + "<div class='fx-note'>為替影響タブでは記事言語を切り替えできます。</div>"
        + "<div class='fx-tabs'>" + ''.join(tabs_html) + "</div>"
        + "<div class='filters fx-article-filter'>"
        + "<button class='flt-btn flt-fx-lang active' data-lang='all'>🌍 全部</button>"
        + "<button class='flt-btn flt-fx-lang' data-lang='ja'>🇯🇵 国内</button>"
        + "<button class='flt-btn flt-fx-lang' data-lang='en'>🌐 海外</button>"
        + "</div>"
        + "<div class='fx-panel-note' id='fxPanelNote'>ドル高材料</div>"
        + "<div class='fx-article-grid'>" + cards_html + "</div>"
        + "<div class='fx-empty' id='fxEmptyState' style='display:none'>この条件に合う記事はありません。</div>"
        + "</div>"
        + script
    )
    return header_html("fx_impact.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


# ─── 中央銀行ページ ───────────────────────────────────────────────

def render_central_bank(items: list[dict], themes_cfg: dict) -> str:
    stamp = jst_stamp()
    cb_sources = {
        "Federal Reserve":          {"icon": "🇺🇸", "desc": "米連邦準備制度"},
        "Federal Reserve Monetary Policy": {"icon": "🇺🇸", "desc": "FRB金融政策リリース"},
        "ECB":                      {"icon": "🇪🇺", "desc": "欧州中央銀行"},
        "日本銀行":                  {"icon": "🇯🇵", "desc": "Bank of Japan"},
        "Bank of Japan Statistics": {"icon": "🇯🇵", "desc": "日本銀行 統計"},
        "BIS":                      {"icon": "🌐",  "desc": "国際決済銀行"},
    }
    cb_grid_html = ""
    for src_name, meta in cb_sources.items():
        src_items = [x for x in items if x.get("source") == src_name]
        latest    = src_items[0].get("published", "N/A") if src_items else "記事なし"
        cb_grid_html += f"""
<div class='cb-card'>
  <div class='cb-name'>{meta['icon']} {html.escape(src_name)}</div>
  <div style='color:var(--muted);font-size:12px;margin-bottom:8px'>{html.escape(meta['desc'])}</div>
  <div class='cb-count'>{len(src_items)}</div>
  <div style='color:var(--muted);font-size:11px'>件の記事</div>
  <div class='cb-recent'>最新: {html.escape(latest)}</div>
</div>"""

    cb_items   = [x for x in items if "金利・中央銀行" in x.get("themes", []) or x.get("official")]
    cards_html = "".join(card_html(it, themes_cfg) for it in cb_items)

    cb_filter = f"""
<div class='panel'>
  <div class='filters'>
    <span class='filter-label'>中央銀行:</span>
    <button class='flt-btn flt-cb-bank active' data-bank='all'>🏦 全行</button>
    <button class='flt-btn flt-cb-bank' data-bank='Federal Reserve'>🇺🇸 FRB</button>
    <button class='flt-btn flt-cb-bank' data-bank='ECB'>🇪🇺 ECB</button>
    <button class='flt-btn flt-cb-bank' data-bank='日本銀行'>🇯🇵 日銀</button>
    <button class='flt-btn flt-cb-bank' data-bank='BIS'>🌐 BIS</button>
  </div>
  <div class='filters'>
    <span class='filter-label'>地域:</span>
    <button class='flt-btn flt-lang-cb active' data-lang='all'>🌍 全部</button>
    <button class='flt-btn flt-lang-cb' data-lang='ja'>🇯🇵 国内</button>
    <button class='flt-btn flt-lang-cb' data-lang='en'>🌐 海外</button>
    <span class='filter-label'>為替影響:</span>
    <button class='flt-btn flt-fx-cb active' data-fx='all'>全部</button>
    <button class='flt-btn flt-fx-cb' data-fx='usd_strong'>🟢 USD Bullish</button>
    <button class='flt-btn flt-fx-cb' data-fx='usd_weak'>🔴 USD Bearish</button>
    <button class='flt-btn flt-fx-cb' data-fx='jpy_strong'>🔵 JPY Bullish</button>
    <button class='flt-btn flt-fx-cb' data-fx='jpy_weak'>🟡 JPY Bearish</button>
    <button class='flt-btn flt-view-cb active' data-view='cards'>▦ カード</button>
    <button class='flt-btn flt-view-cb' data-view='list'>≡ リスト</button>
    <input id='searchBoxCB' class='search' placeholder='タイトル・本文を検索...'>
  </div>
</div>
<script>
(function(){{
  var fBank='all', fLang='all', fFx='all', fView='cards', search='';
  function apply(){{
    var visible=0;
    document.querySelectorAll('#cbGrid .item-card').forEach(function(card){{
      var show=true;
      if(fBank!=='all' && (card.dataset.source||'')!==fBank) show=false;
      if(fLang!=='all' && (card.dataset.lang||'')!==fLang) show=false;
      if(fFx!=='all' && (card.dataset.importance||'')!==fFx) show=false;
      if(search && !(card.textContent||'').toLowerCase().includes(search.toLowerCase())) show=false;
      card.style.display=show?'':'none';
      if(show) visible++;
    }});
    document.getElementById('cbGrid').classList.toggle('list', fView==='list');
    var cnt=document.getElementById('cbCount');
    if(cnt) cnt.textContent=visible;
  }}
  document.querySelectorAll('.flt-cb-bank').forEach(function(btn){{
    btn.addEventListener('click',function(){{
      document.querySelectorAll('.flt-cb-bank').forEach(function(b){{b.classList.remove('active');}});
      btn.classList.add('active'); fBank=btn.dataset.bank; apply();
    }});
  }});
  document.querySelectorAll('.flt-lang-cb').forEach(function(btn){{
    btn.addEventListener('click',function(){{
      document.querySelectorAll('.flt-lang-cb').forEach(function(b){{b.classList.remove('active');}});
      btn.classList.add('active'); fLang=btn.dataset.lang; apply();
    }});
  }});
  document.querySelectorAll('.flt-fx-cb').forEach(function(btn){{
    btn.addEventListener('click',function(){{
      document.querySelectorAll('.flt-fx-cb').forEach(function(b){{b.classList.remove('active');}});
      btn.classList.add('active'); fFx=btn.dataset.fx; apply();
    }});
  }});
  document.querySelectorAll('.flt-view-cb').forEach(function(btn){{
    btn.addEventListener('click',function(){{
      document.querySelectorAll('.flt-view-cb').forEach(function(b){{b.classList.remove('active');}});
      btn.classList.add('active'); fView=btn.dataset.view; apply();
    }});
  }});
  var sb=document.getElementById('searchBoxCB');
  if(sb) sb.addEventListener('input',function(){{search=this.value; apply();}});
  apply();
}})();
</script>"""

    body = f"""
<div class='main'>
  <div class='panel' style='margin-bottom:16px'>
    <h2 style='margin:0 0 14px;font-size:16px'>🏦 中央銀行モニター</h2>
    <div class='cb-grid'>{cb_grid_html}</div>
  </div>
  {cb_filter}
  <div style='margin-bottom:12px;font-weight:700;font-size:15px'>金利・中銀関連記事（<span id='cbCount'>{len(cb_items)}</span>件）</div>
  <div class='grid' id='cbGrid'>{cards_html}</div>
</div>"""
    return header_html("central_bank.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


# ─── テーマ別ページ ───────────────────────────────────────────────

def render_themes(items: list[dict], themes_cfg: dict) -> str:
    stamp = jst_stamp()
    sections_html = ""
    for tk, td in themes_cfg["themes"].items():
        icon     = td.get("icon", "")
        color    = td.get("color", "#636e72")
        label_en = td.get("label_en", tk)
        theme_items = [x for x in items if tk in x.get("themes", [])]
        if not theme_items: continue
        cards = "".join(card_html(it, themes_cfg) for it in theme_items)
        sections_html += f"""
<div class='theme-section' data-section='{html.escape(tk)}'>
  <div class='theme-header'>
    <span class='theme-badge' style='background:{color}22;border-color:{color};color:{color}'>{icon} {html.escape(tk)}</span>
    <span style='color:var(--muted);font-size:13px'>{html.escape(label_en)} · <span class='section-count'>{len(theme_items)}</span>件</span>
  </div>
  <div class='grid theme-grid'>{cards}</div>
</div>"""

    theme_jump_btns = "<button class='flt-btn flt-theme-sec active' data-sec='all'>📋 全テーマ</button>"
    for tk2, td2 in themes_cfg["themes"].items():
        icon2  = td2.get("icon", "")
        color2 = td2.get("color", "#636e72")
        if not any(tk2 in x.get("themes", []) for x in items): continue
        theme_jump_btns += f"<button class='flt-btn flt-theme-sec' data-sec='{html.escape(tk2)}' style='border-color:{color2};color:{color2}'>{icon2} {html.escape(tk2)}</button>"

    filter_bar = (
        "<div class='panel'>"
        "<div class='filters'><span class='filter-label'>テーマ:</span>"
        + theme_jump_btns +
        "</div>"
        "<div class='filters'>"
        "<span class='filter-label'>地域:</span>"
        "<button class='flt-btn flt-lang active' data-lang='all'>🌍 全部</button>"
        "<button class='flt-btn flt-lang' data-lang='ja'>🇯🇵 国内</button>"
        "<button class='flt-btn flt-lang' data-lang='en'>🌐 海外</button>"
        "<span class='filter-label'>為替影響:</span>"
        "<button class='flt-btn flt-fx-t active' data-fx='all'>全部</button>"
        "<button class='flt-btn flt-fx-t' data-fx='usd_strong'>🟢 USD Bullish</button>"
        "<button class='flt-btn flt-fx-t' data-fx='usd_weak'>🔴 USD Bearish</button>"
        "<button class='flt-btn flt-fx-t' data-fx='jpy_strong'>🔵 JPY Bullish</button>"
        "<button class='flt-btn flt-fx-t' data-fx='jpy_weak'>🟡 JPY Bearish</button>"
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
  document.querySelectorAll('.flt-theme-sec').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('.flt-theme-sec').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active');
      fSec = btn.dataset.sec;
      applyThemeFilters();
      if(fSec !== 'all'){
        setTimeout(function(){
          var el = document.querySelector('.theme-section[data-section="' + fSec + '"]');
          if(el){
            var hdr = document.querySelector('header');
            var offset = hdr ? hdr.offsetHeight + 12 : 80;
            var top = el.getBoundingClientRect().top + window.pageYOffset - offset;
            window.scrollTo({top: top, behavior: 'smooth'});
          }
        }, 50);
      }
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


# ─── 注目ソースページ ─────────────────────────────────────────────

def render_sources(items: list[dict], themes_cfg: dict) -> str:
    stamp = jst_stamp()
    source_meta = {
        "Federal Reserve":          {"icon": "🏦", "desc": "FRBの公式リリース"},
        "Federal Reserve Monetary Policy": {"icon": "🏦", "desc": "FRBの金融政策関連"},
        "ECB":                      {"icon": "🏦", "desc": "欧州中央銀行"},
        "日本銀行":                  {"icon": "🏦", "desc": "日本銀行の新着情報"},
        "Bank of Japan Statistics": {"icon": "🏦", "desc": "日本銀行の統計公表"},
        "BIS":                      {"icon": "🌐", "desc": "国際決済銀行"},
        "BLS（米労働統計）":         {"icon": "📊", "desc": "米労働統計局"},
        "BEA News Releases":        {"icon": "📊", "desc": "米BEAの経済統計・ニュース"},
        "U.S. Census Economic Indicators": {"icon": "📊", "desc": "米国勢調査局の経済指標"},
        "METI News Releases":       {"icon": "🏛", "desc": "経済産業省のニュースリリース"},
        "METI Statistics":          {"icon": "📊", "desc": "経済産業省の統計情報"},
        "財務省":                    {"icon": "🏛", "desc": "財務省の新着・公表資料"},
        "内閣府":                    {"icon": "🏛", "desc": "内閣府の政策・経済関連情報"},
        "NVIDIA Press Room":        {"icon": "💾", "desc": "NVIDIAの公式リリース"},
        "Intel Press Releases":     {"icon": "💾", "desc": "Intelの公式リリース"},
    }

    source_buttons = [("all", "📊 全部")]
    for src_name, meta in source_meta.items():
        short = (src_name
            .replace("Federal Reserve Monetary Policy", "FRB金融政策")
            .replace("Federal Reserve", "FRB")
            .replace("Bank of Japan Statistics", "日銀統計")
            .replace("BLS（米労働統計）", "BLS")
            .replace("BEA News Releases", "BEA")
            .replace("U.S. Census Economic Indicators", "米Census")
            .replace("METI News Releases", "経産省")
            .replace("METI Statistics", "経産省統計")
            .replace("NVIDIA Press Room", "NVIDIA")
            .replace("Intel Press Releases", "Intel"))
        source_buttons.append((src_name, f"{meta['icon']} {short}"))

    display_sources = []
    for src_name, meta in source_meta.items():
        src_items = [x for x in items if x.get("source") == src_name]
        latest = src_items[0].get("published", "記事なし") if src_items else "記事なし"
        display_sources.append((src_name, meta, src_items, latest))

    cb_grid_html = ""
    for src_name, meta, src_items, latest in display_sources:
        cb_grid_html += f"""
<div class='cb-card' data-source-card='{html.escape(src_name)}'>
  <div class='cb-name'>{meta['icon']} {html.escape(src_name)}</div>
  <div style='color:var(--muted);font-size:12px;margin-bottom:8px'>{html.escape(meta['desc'])}</div>
  <div class='cb-count'>{len(src_items)}</div>
  <div style='color:var(--muted);font-size:11px'>件の記事</div>
  <div class='cb-recent'>最新: {html.escape(latest)}</div>
</div>"""

    cb_items = [x for x in items if x.get("official") or x.get("source") in source_meta]
    cards_html = "".join(card_html(it, themes_cfg) for it in cb_items)

    source_btns_html = "\n".join(
        f"<button class='flt-btn flt-cb-bank{' active' if key == 'all' else ''}' data-bank='{html.escape(key)}'>{label}</button>"
        for key, label in source_buttons
    )

    cb_filter = f"""
<div class='panel'>
  <div class='filters'>
    <span class='filter-label'>注目ソース:</span>
    {source_btns_html}
  </div>
  <div class='filters'>
    <span class='filter-label'>地域:</span>
    <button class='flt-btn flt-lang-cb active' data-lang='all'>🌍 全部</button>
    <button class='flt-btn flt-lang-cb' data-lang='ja'>🇯🇵 国内</button>
    <button class='flt-btn flt-lang-cb' data-lang='en'>🌐 海外</button>
    <span class='filter-label'>為替影響:</span>
    <button class='flt-btn flt-fx-cb active' data-fx='all'>全部</button>
    <button class='flt-btn flt-fx-cb' data-fx='usd_strong'>🟢 USD Bullish</button>
    <button class='flt-btn flt-fx-cb' data-fx='usd_weak'>🔴 USD Bearish</button>
    <button class='flt-btn flt-fx-cb' data-fx='jpy_strong'>🔵 JPY Bullish</button>
    <button class='flt-btn flt-fx-cb' data-fx='jpy_weak'>🟡 JPY Bearish</button>
    <button class='flt-btn flt-view-cb active' data-view='cards'>▦ カード</button>
    <button class='flt-btn flt-view-cb' data-view='list'>≡ リスト</button>
    <input id='searchBoxCB' class='search' placeholder='タイトル・本文を検索...'>
  </div>
</div>
<script>
(function(){{
  var fBank='all', fLang='all', fFx='all', fView='cards', search='';
  function apply(){{
    var visible=0;
    document.querySelectorAll('#cbGrid .item-card').forEach(function(card){{
      var show=true;
      if(fBank!=='all' && (card.dataset.source||'')!==fBank) show=false;
      if(fLang!=='all' && (card.dataset.lang||'')!==fLang) show=false;
      if(fFx!=='all' && (card.dataset.importance||'')!==fFx) show=false;
      if(search && !(card.textContent||'').toLowerCase().includes(search.toLowerCase())) show=false;
      card.style.display=show?'':'none';
      if(show) visible++;
    }});
    document.querySelectorAll('.cb-card').forEach(function(card){{
      var src = card.dataset.sourceCard || '';
      card.style.display = (fBank==='all' || src===fBank) ? '' : 'none';
    }});
    var grid=document.getElementById('cbGrid');
    if(grid) grid.classList.toggle('list', fView==='list');
    var cnt=document.getElementById('cbCount');
    if(cnt) cnt.textContent=visible;
    var ttl=document.getElementById('cbTitleLabel');
    if(ttl){{
      var activeBtn = document.querySelector('.flt-cb-bank.active');
      var activeText = activeBtn ? (activeBtn.textContent || '').trim() : '';
      var activeLabel = activeText.indexOf(' ') >= 0 ? activeText.split(' ').slice(1).join(' ').trim() : activeText;
      ttl.textContent = (fBank==='all' || !activeLabel) ? '注目ソース関連記事' : (activeLabel + ' 関連記事');
    }}
  }}
  document.querySelectorAll('.flt-cb-bank').forEach(function(btn){{
    btn.addEventListener('click',function(){{
      document.querySelectorAll('.flt-cb-bank').forEach(function(b){{b.classList.remove('active');}});
      btn.classList.add('active'); fBank=btn.dataset.bank; apply();
    }});
  }});
  document.querySelectorAll('.flt-lang-cb').forEach(function(btn){{
    btn.addEventListener('click',function(){{
      document.querySelectorAll('.flt-lang-cb').forEach(function(b){{b.classList.remove('active');}});
      btn.classList.add('active'); fLang=btn.dataset.lang; apply();
    }});
  }});
  document.querySelectorAll('.flt-fx-cb').forEach(function(btn){{
    btn.addEventListener('click',function(){{
      document.querySelectorAll('.flt-fx-cb').forEach(function(b){{b.classList.remove('active');}});
      btn.classList.add('active'); fFx=btn.dataset.fx; apply();
    }});
  }});
  document.querySelectorAll('.flt-view-cb').forEach(function(btn){{
    btn.addEventListener('click',function(){{
      document.querySelectorAll('.flt-view-cb').forEach(function(b){{b.classList.remove('active');}});
      btn.classList.add('active'); fView=btn.dataset.view; apply();
    }});
  }});
  var sb=document.getElementById('searchBoxCB');
  if(sb) sb.addEventListener('input',function(){{search=this.value; apply();}});
  document.querySelectorAll('.cb-card').forEach(function(card){{
    card.style.cursor='pointer';
    card.addEventListener('click', function(){{
      var src = card.dataset.sourceCard || 'all';
      document.querySelectorAll('.flt-cb-bank').forEach(function(b){{
        var active = (b.dataset.bank || '') === src;
        b.classList.toggle('active', active);
      }});
      fBank = src; apply();
    }});
  }});
  apply();
}})();
</script>"""

    body = f"""
<div class='main'>
  <div class='panel' style='margin-bottom:16px'>
    <h2 style='margin:0 0 14px;font-size:16px'>📊 注目ソースモニター</h2>
    <div class='cb-grid'>{cb_grid_html}</div>
  </div>
  {cb_filter}
  <div style='margin-bottom:12px;font-weight:700;font-size:15px'><span id='cbTitleLabel'>注目ソース関連記事</span>（<span id='cbCount'>{len(cb_items)}</span>件）</div>
  <div class='grid' id='cbGrid'>{cards_html}</div>
</div>"""
    return header_html("sources.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


# ─── 分析ページ ───────────────────────────────────────────────────

def render_analysis(items: list[dict], themes_cfg: dict) -> str:
    stamp = jst_stamp()
    source_counts = Counter(x.get("source", "") for x in items)
    theme_counts  = Counter()
    sent_counts   = Counter(x.get("sentiment", "neutral") for x in items)
    fx_counts     = Counter(x.get("fx_impact", "neutral") for x in items)
    market_counts = Counter(x.get("market_context", "neutral") for x in items)
    for item in items:
        for t in item.get("themes", []):
            theme_counts[t] += 1

    top_theme_cards = ""
    for theme, cnt in theme_counts.most_common(3):
        td_info = themes_cfg["themes"].get(theme, {})
        icon  = td_info.get("icon", "")
        color = td_info.get("color", "#636e72")
        top_theme_cards += f"<div class='mini-stat'><div style='font-size:13px;color:{color};font-weight:800'>{icon} {html.escape(theme)}</div><div style='font-size:28px;font-weight:800'>{cnt}</div></div>"

    cb_count = sum(1 for x in items if "金利・中央銀行" in x.get("themes", []))
    official_count = sum(1 for x in items if x.get("official"))
    bull = sent_counts.get("bullish", 0)
    bear = sent_counts.get("bearish", 0)
    neu  = sent_counts.get("neutral", 0)
    total_sent = bull + bear + neu or 1

    src_rows = ""
    for src, cnt in source_counts.most_common():
        src_items = [x for x in items if x.get("source") == src]
        official  = "🏛 公式" if any(x.get("official") for x in src_items) else ""
        lang      = src_items[0].get("lang", "") if src_items else ""
        lang_str  = "🇯🇵 国内" if lang == "ja" else "🌐 海外"
        src_rows += f"<tr><td>{html.escape(src)}</td><td>{lang_str}</td><td>{official}</td><td style='font-weight:800'>{cnt}</td></tr>"

    theme_rows = ""
    for t, cnt in theme_counts.most_common():
        td_info = themes_cfg["themes"].get(t, {})
        icon    = td_info.get("icon", "")
        color   = td_info.get("color", "#636e72")
        theme_rows += f"<tr><td><span style='color:{color}'>{icon} {html.escape(t)}</span></td><td style='font-weight:800'>{cnt}</td></tr>"

    table_css = ".table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}.table th,.table td{padding:10px 14px;border-bottom:1px solid var(--line);text-align:left}.table th{background:var(--panel2);font-size:13px;color:var(--muted)}.table tr:last-child td{border-bottom:none}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}.three-col{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.four-col{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.mini-stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}.summary-row{display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:16px;margin-bottom:16px}@media(max-width:900px){.two-col,.three-col,.four-col,.summary-row{grid-template-columns:1fr}}"

    sentiment_html = f"""
<div class='panel' style='margin-bottom:16px'>
  <h2 style='margin:0 0 14px;font-size:16px'>📌 今日のサマリー</h2>
  <div class='summary-row'>
    <div>
      <div style='font-size:13px;color:var(--muted);margin-bottom:10px'>トップテーマ</div>
      <div class='three-col'>{top_theme_cards or "<div class='mini-stat'>データなし</div>"}</div>
    </div>
    <div>
      <div style='font-size:13px;color:var(--muted);margin-bottom:10px'>為替方向まとめ</div>
      <div class='four-col'>
        <div class='mini-stat'><div style='font-size:12px;color:var(--muted)'>🟢 USD Bullish</div><div style='font-size:26px;font-weight:800'>{fx_counts.get("usd_strong",0)}</div></div>
        <div class='mini-stat'><div style='font-size:12px;color:var(--muted)'>🔴 USD Bearish</div><div style='font-size:26px;font-weight:800'>{fx_counts.get("usd_weak",0)}</div></div>
        <div class='mini-stat'><div style='font-size:12px;color:var(--muted)'>🔵 JPY Bullish</div><div style='font-size:26px;font-weight:800'>{fx_counts.get("jpy_strong",0)}</div></div>
        <div class='mini-stat'><div style='font-size:12px;color:var(--muted)'>🟡 JPY Bearish</div><div style='font-size:26px;font-weight:800'>{fx_counts.get("jpy_weak",0)}</div></div>
      </div>
    </div>
    <div>
      <div style='font-size:13px;color:var(--muted);margin-bottom:10px'>市場センチメント</div>
      <div class='three-col'>
        <div class='mini-stat'><div style='font-size:12px;color:var(--muted)'>🟢 Risk On</div><div style='font-size:26px;font-weight:800'>{market_counts.get("risk_on",0)}</div></div>
        <div class='mini-stat'><div style='font-size:12px;color:var(--muted)'>🔴 Risk Off</div><div style='font-size:26px;font-weight:800'>{market_counts.get("risk_off",0)}</div></div>
        <div class='mini-stat'><div style='font-size:12px;color:var(--muted)'>🏦 中央銀行</div><div style='font-size:26px;font-weight:800'>{cb_count}</div></div>
      </div>
    </div>
  </div>
  <div class='two-col'>
    <div>
      <div style='font-size:13px;color:var(--muted);margin-bottom:10px'>参考度</div>
      <div style='display:flex;gap:20px;flex-wrap:wrap'>
        <div style='text-align:center'><div style='font-size:28px;font-weight:800;color:#2ecc71'>{bull}</div><div style='color:var(--muted);font-size:12px'>✅ 参考 ({bull*100//total_sent}%)</div></div>
        <div style='text-align:center'><div style='font-size:28px;font-weight:800;color:#e74c3c'>{bear}</div><div style='color:var(--muted);font-size:12px'>⚠ 注意 ({bear*100//total_sent}%)</div></div>
        <div style='text-align:center'><div style='font-size:28px;font-weight:800;color:#9ba9bc'>{neu}</div><div style='color:var(--muted);font-size:12px'>➖ Neutral ({neu*100//total_sent}%)</div></div>
      </div>
    </div>
    <div>
      <div style='font-size:13px;color:var(--muted);margin-bottom:10px'>補足</div>
      <div style='display:flex;gap:14px;flex-wrap:wrap'>
        <div class='mini-stat' style='flex:1'><div style='font-size:12px;color:var(--muted)'>公式ソース件数</div><div style='font-size:26px;font-weight:800'>{official_count}</div></div>
        <div class='mini-stat' style='flex:1'><div style='font-size:12px;color:var(--muted)'>総記事数</div><div style='font-size:26px;font-weight:800'>{len(items)}</div></div>
      </div>
    </div>
  </div>
</div>"""

    body = f"""
<style>{table_css}</style>
<div class='main'>
  {sentiment_html}
  <div class='two-col'>
    <div class='panel'>
      <h2 style='margin:0 0 14px;font-size:16px'>📡 ソース別記事数</h2>
      <table class='table'><thead><tr><th>ソース</th><th>地域</th><th>種別</th><th>件数</th></tr></thead><tbody>{src_rows}</tbody></table>
    </div>
    <div class='panel'>
      <h2 style='margin:0 0 14px;font-size:16px'>🏷 テーマ別記事数</h2>
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
    fx_label, fx_cls = FX_IMPACT_MAP.get(fx_impact, ("➖","tag-neutral"))
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

    fx_label, fx_cls = FX_IMPACT_MAP.get(fx_impact, ("➖","tag-neutral"))
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
    stamp = jst_stamp()
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
    <button class='reading-tab' onclick="switchPickupTab('ja',this)">🇯🇵 国内</button>
    <button class='reading-tab' onclick="switchPickupTab('en',this)">🌐 海外</button>
  </div>
  <div class='reading-panel active' id='pickup-tab-mix'>
    <div class='sec-hdr'><h2>🌐 全部 注目記事</h2><span class='count-badge'>{len(mix_sorted)}件</span></div>
    {mix_tab}
  </div>
  <div class='reading-panel' id='pickup-tab-ja'>
    <div class='sec-hdr'><h2>🇯🇵 国内 注目記事</h2><span class='count-badge'>{len(ja_sorted)}件</span></div>
    {ja_tab}
  </div>
  <div class='reading-panel' id='pickup-tab-en'>
    <div class='sec-hdr'><h2>🌐 海外 注目記事</h2><span class='count-badge'>{len(en_sorted)}件</span></div>
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
    log(f"📊 {APP_NAME}")
    log("=" * 50)

    sources    = json.loads((SHARED / "sources.json").read_text(encoding="utf-8"))
    themes_cfg = json.loads((SHARED / "themes.json").read_text(encoding="utf-8"))

    log(f"\n📡 {len(sources)} ソースからデータ収集開始...\n")
    items, ok, err = collect_all(sources, themes_cfg)

    if not items:
        log("⚠ 記事が取得できませんでした。")
        sys.exit(1)

    conn = db_init()
    db_save_items(conn, items)
    db_save_run(conn, len(items), ok, err)
    db_cleanup(conn)
    conn.close()

    cache = load_json(TRANSLATE_CACHE_FILE, {})
    translate_items(items, cache)

    log("\n🖊 HTML生成中...")
    pages = [
        ("index.html",        render_index(items, themes_cfg)),
        ("pickup.html",       render_pickup(items, themes_cfg)),
        ("fx_impact.html",    render_fx_impact(items, themes_cfg)),
        ("central_bank.html", render_central_bank(items, themes_cfg)),
        ("themes.html",       render_themes(items, themes_cfg)),
        ("sources.html",      render_sources(items, themes_cfg)),
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
