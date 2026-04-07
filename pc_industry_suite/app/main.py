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

import calendar, hashlib, html, json, re, sqlite3, time, unicodedata
from difflib import SequenceMatcher
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlparse

import feedparser, requests
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
DB_FILE              = OUTPUT / CONFIG.get("db_name", "pc_industry_suite.db")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 PCIndustrySuite"
TIMEOUT    = 15

PC_CORE_TERMS = [
    "pc","laptop","notebook","desktop","workstation","gaming pc","mini pc","cpu","processor","gpu","graphics","chip","ai pc","npu","motherboard","ssd","windows","linux","surface","thinkpad","macbook","geforce","rtx","radeon","ryzen","core ultra","snapdragon x","software","app","browser","security","update","patch","copilot","utility","notepad","powershell","edge","onedrive","office",
    "ノートpc","デスクトップ","cpu","gpu","半導体","チップ","windows","ai pc","npu","ワークステーション","ゲーミングpc","ソフト","アプリ","ブラウザ","セキュリティ","アップデート","更新プログラム","ユーティリティ"
]
PC_NEGATIVE_TERMS = [
    "smartphone","phone","android phone","earbuds","headphones","smartwatch","wearable","vacuum","robot vacuum","projector","camera phone","car audio","soundbar","tv","television","monitor arm","mwc","xiaomi 15","galaxy s","iphone","pixel 9","oneplus",
    "イヤホン","ヘッドホン","スマートウォッチ","スマホ","スマートフォン","タブレット単体","掃除機","テレビ"
]
NO_DATE_LARGE_FEED_THRESHOLD = 100

def get_source_accept_cap(source_name: str, raw_feed_count: int) -> int | None:
    name = text_lower(source_name)
    if "pc watch" in name:
        return 16
    if "窓の杜" in source_name:
        return 16
    if "itmedia pc user" in name:
        return 16
    if "notebookcheck" in name:
        return 48
    if "tom's hardware" in name:
        return 28
    if "ars technica" in name:
        return 10
    if "lenovo" in name:
        return 18
    if raw_feed_count > 250:
        return 80
    return None

def is_broad_source_pc(source_name: str) -> bool:
    s = text_lower(source_name)
    return any(k in s for k in ["notebookcheck", "tom's hardware", "ars technica", "lenovo storyhub"])

def source_hint_keywords_pc(source_name: str) -> list[str]:
    s = text_lower(source_name)
    mapping = {
        "windows": ["windows","surface","copilot+","copilot pc"],
        "nvidia": ["nvidia","geforce","rtx","gpu","graphics"],
        "intel": ["intel","core ultra","xeon","arc","processor"],
        "lenovo": ["lenovo","thinkpad","thinkbook","legion","yoga","ideapad"],
        "amd": ["amd","ryzen","radeon","epyc","threadripper"],
        "pc watch": ["pc","ノートpc","デスクトップ","cpu","gpu","ssd","windows","ai pc","半導体","intel","amd","nvidia","asus","lenovo","dell","hp","acer","msi"],
        "itmedia": ["pc","ノートpc","デスクトップ","windows","cpu","gpu","レビュー","実機","intel","amd","nvidia","surface","macbook","thinkpad"],
        "窓の杜": ["windows","アプリ","ソフト","セキュリティ","アップデート","ブラウザ","microsoft","explorer","copilot","onedrive","edge","powertoys"],
        "ars technica": ["pc","windows","linux","cpu","gpu","chip","intel","amd","nvidia","surface","thinkpad","macbook","laptop","desktop"],
        "tom's hardware": ["pc","cpu","gpu","graphics","laptop","desktop","ssd","monitor"],
        "notebookcheck": ["laptop","notebook","gaming laptop","cpu","gpu","ryzen","core ultra","snapdragon x","macbook","surface","thinkpad","zenbook","rog","workstation","mini pc"],
        "videocardz": ["gpu","graphics","rtx","radeon","geforce"]
    }
    for k, vals in mapping.items():
        if k in s:
            return vals
    return []

def is_relevant_pc_item(title: str, summary: str, link: str, source_name: str, source_type: str, themes: list[str], brand_hits: list[str]) -> bool:
    s = text_lower(f"{title} {summary} {link} {source_name}")
    core_hit = any(t in s for t in PC_CORE_TERMS)
    category_hit = any(t != "その他" for t in themes)
    brand_hit = bool(brand_hits)
    hint_terms = source_hint_keywords_pc(source_name)
    hint_hit = any(text_lower(t) in s for t in hint_terms) if hint_terms else False
    neg = any(t in s for t in PC_NEGATIVE_TERMS)
    broad = is_broad_source_pc(source_name)
    if neg and not brand_hit:
        return False
    if source_type == 'rumor':
        return (core_hit or brand_hit or hint_hit) and not neg
    if source_name == 'PC Watch':
        mobile_only = any(k in s for k in ['smartphone','android phone','iphone','pixel','galaxy s','oneplus','xiaomi','camera phone','smartwatch','wearable','イヤホン','ヘッドホン','スマホ','スマートフォン','スマートウォッチ','テレビ','tv'])
        hard_negative = any(k in s for k in ['robot vacuum','vacuum','掃除機'])
        curated_ok = any(k in s for k in ['pc','windows','intel','amd','nvidia','asus','acer','dell','hp','msi','lenovo','surface','macbook','thinkpad','ssd','gpu','cpu','ai pc','半導体','ノートpc','デスクトップ','レビュー','ベンチマーク','ゲーミング','自作pc','mini pc','ワークステーション','copilot'])
        return (curated_ok or core_hit or hint_hit or brand_hit or category_hit or not mobile_only) and not hard_negative and not mobile_only
    if source_name == '窓の杜':
        mobile_only = any(k in s for k in ['smartphone','android phone','iphone','pixel','galaxy s','oneplus','xiaomi','smartwatch','wearable','イヤホン','ヘッドホン','スマホ','スマートフォン','スマートウォッチ','テレビ','tv'])
        hard_negative = any(k in s for k in ['robot vacuum','vacuum','掃除機'])
        curated_ok = any(k in s for k in ['windows','microsoft','edge','copilot','onedrive','office','powertoys','notepad','explorer','エクスプローラー','アップデート','セキュリティ','ブラウザ','アプリ','ソフト','ツール','utility','power toys','snipping tool','ペイント','メモ帳','エクセル','word','outlook'])
        return (curated_ok or hint_hit or core_hit or brand_hit or category_hit or not mobile_only) and not hard_negative and not mobile_only
    if source_name == 'ITmedia PC USER':
        return not neg and (core_hit or hint_hit or brand_hit or category_hit or any(k in s for k in ['レビュー','実機','ベンチマーク','ノートpc','デスクトップ','ゲーミングpc','surface','macbook','thinkpad','minipc']))
    if broad:
        return ((core_hit or brand_hit or hint_hit) and not neg) and (category_hit or brand_hit or hint_hit)
    return ((core_hit and (category_hit or brand_hit or hint_hit)) or (brand_hit and (category_hit or hint_hit)) or (hint_hit and (category_hit or brand_hit))) and not neg


