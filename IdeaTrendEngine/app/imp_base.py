from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import traceback
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

try:
    import feedparser  # type: ignore
except Exception:
    import xml.etree.ElementTree as ET

    class _FeedParserFallback:
        @staticmethod
        def parse(url: str):
            class _Result:
                entries = []
            try:
                headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,ja;q=0.8"}
                r = requests.get(url, headers=headers, timeout=TIMEOUT)
                r.raise_for_status()
                root = ET.fromstring(r.content)
                entries = []
                for item in root.findall('.//item'):
                    entries.append({
                        "title": (item.findtext('title') or '').strip(),
                        "link": (item.findtext('link') or '').strip(),
                        "summary": (item.findtext('description') or '').strip(),
                        "description": (item.findtext('description') or '').strip(),
                    })
                if not entries:
                    ns_entries = root.findall('.//{*}entry')
                    for entry in ns_entries:
                        link = ''
                        link_el = entry.find('{*}link')
                        if link_el is not None:
                            link = (link_el.attrib.get('href') or link_el.text or '').strip()
                        entries.append({
                            "title": (entry.findtext('{*}title') or '').strip(),
                            "link": link,
                            "summary": (entry.findtext('{*}summary') or entry.findtext('{*}content') or '').strip(),
                            "description": (entry.findtext('{*}content') or entry.findtext('{*}summary') or '').strip(),
                        })
                res = _Result()
                res.entries = entries
                return res
            except Exception:
                return _Result()

    feedparser = _FeedParserFallback()

try:
    from deep_translator import GoogleTranslator  # type: ignore
except Exception:
    class GoogleTranslator:
        def __init__(self, source: str = "auto", target: str = "ja"):
            self.source = source
            self.target = target
        def translate(self, text: str) -> str:
            return text

APP_NAME = ""
ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
SHARED_DIR = ROOT / "shared"
DB_PATH: Path | None = None
ERROR_LOG_PATH = LOG_DIR / "app_errors.log"
TIMEOUT = 8
_CONFIG = json.loads((ROOT / "shared" / "config.json").read_text(encoding="utf-8"))
TRANSLATE_WORKERS = int(_CONFIG.get("translate_workers", 4))
TRANSLATE_RETRIES = int(_CONFIG.get("translate_retries", 2))
HISTORY_DAYS = int(_CONFIG.get("history_days", 14))
TRANSLATE_CACHE_FILE = OUTPUT_DIR / "translate_cache.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36 IdeaTrendEngine"
)

JST = ZoneInfo("Asia/Tokyo")
WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]

# ─────────────────────────────────────────
#  分類・評価ルール
# ─────────────────────────────────────────
SOURCE_META = {
    "microsoft":    {"region": "海外", "label": "海外"},
    "aws":          {"region": "海外", "label": "海外"},
    "zapier":       {"region": "海外", "label": "海外"},
    "publickey_jp": {"region": "日本", "label": "日本"},
    "techtarget_jp":{"region": "日本", "label": "日本"},
    "itmedia_jp":   {"region": "日本", "label": "日本"},
    "codezine_jp":  {"region": "日本", "label": "日本"},
    "other":        {"region": "不明", "label": "不明"},
}

CATEGORY_RULES = [
    ("AI支援", [
        "ai", "agent", "agents", "bedrock", "copilot", "llm", "assistant", "generative",
        "genai", "machine learning", "生成ai", "エージェント", "要約", "ai支援",
        "chatgpt", "claude", "gemini",
    ]),
    ("入力自動化", [
        "form", "forms", "input", "data entry", "fill", "capture", "expense", "intake",
        "entry", "submit", "submission", "入力", "登録", "受付", "申込", "申請入力",
        "フォーム", "転記", "記入", "手入力", "二重入力",
    ]),
    ("収集自動化", [
        "collect", "collection", "sync", "ingestion", "fetch", "rss", "pull", "scrape",
        "import", "extract", "feed", "connector", "crawl", "harvest", "収集", "取得",
        "巡回", "監視", "新着", "フィード", "ニュース収集", "情報収集",
    ]),
    ("通知・連携", [
        "notify", "notification", "email", "message", "messaging", "slack", "teams",
        "todo", "reminder", "alert", "webhook", "integration", "integrate", "通知",
        "連携", "メール", "配信", "回覧", "投稿", "催促", "リマインド", "自動送信",
    ]),
    ("承認・申請", [
        "approval", "approvals", "workflow", "request", "review", "conditional branching",
        "delegation", "approve", "approval flow", "承認", "申請", "決裁", "承認依頼",
        "ワークフロー", "稟議",
    ]),
    ("レポート・可視化", [
        "report", "dashboard", "analytics", "visual", "insight", "kpi", "monitoring",
        "export", "chart", "summary", "summarize", "summarization", "レポート", "可視化",
        "見える化", "集計", "分析", "ダッシュボード", "一覧化", "グラフ", "サマリー",
    ]),
    ("文書処理", [
        "document", "documents", "pdf", "template", "ocr", "idp", "invoice", "attachment",
        "file naming", "contract", "文書", "帳票", "ファイル", "添付", "命名",
        "テンプレート", "請求書", "台帳", "契約書",
    ]),
    ("検索・共有", [
        "knowledge", "search", "share", "portal", "workspace", "catalog", "index",
        "browse", "find", "discover", "検索", "共有", "ナレッジ", "faq", "手順書",
        "探しやすく", "一覧", "参照", "検索性",
    ]),
    ("運用・保守", [
        "maintenance", "deployment", "remediation", "systems manager", "software management",
        "patch", "automation", "runbook", "operations", "ops", "incident", "troubleshoot",
        "運用", "保守", "障害", "初動", "パッチ", "再起動", "標準化", "運用品質",
    ]),
    ("セキュリティ", [
        "security", "waf", "firewall", "attack", "protect", "guard", "identity",
        "access control", "vulnerability", "threat", "セキュリティ", "脆弱性", "防御",
        "攻撃", "保護", "認証", "権限", "アクセス制御",
    ]),
]

IMPACT_RULES = [
    ("時間削減",      ["automate", "streamline", "faster", "accelerate", "productivity", "save time"]),
    ("ミス削減",      ["error", "minimize", "reduce errors", "quality", "standardize", "consistency"]),
    ("見える化",      ["dashboard", "analytics", "visibility", "monitor", "insight", "report"]),
    ("標準化",        ["template", "workflow", "approval", "process", "forms", "guide"]),
    ("意思決定高速化", ["decision", "insight", "intelligence", "prioritize", "triage"]),
]