# ─── 重要タグキーワード ───────────────────────────────────────

HEALTH_ALERT = [
    "cpu", "processor", "client cpu", "server cpu", "xeon", "ryzen", "core ultra", "core", "semiconductor",
    "chip", "chiplet", "soc", "foundry", "fab", "wafer", "tsmc", "samsung foundry", "intel foundry",
    "CPU", "プロセッサ", "半導体", "チップ", "SoC", "ファウンドリ", "製造", "工場", "歩留まり"
]
FOOD_RECALL = [
    "gpu", "graphics", "geforce", "rtx", "radeon", "arc", "dlss", "fsr", "ai pc", "npu", "cuda",
    "GPU", "グラフィックス", "AI PC", "NPU", "生成AI", "推論", "学習", "アクセラレータ"
]
TRAINING_BEHAVIOR = [
    "laptop", "notebook", "desktop", "gaming pc", "mini pc", "workstation", "monitor", "ssd", "memory",
    "windows", "surface", "driver", "update", "security patch", "bios", "firmware",
    "ノートPC", "デスクトップ", "ゲーミングPC", "ワークステーション", "モニター", "SSD", "メモリ", "Windows", "アップデート", "ドライバ"
]
BREED_ADOPTION = [
    "market", "shipment", "earnings", "guidance", "pricing", "price", "demand", "supply", "inventory",
    "channel", "forecast", "outlook", "pc market", "semiconductor market", "capex", "investment", "merger", "partnership",
    "市場", "出荷", "決算", "見通し", "価格", "需要", "供給", "在庫", "投資", "提携", "買収"
]

# 参考度判定用
POSITIVE_WORDS = [
    "record", "records", "strong", "surge", "growth", "grow", "grows", "beat", "beats", "upgrade", "expands", "expansion",
    "recovery", "profitable", "adoption", "partnership", "investment", "funding", "wins", "volume production", "mass production", "availability",
    "increase", "increased", "rises", "rose", "improves", "improved", "better than expected", "raises guidance", "record revenue", "record profit",
    "好調", "増収", "増益", "過去最高", "上方修正", "拡大", "成長", "回復", "量産", "提携", "投資", "採用"
]
NEGATIVE_WORDS = [
    "cut", "cuts", "delay", "delays", "delayed", "drop", "drops", "decline", "declines", "slump", "weak", "warning",
    "shortage", "oversupply", "inventory correction", "lawsuit", "ban", "risk", "miss", "misses", "downturn",
    "price cut", "falls", "fell", "lower guidance", "below expectations", "postponed",
    "slowdown", "slows", "slowed", "reduced", "reduces", "reduction",
    "減収", "減益", "下方修正", "延期", "遅延", "不足", "供給不足", "在庫調整", "リスク", "警告", "不振", "低迷"
]

# スコア加算用イベントキーワード
EVENT_KEYWORDS = {
    3: ["recall", "outbreak", "warning", "ban", "リコール", "回収", "注意喚起", "流行", "死亡"],
    2: ["vaccine", "research", "guideline", "law", "study", "ワクチン", "研究", "指針", "法改正"],
    1: ["nutrition", "training", "adoption", "breed", "dog show", "栄養", "しつけ", "譲渡", "犬種"],
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
        return datetime.now(JST) - pub_dt <= timedelta(hours=NEW_HOURS)
    except: return False


# ─── 判定ロジック ─────────────────────────────────────────────────

def detect_sentiment(title: str, summary: str) -> str:
    combined = text_lower(f"{title} {summary}")

    positive_strong = [
        "raises guidance", "record revenue", "record profit", "better than expected", "beat expectations",
        "strong demand", "record sales", "record earnings", "上方修正", "過去最高", "増収増益"
    ]
    positive_mid = [
        "increase", "growth", "expand", "improve", "improved", "improving", "recovery", "recovers",
        "recovered", "gain", "gains", "momentum", "better", "増収", "増益", "好調", "拡大", "成長", "回復"
    ]
    positive_weak = [
        "up", "stable demand", "firm demand", "healthy demand", "adoption", "partnership", "investment",
        "採用", "堅調", "伸び"
    ]

    negative_strong = [
        "cut forecast", "cuts forecast", "lower guidance", "below expectations", "misses expectations",
        "missed expectations", "loss", "losses", "下方修正", "赤字"
    ]
    negative_mid = [
        "decline", "declines", "drop", "drops", "slowdown", "weak", "weaker", "delayed", "delay",
        "shortage", "oversupply", "inventory correction", "slump", "falls", "fell", "減収", "減益",
        "延期", "遅延", "在庫調整", "低迷", "不振"
    ]
    negative_weak = [
        "concern", "concerns", "pressure", "risk", "uncertain", "uncertainty", "soft demand",
        "cautious", "headwind", "懸念", "圧力", "リスク", "不透明"
    ]

    score = 0.0

    for w in positive_strong:
        if text_lower(w) in combined:
            score += 2
    for w in positive_mid:
        if text_lower(w) in combined:
            score += 1
    for w in positive_weak:
        if text_lower(w) in combined:
            score += 0.25 if any(k in w for k in ['investment','partnership','adoption','up','momentum']) else 0.5

    for w in negative_strong:
        if text_lower(w) in combined:
            score -= 2
    for w in negative_mid:
        if text_lower(w) in combined:
            score -= 1
    for w in negative_weak:
        if text_lower(w) in combined:
            score -= 0.5

    # 決算・業績の組み合わせは少し強く拾う
    if ("revenue" in combined or "earnings" in combined or "profit" in combined or "results" in combined or "決算" in combined or "業績" in combined):
        if ("increase" in combined or "growth" in combined or "beat" in combined or "上方修正" in combined or "増収" in combined or "増益" in combined):
            score += 1.5
        if ("decline" in combined or "drop" in combined or "loss" in combined or "cut" in combined or "下方修正" in combined or "減収" in combined or "減益" in combined):
            score -= 1.5

    # 市場・企業動向系は弱シグナルでも少し拾う
    market_context = any(k in combined for k in [
        "market", "pricing", "price", "demand", "supply", "inventory", "shipment", "outlook", "forecast",
        "市場", "価格", "需要", "供給", "在庫", "出荷", "見通し", "予想", "市況"
    ])
    if market_context and score == 0:
        if any(text_lower(w) in combined for w in positive_weak):
            score += 0.25 if any(k in w for k in ['investment','partnership','adoption','up','momentum']) else 0.5
        elif any(text_lower(w) in combined for w in negative_weak):
            score -= 0.5

    if score > 0:
        return "bullish"
    if score < 0:
        return "bearish"
    return "neutral"

def detect_fx_impact(title: str, summary: str) -> str:
    """重要タグを判定: CPU・半導体 / GPU・AI PC / PC・OS / 市場・企業動向 / neutral"""
    combined = text_lower(f"{title} {summary}")
    scores = {
        "health_alert": sum(1 for w in HEALTH_ALERT if text_lower(w) in combined),
        "food_recall": sum(1 for w in FOOD_RECALL if text_lower(w) in combined),
        "training_behavior": sum(1 for w in TRAINING_BEHAVIOR if text_lower(w) in combined),
        "breed_adoption": sum(1 for w in BREED_ADOPTION if text_lower(w) in combined),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "neutral"


def infer_theme_from_source(source_name: str) -> list[str]:
    src = text_lower(source_name)
    mapping = [
        (("intel", "amd", "semiconductor", "chip", "processor"), "CPU・半導体"),
        (("nvidia", "videocardz", "graphics", "gpu", "rtx", "radeon", "ai pc", "npu"), "GPU・AI PC"),
        (("pc watch", "pc user", "notebookcheck", "lenovo", "laptop", "notebook", "desktop"), "ノートPC・デスクトップ"),
        (("windows blog", "windows", "ars technica", "tom's hardware", "software", "driver", "update", "patch", "security"), "OS・ソフトウェア"),
        (("market", "earnings", "storyhub", "industry", "pricing", "shipment", "supply"), "市場・企業動向"),
    ]
    matched = [label for keys, label in mapping if any(k in src for k in keys)]
    if not matched:
        return ["その他"]
    ordered = []
    for label in ["CPU・半導体", "GPU・AI PC", "ノートPC・デスクトップ", "OS・ソフトウェア", "市場・企業動向"]:
        if label in matched and label not in ordered:
            ordered.append(label)
    return ordered or ["その他"]


def infer_importance_from_source(source_name: str) -> str:
    src = text_lower(source_name)
    if any(k in src for k in ["intel", "amd", "semiconductor", "chip", "processor"]):
        return "health_alert"
    if any(k in src for k in ["nvidia", "videocardz", "graphics", "gpu", "rtx", "radeon", "ai pc", "npu"]):
        return "food_recall"
    if any(k in src for k in ["windows blog", "windows", "pc watch", "pc user", "notebookcheck", "lenovo", "laptop", "notebook", "desktop"]):
        return "training_behavior"
    if any(k in src for k in ["market", "earnings", "industry", "pricing", "shipment", "supply", "storyhub", "tom's hardware", "ars technica"]):
        return "breed_adoption"
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
    raw_feed_count = len(getattr(feed, "entries", []))
    accepted = 0
    skipped_missing_date = 0
    skipped_old = 0
    skipped_irrelevant = 0
    skipped_cap = 0
    source_cap = get_source_accept_cap(name, raw_feed_count)

    for entry in feed.entries:
        title   = clean_text(getattr(entry, "title", ""))
        summary = clean_text(getattr(entry, "summary", getattr(entry, "description", "")))
        link    = getattr(entry, "link", "")

        if not title or not link:
            continue
        if not is_allowed_domain(link, allowed_domains):
            continue
        if is_image_or_gallery_item(title, summary, link):
            continue

        pub_dt = None
        try:
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_dt = datetime.fromtimestamp(calendar.timegm(entry.published_parsed), JST)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                pub_dt = datetime.fromtimestamp(calendar.timegm(entry.updated_parsed), JST)
        except:
            pass

        if raw_feed_count >= NO_DATE_LARGE_FEED_THRESHOLD and not pub_dt:
            skipped_missing_date += 1
            continue

        if pub_dt:
            if datetime.now(JST) - pub_dt > timedelta(days=HISTORY_DAYS):
                skipped_old += 1
                continue
            pub_str = pub_dt.astimezone(JST).strftime("%Y-%m-%d %H:%M")
        else:
            pub_str = ""

        themes = classify_themes(title, summary, lang, themes_cfg)
        if themes == ["その他"]:
            themes = infer_theme_from_source(name)
        brand_hits = classify_brand_labels({"title": title, "summary": summary, "source": name}, load_json(SHARED / "brands.json", {}))
        if not is_relevant_pc_item(title, summary, link, name, source.get("source_type", "media"), themes, brand_hits):
            skipped_irrelevant += 1
            continue

        if source_cap is not None and accepted >= source_cap:
            skipped_cap += 1
            continue

        sentiment = detect_sentiment(title, summary)
        fx_impact = detect_fx_impact(title, summary)
        if fx_impact == "neutral":
            fx_impact = infer_importance_from_source(name)

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
        accepted += 1

    log(f"  ✓ {name}: feed={raw_feed_count} 採用={accepted} 除外(日付なし)={skipped_missing_date} 除外(期間外)={skipped_old} 除外(関連外)={skipped_irrelevant} 除外(調整)={skipped_cap}")
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
.cb-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}.cb-grid > *{min-width:0}@media(max-width:1200px){.cb-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:700px){.cb-grid{grid-template-columns:1fr}}
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
var READ_KEY = 'pc_industry_suite_read';
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
        ("categories.html",    "🧩 カテゴリ別"),
        ("brands.html",        "🏷 ブランド別"),
        ("market.html", "📈 市場動向"),
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
      <h1>💻 {html.escape(APP_NAME)} <span style='font-weight:400;color:var(--muted);font-size:16px'>｜ {html.escape(CONFIG.get("subtitle", ""))}</span></h1>
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
    "health_alert": ("🧠 CPU・半導体", "tag-usd-strong"),
    "food_recall": ("🎮 GPU・AI PC", "tag-usd-weak"),
    "training_behavior": ("💻 PC・OS", "tag-jpy-strong"),
    "breed_adoption": ("📊 市場・企業動向", "tag-jpy-weak"),
    "neutral": ("➖ その他", "tag-neutral"),
}