SOURCES = [
    {"name": "Publickey Feed",           "kind": "feed",   "source_type": "publickey_jp",  "url": "https://www.publickey1.jp/atom.xml"},
    {"name": "TechTargetジャパン Feed",  "kind": "feed",   "source_type": "techtarget_jp", "url": "https://rss.itmedia.co.jp/rss/1.0/techtarget.xml"},
    {"name": "ITmedia NEWS Feed",        "kind": "feed",   "source_type": "itmedia_jp",    "url": "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml"},
    {"name": "CodeZine Feed",            "kind": "feed",   "source_type": "codezine_jp",   "url": "https://codezine.jp/rss/new/20/index.xml"},
    {"name": "Zapier Blog Feed",         "kind": "feed",   "source_type": "zapier",        "url": "https://zapier.com/blog/feed/"},
    {"name": "AWS News Blog Feed",       "kind": "feed",   "source_type": "aws",           "url": "https://aws.amazon.com/blogs/aws/feed/"},
    {"name": "Microsoft Teams Workflows","kind": "single", "source_type": "microsoft",     "url": "https://support.microsoft.com/en-us/office/browse-and-add-workflows-in-microsoft-teams-4998095c-8b72-4b0e-984c-f2ad39e6ba9a"},
    {"name": "Microsoft RSS Connector",  "kind": "single", "source_type": "microsoft",     "url": "https://learn.microsoft.com/en-us/connectors/rss/"},
    {"name": "AWS Systems Manager",      "kind": "single", "source_type": "aws",           "url": "https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-automation.html"},
]

SEED_IDEAS = [
    {"title":"国内技術ニュースを一覧化して改善ネタの種を拾う","url":"https://www.publickey1.jp/atom.xml","source_name":"Publickey Feed","source_type":"publickey_jp","raw_text":"国内の技術ニュースを定期収集し、気になるテーマをタグで分けて改善ネタ候補をためる。情報システム部門のひらめき収集に向く。"},
    {"title":"国内の導入・運用記事から他社事例を探しやすくする","url":"https://rss.itmedia.co.jp/rss/1.0/techtarget.xml","source_name":"TechTargetジャパン Feed","source_type":"techtarget_jp","raw_text":"国内ITメディアの新着を収集し、運用、セキュリティ、クラウド、AIの観点で分類する。日本向けの改善ヒント収集に使える。"},
    {"title":"国内ITニュースを部門向けに要点整理して回覧する","url":"https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml","source_name":"ITmedia NEWS Feed","source_type":"itmedia_jp","raw_text":"国内のITニュースを定期収集し、社内向けに要点だけを並べて共有する。情報収集の初動短縮と回覧の標準化に向く。"},
    {"title":"国内開発記事から実装ヒントを拾って改善候補化する","url":"https://codezine.jp/rss/new/20/index.xml","source_name":"CodeZine Feed","source_type":"codezine_jp","raw_text":"開発者向けの記事を収集し、設計、運用、自動化などの観点でタグ付けする。改善アイデアの種をためる用途に向く。"},
    {"title":"Teamsで定型通知を自動配信するワークフロー","url":"https://support.microsoft.com/en-us/office/browse-and-add-workflows-in-microsoft-teams-4998095c-8b72-4b0e-984c-f2ad39e6ba9a","source_name":"Microsoft Teams Workflows","source_type":"microsoft","raw_text":"Teams の workflows を使い、繰り返し作業や複数アプリ連携を自動化する。定型通知、申請受付、チャンネル投稿の標準化に転用できる。"},
    {"title":"RSS収集から自動通知へつなぐ改善ネタ","url":"https://learn.microsoft.com/en-us/connectors/rss/","source_name":"Microsoft RSS Connector","source_type":"microsoft","raw_text":"RSS connector で新着情報を取得し、Teams やメールへ自動通知する。競合監視、業界ニュース配信の改善ネタに向く。"},
    {"title":"フォーム入力から承認依頼まで自動化","url":"https://support.microsoft.com/en-us/office/browse-and-add-workflows-in-microsoft-teams-4998095c-8b72-4b0e-984c-f2ad39e6ba9a","source_name":"Microsoft Teams Workflows","source_type":"microsoft","raw_text":"フォーム入力を起点に approval workflow を回し、担当者通知、期限リマインド、履歴記録までつなげる。申請業務の標準化に使える。"},
    {"title":"複数アプリの更新を1回入力で同期","url":"https://zapier.com/blog/","source_name":"Zapier Blog Feed","source_type":"zapier","raw_text":"connect apps の考え方を使い、1回の入力をスプレッドシート、メール、タスク管理へ同期する。二重入力削減の定番ネタ。"},
    {"title":"メール受信を自動でタスク化して見える化","url":"https://zapier.com/blog/","source_name":"Zapier Blog Feed","source_type":"zapier","raw_text":"受信メールを条件分岐でタスクへ変換し、担当と期限を付与して管理する。問い合わせ対応や依頼漏れ防止に使える。"},
    {"title":"手作業レポートを自動集計・自動配信","url":"https://zapier.com/blog/","source_name":"Zapier Blog Feed","source_type":"zapier","raw_text":"日次や週次の report を自動生成し、関係者に配信する。数値転記の時間削減と見える化の両方に効く。"},
    {"title":"パッチ適用や定型保守を手順化して自動実行","url":"https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-automation.html","source_name":"AWS Systems Manager","source_type":"aws","raw_text":"Automation runbook の考え方を使って、保守作業、再起動、パッチ適用、チェック手順を標準化する。運用品質向上に向く。"},
    {"title":"トラブル初動をrunbook化して属人化を減らす","url":"https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-automation.html","source_name":"AWS Systems Manager","source_type":"aws","raw_text":"障害時の初動、確認項目、エスカレーションを runbook 化し、担当者ごとの差を減らす。保守とセキュリティの改善ネタ。"},
    {"title":"承認の滞留をリマインドして止まりを減らす","url":"https://support.microsoft.com/en-us/office/browse-and-add-workflows-in-microsoft-teams-4998095c-8b72-4b0e-984c-f2ad39e6ba9a","source_name":"Microsoft Teams Workflows","source_type":"microsoft","raw_text":"承認依頼に期限、催促、代替承認者の仕組みを付ける。承認待ちの停滞可視化と処理時間短縮に効く。"},
    {"title":"FAQやよく使う手順を検索しやすく整理","url":"https://zapier.com/blog/","source_name":"Zapier Blog Feed","source_type":"zapier","raw_text":"問い合わせ内容や手順書のタイトル、タグ、更新日を一覧化して検索しやすくする。探す時間削減の改善ネタ。"},
    {"title":"添付ファイルを自動保存して命名を統一","url":"https://zapier.com/blog/","source_name":"Zapier Blog Feed","source_type":"zapier","raw_text":"メール添付やフォーム添付を決まったルールで保存し、ファイル名を標準化する。共有と文書処理の両方に効く。"},
    {"title":"運用ログから異常兆候を集約して可視化","url":"https://aws.amazon.com/blogs/aws/","source_name":"AWS News Blog Feed","source_type":"aws","raw_text":"monitoring と alert の考え方を使い、複数ログや運用結果を集約して dashboard 化する。保守判断の高速化に使える。"},
    {"title":"AIを使った要約補助で一次整理を短縮","url":"https://aws.amazon.com/blogs/aws/","source_name":"AWS News Blog Feed","source_type":"aws","raw_text":"AI assistance の考え方を使い、収集記事や問い合わせ文の一次要約を自動作成する。人は最終判断に集中できる。"},
]