SENT_MAP = {
    "bullish": ("✅ 好材料", "tag-bullish"),
    "bearish": ("⚠ 懸念", "tag-bearish"),
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




def classify_brand_labels(item: dict, brands_cfg: dict) -> list[str]:
    combined = text_lower(f"{item.get('title','')} {item.get('summary','')} {item.get('source','')}")
    matched = []
    for brand, words in brands_cfg.items():
        if any(text_lower(w) in combined for w in words):
            matched.append(brand)
    return matched


MARKET_BUCKETS = {
    '価格・需給': [
        'price', 'pricing', 'priced', 'msrp', 'asp', 'demand', 'supply', 'inventory', 'shortage', 'surplus',
        'shipment', 'shipments', 'sell-through', 'lead time', 'channel inventory', 'price cut', 'price hike',
        '価格', '需要', '供給', '在庫', '出荷', '値上げ', '値下げ', '市況', '需給', '販売台数'
    ],
    '決算・業績': [
        'earnings', 'revenue', 'guidance', 'outlook', 'forecast', 'results', 'quarterly', 'q1', 'q2', 'q3', 'q4',
        'profit', 'operating income', 'gross margin', 'eps', 'sales', 'net income', 'fiscal', 'fy2024', 'fy2025', 'guides', 'guidance raised', 'guidance cut', 'financial results', 'quarter', 'quarter results', 'quarterly results', 'expects', 'forecast cut', 'forecast raise', '決算', '業績', '売上', '利益', '見通し', '予想', '通期', '四半期', '営業利益', '純利益', '売上高', '上方修正', '下方修正'
    ],
    '投資・提携': [
        'investment', 'invests', 'capex', 'funding', 'factory', 'fab', 'plant', 'expansion', 'partnership',
        'collaboration', 'joint venture', 'acquisition', 'acquires', 'merger', 'builds new',
        '投資', '設備投資', '工場', 'fab', '提携', '協業', '共同開発', '買収', '合併', '増設', '出資', '資本提携', '新工場'
    ],
    '製品発表': [
        'launch', 'launches', 'launched', 'announce', 'announces', 'announced', 'unveil', 'unveils', 'unveiled',
        'introduce', 'introduces', 'introduced', 'release', 'released', 'available now', 'availability',
        '発表', '発売', '投入', '提供開始', '公開'
    ],
}

MARKET_CORE_TERMS = [
    'pc', 'computer', 'desktop', 'laptop', 'notebook', 'workstation', 'mini pc', 'gaming pc', 'handheld',
    'cpu', 'processor', 'chip', 'semiconductor', 'soc', 'gpu', 'graphics', 'ai pc', 'npu', 'windows',
    'ssd', 'memory', 'dram', 'nand', 'monitor',
    'パソコン', 'ノートpc', 'デスクトップ', '半導体', 'ai pc', 'windows', 'ssd', 'メモリ'
]

MARKET_GENERAL_TERMS = [
    'market', 'industry', 'roadmap', 'supply chain', 'channel', 'ecosystem', 'share', 'share gain', 'trend',
    '市場', '業界', '動向', 'ロードマップ', '供給網', 'シェア', '競争', '再編'
]

MARKET_NOISE_TERMS = [
    'review', 'hands-on', 'benchmark', 'benchmarks', 'tested', 'test', 'vs.', 'versus', 'deal', 'discount',
    'best', 'buying guide', 'guide', 'how to', 'wallpaper', 'rumor', 'leak',
    'レビュー', '実機', 'ベンチマーク', '比較', '最安', 'セール', '買い方', '使い方', '壁紙', '噂', 'リーク'
]

MAJOR_ANNOUNCE_TERMS = [
    'rtx', 'geforce', 'radeon', 'arc', 'ryzen', 'core ultra', 'xeon', 'epyc', 'snapdragon', 'blackwell',
    'windows 11', 'copilot+', 'lunar lake', 'strix', 'threadripper', 'ssd', 'dram', 'nand',
    'gpu', 'cpu', 'npu', 'ryzen ai',
    'RTX', 'Ryzen', 'Core Ultra', 'Xeon', 'EPYC', 'Snapdragon', 'Blackwell', 'Windows 11', 'Copilot+', 'AI PC', 'SSD', 'DRAM', 'NAND'
]

MARKET_GROUP_WEIGHTS = {
    '価格・需給': 3,
    '決算・業績': 3,
    '投資・提携': 2,
    '市場一般': 2,
    '製品発表': 1,
}


def _contains_any(combined: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if text_lower(kw) in combined)


def market_group_for_item(item: dict) -> str:
    title = item.get('title', '')
    summary = item.get('summary', '')
    combined = text_lower(f"{title} {summary}")

    # 決算・業績は優先判定
    earnings_priority_terms = [
        "earnings", "revenue", "profit", "loss", "forecast", "outlook", "guidance",
        "results", "financial results", "sales", "operating income", "net income",
        "決算", "業績", "売上", "売上高", "利益", "営業利益", "純利益", "見通し", "予想", "上方修正", "下方修正"
    ]
    if _contains_any(combined, earnings_priority_terms) > 0:
        return '決算・業績'

    if _contains_any(combined, MARKET_NOISE_TERMS) >= 2 and _contains_any(combined, MARKET_BUCKETS['製品発表']) == 0:
        return '市場一般'

    scores = {k: _contains_any(combined, words) for k, words in MARKET_BUCKETS.items()}
    core_hits = _contains_any(combined, MARKET_CORE_TERMS)
    major_hits = _contains_any(combined, MAJOR_ANNOUNCE_TERMS)

    if scores['製品発表']:
        if core_hits == 0:
            scores['製品発表'] = 0
        elif major_hits == 0 and not item.get('official'):
            scores['製品発表'] = max(0, scores['製品発表'] - 1)
        elif major_hits > 0:
            scores['製品発表'] += 2

    themes = item.get('themes', [])
    if '市場・企業動向' in themes:
        for key in ('価格・需給', '決算・業績', '投資・提携'):
            if scores[key] > 0:
                scores[key] += 1

    best = max(scores, key=scores.get)
    if scores[best] <= 0:
        return '市場一般'
    if best == '製品発表' and core_hits == 0:
        return '市場一般'
    return best

def market_items_only(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        title = item.get('title', '')
        summary = item.get('summary', '')
        combined = text_lower(f"{title} {summary}")
        themes = item.get('themes', [])
        group = market_group_for_item(item)
        core_hits = _contains_any(combined, MARKET_CORE_TERMS)
        general_hits = _contains_any(combined, MARKET_GENERAL_TERMS)
        noise_hits = _contains_any(combined, MARKET_NOISE_TERMS)

        if core_hits == 0 and group == '市場一般' and '市場・企業動向' not in themes:
            continue
        if noise_hits >= 2 and group in ('市場一般', '製品発表') and not item.get('official'):
            continue

        include = False
        if group in ('価格・需給', '決算・業績', '投資・提携'):
            include = True
        elif group == '製品発表':
            include = core_hits > 0 and (_contains_any(combined, MAJOR_ANNOUNCE_TERMS) > 0 or item.get('official') or item.get('score', 1) >= 4)
        elif '市場・企業動向' in themes and (general_hits > 0 or item.get('official')):
            include = True

        if include:
            out.append(item)

    out.sort(key=lambda x: (market_group_for_item(x), x.get('score', 1), x.get('pub_dt', '') or ''), reverse=True)
    return out

# ─── カテゴリ別ページ ───────────────────────────────────────────────

def render_categories(items: list[dict], themes_cfg: dict) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    tab_cfg = []
    cats = {}
    for key, td in themes_cfg["themes"].items():
        group_items = [x for x in items if key in x.get("themes", [])]
        cats[key] = group_items
        tab_cfg.append((key, f"{td.get('icon','')} {key}", td.get('color','#636e72'), td.get('color','#636e72')+'22'))
    neutral_items = [x for x in items if x.get("themes") == ["その他"] or not x.get("themes")]
    summary_cards = "<div class='fx-summary-grid'>"
    for key, td in themes_cfg['themes'].items():
        color = td.get('color','#636e72')
        icon = td.get('icon','')
        summary_cards += f"<div class='fx-summary-card' style='background:{color}22;border-color:{color}'><div class='fx-num' style='color:{color}'>{len(cats[key])}</div><div class='fx-label'>{icon} {html.escape(key)}</div><div class='fx-desc'>Category</div></div>"
    summary_cards += f"<div class='fx-summary-card' style='background:var(--panel2);border-color:var(--line)'><div class='fx-num' style='color:var(--muted)'>{len(neutral_items)}</div><div class='fx-label'>➖ その他</div><div class='fx-desc'>General</div></div></div>"
    tab_meta = {k: {'color': c, 'bg': bg} for k, _, c, bg in tab_cfg}
    tabs_html = ''
    panels_html = ''
    for i, (key, label, color, bg) in enumerate(tab_cfg):
        active = 'active' if i == 0 else ''
        style = f"background:{bg};border-color:{color};color:{color}" if active else ''
        tabs_html += f"<button class='fx-tab {active}' data-key='{html.escape(key)}' style='{style}' onclick=\"switchFxTab('{html.escape(key)}',this)\">{label} ({len(cats[key])})</button>"
        cards = ''.join(card_html(it, themes_cfg) for it in cats[key]) if cats[key] else "<div style='color:var(--muted);padding:20px'>該当記事なし</div>"
        panels_html += f"<div class='fx-panel {active}' id='fx-{html.escape(key)}'><div class='grid'>{cards}</div></div>"
    fx_css = """<style>
.fx-summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:24px}
.fx-summary-card{border-radius:16px;padding:18px;text-align:center;border:1px solid transparent}
.fx-summary-card .fx-num{font-size:36px;font-weight:900;margin-bottom:6px}
.fx-summary-card .fx-label{font-size:14px;font-weight:700}
.fx-summary-card .fx-desc{font-size:11px;color:var(--muted);margin-top:4px}
.fx-tabs{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.fx-tab{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:12px;padding:10px 18px;font-size:14px;font-weight:700;cursor:pointer;transition:all .18s}
.fx-tab.active{border-color:transparent;box-shadow:0 4px 16px rgba(0,0,0,.3)}
.fx-panel{display:none}.fx-panel.active{display:block}
</style>"""
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
  var impLang='all', impSearch='';
  function applyImportanceFilters(){{
    document.querySelectorAll('.fx-panel .item-card').forEach(function(card){{
      var show=true;
      if(impLang!=='all' && (card.dataset.lang||'')!==impLang) show=false;
      if(impSearch && !(card.textContent||'').toLowerCase().includes(impSearch.toLowerCase())) show=false;
      card.style.display=show?'':'none';
    }});
  }}
  document.querySelectorAll('.flt-imp-lang').forEach(function(btn){{
    btn.addEventListener('click', function(){{
      document.querySelectorAll('.flt-imp-lang').forEach(function(b){{ b.classList.remove('active'); }});
      btn.classList.add('active'); impLang=btn.dataset.lang; applyImportanceFilters();
    }});
  }});
  var sb=document.getElementById('searchBoxImportance');
  if(sb) sb.addEventListener('input', function(){{ impSearch=this.value; applyImportanceFilters(); }});
  applyImportanceFilters();
}})();
function switchFxTab(key, btn) {{
  document.querySelectorAll('.fx-panel').forEach(function(p){{p.classList.remove('active');}});
  document.querySelectorAll('.fx-tab').forEach(function(b){{b.classList.remove('active');b.style.background='';b.style.borderColor='var(--line)';b.style.color='';}});
  document.getElementById('fx-' + key).classList.add('active');
  btn.classList.add('active');
  var meta=FX_TAB_META[key]||null;
  if(meta){{btn.style.background=meta.bg;btn.style.borderColor=meta.color;btn.style.color=meta.color;}}
}}
</script>"""
    return header_html("categories.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


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
    stamp  = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    total  = len(items)
    new_count     = sum(1 for x in items if x.get("is_new"))
    official_count= sum(1 for x in items if x.get("official"))
    health_alert = sum(1 for x in items if x.get("fx_impact") == "health_alert")
    food_recall   = sum(1 for x in items if x.get("fx_impact") == "food_recall")
    training_behavior = sum(1 for x in items if x.get("fx_impact") == "training_behavior")
    breed_adoption   = sum(1 for x in items if x.get("fx_impact") == "breed_adoption")

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
  <div class='stat'><div class='icon'>🟢</div><div class='num' style='color:#4ea1ff'>{health_alert}</div><div class='label'>CPU・半導体</div></div>
  <div class='stat'><div class='icon'>🔴</div><div class='num' style='color:#e74c3c'>{food_recall}</div><div class='label'>GPU・AI PC</div></div>
  <div class='stat'><div class='icon'>🔵</div><div class='num' style='color:#2ecc71'>{training_behavior}</div><div class='label'>PC・OS</div></div>
  <div class='stat'><div class='icon'>🟡</div><div class='num' style='color:#f39c12'>{breed_adoption}</div><div class='label'>市場・企業動向</div></div>
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
    <span class='filter-label'>言語:</span>
    <button class='flt-btn flt-lang active' data-lang='all'>🌍 全部</button>
    <button class='flt-btn flt-lang' data-lang='ja'>🇯🇵 日本語</button>
    <button class='flt-btn flt-lang' data-lang='en'>🌐 English</button>
    <span class='filter-label'>分類:</span>
    <button class='flt-btn flt-fx active' data-fx='all'>全部</button>
    <button class='flt-btn flt-fx' data-fx='health_alert'>🧠 CPU・半導体</button>
    <button class='flt-btn flt-fx' data-fx='food_recall'>🎮 GPU・AI PC</button>
    <button class='flt-btn flt-fx' data-fx='training_behavior'>💻 PC・OS</button>
    <button class='flt-btn flt-fx' data-fx='breed_adoption'>📊 市場・企業動向</button>
  </div>
  <div class='filters'>
    <span class='filter-label'>論調:</span>
    <button class='flt-btn flt-sentiment active' data-sentiment='all'>全部</button>
    <button class='flt-btn flt-sentiment' data-sentiment='bullish'>✅ 好材料</button>
    <button class='flt-btn flt-sentiment' data-sentiment='bearish'>⚠ 懸念</button>
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


# ─── ブランド別ページ ───────────────────────────────────────────────

def render_brands(items: list[dict], themes_cfg: dict) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    brands_cfg = json.loads((SHARED / "brands.json").read_text(encoding="utf-8"))
    available_brands = []
    sections_html = ""
    for brand in brands_cfg:
        brand_items = [x for x in items if brand in classify_brand_labels(x, brands_cfg)]
        if not brand_items:
            continue
        available_brands.append(brand)
        cards = "".join(card_html(it, themes_cfg) for it in brand_items)
        sections_html += f"""
<div class='theme-section' data-section='{html.escape(brand)}'>
  <div class='theme-header'>
    <span class='theme-badge' style='background:#7c5cff22;border-color:#7c5cff;color:#b9a8ff'>🏷 {html.escape(brand)}</span>
    <span style='color:var(--muted);font-size:13px'><span class='section-count'>{len(brand_items)}</span>件</span>
  </div>
  <div class='grid theme-grid'>{cards}</div>
</div>"""
    jump_btns = "<button class='flt-btn flt-theme-sec active' data-sec='all'>📋 全ブランド</button>"
    for brand in available_brands:
        jump_btns += f"<button class='flt-btn flt-theme-sec' data-sec='{html.escape(brand)}'>🏷 {html.escape(brand)}</button>"
    theme_btns = "".join(
        f"<button class='flt-btn flt-fx-t' data-fx='{html.escape(t)}'>{td.get('icon','')} {html.escape(t)}</button>"
        for t, td in themes_cfg['themes'].items()
    )
    filter_bar = f"""
<div class='panel'>
  <div class='filters'><span class='filter-label'>ブランド:</span>{jump_btns}</div>
  <div class='filters'>
    <span class='filter-label'>言語:</span>
    <button class='flt-btn flt-lang active' data-lang='all'>🌍 全部</button>
    <button class='flt-btn flt-lang' data-lang='ja'>🇯🇵 日本語</button>
    <button class='flt-btn flt-lang' data-lang='en'>🌐 English</button>
    <span class='filter-label'>カテゴリ:</span>
    <button class='flt-btn flt-fx-t active' data-fx='all'>全部</button>
    {theme_btns}
    <input id='searchBoxTheme' class='search' placeholder='タイトル・本文を検索...'>
  </div>