# ─────────────────────────────────────────
#  ユーティリティ
# ─────────────────────────────────────────
def jst_now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")

def format_dt(value: str | None) -> str:
    if not value:
        return "－"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(JST)
        return f"{dt:%Y/%m/%d}（{WEEKDAYS_JA[dt.weekday()]}）{dt:%H:%M}"
    except Exception:
        return str(value)

def esc(text: str | None) -> str:
    t = str(text or "")
    return t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())

def fingerprint_for(title: str, url: str) -> str:
    return hashlib.sha1(f"{title}|{url}".encode()).hexdigest()

def write_error_log(context: str, exc: Exception) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{jst_now_iso()}] {context}\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            f.write("\n")
    except Exception:
        pass

def has_japanese(text: str) -> bool:
    return bool(re.search(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]', text))

def has_hangul(text: str) -> bool:
    return bool(re.search(r'[\uac00-\ud7af]', text))

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

def translate_items(conn: sqlite3.Connection, cache: dict) -> None:
    rows = conn.execute(
        "SELECT id, title, summary FROM ideas WHERE title_ja = ''"
    ).fetchall()
    en_items = [dict(r) for r in rows if not has_japanese(r["title"] or "")]
    if not en_items:
        return
    def do_translate(item: dict) -> dict:
        item["title_ja"]   = translate_text(item["title"] or "", cache)
        item["summary_ja"] = translate_text(item["summary"] or "", cache)
        return item
    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as ex:
        futures = {ex.submit(do_translate, item): item for item in en_items}
        for f in as_completed(futures):
            try: f.result()
            except: pass
    for item in en_items:
        conn.execute(
            "UPDATE ideas SET title_ja=?, summary_ja=? WHERE id=?",
            (item.get("title_ja", ""), item.get("summary_ja", ""), item["id"])
        )
    conn.commit()
    if len(cache) > 5000:
        cache = dict(list(cache.items())[-5000:])
    p = TRANSLATE_CACHE_FILE
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

# ─────────────────────────────────────────
#  DB
# ─────────────────────────────────────────
def open_db() -> sqlite3.Connection:
    if DB_PATH is None:
        raise RuntimeError("DB_PATH is not set. Call set_mode() first.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            summary TEXT,
            category TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            impact_tags TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT '未着手',
            favorite INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL UNIQUE,
            collected_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            region     TEXT NOT NULL DEFAULT '不明',
            title_ja   TEXT NOT NULL DEFAULT '',
            summary_ja TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS collection_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            fetched_count INTEGER NOT NULL DEFAULT 0,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running',
            detail TEXT NOT NULL DEFAULT ''
        );
    """)
    conn.commit()
    # 古いDBにカラムがない場合の対応
    for _col, _default in [
        ("region",     "'不明'"),
        ("title_ja",   "''"),
        ("summary_ja", "''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE ideas ADD COLUMN {_col} TEXT NOT NULL DEFAULT {_default}")
            conn.commit()
        except Exception:
            pass
    return conn

# ─────────────────────────────────────────
#  分類・スコアリング
# ─────────────────────────────────────────
def detect_category(text: str) -> str:
    low = text.lower()
    for cat, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in low:
                return cat
    return "その他"

def detect_impact_tags(text: str) -> list[str]:
    low = text.lower()
    return [tag for tag, kws in IMPACT_RULES if any(kw in low for kw in kws)]

def estimate_difficulty(text: str, category: str) -> str:
    low = text.lower()
    hard = ["custom", "integration", "api", "development", "build", "architecture"]
    easy = ["form", "template", "click", "simple", "easy", "quick", "no-code", "low-code"]
    if any(k in low for k in hard) or category in ("運用・保守", "セキュリティ"):
        return "高"
    if any(k in low for k in easy) or category in ("通知・連携", "入力自動化"):
        return "低"
    return "中"

def score_idea(category: str, difficulty: str, impacts: list[str], text: str) -> int:
    base = {"高": 20, "中": 40, "低": 60}.get(difficulty, 30)
    impact_bonus = len(impacts) * 10
    cat_bonus = {"AI支援": 15, "レポート・可視化": 10, "承認・申請": 8}.get(category, 5)
    low = text.lower()
    kw_bonus = sum(5 for kw in ["ai", "dashboard", "automate", "report", "approval"] if kw in low)
    return min(base + impact_bonus + cat_bonus + kw_bonus, 100)

def make_summary(text: str, category: str, impacts: list[str], difficulty: str) -> str:
    base = normalize_space(text)
    if len(base) > 130:
        base = base[:130].rstrip("、。,. ") + "…"
    return f"分類: {category} / 難易度: {difficulty} / 効果: {'・'.join(impacts) or '－'} / {base}"

def detect_region(source_type: str) -> str:
    return SOURCE_META.get(source_type, SOURCE_META["other"])["region"]

def recalculate_idea_fields(title: str, source_name: str, source_type: str, text: str) -> dict:
    combined   = f"{title} {source_name} {source_type} {text}"
    category   = detect_category(combined)
    impacts    = detect_impact_tags(combined)
    difficulty = estimate_difficulty(combined, category)
    score      = score_idea(category, difficulty, impacts, combined)
    summary    = make_summary(text or title, category, impacts, difficulty)
    region     = detect_region(source_type)
    return {"category": category, "impacts": impacts, "difficulty": difficulty,
            "score": score, "summary": summary, "region": region}

def refresh_all_categories(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT id, title, source_name, source_type, summary FROM ideas").fetchall()
    updated = 0
    for row in rows:
        rec = recalculate_idea_fields(row["title"], row["source_name"], row["source_type"], row["summary"] or "")
        conn.execute("""
            UPDATE ideas SET category=?, difficulty=?, impact_tags=?, score=?, region=?, updated_at=?
            WHERE id=?
        """, (rec["category"], rec["difficulty"], ", ".join(rec["impacts"]),
              rec["score"], rec["region"], jst_now_iso(), row["id"]))
        updated += 1
    conn.commit()
    return updated

# ─────────────────────────────────────────
#  DB書き込み
# ─────────────────────────────────────────
def upsert_idea(conn: sqlite3.Connection, item: dict) -> tuple[bool, bool]:
    now   = jst_now_iso()
    title = normalize_space(item.get("title", ""))
    url   = normalize_space(item.get("url", ""))
    if not title or not url:
        return False, False
    source_name = item.get("source_name", "Unknown")
    source_type = item.get("source_type", "other")
    text = normalize_space(item.get("raw_text", ""))
    rec  = recalculate_idea_fields(title, source_name, source_type, text)
    fp   = fingerprint_for(title, url)
    existing = conn.execute("SELECT id FROM ideas WHERE fingerprint=?", (fp,)).fetchone()
    if existing:
        conn.execute("""
            UPDATE ideas SET source_name=?, source_type=?, summary=?, category=?,
                difficulty=?, impact_tags=?, score=?, region=?, updated_at=? WHERE fingerprint=?
        """, (source_name, source_type, rec["summary"], rec["category"], rec["difficulty"],
              ", ".join(rec["impacts"]), rec["score"], rec["region"], now, fp))
        return True, False
    conn.execute("""
        INSERT INTO ideas (title, url, source_name, source_type, summary, category,
            difficulty, impact_tags, score, status, favorite, notes, fingerprint,
            collected_at, updated_at, region)
        VALUES (?,?,?,?,?,?,?,?,?,'未着手',0,'',?,?,?,?)
    """, (title, url, source_name, source_type, rec["summary"], rec["category"],
          rec["difficulty"], ", ".join(rec["impacts"]), rec["score"], fp, now, now, rec["region"]))
    return False, True

def enrich_and_store(conn: sqlite3.Connection, raw_items: Iterable[dict]) -> tuple[int, int, int]:
    fetched = inserted = updated = 0
    for item in raw_items:
        if not normalize_space(item.get("title","")) or not normalize_space(item.get("url","")):
            continue
        fetched += 1
        was_updated, was_inserted = upsert_idea(conn, item)
        if was_updated:  updated  += 1
        if was_inserted: inserted += 1
    conn.commit()
    return fetched, inserted, updated

# ─────────────────────────────────────────
#  収集
# ─────────────────────────────────────────
def request_html(url: str) -> str:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,ja;q=0.8"}
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.encoding or r.apparent_encoding or "utf-8"
    return r.text

def extract_page_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script","style","nav","footer","header"]):
        tag.decompose()
    paras = [p.get_text(" ", strip=True) for p in soup.find_all(["p","li","h2","h3"])]
    return " ".join(p for p in paras if len(p) > 20)[:3000]

def collect_from_single(source: dict) -> list[dict]:
    html = request_html(source["url"])
    soup = BeautifulSoup(html, "html.parser")
    text = extract_page_text(soup)
    title = normalize_space(soup.title.string if soup.title else source["name"])
    return [{"title": title, "url": source["url"], "source_name": source["name"],
             "source_type": source["source_type"], "raw_text": text}]

def collect_from_feed(source: dict, limit: int = 20) -> list[dict]:
    feed = feedparser.parse(source["url"])
    items = []
    for entry in feed.entries[:limit]:
        title = normalize_space(entry.get("title",""))
        url   = entry.get("link","")
        if not title or not url:
            continue
        raw = normalize_space(entry.get("summary","") or entry.get("description","") or "")
        items.append({"title": title, "url": url, "source_name": source["name"],
                      "source_type": source["source_type"], "raw_text": raw})
    return items

def run_collection(conn: sqlite3.Connection) -> dict:
    print("[INFO] データ収集中...")
    started = jst_now_iso()
    cur = conn.execute(
        "INSERT INTO collection_logs (started_at, status, detail) VALUES (?,'running','')", (started,))
    log_id = cur.lastrowid
    conn.commit()
    all_items: list[dict] = []
    details:   list[str]  = []
    try:
        sf, si, su = enrich_and_store(conn, SEED_IDEAS)
        details.append(f"スターターネタ {sf}件（新規{si}件）")
        max_workers = min(8, len(SOURCES))
        print(f"  🚀 並列取得: {len(SOURCES)} ソース / {max_workers} workers")
        def _collect_one(source):
            if source["kind"] == "feed":
                return source, collect_from_feed(source), None
            else:
                return source, collect_from_single(source), None
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_collect_one, s): s for s in SOURCES}
            for f in as_completed(futs):
                try:
                    source, items, _ = f.result()
                    all_items.extend(items)
                    details.append(f"OK: {source['name']}（{len(items)}件）")
                    print(f"  ✓ {source['name']} {len(items)}件")
                except Exception as exc:
                    source = futs[f]
                    details.append(f"スキップ: {source['name']}（{exc}）")
                    print(f"[WARN] {source['name']} 取得不可: {exc}")
        lf, li, lu = enrich_and_store(conn, all_items)
        fetched  = sf + lf
        inserted = si + li
        updated  = su + lu
        conn.execute("""
            UPDATE collection_logs SET finished_at=?, fetched_count=?, inserted_count=?,
                updated_count=?, status='done', detail=? WHERE id=?
        """, (jst_now_iso(), fetched, inserted, updated, "\n".join(details), log_id))
        conn.commit()
        return {"fetched": fetched, "inserted": inserted, "updated": updated, "detail": details}
    except Exception as exc:
        conn.execute("UPDATE collection_logs SET finished_at=?, status='error', detail=? WHERE id=?",
                     (jst_now_iso(), f"{type(exc).__name__}: {exc}", log_id))
        conn.commit()
        return {"fetched": 0, "inserted": 0, "updated": 0, "detail": [f"ERROR: {exc}"]}

# ─────────────────────────────────────────
#  クエリ
# ─────────────────────────────────────────
def query_ideas(conn: sqlite3.Connection, order_by: str = "score DESC") -> list[sqlite3.Row]:
    return conn.execute(f"SELECT * FROM ideas ORDER BY {order_by}").fetchall()

def get_stats(conn: sqlite3.Connection) -> dict:
    total     = conn.execute("SELECT COUNT(*) FROM ideas").fetchone()[0]
    favorites = conn.execute("SELECT COUNT(*) FROM ideas WHERE favorite=1").fetchone()[0]
    avg_score = conn.execute("SELECT COALESCE(ROUND(AVG(score),1),0) FROM ideas").fetchone()[0]
    categories = conn.execute(
        "SELECT category, COUNT(*) AS cnt FROM ideas GROUP BY category "
        "ORDER BY CASE WHEN category='その他' THEN 1 ELSE 0 END, cnt DESC"
    ).fetchall()
    latest = conn.execute("SELECT * FROM collection_logs ORDER BY id DESC LIMIT 1").fetchone()
    by_source = conn.execute(
        "SELECT source_type, COUNT(*) AS cnt FROM ideas GROUP BY source_type ORDER BY cnt DESC"
    ).fetchall()
    by_diff = conn.execute(
        "SELECT difficulty, COUNT(*) AS cnt FROM ideas GROUP BY difficulty ORDER BY cnt DESC"
    ).fetchall()
    by_impact = conn.execute("SELECT impact_tags FROM ideas WHERE impact_tags!=''").fetchall()
    impact_counts: dict[str, int] = defaultdict(int)
    for row in by_impact:
        for tag in (row["impact_tags"] or "").split(", "):
            tag = tag.strip()
            if tag:
                impact_counts[tag] += 1
    return {
        "total": total, "favorites": favorites, "avg_score": avg_score,
        "categories": categories, "latest": latest,
        "by_source": by_source, "by_diff": by_diff,
        "impact_counts": dict(sorted(impact_counts.items(), key=lambda x: -x[1])),
    }

# ─────────────────────────────────────────
#  HTML共通
# ─────────────────────────────────────────
CSS = """
:root{--bg:#101214;--sur:#1b1f24;--sur2:#20262d;--brd:#2f3741;--tx:#f3f4f6;--mt:#94a3b8;--ac:#a78bfa;--ac2:#34d399;--warn:#facc15;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--tx);font-family:Arial,sans-serif;font-size:14px;}
a{color:#7dd3fc;text-decoration:none;}a:hover{text-decoration:underline;}
.topbar{background:var(--sur2);border-bottom:1px solid var(--brd);padding:10px 18px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:40;}
.topbar .logo{color:var(--ac);font-weight:bold;font-size:16px;white-space:nowrap;}
nav a{color:var(--mt);font-size:13px;padding:4px 10px;border-radius:6px;}
nav a:hover,nav a.active{background:var(--ac);color:#fff;text-decoration:none;}
.page{padding:18px;}
.sub{color:var(--mt);font-size:12px;margin-bottom:14px;}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-bottom:18px;}
.stat{background:var(--sur);border-radius:10px;padding:10px 12px;}
.stat .lbl{color:var(--mt);font-size:11px;margin-bottom:3px;}
.stat .val{font-size:18px;font-weight:bold;}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:18px;}
.card{background:var(--sur);border-radius:10px;padding:12px;}
.card-title{font-size:15px;font-weight:bold;color:var(--warn);margin-bottom:6px;}
.subitem{padding:7px 0;border-top:1px solid var(--brd);display:flex;justify-content:space-between;gap:12px;align-items:baseline;}
.subitem:first-child{border-top:none;}
.table-wrap{max-height:520px;overflow:auto;border-radius:10px;border:1px solid var(--brd);-webkit-overflow-scrolling:touch;}
table{width:100%;border-collapse:collapse;background:var(--sur);table-layout:auto;min-width:860px;}
table.idea-table{table-layout:fixed;}
table.idea-table th:first-child,table.idea-table td:first-child{width:48%;}
td{word-break:break-word;overflow-wrap:break-word;}
.tl-en,.tl-ja{font-size:inherit;font-weight:inherit;color:inherit;}
th,td{padding:8px 10px;border-bottom:1px solid var(--brd);text-align:left;vertical-align:top;font-size:13px;}
th{background:var(--sur2);color:#e2e8f0;position:sticky;top:0;z-index:2;white-space:nowrap;}
td{word-break:break-word;}
tr:last-child td{border-bottom:none;}
.desktop-table{display:block;}
.mobile-cards{display:none;}
.idea-mobile-card{background:var(--sur);border:1px solid var(--brd);border-radius:12px;padding:12px 12px 10px;margin-bottom:10px;}
.idea-mobile-card:last-child{margin-bottom:0;}
.idea-mobile-title{font-size:15px;font-weight:700;line-height:1.4;margin-bottom:8px;}
.idea-mobile-meta{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;align-items:center;}
.idea-mobile-impact{margin-bottom:8px;color:var(--tx);font-size:13px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.idea-mobile-foot{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;color:var(--mt);font-size:12px;}
.idea-mobile-source,.idea-mobile-date{min-width:0;}
.idea-mobile-source{flex:1 1 auto;}
.idea-mobile-date{flex:0 0 auto;text-align:right;}
.lang-btn{display:inline-flex;align-items:center;gap:4px;background:var(--sur2);border:1px solid var(--brd);border-radius:6px;padding:4px 10px;font-size:12px;color:var(--mt);cursor:pointer;white-space:nowrap;}
.lang-btn:hover{border-color:var(--ac);color:var(--ac);}
.lang-btn.lang-active{border-color:var(--ac);color:var(--ac);background:var(--sur2);}
.pill{display:inline-block;background:#334155;color:#e2e8f0;border-radius:999px;padding:2px 8px;font-size:11px;margin-right:4px;white-space:nowrap;}
.pill-ac{background:#4c1d95;color:#ddd6fe;}
.pill-green{background:#14532d;color:#86efac;}
.pill-warn{background:#713f12;color:#fde68a;}
.muted{color:var(--mt);font-size:12px;}
.score-hi{color:#a78bfa;font-weight:bold;}
.score-mid{color:#67e8f9;}
.score-lo{color:var(--mt);}
.diff-hi{color:#f87171;}
.diff-mid{color:#facc15;}
.diff-lo{color:#4ade80;}
.bar-wrap{background:var(--brd);border-radius:999px;height:8px;width:100%;margin-top:4px;}
.bar{background:var(--ac);border-radius:999px;height:8px;}
.section{margin-top:22px;}
.section h2{color:var(--tx);font-size:17px;margin-bottom:10px;}
.empty{color:var(--mt);padding:16px;text-align:center;}
.search-bar{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:7px 12px;color:var(--tx);font-size:14px;width:180px;min-width:0;}
.filter-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px;}
.filter-row > *{min-width:0;}
select{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:7px 10px;color:var(--tx);font-size:13px;}
.hidden{display:none!important;}
.lang-btn{display:inline-flex;align-items:center;gap:4px;background:var(--sur2);border:1px solid var(--brd);border-radius:6px;padding:4px 10px;font-size:12px;color:var(--mt);cursor:pointer;white-space:nowrap;}
.lang-btn:hover{border-color:var(--ac);color:var(--ac);}
.lang-btn.lang-active{border-color:var(--ac);color:var(--ac);}
@media (max-width: 980px){.search-bar{flex:1 1 220px;width:auto;} .filter-row select{flex:1 1 140px;}}
@media (max-width: 768px){.page{padding:14px;} .stats{grid-template-columns:repeat(2,minmax(0,1fr));} .desktop-table{display:none;} .mobile-cards{display:block;} .table-wrap{max-height:none;} table{min-width:760px;} .search-bar{flex:1 1 100%;width:100%;} .filter-row select{flex:1 1 calc(50% - 4px);} .idea-mobile-foot{flex-direction:column;} .idea-mobile-date{text-align:left;}}
@media (max-width: 520px){.stats{grid-template-columns:1fr;} .filter-row select{flex:1 1 100%;}}
"""

NAV_ITEMS = [
    ("index.html",    "Latest"),
    ("pickup.html",   "Pickup"),
    ("focus.html",    "Focus"),
    ("themes.html",   "Themes"),
    ("sources.html",  "Sources"),
    ("analysis.html", "Analysis"),
]

def page_wrap(title: str, active_page: str, body: str) -> str:
    nav_html = " ".join(
        f'<a href="{href}" class="{"active" if href == active_page else ""}">{label}</a>'
        for href, label in NAV_ITEMS
    )
    return f"""<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} - {esc(APP_NAME)}</title>
<style>{CSS}</style>
</head><body>
<div class="topbar">
  <span class="logo">🛠 {esc(APP_NAME)}</span>
  <nav style="display:flex;gap:4px;">{nav_html}</nav>
</div>
<div class="page">{body}</div>
</body></html>"""

def score_class(score: int) -> str:
    if score >= 70: return "score-hi"
    if score >= 45: return "score-mid"
    return "score-lo"

def diff_class(diff: str) -> str:
    return {"低": "diff-lo", "中": "diff-mid", "高": "diff-hi"}.get(diff, "")

def idea_row(idea: sqlite3.Row) -> str:
    impacts = esc(idea["impact_tags"] or "－")
    cat     = esc(idea["category"])
    cols    = [c[0] for c in idea.description] if hasattr(idea, "description") else idea.keys()
    region  = idea["region"]    if "region"    in cols else "不明"
    title_ja   = (idea["title_ja"]   if "title_ja"   in cols else "") or ""
    summary_ja = (idea["summary_ja"] if "summary_ja" in cols else "") or ""
    if title_ja:
        title_html = (
            f'<a href="{esc(idea["url"])}" target="_blank" rel="noopener noreferrer">'
            f'<span class="tl-en">{esc(idea["title"])}</span>'
            f'<span class="tl-ja hidden">{esc(title_ja)}</span>'
            f'</a>'
        )
    else:
        title_html = (
            f'<a href="{esc(idea["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{esc(idea["title"])}</a>'
        )
    if summary_ja:
        summary_html = (
            f'<span class="tl-en">{esc(idea["summary"] or "")}</span>'
            f'<span class="tl-ja hidden">{esc(summary_ja)}</span>'
        )
    else:
        summary_html = esc(idea["summary"] or "")
    return (
        f'<tr data-cat="{cat}" data-diff="{esc(idea["difficulty"])}" '
        f'data-src="{esc(idea["source_type"])}" data-region="{esc(region)}">'
        f'<td>{title_html}'
        f'<div class="muted">{summary_html}</div></td>'
        f'<td><span class="pill pill-ac">{cat}</span></td>'
        f'<td class="{diff_class(idea["difficulty"])}">{esc(idea["difficulty"])}</td>'
        f'<td class="{score_class(idea["score"])}">{idea["score"]}</td>'
        f'<td><span class="muted">{impacts}</span></td>'
        f'<td class="muted">{esc(idea["source_name"])}</td>'
        f'<td class="muted">{format_dt(idea["collected_at"])}</td>'
        f'</tr>'
    )

def idea_mobile_card(idea: sqlite3.Row) -> str:
    impacts = esc(idea["impact_tags"] or "－")
    cat     = esc(idea["category"])
    cols    = [c[0] for c in idea.description] if hasattr(idea, "description") else idea.keys()
    region  = idea["region"] if "region" in cols else "不明"
    title_ja   = (idea["title_ja"] if "title_ja" in cols else "") or ""
    summary_ja = (idea["summary_ja"] if "summary_ja" in cols else "") or ""
    if title_ja:
        title_html = (
            f'<span class="tl-en">{esc(idea["title"])}</span>'
            f'<span class="tl-ja hidden">{esc(title_ja)}</span>'
        )
    else:
        title_html = esc(idea["title"])
    if summary_ja:
        summary_html = (
            f'<span class="tl-en">{esc(idea["summary"] or "")}</span>'
            f'<span class="tl-ja hidden">{esc(summary_ja)}</span>'
        )
    else:
        summary_html = esc(idea["summary"] or "")
    return (
        f'<article class="idea-mobile-card" data-cat="{cat}" data-diff="{esc(idea["difficulty"])}" '
        f'data-src="{esc(idea["source_type"])}" data-region="{esc(region)}">'
        f'<div class="idea-mobile-meta">'
        f'<span class="pill pill-ac">{cat}</span>'
        f'<span class="pill">難易度 {esc(idea["difficulty"])}</span>'
        f'<span class="pill pill-warn">Score {idea["score"]}</span>'
        f'</div>'
        f'<div class="idea-mobile-title"><a href="{esc(idea["url"])}" target="_blank" rel="noopener noreferrer">{title_html}</a></div>'
        f'<div class="idea-mobile-impact">{summary_html or impacts}</div>'
        f'<div class="idea-mobile-foot">'
        f'<div class="idea-mobile-source">{esc(idea["source_name"])} · {impacts}</div>'
        f'<div class="idea-mobile-date">{format_dt(idea["collected_at"])}</div>'
        f'</div>'
        f'</article>'
    )

FILTER_SCRIPT = """<script>
function filterTable(){
  var q=(document.getElementById('q')||{value:''}).value.toLowerCase();
  var cat=(document.getElementById('cat')||{value:''}).value;
  var diff=(document.getElementById('diff')||{value:''}).value;
  var src=(document.getElementById('src')||{value:''}).value;
  var region=(document.getElementById('region')||{value:''}).value;
  var vis=0;
  function matchNode(r){
    var txt=(r.textContent||'').toLowerCase();
    return ((!q||txt.includes(q))&&(!cat||r.dataset.cat===cat)&&
            (!diff||r.dataset.diff===diff)&&(!src||r.dataset.src===src)&&
            (!region||r.dataset.region===region));
  }
  document.querySelectorAll('#ideaBody tr').forEach(function(r){
    var show=matchNode(r);
    r.classList.toggle('hidden',!show);
    if(show)vis++;
  });
  document.querySelectorAll('.idea-mobile-card').forEach(function(card){
    var show=matchNode(card);
    card.classList.toggle('hidden',!show);
  });
  var em=document.getElementById('emptyMsg');
  if(em) em.classList.toggle('hidden',vis>0);
}
</script>"""

def build_filter_row(stats: dict, src_types: list[str]) -> str:
    cats    = [""] + [r["category"] for r in stats["categories"]]
    diffs   = ["", "低", "中", "高"]
    regions = ["", "日本", "海外", "不明"]
    cat_opts    = "".join(f'<option value="{esc(c)}">{esc(c) or "全カテゴリ"}</option>' for c in cats)
    diff_opts   = "".join(f'<option value="{esc(d)}">{esc(d) or "全難易度"}</option>' for d in diffs)
    src_opts    = "".join(f'<option value="{esc(s)}">{esc(s) or "全ソース"}</option>' for s in [""] + src_types)
    region_opts = "".join(f'<option value="{esc(r)}">{esc(r) or "全地域"}</option>' for r in regions)
    return (
        f'<div class="filter-row">'
        f'<input id="q" class="search-bar" placeholder="キーワード検索..." oninput="filterTable()">'
        f'<select id="cat" onchange="filterTable()">{cat_opts}</select>'
        f'<select id="diff" onchange="filterTable()">{diff_opts}</select>'
        f'<select id="src" onchange="filterTable()">{src_opts}</select>'
        f'<select id="region" onchange="filterTable()">{region_opts}</select>'
        f'</div>'
    )

# ─────────────────────────────────────────
#  6ページ生成
# ─────────────────────────────────────────
def gen_index(conn: sqlite3.Connection) -> str:
    ideas = query_ideas(conn, "collected_at DESC")
    stats = get_stats(conn)
    rows  = "".join(idea_row(i) for i in ideas) or '<tr><td colspan="7" class="empty">データなし</td></tr>'
    cards = "".join(idea_mobile_card(i) for i in ideas) or '<div class="empty">データなし</div>'
    src_types = [s["source_type"] for s in SOURCES]
    body = f"""
<div class="sub">アイデアを探しやすく整理しています</div>
<div class="stats">
  <div class="stat"><div class="lbl">総件数</div><div class="val">{stats["total"]}</div></div>
  <div class="stat"><div class="lbl">平均スコア</div><div class="val">{stats["avg_score"]}</div></div>
  <div class="stat"><div class="lbl">お気に入り</div><div class="val">{stats["favorites"]}</div></div>
  <div class="stat"><div class="lbl">カテゴリ数</div><div class="val">{len(stats["categories"])} </div></div>
</div>
{build_filter_row(stats, src_types)}
<div class="desktop-table"><div class="table-wrap"><table class="idea-table">
<thead><tr><th>タイトル</th><th>カテゴリ</th><th>難易度</th><th>スコア</th><th>インパクト</th><th>ソース</th><th>収集日</th></tr></thead>
<tbody id="ideaBody">{rows}</tbody>
</table></div></div>
<div class="mobile-cards">{cards}</div>
<div id="emptyMsg" class="empty hidden">条件に一致するネタがありません。</div>
{FILTER_SCRIPT}"""
    return page_wrap("Latest", "index.html", body)

def gen_pickup(conn: sqlite3.Connection) -> str:
    ideas = query_ideas(conn, "score DESC")[:25]
    stats = get_stats(conn)
    rows  = "".join(idea_row(i) for i in ideas) or '<tr><td colspan="7" class="empty">データなし</td></tr>'
    cards = "".join(idea_mobile_card(i) for i in ideas) or '<div class="empty">データなし</div>'
    src_types = [s["source_type"] for s in SOURCES]
    body = f"""
<div class="sub">スコア上位 25 件 &nbsp;|&nbsp; 実施優先度の高い改善ネタ</div>
<div class="stats">
  <div class="stat"><div class="lbl">総件数</div><div class="val">{stats["total"]}</div></div>
  <div class="stat"><div class="lbl">平均スコア</div><div class="val">{stats["avg_score"]}</div></div>
  <div class="stat"><div class="lbl">お気に入り</div><div class="val">{stats["favorites"]}</div></div>
  <div class="stat"><div class="lbl">カテゴリ数</div><div class="val">{len(stats["categories"])} </div></div>
</div>
{build_filter_row(stats, src_types)}
<div class="desktop-table"><div class="table-wrap"><table class="idea-table">
<thead><tr><th>タイトル</th><th>カテゴリ</th><th>難易度</th><th>スコア</th><th>インパクト</th><th>ソース</th><th>収集日</th></tr></thead>
<tbody id="ideaBody">{rows}</tbody>
</table></div></div>
<div class="mobile-cards">{cards}</div>
<div id="emptyMsg" class="empty hidden">条件に一致するネタがありません。</div>
{FILTER_SCRIPT}"""
    return page_wrap("Pickup", "pickup.html", body)

def gen_focus(conn: sqlite3.Connection) -> str:
    all_ideas = query_ideas(conn, "score DESC")
    by_cat: dict[str, list] = defaultdict(list)
    for idea in all_ideas:
        by_cat[idea["category"]].append(idea)
    sections = ""
    for cat, ideas in sorted(by_cat.items(), key=lambda x: -max(i["score"] for i in x[1])):
        top3 = ideas[:3]
        cards_html = ""
        for idea in top3:
            cols       = idea.keys()
            title_ja   = (idea["title_ja"]   if "title_ja"   in cols else "") or ""
            summary_ja = (idea["summary_ja"] if "summary_ja" in cols else "") or ""
            if title_ja:
                title_inner = (
                    f'<span class="tl-en">{esc(idea["title"])}</span>'
                    f'<span class="tl-ja hidden">{esc(title_ja)}</span>'
                )
            else:
                title_inner = esc(idea["title"])
            if summary_ja:
                summary_inner = (
                    f'<span class="tl-en">{esc(idea["summary"] or "")}</span>'
                    f'<span class="tl-ja hidden">{esc(summary_ja)}</span>'
                )
            else:
                summary_inner = esc(idea["summary"] or "")
            cards_html += f"""
<div class="card">
  <div class="card-title"><a href="{esc(idea['url'])}" target="_blank" rel="noopener noreferrer">{title_inner}</a></div>
  <div class="muted" style="margin-bottom:6px;">{summary_inner}</div>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
    <span class="{diff_class(idea['difficulty'])}">難易度: {esc(idea['difficulty'])}</span>
    <span class="{score_class(idea['score'])}">スコア: {idea['score']}</span>
    <span class="muted">{esc(idea['source_name'])}</span>
  </div>
</div>"""
        sections += f"""
<div class="section">
  <h2><span class="pill pill-ac">{esc(cat)}</span> &nbsp;{len(ideas)} 件中 TOP 3</h2>
  <div class="cards">{cards_html}</div>
</div>"""
    body = f"""
<div class="sub">カテゴリ別 スコアTOP3 ピックアップ</div>
{sections}"""
    return page_wrap("Focus", "focus.html", body)

def gen_themes(conn: sqlite3.Connection) -> str:
    stats = get_stats(conn)
    max_cnt = max((r["cnt"] for r in stats["categories"]), default=1)
    cards_html = ""
    for row in stats["categories"]:
        pct = int(row["cnt"] / max_cnt * 100)
        cats_ideas = conn.execute(
            "SELECT title, title_ja, score, url FROM ideas WHERE category=? ORDER BY score DESC LIMIT 5",
            (row["category"],)
        ).fetchall()
        tops = "".join(
            f'<div class="subitem"><div><a href="{esc(i["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{esc((i["title_ja"] or i["title"])[:50])}{"…" if len(i["title_ja"] or i["title"])>50 else ""}</a></div>'
            f'<div class="{score_class(i["score"])}">{i["score"]}</div></div>'
            for i in cats_ideas
        )
        cards_html += f"""
<div class="card">
  <div class="card-title">{esc(row['category'])}</div>
  <div class="muted">{row['cnt']} 件</div>
  <div class="bar-wrap"><div class="bar" style="width:{pct}%"></div></div>
  <div style="margin-top:10px;">{tops}</div>
</div>"""
    body = f"""
<div class="sub">カテゴリごとのネタ数と上位タイトル</div>
<div class="cards">{cards_html}</div>"""
    return page_wrap("Themes", "themes.html", body)

def gen_sources(conn: sqlite3.Connection) -> str:
    stats = get_stats(conn)
    rows_html = ""
    for row in stats["by_source"]:
        sm = SOURCE_META.get(row["source_type"], SOURCE_META["other"])
        top = conn.execute(
            "SELECT title, title_ja, score, url FROM ideas WHERE source_type=? ORDER BY score DESC LIMIT 3",
            (row["source_type"],)
        ).fetchall()
        top_html = " / ".join(
            f'<a href="{esc(i["url"])}" target="_blank" rel="noopener noreferrer">{esc((i["title_ja"] or i["title"])[:40])}</a>'
            for i in top
        )
        rows_html += (
            f'<tr><td>{esc(row["source_type"])}</td>'
            f'<td><span class="pill">{esc(sm["region"])}</span></td>'
            f'<td>{row["cnt"]}</td>'
            f'<td class="muted">{top_html}</td></tr>'
        )
    body = f"""
<div class="sub">収集元ごとのネタ数と代表タイトル</div>
<div class="table-wrap"><table>
<thead><tr><th>ソース種別</th><th>地域</th><th>件数</th><th>代表ネタ</th></tr></thead>
<tbody>{rows_html}</tbody>
</table></div>"""
    return page_wrap("Sources", "sources.html", body)

def gen_analysis(conn: sqlite3.Connection) -> str:
    stats = get_stats(conn)
    total = stats["total"] or 1
    diff_html = ""
    for row in stats["by_diff"]:
        pct = int(row["cnt"] / total * 100)
        diff_html += (
            f'<div class="subitem"><div class="{diff_class(row["difficulty"])}">{esc(row["difficulty"])}</div>'
            f'<div>{row["cnt"]} 件 <span class="muted">({pct}%)</span></div></div>'
        )
    impact_html = ""
    for tag, cnt in stats["impact_counts"].items():
        pct = int(cnt / total * 100)
        impact_html += (
            f'<div class="subitem"><div><span class="pill pill-green">{esc(tag)}</span></div>'
            f'<div>{cnt} 件<div class="bar-wrap"><div class="bar" style="width:{min(pct*2,100)}%;background:var(--ac2);"></div></div></div></div>'
        )
    score_buckets = {"0-29":0,"30-49":0,"50-69":0,"70-89":0,"90-100":0}
    for idea in query_ideas(conn, "score DESC"):
        s = idea["score"]
        if s < 30:   score_buckets["0-29"]   += 1
        elif s < 50: score_buckets["30-49"]  += 1
        elif s < 70: score_buckets["50-69"]  += 1
        elif s < 90: score_buckets["70-89"]  += 1
        else:        score_buckets["90-100"] += 1
    score_html = ""
    for rng, cnt in score_buckets.items():
        pct = int(cnt / total * 100)
        lo  = int(rng.split("-")[0])
        sc  = "hi" if lo >= 70 else "mid" if lo >= 45 else "lo"
        score_html += (
            f'<div class="subitem"><div class="score-{sc}">{rng}</div>'
            f'<div>{cnt} 件<div class="bar-wrap"><div class="bar" style="width:{min(pct*3,100)}%"></div></div></div></div>'
        )
    body = f"""
<div class="sub">難易度・インパクト・スコア分布</div>
<div class="cards">
  <div class="card"><div class="card-title">難易度分布</div>{diff_html}</div>
  <div class="card"><div class="card-title">インパクトタグ分布</div>{impact_html}</div>
  <div class="card"><div class="card-title">スコア帯分布</div>{score_html}</div>
</div>"""
    return page_wrap("Analysis", "analysis.html", body)

def main() -> None:
    print("=" * 46)
    print(f"  {APP_NAME}")
    print("=" * 46)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    conn = open_db()
    try:
        run_collection(conn)
        refresh_all_categories(conn)
        print("[INFO] HTML生成中...")
        pages = {
            "index.html":    gen_index(conn),
            "pickup.html":   gen_pickup(conn),
            "focus.html":    gen_focus(conn),
            "themes.html":   gen_themes(conn),
            "sources.html":  gen_sources(conn),
            "analysis.html": gen_analysis(conn),
        }
        for filename, html in pages.items():
            path = OUTPUT_DIR / filename
            path.write_text(html, encoding="utf-8")
            print(f"  ✓ {filename}")
    finally:
        cutoff = (datetime.now(JST) - timedelta(days=HISTORY_DAYS)).isoformat()
        conn.execute("DELETE FROM ideas WHERE pub_dt < ? AND pub_dt != ''", (cutoff,))
        conn.commit()
        conn.close()
    print()
    print("[DONE] 生成完了")
    print(f"  output: {OUTPUT_DIR / 'index.html'}")

if __name__ == "__main__":
    main()