</div>
<script>
(function(){{
  var fSec='all', fLang='all', fFx='all', search='';
  function applyThemeFilters(){{
    document.querySelectorAll('.theme-section').forEach(function(sec){{
      var secKey = sec.getAttribute('data-section') || '';
      if(fSec !== 'all' && secKey !== fSec){{ sec.style.display='none'; return; }}
      var visible = 0;
      sec.querySelectorAll('.item-card').forEach(function(card){{
        var show = true;
        if(fLang !== 'all' && (card.dataset.lang || '') !== fLang) show = false;
        if(fFx !== 'all' && !(card.dataset.themes || '').split('|').includes(fFx)) show = false;
        if(search && !(card.textContent || '').toLowerCase().includes(search.toLowerCase())) show = false;
        card.style.display = show ? '' : 'none';
        if(show) visible++;
      }});
      sec.style.display = visible > 0 ? '' : 'none';
      var cnt = sec.querySelector('.section-count');
      if(cnt) cnt.textContent = visible;
    }});
  }}
  document.querySelectorAll('.flt-theme-sec').forEach(function(btn){{btn.addEventListener('click', function(){{document.querySelectorAll('.flt-theme-sec').forEach(function(b){{ b.classList.remove('active'); }});btn.classList.add('active');fSec=btn.dataset.sec;applyThemeFilters();}});}});
  document.querySelectorAll('.flt-lang').forEach(function(btn){{btn.addEventListener('click', function(){{document.querySelectorAll('.flt-lang').forEach(function(b){{ b.classList.remove('active'); }});btn.classList.add('active'); fLang = btn.dataset.lang; applyThemeFilters();}});}});
  document.querySelectorAll('.flt-fx-t').forEach(function(btn){{btn.addEventListener('click', function(){{document.querySelectorAll('.flt-fx-t').forEach(function(b){{ b.classList.remove('active'); }});btn.classList.add('active'); fFx = btn.dataset.fx; applyThemeFilters();}});}});
  var sb = document.getElementById('searchBoxTheme');
  if(sb) sb.addEventListener('input', function(){{ search = this.value; applyThemeFilters(); }});
}})();
</script>"""
    body = f"<div class='main'>{filter_bar}{sections_html}</div>"
    return header_html("brands.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


# ─── 市場動向ページ ───────────────────────────────────────────────

def render_market(items: list[dict], themes_cfg: dict) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    market_items = market_items_only(items)
    group_names = ['価格・需給', '決算・業績', '投資・提携', '製品発表', '市場一般']
    group_meta = {
        '価格・需給': ('💹', '価格・需給関連'),
        '決算・業績': ('🧾', '決算・業績関連'),
        '投資・提携': ('🏭', '投資・提携関連'),
        '製品発表': ('🚀', '発表・発売関連'),
        '市場一般': ('📈', '市場一般'),
    }
    cards_html = ''.join(card_html(it, themes_cfg).replace("data-isnew='", f"data-marketgroup='{html.escape(market_group_for_item(it))}' data-isnew='") for it in market_items)
    group_cards = ''
    for grp in group_names:
        grp_items = [x for x in market_items if market_group_for_item(x) == grp]
        icon, desc = group_meta[grp]
        latest = grp_items[0].get('published', '記事なし') if grp_items else '記事なし'
        group_cards += f"<div class='cb-card'><div class='cb-name'>{icon} {html.escape(grp)}</div><div style='color:var(--muted);font-size:12px;margin-bottom:8px'>{desc}</div><div class='cb-count'>{len(grp_items)}</div><div style='color:var(--muted);font-size:11px'>件の記事</div><div class='cb-recent'>最新: {html.escape(latest)}</div></div>"
    filter_btns = ''.join(f"<button class='flt-btn flt-cb-bank' data-bank='{html.escape(g)}'>{group_meta[g][0]} {html.escape(g)}</button>" for g in group_names)
    filter_bar = f"""
<div class='panel'>
  <div class='filters'>
    <span class='filter-label'>市場分類:</span>
    <button class='flt-btn flt-cb-bank active' data-bank='all'>全部</button>
    {filter_btns}
  </div>
  <div class='filters'>
    <span class='filter-label'>言語:</span>
    <button class='flt-btn flt-lang-cb active' data-lang='all'>🌍 全部</button>
    <button class='flt-btn flt-lang-cb' data-lang='ja'>🇯🇵 日本語</button>
    <button class='flt-btn flt-lang-cb' data-lang='en'>🌐 English</button>
    <span class='filter-label'>論調:</span>
    <button class='flt-btn flt-fx-cb active' data-fx='all'>全部</button>
    <button class='flt-btn flt-fx-cb' data-fx='bullish'>✅ 好材料</button>
    <button class='flt-btn flt-fx-cb' data-fx='bearish'>⚠ 懸念</button>
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
      var grp = card.dataset.marketgroup || '市場一般';
      if(fBank!=='all' && grp!==fBank) show=false;
      if(fLang!=='all' && (card.dataset.lang||'')!==fLang) show=false;
      if(fFx!=='all' && (card.dataset.sentiment||'')!==fFx) show=false;
      if(search && !(card.textContent||'').toLowerCase().includes(search.toLowerCase())) show=false;
      card.style.display=show?'':'none';
      if(show) visible++;
    }});
    document.getElementById('cbGrid').classList.toggle('list', fView==='list');
    var cnt=document.getElementById('cbCount'); if(cnt) cnt.textContent=visible;
  }}
  document.querySelectorAll('.flt-cb-bank').forEach(function(btn){{btn.addEventListener('click',function(){{document.querySelectorAll('.flt-cb-bank').forEach(function(b){{b.classList.remove('active');}});btn.classList.add('active'); fBank=btn.dataset.bank; apply();}});}});
  document.querySelectorAll('.flt-lang-cb').forEach(function(btn){{btn.addEventListener('click',function(){{document.querySelectorAll('.flt-lang-cb').forEach(function(b){{b.classList.remove('active');}});btn.classList.add('active'); fLang=btn.dataset.lang; apply();}});}});
  document.querySelectorAll('.flt-fx-cb').forEach(function(btn){{btn.addEventListener('click',function(){{document.querySelectorAll('.flt-fx-cb').forEach(function(b){{b.classList.remove('active');}});btn.classList.add('active'); fFx=btn.dataset.fx; apply();}});}});
  document.querySelectorAll('.flt-view-cb').forEach(function(btn){{btn.addEventListener('click',function(){{document.querySelectorAll('.flt-view-cb').forEach(function(b){{b.classList.remove('active');}});btn.classList.add('active'); fView=btn.dataset.view; apply();}});}});
  var sb=document.getElementById('searchBoxCB'); if(sb) sb.addEventListener('input',function(){{search=this.value; apply();}});
}})();
</script>"""
    body = f"""
<div class='main'>
  <div class='panel' style='margin-bottom:16px'>
    <h2 style='margin:0 0 14px;font-size:16px'>📈 市場動向モニター</h2>
    <div class='cb-grid'>{group_cards}</div>
  </div>
  {filter_bar}
  <div style='margin-bottom:12px;font-weight:700;font-size:15px'>市場関連記事（<span id='cbCount'>{len(market_items)}</span>件）</div>
  <div class='grid' id='cbGrid'>{cards_html}</div>
</div>"""
    return header_html("market.html", stamp) + body + footer_html(stamp) + page_lang_js() + read_js()


# ─── 分析ページ ───────────────────────────────────────────────────

def render_analysis(items: list[dict], themes_cfg: dict) -> str:
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    source_counts = Counter(x.get("source", "") for x in items)
    theme_counts  = Counter()
    sent_counts   = Counter(x.get("sentiment", "neutral") for x in items)
    fx_counts     = Counter(x.get("fx_impact", "neutral") for x in items)
    for item in items:
        for t in item.get("themes", []): theme_counts[t] += 1

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

    market_items = market_items_only(items)
    market_bull = sum(1 for x in market_items if x.get("sentiment") == "bullish")
    market_bear = sum(1 for x in market_items if x.get("sentiment") == "bearish")

    market_score = 0
    for x in market_items:
        group = market_group_for_item(x)
        weight = MARKET_GROUP_WEIGHTS.get(group, 1)
        if x.get("sentiment") == "bullish":
            market_score += weight
        elif x.get("sentiment") == "bearish":
            market_score -= weight

    if market_score > 5:
        trend_arrow = '↑'
        trend_label = '上向き'
        trend_color = '#2ecc71'
    elif market_score < -5:
        trend_arrow = '↓'
        trend_label = '下向き'
        trend_color = '#e74c3c'
    else:
        trend_arrow = '→'
        trend_label = '横ばい'
        trend_color = '#9ba9bc' 

    fx_rows = ""
    for key, (label, cls) in IMPORTANCE_MAP.items():
        cnt = fx_counts.get(key, 0)
        fx_rows += f"<tr><td><span class='tag {cls}' style='display:inline-block'>{label}</span></td><td style='font-weight:800'>{cnt}</td></tr>"

    table_css = ".table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}.table th,.table td{padding:10px 14px;border-bottom:1px solid var(--line);text-align:left}.table th{background:var(--panel2);font-size:13px;color:var(--muted)}.table tr:last-child td{border-bottom:none}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}.three-col{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}@media(max-width:900px){.two-col,.three-col{grid-template-columns:1fr}}"
    sent_html = f"""
<div class='panel' style='margin-bottom:16px'>
  <h2 style='margin:0 0 14px;font-size:16px'>📊 論調・分類サマリー</h2>
  <div class='three-col'>
    <div>
      <div style='font-size:13px;color:var(--muted);margin-bottom:10px'>総合方向</div>
      <div style='display:flex;align-items:center;gap:14px;flex-wrap:wrap'>
        <div style='font-size:42px;font-weight:900;line-height:1;color:{trend_color}'>{trend_arrow}</div>
        <div>
          <div style='font-size:24px;font-weight:800;color:{trend_color}'>{trend_label}</div>
          <div style='color:var(--muted);font-size:12px'>市場系 好材料 {market_bull} / 懸念 {market_bear} / スコア {market_score}</div>
        </div>
      </div>
    </div>
    <div>
      <div style='font-size:13px;color:var(--muted);margin-bottom:10px' >論調</div>
      <div style='display:flex;gap:20px;flex-wrap:wrap'>
        <div style='text-align:center'><div style='font-size:28px;font-weight:800;color:#2ecc71'>{bull}</div><div style='color:var(--muted);font-size:12px'>✅ 好材料 ({bull*100//total_sent}%)</div></div>
        <div style='text-align:center'><div style='font-size:28px;font-weight:800;color:#e74c3c'>{bear}</div><div style='color:var(--muted);font-size:12px'>⚠ 懸念 ({bear*100//total_sent}%)</div></div>
        <div style='text-align:center'><div style='font-size:28px;font-weight:800;color:#9ba9bc'>{neu}</div><div style='color:var(--muted);font-size:12px'>➖ Neutral ({neu*100//total_sent}%)</div></div>
      </div>
    </div>
    <div>
      <div style='font-size:13px;color:var(--muted);margin-bottom:10px'>分類</div>
      <div style='display:flex;gap:14px;flex-wrap:wrap'>
        <div style='text-align:center'><div style='font-size:24px;font-weight:800;color:#4ea1ff'>{fx_counts.get("health_alert",0)}</div><div style='color:var(--muted);font-size:11px'>🧠 CPU・半導体</div></div>
        <div style='text-align:center'><div style='font-size:24px;font-weight:800;color:#e74c3c'>{fx_counts.get("food_recall",0)}</div><div style='color:var(--muted);font-size:11px'>🎮 GPU・AI PC</div></div>
        <div style='text-align:center'><div style='font-size:24px;font-weight:800;color:#2ecc71'>{fx_counts.get("training_behavior",0)}</div><div style='color:var(--muted);font-size:11px'>💻 PC・OS</div></div>
        <div style='text-align:center'><div style='font-size:24px;font-weight:800;color:#f39c12'>{fx_counts.get("breed_adoption",0)}</div><div style='color:var(--muted);font-size:11px'>📊 市場・企業動向</div></div>
      </div>
    </div>
  </div>
</div>"""

    body = f"""
<style>{table_css}</style>
<div class='main'>
  {sent_html}
  <div class='two-col'>
    <div class='panel'>
      <h2 style='margin:0 0 14px;font-size:16px'>📡 ソース別記事数</h2>
      <table class='table'><thead><tr><th>ソース</th><th>言語</th><th>種別</th><th>件数</th></tr></thead><tbody>{src_rows}</tbody></table>
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
    published  = html.escape(item.get("published", "")[:20])
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
    published = html.escape(item.get("published", "")[:20])
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
    log(f"💻 {APP_NAME}")
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
    db_cleanup(conn)
    conn.close()

    # 翻訳（英語記事全件）
    cache = load_json(TRANSLATE_CACHE_FILE, {})
    translate_items(items, cache)

    log("\n🖊 HTML生成中...")
    pages = [
        ("index.html",        render_index(items, themes_cfg)),
        ("pickup.html",       render_pickup(items, themes_cfg)),
        ("categories.html",   render_categories(items, themes_cfg)),
        ("brands.html",       render_brands(items, themes_cfg)),
        ("market.html",       render_market(items, themes_cfg)),
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
