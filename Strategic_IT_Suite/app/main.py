import html
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import json
import feedparser
import requests
import time
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "shared" / "config.json").read_text(encoding="utf-8"))
APP_ICON = CONFIG.get("app_icon", "🛡️")
OUTPUT_DIR = ROOT / "output"
OUTPUT_FILE = OUTPUT_DIR / "index.html"
DB_FILE     = OUTPUT_DIR / CONFIG.get("db_name", "strategic_it_suite.db")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 StrategicITSuite"
JST = ZoneInfo("Asia/Tokyo")
HTTP_TIMEOUT = 10
LEGAL_SOURCE_TIMEOUT = 8
LEGAL_TOTAL_TIMEOUT = 20
MAIN_FUTURE_TIMEOUT = 25


HISTORY_DAYS = int(CONFIG.get("history_days", 14))

def format_date(raw):
    if not raw:
        return ""
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


def parse_feed_with_timeout(url: str):
    res = requests.get(
        url,
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Connection": "close",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        },
    )
    res.raise_for_status()
    return feedparser.parse(res.content)


def safe_text(value):
    return html.escape(str(value)) if value is not None else ""


def parse_any_datetime(raw_value):
    raw = (raw_value or "").strip()
    if not raw:
        return None
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(JST)
        except ValueError:
            pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST)
    except Exception:
        pass
    try:
        iso = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def get_ai_risk_entries():
    url = "https://news.google.com/rss/search?q=AI+脆弱性+規制+漏洩+セキュリティ&hl=ja&gl=JP&ceid=JP:ja"
    print("  [AI Risk] RSS取得開始")
    parsed = parse_feed_with_timeout(url)
    return parsed.entries[:100]


def get_dev_articles():
    url = "https://zenn.dev/api/articles?topicname=ai&order=latest"
    print("  [Dev Articles] API取得開始")
    res = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    res.raise_for_status()
    return res.json().get("articles", [])[:100]



# ── 法規制情報取得 ────────────────────────────────────────────────────────────
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin

LAW_DEFINITIONS = [
    {
        "law_key": "personal_info",
        "law_name": "個人情報保護法",
        "law_abbr": "APPI",
        "description": "個人情報の適正な取扱いと保護を定めた法律。個人データ保護と委託・第三者提供管理の実務に直結します。",
        "official_url": "https://www.ppc.go.jp/",
        "keywords": ["個人情報", "個人データ", "仮名加工", "匿名加工", "漏えい", "課徴金", "ガイドライン", "保護法"],
        "focus_keywords": ["改正", "改訂", "法案", "施行", "意見募集", "パブリックコメント", "パブコメ", "告示", "答申", "課徴金", "漏えい", "行政指導", "行政処分", "委員会決定", "報告徴収", "勧告", "命令", "注意喚起"],
        "exclude_keywords": ["個人情報保護委員会について", "について", "漫画", "パンフレット", "リーフレット", "紹介", "案内", "開催", "募集", "公表資料一覧", "開示(委員会保有個人情報)", "認定個人情報保護団体", "監視・監督方針", "漏えい等の対応", "個人情報保護政策懇談会", "個人情報を考える週間", "令和2年 改正個人情報保護法", "令和3年 改正個人情報保護法", "ページを更新", "資料集を更新", "特集ページ", "広報資料", "仮訳", "オプトアウト届出", "記入要領", "法令・ガイドライン等", "EDPB", "欧州データ保護会議"],
        "sources": [
            {"label": "個人情報保護委員会 報道発表", "type": "html", "url": "https://www.ppc.go.jp/news/press/2026"},
            {"label": "個人情報保護委員会 新着情報", "type": "html", "url": "https://www.ppc.go.jp/information"},
            {"label": "e-Govパブコメ 意見募集", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_list.xml"},
            {"label": "e-Govパブコメ 結果公示", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_result.xml"},
        ],
    },
    {
        "law_key": "unauthorized_access",
        "law_name": "不正アクセス禁止法",
        "law_abbr": "UCA",
        "description": "不正アクセス行為の禁止と関連対策を定めた法律。不正侵入、認証情報悪用、サイバー事案対応の基礎です。",
        "official_url": "https://www.npa.go.jp/cyber/",
        "keywords": ["不正アクセス", "アクセス制御", "識別符号", "認証情報", "サイバー", "禁止法"],
        "sources": [
            {"label": "警察庁 報道発表資料", "type": "html", "url": "https://www.npa.go.jp/news/release/index.html"},
            {"label": "警察庁 新着情報", "type": "html", "url": "https://www.npa.go.jp/newlyarrived/"},
            {"label": "e-Govパブコメ 意見募集", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_list.xml"},
            {"label": "e-Govパブコメ 結果公示", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_result.xml"},
        ],
    },
    {
        "law_key": "e_document",
        "law_name": "e-文書法",
        "law_abbr": "e-Doc",
        "description": "書類の電子保存を認める法律。電子保存、スキャナ保存、文書管理の見直しに関わります。",
        "official_url": "https://www.meti.go.jp/",
        "keywords": ["e-文書", "電子文書", "電子保存", "スキャナ保存", "電磁的記録", "文書保存"],
        "sources": [
            {"label": "経済産業省 ニュースリリース", "type": "html", "url": "https://www.meti.go.jp/press/"},
            {"label": "e-Govパブコメ 意見募集", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_list.xml"},
            {"label": "e-Govパブコメ 結果公示", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_result.xml"},
        ],
    },
    {
        "law_key": "electronic_signature",
        "law_name": "電子署名認証法",
        "law_abbr": "ESA",
        "description": "電子署名の法的効力と認証業務を定めた法律。電子契約、電子証明書、認証基盤に関わります。",
        "official_url": "https://www.meti.go.jp/",
        "keywords": ["電子署名", "認証業務", "認定認証", "電子証明書", "電子契約", "署名法"],
        "sources": [
            {"label": "経済産業省 ニュースリリース", "type": "html", "url": "https://www.meti.go.jp/press/"},
            {"label": "e-Govパブコメ 意見募集", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_list.xml"},
            {"label": "e-Govパブコメ 結果公示", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_result.xml"},
        ],
    },
    {
        "law_key": "unfair_competition",
        "law_name": "不正競争防止法",
        "law_abbr": "UCPA-JP",
        "description": "営業秘密と限定提供データの保護、不正競争行為の防止を定めた法律。情報漏えい対策や知財保護に関わります。",
        "official_url": "https://www.meti.go.jp/",
        "keywords": ["不正競争", "営業秘密", "限定提供データ", "技術情報", "知的財産", "模倣品"],
        "sources": [
            {"label": "経済産業省 ニュースリリース", "type": "html", "url": "https://www.meti.go.jp/press/"},
            {"label": "e-Govパブコメ 意見募集", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_list.xml"},
            {"label": "e-Govパブコメ 結果公示", "type": "rss", "url": "https://public-comment.e-gov.go.jp/rss/pcm_result.xml"},
        ],
    },
]

LEGAL_SOURCES = LAW_DEFINITIONS

_DATE_PATTERN = re.compile(
    r"(?:令和|平成)(\d{1,2})年\s*(\d{1,2})月(?:\s*(\d{1,2})日)?|"
    r"(\d{4})年\s*(\d{1,2})月(?:\s*(\d{1,2})日)?"
)
_ENFORCE_KEYWORDS = ["施行", "施行日", "適用開始", "発効"]
_DEADLINE_KEYWORDS = ["期限", "猶予期間", "移行期限", "対応期限", "までに"]

LAW_DISPLAY_ORDER = [
    "personal_info",
    "unfair_competition",
    "unauthorized_access",
    "electronic_signature",
    "e_document",
]


def _extract_dates_from_text(text: str) -> dict:
    result = {"enforce_date": "", "deadline": ""}
    sentences = re.split(r"[。\n]", text or "")
    for sent in sentences:
        has_enforce = any(k in sent for k in _ENFORCE_KEYWORDS)
        has_deadline = any(k in sent for k in _DEADLINE_KEYWORDS)
        if not (has_enforce or has_deadline):
            continue
        m = _DATE_PATTERN.search(sent)
        if not m:
            continue
        if m.group(1):
            era_year = int(m.group(1))
            month = int(m.group(2))
            day = int(m.group(3)) if m.group(3) else 1
            era_text = sent[:m.start()]
            year = (2018 + era_year) if "令和" in era_text else (1988 + era_year)
        else:
            year = int(m.group(4))
            month = int(m.group(5))
            day = int(m.group(6)) if m.group(6) else 1
        date_str = f"{year}-{month:02d}-{day:02d}"
        if has_enforce and not result["enforce_date"]:
            result["enforce_date"] = date_str
        if has_deadline and not result["deadline"]:
            result["deadline"] = date_str
    return result


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lowered = (text or "").lower()
    return any(kw.lower() in lowered for kw in keywords)


def _matches_exclude_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return False
    lowered = (text or "").lower()
    return any(kw.lower() in lowered for kw in keywords)


def _matches_focus_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lowered = (text or "").lower()
    return any(kw.lower() in lowered for kw in keywords)


def _needs_focus_filter(law: dict, source: dict) -> bool:
    if law.get("law_key") != "personal_info":
        return False
    label = (source.get("label") or "")
    return "新着情報" in label or "報道発表" in label


def _passes_personal_info_practical_filter(text: str) -> bool:
    lowered = (text or "").lower()

    hard_excludes = [
        "ページを更新", "資料集を更新", "特集ページ", "広報資料", "仮訳",
        "オプトアウト届出", "記入要領", "法令・ガイドライン等", "edpb",
        "欧州データ保護会議", "個人情報保護委員会について", "漫画", "パンフレット",
        "リーフレット", "認定個人情報保護団体", "個人情報保護政策懇談会",
        "個人情報を考える週間", "監視・監督方針", "開示(委員会保有個人情報)"
    ]
    if any(kw.lower() in lowered for kw in hard_excludes):
        return False

    strong_terms = [
        "改正", "改訂", "法案", "施行", "意見募集", "パブリックコメント", "パブコメ",
        "告示", "答申", "課徴金", "漏えい", "行政指導", "行政処分", "委員会決定",
        "報告徴収", "勧告", "命令", "注意喚起"
    ]
    if any(term.lower() in lowered for term in strong_terms):
        return True

    guideline_terms = ["ガイドライン", "指針", "q&a", "q＆a"]
    guideline_actions = ["改正", "改訂", "見直し", "策定", "制定", "新設"]
    if any(term in text for term in guideline_terms) and any(action in text for action in guideline_actions):
        return True

    return False


def _normalize_legal_item(law: dict, source_label: str, title: str, link: str, published: str, body_text: str = "") -> dict:
    dates = _extract_dates_from_text((title or "") + "\n" + (body_text or ""))
    return {
        "law_key": law["law_key"],
        "law_name": law["law_name"],
        "title": title.strip(),
        "link": link,
        "source": source_label,
        "published": published,
        "enforce_date": dates["enforce_date"],
        "deadline": dates["deadline"],
    }


def _parse_html_date(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = re.search(r"(20\d{2})[./年-]\s*(\d{1,2})[./月-]\s*(\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        year = 2018 + int(m.group(1))
        return f"{year:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _fetch_rss_for_law(law: dict, source: dict) -> list[dict]:
    parsed = parse_feed_with_timeout(source["url"])
    result = []
    for entry in getattr(parsed, "entries", [])[:80]:
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        combined = f"{title}\n{summary}"
        if not _matches_keywords(combined, law.get("keywords", [])):
            continue
        if _matches_exclude_keywords(combined, law.get("exclude_keywords", [])):
            continue
        if _needs_focus_filter(law, source):
            if not _matches_focus_keywords(combined, law.get("focus_keywords", [])):
                continue
            if not _passes_personal_info_practical_filter(combined):
                continue
        link = getattr(entry, "link", "#")
        published = format_date(getattr(entry, "published", "") or getattr(entry, "updated", "") or getattr(entry, "pubDate", ""))
        result.append(_normalize_legal_item(law, source["label"], title, link, published, summary))
        if len(result) >= 12:
            break
    return result


def _fetch_html_for_law(law: dict, source: dict) -> list[dict]:
    res = requests.get(
        source["url"],
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Connection": "close"},
    )
    res.raise_for_status()
    res.encoding = res.apparent_encoding or "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")
    result = []
    seen = set()
    for a in soup.find_all("a", href=True):
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 8:
            continue
        context = a.parent.get_text(" ", strip=True) if a.parent else title
        combined = f"{title}\n{context}"
        if not _matches_keywords(combined, law.get("keywords", [])):
            continue
        if _matches_exclude_keywords(combined, law.get("exclude_keywords", [])):
            continue
        if _needs_focus_filter(law, source):
            if not _matches_focus_keywords(combined, law.get("focus_keywords", [])):
                continue
            if not _passes_personal_info_practical_filter(combined):
                continue
        link = urljoin(source["url"], a["href"])
        key = (title, link)
        if key in seen:
            continue
        seen.add(key)
        published = _parse_html_date(context)
        result.append(_normalize_legal_item(law, source["label"], title, link, published, context))
        if len(result) >= 12:
            break
    return result


def _dedupe_legal_items(items: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for item in sorted(items, key=lambda x: (x.get("published", ""), x.get("title", "")), reverse=True):
        key = (item.get("law_key", ""), item.get("title", ""), item.get("link", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _fetch_legal_law(law: dict) -> list[dict]:
    started = time.time()
    print(f"  [Legal] 取得開始: {law['law_name']}")
    collected = []
    for source in law.get("sources", []):
        src_started = time.time()
        try:
            if source["type"] == "rss":
                items = _fetch_rss_for_law(law, source)
            else:
                items = _fetch_html_for_law(law, source)
            collected.extend(items)
            elapsed = time.time() - src_started
            print(f"    [Legal] {law['law_name']} / {source['label']}: {len(items)}件 ({elapsed:.1f}s)")
        except Exception as ex:
            elapsed = time.time() - src_started
            print(f"[WARN] 法規制取得失敗 ({law['law_name']} / {source['label']}, {elapsed:.1f}s): {ex}")
    collected = _dedupe_legal_items(collected)[:15]
    elapsed = time.time() - started
    print(f"  [Legal] 取得完了: {law['law_name']} ({len(collected)}件, {elapsed:.1f}s)")
    return collected


def get_all_legal_news() -> list[dict]:
    results = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=min(5, len(LAW_DEFINITIONS))) as ex:
        futures = {ex.submit(_fetch_legal_law, law): law for law in LAW_DEFINITIONS}
        try:
            for future in as_completed(futures, timeout=LEGAL_TOTAL_TIMEOUT):
                law = futures[future]
                try:
                    items = future.result(timeout=LEGAL_SOURCE_TIMEOUT)
                    results.extend(items)
                except TimeoutError:
                    print(f"[WARN] 法規制個別タイムアウト ({law['law_name']} > {LEGAL_SOURCE_TIMEOUT}s)")
                except Exception as ex_:
                    print(f"[WARN] 法規制取得失敗 ({law['law_name']}): {ex_}")
        except TimeoutError:
            print(f"[WARN] 法規制取得全体タイムアウト ({LEGAL_TOTAL_TIMEOUT}s) - 未完了法令はスキップ")
        for future, law in futures.items():
            if future.done():
                continue
            future.cancel()
            print(f"[WARN] 法規制未完了をキャンセル: {law['law_name']}")
    elapsed = time.time() - started
    results = _dedupe_legal_items(results)
    results.sort(key=lambda x: (x.get("published", ""), x.get("title", "")), reverse=True)
    print(f"  [Legal] 集約完了: {len(results)}件 ({elapsed:.1f}s)")
    return results



def entry_to_dict(entry, default_source: str = "") -> dict:
    source = default_source
    try:
        source = entry.source.get("title", "") or default_source
    except Exception:
        source = default_source
    return {
        "title": getattr(entry, "title", ""),
        "link": getattr(entry, "link", "#"),
        "source": source,
        "published": getattr(entry, "published", "") or getattr(entry, "updated", "") or getattr(entry, "pubDate", ""),
    }


def dedupe_news_items(items: list[dict], limit: int | None = None) -> list[dict]:
    deduped = []
    seen = set()
    def sort_key(item: dict):
        return (format_date(item.get("published", "")), item.get("title", ""))
    for item in sorted(items, key=sort_key, reverse=True):
        key = ((item.get("title") or "").strip(), (item.get("link") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if limit and len(deduped) >= limit:
            break
    return deduped


def google_news_rss_url(query: str) -> str:
    from urllib.parse import quote_plus
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ja&gl=JP&ceid=JP:ja"


def fetch_google_news_topic(queries: list[str], limit: int = 20) -> list[dict]:
    collected = []
    for query in queries:
        try:
            print(f"  取得中: Google News / {query}")
            parsed = parse_feed_with_timeout(google_news_rss_url(query))
            collected.extend(entry_to_dict(e, "Google News") for e in getattr(parsed, "entries", [])[:limit])
        except Exception as ex:
            print(f"[WARN] Google News取得失敗 ({query}): {ex}")
    return dedupe_news_items(collected, limit=limit)


def get_security_trend_entries() -> list[dict]:
    return fetch_google_news_topic([
        'サイバーセキュリティ 注意喚起 OR フィッシング OR ランサムウェア',
        'site:ipa.go.jp 情報セキュリティ 注意喚起 OR site:jpcert.or.jp 注意喚起 OR site:nisc.go.jp サイバーセキュリティ',
    ], limit=18)


def get_incident_entries() -> list[dict]:
    return fetch_google_news_topic([
        '情報漏えい OR 不正アクセス OR ランサムウェア 被害',
        'サイバー攻撃 被害 OR 侵害 OR フィッシング 被害',
    ], limit=18)


def get_vulnerability_entries() -> list[dict]:
    collected = []
    jvn_urls = [
        'https://jvn.jp/rss/jvn.rdf',
        'https://jvndb.jvn.jp/ja/rss/jvndb_new.rdf',
    ]
    for url in jvn_urls:
        try:
            print(f"  取得中: JVN / {url}")
            parsed = parse_feed_with_timeout(url)
            collected.extend(entry_to_dict(e, 'JVN') for e in getattr(parsed, 'entries', [])[:20])
        except Exception as ex:
            print(f"[WARN] JVN取得失敗 ({url}): {ex}")
    collected.extend(fetch_google_news_topic([
        'CVE OR ゼロデイ OR 脆弱性 OR 緊急パッチ',
        'site:cisa.gov known exploited vulnerability OR site:jvn.jp 脆弱性',
    ], limit=12))
    return dedupe_news_items(collected, limit=24)


def get_jama_guideline_content() -> dict:
    resource_cards = [
        {"title": "自工会/部工会ガイドライン V2.3", "desc": "エンタープライズ領域の本体資料。最新版は v2.3。", "link": "https://www.jama.or.jp/operation/it/cyb_sec/cyb_sec_guideline.html", "meta": "JAMA 公式"},
        {"title": "解説書 V2.3", "desc": "用語・要求事項・達成基準の解釈補助。", "link": "https://www.jama.or.jp/operation/it/cyb_sec/cyb_sec_guideline.html", "meta": "JAMA 公式"},
        {"title": "チェックシート V2.3", "desc": "自己評価用チェックシート。", "link": "https://www.jama.or.jp/operation/it/cyb_sec/cyb_sec_guideline.html", "meta": "JAMA 公式"},
        {"title": "優先項目の解説資料", "desc": "レベル1の中から優先的に取り組むべき項目の解説。", "link": "https://www.jama.or.jp/operation/it/cyb_sec/cyb_sec_guideline.html", "meta": "JAMA 公式"},
        {"title": "中小企業向け手引き", "desc": "実践的な始め方・進め方の手引き。", "link": "https://www.jama.or.jp/operation/it/cyb_sec/cyb_sec_guideline.html", "meta": "JAMA 公式"},
        {"title": "工場領域版 v1.0", "desc": "OT 環境向けガイドラインとチェックシート。", "link": "https://www.jama.or.jp/operation/it/cyb_sec/cyb_sec_guideline.html", "meta": "JAMA 公式"},
        {"title": "最新情報・FAQ・説明会", "desc": "提出方法、FAQ、説明会資料などの関連導線。", "link": "https://www.japia.or.jp/work/ict/cybersecurity-newest", "meta": "JAPIA 公式"},
        {"title": "サプライチェーン向け情報", "desc": "自己評価提出方法、FAQ、説明会資料への導線。", "link": "https://www.jama.or.jp/operation/it/cyb_sec/cyb_sec_supply_chain.html", "meta": "JAMA 公式"},
    ]
    updates = [
        {"date": "2026-04-16", "title": "工場領域版 v1.0 公開", "detail": "工場領域向けガイドラインとチェックシートが公開。"},
        {"date": "2025-12-26", "title": "エンタープライズ領域 v2.3", "detail": "本体・解説書・チェックシートが v2.3 系で整理。"},
        {"date": "2025-12-26", "title": "FAQ / 説明会導線更新", "detail": "提出方法、FAQ、説明会資料の導線を最新化。"},
    ]
    clause_columns = {
        "Lv1": [
            "方針・体制整備",
            "資産把握・管理",
            "アクセス制御",
            "脆弱性管理",
            "委託先・取引先管理",
            "インシデント対応",
            "教育・訓練",
        ],
        "Lv2": [
            "ネットワーク管理",
            "認証強化",
            "ログ取得・監視",
            "バックアップ・復旧",
            "サプライチェーン統制",
            "点検・評価",
            "運用手順の標準化",
        ],
        "Lv3": [
            "高度監視・SOC連携",
            "ゼロトラスト寄り統制",
            "工場領域対策",
            "委託先評価の高度化",
            "演習・復旧計画",
            "経営層レビュー",
            "継続的改善",
        ],
    }
    return {
        "headline": "上段で関連資料と変更内容を見て、下段で Lv1 / Lv2 / Lv3 の条項イメージを確認する構成です。",
        "resources": resource_cards,
        "updates": updates,
        "clause_columns": clause_columns,
    }


def build_news_cards(items: list[dict]) -> str:
    if not items:
        return '<div class="empty-box">通知なし</div>'
    parts = []
    for idx, item in enumerate(items, 1):
        title = safe_text(item.get("title", "無題"))
        link = safe_text(item.get("link", "#"))
        source = safe_text(item.get("source", ""))
        published = format_date(item.get("published", ""))
        meta = " / ".join([x for x in [source, published] if x])
        meta_html = f'<div class="item-meta">{meta}</div>' if meta else ""
        parts.append(f"""
        <div class="item-card">
            <div class="item-index">{idx:02d}</div>
            <div class="item-body">
                <a class="item-title" href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>
                {meta_html}
            </div>
        </div>
        """)
    return "\n".join(parts)


def build_guideline_columns_html(guideline_content: dict) -> str:
    resource_cards_html = []
    for item in guideline_content.get("resources", []):
        resource_cards_html.append(f"""
        <div class="guideline-card">
          <div class="guideline-card-title">{safe_text(item['title'])}</div>
          <div class="guideline-card-desc">{safe_text(item['desc'])}</div>
          <div class="guideline-card-meta">{safe_text(item['meta'])}</div>
          <a class="guideline-link" href="{safe_text(item['link'])}" target="_blank" rel="noopener noreferrer">開く</a>
        </div>
        """)

    updates_html = []
    for item in guideline_content.get("updates", []):
        updates_html.append(f"""
        <div class="item-card">
            <div class="item-index">更新</div>
            <div class="item-body">
                <div class="item-title">{safe_text(item.get('title',''))}</div>
                <div class="item-meta">{safe_text(item.get('date',''))}</div>
                <div class="guideline-card-desc" style="margin-top:6px;">{safe_text(item.get('detail',''))}</div>
            </div>
        </div>
        """)

    col_parts = []
    for level, items in guideline_content.get("clause_columns", {}).items():
        lis = ''.join([f'<li>{safe_text(x)}</li>' for x in items])
        col_parts.append(f"""
        <div class="guideline-column">
          <div class="guideline-column-title">{safe_text(level)}</div>
          <div class="guideline-column-sub">条項イメージ</div>
          <ul class="info-list">{lis}</ul>
        </div>
        """)

    updates_block = ''.join(updates_html) if updates_html else '<div class="empty-box">変更情報なし</div>'
    return f"""
    <div class="notice-box" style="margin-bottom:16px;">{safe_text(guideline_content.get('headline', ''))}</div>
    <div class="panel" style="margin-bottom:16px;">
      <div class="section-title" style="font-size:18px; margin-bottom:6px;">変更内容</div>
      <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">新規追加、版数変更、更新日変更などを先に確認する欄です。</div>
      {updates_block}
    </div>
    <div class="panel" style="margin-bottom:16px;">
      <div class="section-title" style="font-size:18px; margin-bottom:6px;">関連資料</div>
      <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">本体・FAQ・チェックシート・工場領域など、参照する資料を次にまとめています。</div>
      <div class="guideline-grid">{''.join(resource_cards_html)}</div>
    </div>
    <div class="panel">
      <div class="section-title" style="font-size:18px; margin-bottom:6px;">条項一覧（参考）</div>
      <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">資料確認のあとに、Lv1 / Lv2 / Lv3 の条項イメージを下で確認します。</div>
      <div class="guideline-grid">{''.join(col_parts)}</div>
    </div>
    """


def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS run_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            risk_count INTEGER NOT NULL,
            dev_count INTEGER NOT NULL,
            security_count INTEGER NOT NULL DEFAULT 0,
            incident_count INTEGER NOT NULL DEFAULT 0,
            vuln_count INTEGER NOT NULL DEFAULT 0,
            config_name TEXT NOT NULL,
            config_key TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            title TEXT,
            link TEXT,
            source TEXT,
            published TEXT,
            FOREIGN KEY(run_id) REFERENCES run_history(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dev_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            title TEXT,
            link TEXT,
            published TEXT,
            liked_count INTEGER,
            FOREIGN KEY(run_id) REFERENCES run_history(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS legal_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            law_key TEXT,
            law_name TEXT,
            title TEXT,
            link TEXT,
            source TEXT,
            published TEXT,
            enforce_date TEXT,
            deadline TEXT,
            FOREIGN KEY(run_id) REFERENCES run_history(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            link TEXT PRIMARY KEY,
            title TEXT,
            summary TEXT,
            pub_dt TEXT,
            source TEXT,
            category TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS extra_news_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_key TEXT NOT NULL,
            title TEXT,
            link TEXT,
            source TEXT,
            published TEXT,
            UNIQUE(section_key, link)
        )
        """
    )

    existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(run_history)").fetchall()}
    for col in ("security_count", "incident_count", "vuln_count"):
        if col not in existing_cols:
            cur.execute(f"ALTER TABLE run_history ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")

    con.commit()
    return con


def cleanup_db(con):
    cutoff = (datetime.now(JST) - timedelta(days=HISTORY_DAYS)).isoformat()
    con.execute("DELETE FROM articles WHERE pub_dt < ? AND pub_dt != ''", (cutoff,))
    con.commit()


def save_run_history(con, run_at, risk_entries, dev_articles, config_result, legal_news=None, security_entries=None, incident_entries=None, vuln_entries=None):
    cur = con.cursor()
    security_entries = security_entries or []
    incident_entries = incident_entries or []
    vuln_entries = vuln_entries or []
    cur.execute(
        "INSERT INTO run_history (run_at, risk_count, dev_count, security_count, incident_count, vuln_count, config_name, config_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_at, len(risk_entries), len(dev_articles), len(security_entries), len(incident_entries), len(vuln_entries), config_result["name"], config_result["key"]),
    )
    run_id = cur.lastrowid

    for n in risk_entries:
        title = getattr(n, "title", "")
        link = getattr(n, "link", "")
        source = ""
        try:
            source = n.source.get("title", "")
        except Exception:
            source = ""
        published = getattr(n, "published", "")
        cur.execute(
            "INSERT INTO risk_items (run_id, title, link, source, published) VALUES (?, ?, ?, ?, ?)",
            (run_id, title, link, source, published),
        )

    for a in dev_articles:
        title = a.get("title", "")
        link = "https://zenn.dev" + a.get("path", "")
        published = a.get("published_at", "")
        liked_count = int(a.get("liked_count", 0) or 0)
        cur.execute(
            "INSERT INTO dev_items (run_id, title, link, published, liked_count) VALUES (?, ?, ?, ?, ?)",
            (run_id, title, link, published, liked_count),
        )

    for item in (legal_news or []):
        cur.execute(
            "INSERT INTO legal_items (run_id, law_key, law_name, title, link, source, published, enforce_date, deadline) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, item.get("law_key",""), item.get("law_name",""), item.get("title",""),
             item.get("link","#"), item.get("source",""), item.get("published",""),
             item.get("enforce_date",""), item.get("deadline","")),
        )

    con.commit()


def save_extra_news_items(con, section_key, items):
    cur = con.cursor()
    rows = []
    for item in (items or []):
        rows.append((
            section_key,
            item.get("title", ""),
            item.get("link", "#"),
            item.get("source", ""),
            item.get("published", ""),
        ))
    if rows:
        cur.executemany(
            "INSERT OR IGNORE INTO extra_news_items (section_key, title, link, source, published) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        con.commit()


def split_news_items_by_day(items):
    now_jst = datetime.now(JST)
    today = now_jst.date()
    yesterday = today - timedelta(days=1)

    today_items = []
    yesterday_items = []
    older_items = []

    for item in items or []:
        raw_published = item.get("published") or item.get("published_at") or ""
        published_dt = parse_any_datetime(raw_published)
        if not published_dt:
            older_items.append(item)
            continue

        if published_dt.date() == today:
            today_items.append(item)
        elif published_dt.date() == yesterday:
            yesterday_items.append(item)
        else:
            older_items.append(item)

    sort_key = lambda a: parse_any_datetime(a.get("published") or a.get("published_at") or "") or datetime.min.replace(tzinfo=JST)
    today_items.sort(key=sort_key, reverse=True)
    yesterday_items.sort(key=sort_key, reverse=True)
    older_items.sort(key=sort_key, reverse=True)
    return today_items, yesterday_items, older_items


def get_older_extra_news(con, section_key, border_dt, limit):
    border_date = border_dt.astimezone(JST).date()
    cur = con.cursor()
    cur.execute(
        """
        SELECT title, link, source, published
        FROM extra_news_items
        WHERE section_key = ?
          AND published IS NOT NULL
          AND published <> ''
        ORDER BY published DESC, id DESC
        LIMIT ?
        """,
        (section_key, max(limit * 3, 300)),
    )

    rows = []
    seen = set()
    for title, link, source, published in cur.fetchall():
        dt = parse_any_datetime(published)
        if not dt or dt.date() >= border_date:
            continue
        key = (title or "", link or "", published or "")
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "title": title or "",
            "link": link or "#",
            "source": source or "",
            "published": published or "",
        })
        if len(rows) >= limit:
            break
    rows.sort(key=lambda a: parse_any_datetime(a.get("published") or "") or datetime.min.replace(tzinfo=JST), reverse=True)
    return rows


def build_day_group_html(title, items):
    if not items:
        return ""
    return f"""
    <div class="panel" style="margin-bottom:16px;">
      <div class="section-title" style="font-size:18px; margin-bottom:10px;">{safe_text(title)}（{len(items)}件）</div>
      {build_news_cards(items)}
    </div>
    """


def build_news_history_page_html(today_items, yesterday_items, older_items, intro_text=""):
    today_items = today_items or []
    yesterday_items = yesterday_items or []
    older_items = (older_items or [])[:200]

    parts = []
    if intro_text:
        parts.append(f'<div class="notice-box" style="margin-bottom:18px; font-size:13px;">{safe_text(intro_text)}</div>')

    parts.append(f"""
    <div class="section-title" style="font-size:18px; margin-bottom:6px;">🟢 今日</div>
    <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">本日の取得記事 / {len(today_items)}件</div>
    {build_news_cards(today_items)}
    <div class="section-title" style="font-size:18px; margin-top:22px; margin-bottom:6px;">🟡 昨日</div>
    <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">前日の取得記事 / {len(yesterday_items)}件</div>
    {build_news_cards(yesterday_items)}
    <div class="section-title" style="font-size:18px; margin-top:22px; margin-bottom:6px;">📚 過去</div>
    <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">SQLiteに蓄積された過去履歴 / {len(older_items)}件（最大200件表示）</div>
    {build_news_cards(older_items)}
    """)

    return "\n".join([x for x in parts if x])



def get_recent_history(con, limit=8):
    cur = con.cursor()
    cols = {row[1] for row in cur.execute("PRAGMA table_info(run_history)").fetchall()}
    if {"security_count", "incident_count", "vuln_count"}.issubset(cols):
        cur.execute(
            """
            SELECT run_at, risk_count, dev_count, security_count, incident_count, vuln_count, config_name
            FROM run_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
    else:
        cur.execute(
            """
            SELECT run_at, risk_count, dev_count, config_name
            FROM run_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
    return cur.fetchall()


def build_history_rows(history):
    if not history:
        return '<tr><td colspan="7">履歴なし</td></tr>'

    rows = []
    for rec in history:
        if len(rec) >= 7:
            run_at, risk_count, dev_count, security_count, incident_count, vuln_count, config_name = rec[:7]
        else:
            run_at, risk_count, dev_count, config_name = rec[:4]
            security_count = incident_count = vuln_count = 0
        rows.append(
            "<tr>"
            f"<td>{safe_text(run_at)}</td>"
            f"<td>{safe_text(risk_count)}</td>"
            f"<td>{safe_text(dev_count)}</td>"
            f"<td>{safe_text(security_count)}</td>"
            f"<td>{safe_text(incident_count)}</td>"
            f"<td>{safe_text(vuln_count)}</td>"
            f"<td>{safe_text(config_name)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def collect_texts(risk_entries, dev_articles):
    texts = []
    for n in risk_entries:
        texts.append(getattr(n, "title", ""))
    for a in dev_articles:
        texts.append(a.get("title", ""))
    return "\n".join(texts).lower()


def count_keywords(text_blob, keywords):
    total = 0
    hits = []
    for kw in keywords:
        c = text_blob.count(kw.lower())
        total += c
        if c > 0:
            hits.append((kw, c))
    return total, hits


def make_config_result(key, risk_entries, dev_articles, hits_map, scores):
    config_map = {
        "closed_governance": {
            "name": "社内限定・管理重視型",
            "summary": "社内データを外に出さず、利用者・操作・ログを管理しながらAIを運用する構成です。",
            "plain_meaning": "機密データを外に出したくない業務向けです。誰が何をしたかを管理しながら使う前提のAI運用です。",
            "fit_for": [
                "顧客情報、設計情報、生産情報など機密データを扱う",
                "利用者を部署や権限で制限したい",
                "監査ログや利用履歴を残したい",
            ],
            "features": [
                "社外へのデータ送信を抑えやすい",
                "アクセス制御、承認、ログ管理を組み込みやすい",
                "情報システム部門が統制しやすい",
            ],
            "cautions": [
                "導入コストと運用負荷が高くなりやすい",
                "素早い拡張より安全性と統制を優先する構成",
                "使い勝手よりルール整備が先に必要",
            ],
            "next_actions": [
                "対象データを機密区分で分類する",
                "利用者、権限、操作ログの要件を決める",
                "社内限定で試験運用し、統制ルールを固める",
            ],
        },
        "rag_knowledge": {
            "name": "社内データ参照型（RAG）",
            "summary": "社内文書やFAQを検索して、その結果をもとにAIが回答する構成です。",
            "plain_meaning": "社内にある資料を探してから答えるAIです。社内規程、手順書、過去資料の活用に向いています。",
            "fit_for": [
                "社内FAQ・規程・手順書を検索したい",
                "回答の根拠を社内文書に寄せたい",
                "ナレッジが散在していて探す手間を減らしたい",
            ],
            "features": [
                "社内文書を検索してから回答する",
                "一般的な生成AI単体より回答の根拠を持たせやすい",
                "SharePoint、PDF、手順書、FAQとの相性が良い",
            ],
            "cautions": [
                "元データが古いと回答も古くなる",
                "検索対象の整備と更新ルールが必要",
                "アクセス権の考慮が必要",
            ],
            "next_actions": [
                "検索対象文書を決める",
                "更新頻度の高い文書から優先登録する",
                "回答評価用の質問セットを作って精度確認する",
            ],
        },
        "cloud_api": {
            "name": "クラウドAI活用型",
            "summary": "外部のAI APIやSaaSを使って、要約・文章生成・翻訳・試作を素早く進める構成です。",
            "plain_meaning": "まず早く使い始めたいときの構成です。PoCや小規模業務改善に向いています。",
            "fit_for": [
                "PoCを短期間で回したい",
                "要約、翻訳、文書作成をすぐ業務に入れたい",
                "最新モデルを素早く試したい",
            ],
            "features": [
                "導入が比較的早い",
                "最新モデルや機能を使いやすい",
                "小さく始めて広げやすい",
            ],
            "cautions": [
                "送信データの取り扱いルールが必要",
                "利用部門や用途の制限を決めないと広がりやすい",
                "コスト管理とログ管理の設計が必要",
            ],
            "next_actions": [
                "利用するサービスと用途を絞る",
                "入力禁止データと利用ルールを決める",
                "小規模PoCで費用対効果を確認する",
            ],
        },
        "api_integration": {
            "name": "API連携・組み込み型",
            "summary": "AIを単体で使うのではなく、既存システムや社内ツールに組み込んで使う構成です。",
            "plain_meaning": "チャット画面だけで終わらず、業務システムの中でAIを動かしたいときの構成です。",
            "fit_for": [
                "社内Web、Excel、業務システムとつなげたい",
                "入力→判定→出力までを一連で処理したい",
                "人手作業を減らしたい",
            ],
            "features": [
                "既存システムの流れにAIを組み込める",
                "API、バッチ、Webhook連携と相性が良い",
                "定型業務の省力化につなげやすい",
            ],
            "cautions": [
                "連携先システムごとの制約確認が必要",
                "エラー時の再実行や例外処理設計が必要",
                "仕様変更時の保守が増えやすい",
            ],
            "next_actions": [
                "連携対象システムと入出力を整理する",
                "API化する処理範囲を決める",
                "失敗時の戻し方と監視方法を決める",
            ],
        },
        "agent_automation": {
            "name": "業務自動化・エージェント型",
            "summary": "AIが複数ステップの処理を順番に実行し、調査・整理・通知まで進める構成です。",
            "plain_meaning": "人が毎回指示しなくても、AIにある程度の手順を任せたいときの構成です。",
            "fit_for": [
                "定期的な情報収集や仕分けを自動化したい",
                "通知、要約、分類をまとめて処理したい",
                "手順がある程度決まっている業務を自動化したい",
            ],
            "features": [
                "複数工程をまとめて実行しやすい",
                "定期実行やルールベース処理と相性が良い",
                "調査→要約→通知の流れを作りやすい",
            ],
            "cautions": [
                "暴走防止のため権限と実行範囲を絞る必要がある",
                "誤判定時の確認フローが必要",
                "全自動にしすぎると品質事故の原因になる",
            ],
            "next_actions": [
                "自動化したい手順を1本に絞る",
                "人が確認する停止点を決める",
                "通知先、実行頻度、失敗時対応を決める",
            ],
        },
        "analytics_insight": {
            "name": "分析・意思決定支援型",
            "summary": "ニュース、ログ、実績データを整理・分類し、傾向把握や判断材料づくりに使う構成です。",
            "plain_meaning": "文章を作るためではなく、状況を見える化して判断しやすくするためのAI活用です。",
            "fit_for": [
                "市場分析、競合分析、IT動向整理をしたい",
                "経営向けの示唆や観測点を作りたい",
                "複数情報源をまとめて見たい",
            ],
            "features": [
                "複数ソースを横断して整理しやすい",
                "分類、要約、傾向抽出に向いている",
                "ダッシュボードやレポート化につなげやすい",
            ],
            "cautions": [
                "分類ルールが粗いと示唆の質も落ちる",
                "元データの偏りがあると結論も偏る",
                "定義や観点の見直しが継続的に必要",
            ],
            "next_actions": [
                "見たい指標と分類軸を決める",
                "データ源ごとの偏りを確認する",
                "レポート出力形式を先に決める",
            ],
        },
        "local_llm": {
            "name": "社内実験・ローカル検証型",
            "summary": "ローカルLLMやオンプレ環境で、限定用途を試験的に検証する構成です。",
            "plain_meaning": "本番導入前に社内で試したいときの構成です。実験や検証向けで、全社運用向けとは限りません。",
            "fit_for": [
                "ローカルLLMを試したい",
                "GPUやオンプレ環境で実験したい",
                "閉域でまず検証してから判断したい",
            ],
            "features": [
                "外部接続なしで試験しやすい",
                "モデル比較や検証用途に向く",
                "限定PoCとして始めやすい",
            ],
            "cautions": [
                "性能や精度はモデルとGPU性能に左右される",
                "運用を広げると管理負荷が増える",
                "本番利用には追加設計が必要",
            ],
            "next_actions": [
                "対象業務を1つに絞る",
                "必要GPUと推論速度を確認する",
                "本番化の条件を事前に決める",
            ],
        },
    }

    result = dict(config_map[key])
    result["key"] = key

    sorted_hits = sorted(hits_map.get(key, []), key=lambda x: x[1], reverse=True)[:5]
    if not sorted_hits:
        sorted_hits = [("AI", len(dev_articles) + len(risk_entries))]

    score_text = f"判定スコア {scores.get(key, 0)}"
    result["reasons"] = [
        f"AIリスク記事 {len(risk_entries)} 件",
        f"開発記事 {len(dev_articles)} 件",
        score_text,
        "関連キーワード: " + " / ".join([f"{k}({v})" for k, v in sorted_hits]),
    ]
    return result



def analyze_config(risk_entries, dev_articles):
    text_blob = collect_texts(risk_entries, dev_articles)

    keyword_sets = {
        "closed_governance": [
            "security", "privacy", "leak", "leakage", "regulation", "compliance", "governance",
            "audit", "permission", "identity", "脆弱性", "漏洩", "規制", "法", "セキュリティ",
            "監査", "統制", "権限", "ガバナンス", "認証", "認可", "ゼロトラスト"
        ],
        "rag_knowledge": [
            "rag", "vector", "embedding", "embeddings", "search", "retrieval", "knowledge",
            "faq", "document", "documents", "sharepoint", "検索", "ナレッジ", "文書",
            "knowledge base", "index", "indexed", "pdf", "manual", "マニュアル"
        ],
        "cloud_api": [
            "azure", "openai", "api", "microsoft", "gpt", "claude", "bedrock", "vertex",
            "gemini", "copilot", "saas", "cloud", "llm api", "model"
        ],
        "api_integration": [
            "integration", "workflow", "webhook", "sdk", "connector", "erp", "crm",
            "system", "systems", "batch", "pipeline", "連携", "組み込み", "社内ツール",
            "業務システム", "excel", "sharepoint api", "automation api"
        ],
        "agent_automation": [
            "agent", "agents", "automation", "automate", "orchestrator", "orchestration",
            "task", "planner", "multi-step", "autonomous", "assistant", "scheduler",
            "workflow agent", "エージェント", "自動化", "定期実行", "通知"
        ],
        "analytics_insight": [
            "analytics", "analysis", "insight", "insights", "forecast", "trend", "benchmark",
            "dashboard", "report", "reporting", "market", "metrics", "observability",
            "分析", "予測", "傾向", "可視化", "レポート", "ダッシュボード", "指標"
        ],
        "local_llm": [
            "local", "on-prem", "onprem", "ollama", "llama", "gpu", "vram", "offline",
            "self-hosted", "ローカル", "オンプレ", "閉域", "社内gpu"
        ],
    }

    phrase_weights = {
        "closed_governance": {
            "data leakage": 3, "zero trust": 3, "access control": 3, "監査ログ": 3,
            "情報漏洩": 3, "権限管理": 3,
        },
        "rag_knowledge": {
            "vector database": 3, "knowledge base": 3, "enterprise search": 3,
            "semantic search": 3, "社内文書": 3,
        },
        "cloud_api": {
            "openai api": 3, "azure openai": 3, "foundation model": 2, "api pricing": 2,
        },
        "api_integration": {
            "system integration": 3, "workflow orchestration": 3, "business workflow": 3,
            "api integration": 3, "社内システム": 3,
        },
        "agent_automation": {
            "ai agent": 3, "multi agent": 3, "agent workflow": 3, "task automation": 3,
            "自律実行": 3,
        },
        "analytics_insight": {
            "market analysis": 3, "trend analysis": 3, "executive report": 3,
            "意思決定": 3,
        },
        "local_llm": {
            "local llm": 3, "on-premise": 3, "self hosted": 3, "air gapped": 3,
        },
    }

    scores = {key: 0 for key in keyword_sets}
    hits_map = {key: [] for key in keyword_sets}

    for key, words in keyword_sets.items():
        score, hits = count_keywords(text_blob, words)
        scores[key] += score
        hits_map[key].extend(hits)

    for key, mapping in phrase_weights.items():
        for phrase, weight in mapping.items():
            count = text_blob.count(phrase.lower())
            if count > 0:
                scores[key] += count * weight
                hits_map[key].append((phrase, count * weight))

    if len(dev_articles) >= 6:
        scores["cloud_api"] += 1
        scores["rag_knowledge"] += 1
        scores["api_integration"] += 1
    if len(risk_entries) >= 5:
        scores["closed_governance"] += 2
    if len(risk_entries) >= 7:
        scores["analytics_insight"] += 1

    if any(k in text_blob for k in ["agent", "agents", "エージェント"]):
        scores["agent_automation"] += 2
    if any(k in text_blob for k in ["dashboard", "trend", "analysis", "分析", "予測"]):
        scores["analytics_insight"] += 2
    if any(k in text_blob for k in ["integration", "webhook", "連携", "組み込み"]):
        scores["api_integration"] += 2

    best_key = max(scores, key=scores.get)
    if scores[best_key] == 0:
        best_key = "cloud_api"

    return make_config_result(best_key, risk_entries, dev_articles, hits_map, scores)

def build_list_items_news(entries):
    if not entries:
        return '<div class="empty-box">通知なし</div>'

    parts = []
    for idx, n in enumerate(entries, 1):
        title = safe_text(getattr(n, "title", "無題"))
        link = safe_text(getattr(n, "link", "#"))
        source = ""
        try:
            source = safe_text(n.source.get("title", ""))
        except Exception:
            source = ""
        published = format_date(getattr(n, "published", ""))
        meta = " / ".join([x for x in [source, published] if x])
        meta_html = f'<div class="item-meta">{meta}</div>' if meta else ""

        parts.append(
            f"""
        <div class="item-card">
            <div class="item-index">{idx:02d}</div>
            <div class="item-body">
                <a class="item-title" href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>
                {meta_html}
            </div>
        </div>
        """
        )
    return "\n".join(parts)


def build_list_items_articles(articles):
    if not articles:
        return '<div class="empty-box">通知なし</div>'

    parts = []
    for idx, a in enumerate(articles, 1):
        title = safe_text(a.get("title", "無題"))
        raw_link = a.get("link") or ""
        if not raw_link and a.get("path"):
            raw_link = "https://zenn.dev" + a.get("path", "")
        link = safe_text(raw_link or "#")
        published = format_date(a.get("published_at") or a.get("published") or "")
        liked = safe_text(a.get("liked_count", 0))
        meta = " / ".join([x for x in [published, f"いいね {liked}"] if x])
        meta_html = f'<div class="item-meta">{meta}</div>' if meta else ""

        parts.append(
            f"""
        <div class="item-card">
            <div class="item-index">{idx:02d}</div>
            <div class="item-body">
                <a class="item-title" href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>
                {meta_html}
            </div>
        </div>
        """
        )
    return "\n".join(parts)


def split_dev_articles_by_day(dev_articles):
    now_jst = datetime.now(JST)
    today = now_jst.date()
    yesterday = today - timedelta(days=1)

    today_articles = []
    yesterday_articles = []
    older_articles = []

    for article in dev_articles:
        raw_published = article.get("published_at") or article.get("published") or ""
        published_dt = parse_any_datetime(raw_published)
        if not published_dt:
            older_articles.append(article)
            continue

        if published_dt.date() == today:
            today_articles.append(article)
        elif published_dt.date() == yesterday:
            yesterday_articles.append(article)
        else:
            older_articles.append(article)

    sort_key = lambda a: parse_any_datetime(a.get("published_at") or a.get("published") or "") or datetime.min.replace(tzinfo=JST)
    today_articles.sort(key=sort_key, reverse=True)
    yesterday_articles.sort(key=sort_key, reverse=True)
    older_articles.sort(key=sort_key, reverse=True)
    return today_articles, yesterday_articles, older_articles




def split_risk_entries_by_day(risk_entries):
    now_jst = datetime.now(JST)
    today = now_jst.date()
    yesterday = today - timedelta(days=1)

    today_items = []
    yesterday_items = []
    older_items = []

    def _to_item(entry):
        source = ""
        try:
            source = entry.source.get("title", "")
        except Exception:
            source = ""
        return {
            "title": getattr(entry, "title", "") or "",
            "link": getattr(entry, "link", "#") or "#",
            "source": source or "",
            "published": getattr(entry, "published", "") or getattr(entry, "updated", "") or getattr(entry, "pubDate", "") or "",
        }

    for entry in risk_entries or []:
        item = _to_item(entry)
        published_dt = parse_any_datetime(item.get("published", ""))
        if not published_dt:
            older_items.append(item)
            continue

        if published_dt.date() == today:
            today_items.append(item)
        elif published_dt.date() == yesterday:
            yesterday_items.append(item)
        else:
            older_items.append(item)

    sort_key = lambda a: parse_any_datetime(a.get("published") or "") or datetime.min.replace(tzinfo=JST)
    today_items.sort(key=sort_key, reverse=True)
    yesterday_items.sort(key=sort_key, reverse=True)
    older_items.sort(key=sort_key, reverse=True)
    return today_items, yesterday_items, older_items


def get_older_risk_history(con, border_dt, limit):
    border_date = border_dt.astimezone(JST).date()
    cur = con.cursor()
    cur.execute(
        """
        SELECT title, link, source, published
        FROM risk_items
        WHERE published IS NOT NULL
          AND published <> ''
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(limit * 3, 300),),
    )

    rows = []
    seen = set()
    for title, link, source, published in cur.fetchall():
        dt = parse_any_datetime(published)
        if not dt or dt.date() >= border_date:
            continue
        key = (title or "", link or "", published or "")
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "title": title or "",
            "link": link or "#",
            "source": source or "",
            "published": published or "",
        })
        if len(rows) >= limit:
            break

    rows.sort(key=lambda a: parse_any_datetime(a.get("published") or "") or datetime.min.replace(tzinfo=JST), reverse=True)
    return rows
def get_older_dev_history(con, border_dt, limit):
    border_iso = border_dt.astimezone(timezone.utc).isoformat()
    cur = con.cursor()
    cur.execute(
        """
        SELECT title, link, published, liked_count
        FROM dev_items
        WHERE published IS NOT NULL
          AND published <> ''
          AND published < ?
        ORDER BY published DESC, id DESC
        LIMIT ?
        """,
        (border_iso, limit),
    )

    rows = []
    seen = set()
    for title, link, published, liked_count in cur.fetchall():
        key = (title or "", link or "", published or "")
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "title": title or "",
                "link": link or "#",
                "published": published or "",
                "liked_count": int(liked_count or 0),
            }
        )
    return rows


def build_dev_page_html(today_dev_articles, yesterday_dev_articles, older_dev_articles):
    today_count = len(today_dev_articles)
    yesterday_count = len(yesterday_dev_articles)
    older_count = len(older_dev_articles)
    today_html = build_list_items_articles(today_dev_articles)
    yesterday_html = build_list_items_articles(yesterday_dev_articles)
    older_html = build_list_items_articles(older_dev_articles)

    return f"""
    <div class="section-title" style="font-size:18px; margin-bottom:6px;">🔥 今日</div>
    <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">Zenn AIトピックの最新記事 / {today_count}件</div>
    {today_html}
    <div class="section-title" style="font-size:18px; margin-top:22px; margin-bottom:6px;">🟡 昨日</div>
    <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">前日の取得記事 / {yesterday_count}件</div>
    {yesterday_html}
    <div class="section-title" style="font-size:18px; margin-top:22px; margin-bottom:6px;">📚 過去</div>
    <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">SQLiteに蓄積された過去履歴 / {older_count}件（最大200件表示）</div>
    {older_html}
    """


def build_list_html(items):
    return "".join([f"<li>{safe_text(x)}</li>" for x in items])


def _law_latest(legal_news: list[dict], law_key: str) -> dict | None:
    """指定法令の最新アイテムを返す"""
    for item in legal_news:
        if item.get("law_key") == law_key:
            return item
    return None


def _law_items(legal_news: list[dict], law_key: str) -> list[dict]:
    """指定法令のアイテム一覧を返す（日付降順）"""
    return [i for i in legal_news if i.get("law_key") == law_key]


def _date_badge(date_str: str, label: str, color: str) -> str:
    """施行日・対応期限バッジHTML"""
    if not date_str:
        return ""
    return (
        f'<span style="display:inline-block; padding:3px 10px; border-radius:6px;'
        f' background:{color}; color:#fff; font-size:11px; font-weight:700;'
        f' margin-right:6px;">{label}　{safe_text(date_str)}</span>'
    )


def build_legal_summary_panel(legal_news: list[dict]) -> str:
    """MAINページ用 法規制サマリーパネル"""
    # 法令を最新ニュース日付の新しい順に並べる
    src_map = {s["law_key"]: s for s in LEGAL_SOURCES}
    order_map = {k: i for i, k in enumerate(LAW_DISPLAY_ORDER)}
    law_keys_sorted = sorted(
        [s["law_key"] for s in LEGAL_SOURCES],
        key=lambda k: order_map.get(k, 999),
    )

    tag_colors = {
        "personal_info":       ("#0c4f90", "#4db4ff"),
        "unauthorized_access": ("#1a4a1a", "#6dcc7f"),
        "e_document":          ("#4a3000", "#ffcc55"),
        "electronic_signature":("#3a0a55", "#d07aff"),
        "unfair_competition":  ("#5a1010", "#ff7a7a"),
    }

    rows = []
    for key in law_keys_sorted:
        src     = src_map[key]
        latest  = _law_latest(legal_news, key)
        items   = _law_items(legal_news, key)
        bg, fg  = tag_colors.get(key, ("#123c66", "#dff3ff"))

        latest_date  = safe_text(latest["published"]) if latest else "―"
        latest_title = latest["title"] if latest else "情報なし"
        latest_link  = latest["link"]  if latest else "#"

        enforce_badge = _date_badge(latest.get("enforce_date","") if latest else "", "施行", "#1f7a43")
        deadline_badge= _date_badge(latest.get("deadline","")     if latest else "", "期限", "#8a3a00")

        # 新着バッジ（7日以内）
        new_badge = ""
        if latest and latest.get("published","") >= (
            datetime.now(JST).strftime("%Y-%m-%d")[:8] + "01"
        ):
            new_badge = '<span style="background:#c0392b;color:#fff;font-size:10px;font-weight:800;padding:2px 7px;border-radius:4px;margin-left:6px;">NEW</span>'

        rows.append(f"""
      <div style="display:grid; grid-template-columns:140px 1fr; gap:0; border-bottom:1px solid #17202a; padding:12px 0; align-items:start;">
        <div style="display:flex; flex-direction:column; gap:4px;">
          <span class="badge" style="background:{bg}; color:{fg}; font-size:11px; padding:4px 8px; width:fit-content;">{safe_text(src["law_abbr"])}</span>
          <span style="font-size:12px; color:#b8c0cc; font-weight:700;">{safe_text(src["law_name"])}</span>
          <span style="font-size:11px; color:#5a6a7a;">最終更新: {latest_date}</span>
        </div>
        <div style="padding-left:12px;">
          <div style="margin-bottom:5px;">{enforce_badge}{deadline_badge}{new_badge}</div>
          <a href="{safe_text(latest_link)}" target="_blank" rel="noopener noreferrer"
             style="color:#8ad1ff; text-decoration:none; font-size:13px; font-weight:700; line-height:1.55;">
            {safe_text(latest_title)}
          </a>
          <div style="font-size:11px; color:#5a6a7a; margin-top:3px;">全{len(items)}件
            <a href="#" onclick="document.querySelector('.nav-btn[data-page=page-legal]').click(); return false;"
               style="color:#4db4ff; margin-left:8px;">詳細を見る →</a>
          </div>
        </div>
      </div>""")

    return "\n".join(rows)


def build_legal_page_html(legal_news: list[dict]) -> str:
    """法規制情報ページのHTMLを構築"""
    src_map = {s["law_key"]: s for s in LEGAL_SOURCES}

    # 法令を最新ニュース日付の新しい順
    order_map = {k: i for i, k in enumerate(LAW_DISPLAY_ORDER)}
    law_keys_sorted = sorted(
        [s["law_key"] for s in LEGAL_SOURCES],
        key=lambda k: order_map.get(k, 999),
    )

    tag_colors = {
        "personal_info":       ("#0c4f90", "#4db4ff"),
        "unauthorized_access": ("#1a4a1a", "#6dcc7f"),
        "e_document":          ("#4a3000", "#ffcc55"),
        "electronic_signature":("#3a0a55", "#d07aff"),
        "unfair_competition":  ("#5a1010", "#ff7a7a"),
    }

    sections = []
    for key in law_keys_sorted:
        src   = src_map[key]
        items = _law_items(legal_news, key)
        bg, fg = tag_colors.get(key, ("#123c66", "#dff3ff"))
        latest = items[0] if items else None

        # 施行日・期限バッジ（最新アイテムから）
        enforce_badge = _date_badge(latest.get("enforce_date","") if latest else "", "施行開始", "#1f7a43")
        deadline_badge= _date_badge(latest.get("deadline","")     if latest else "", "対応期限", "#8a3a00")
        date_badges   = enforce_badge + deadline_badge

        # ニュース行
        if items:
            news_rows = []
            for idx, item in enumerate(items, 1):
                title    = safe_text(item.get("title", "無題"))
                link     = safe_text(item.get("link", "#"))
                published= safe_text(item.get("published", ""))
                e_badge  = _date_badge(item.get("enforce_date",""), "施行", "#1f7a43")
                d_badge  = _date_badge(item.get("deadline",""),     "期限", "#8a3a00")
                meta_parts = [published]
                meta = " / ".join([x for x in meta_parts if x])
                meta_html = f'<div class="item-meta">{meta}</div>' if meta else ""
                badge_html = (e_badge + d_badge) if (e_badge or d_badge) else ""
                news_rows.append(f"""
        <div class="item-card">
            <div class="item-index">{idx:02d}</div>
            <div class="item-body">
                <a class="item-title" href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>
                {f'<div style="margin-top:4px;">{badge_html}</div>' if badge_html else ""}
                {meta_html}
            </div>
        </div>""")
            news_html = "\n".join(news_rows)
        else:
            news_html = '<div class="empty-box">最新情報なし（公式サイトをご確認ください）</div>'

        sections.append(f"""
    <div class="panel" style="margin-bottom:18px;">
      <div style="display:flex; align-items:center; gap:12px; margin-bottom:10px; flex-wrap:wrap;">
        <span class="badge" style="background:{bg}; color:{fg}; font-size:13px; padding:6px 14px;">{safe_text(src["law_abbr"])}</span>
        <span style="font-size:20px; font-weight:800; color:#e8f4ff;">{safe_text(src["law_name"])}</span>
        {date_badges}
        <a href="{safe_text(src["official_url"])}" target="_blank" rel="noopener noreferrer"
           style="margin-left:auto; font-size:12px; color:#4db4ff; text-decoration:none; white-space:nowrap;">
          📎 公式サイト
        </a>
      </div>
      <div class="notice-box" style="margin-bottom:14px; font-size:13px;">{safe_text(src["description"])}</div>
      <div style="font-size:13px; color:#b8c0cc; margin-bottom:8px; font-weight:700;">📰 最新情報（{len(items)}件）</div>
      {news_html}
    </div>""")

    return "\n".join(sections)


def build_html(risk_entries, today_dev_articles, yesterday_dev_articles, older_dev_articles, generated_at, history, config_result, legal_news=None, fetched_dev_count=None, risk_today=None, risk_yesterday=None, risk_older=None, security_today=None, security_yesterday=None, security_older=None, incident_today=None, incident_yesterday=None, incident_older=None, vuln_today=None, vuln_yesterday=None, vuln_older=None, guideline_content=None):
    risk_today = risk_today or []
    risk_yesterday = risk_yesterday or []
    risk_older = risk_older or []
    security_today = security_today or []
    security_yesterday = security_yesterday or []
    security_older = security_older or []
    incident_today = incident_today or []
    incident_yesterday = incident_yesterday or []
    incident_older = incident_older or []
    vuln_today = vuln_today or []
    vuln_yesterday = vuln_yesterday or []
    vuln_older = vuln_older or []
    security_count = len(security_today) + len(security_yesterday) + len(security_older)
    incident_count = len(incident_today) + len(incident_yesterday) + len(incident_older)
    vuln_count = len(vuln_today) + len(vuln_yesterday) + len(vuln_older)
    risk_count = len(risk_entries)
    dev_count = fetched_dev_count if fetched_dev_count is not None else (len(today_dev_articles) + len(yesterday_dev_articles))
    risk_page_html = build_news_history_page_html(risk_today or [], risk_yesterday or [], risk_older or [], "AI関連の規制・脆弱性・漏えい・ガバナンス動向を、今日 / 昨日 / 過去 で確認します。")
    dev_html = build_dev_page_html(today_dev_articles, yesterday_dev_articles, older_dev_articles)
    history_rows = build_history_rows(history)
    reasons_html = build_list_html(config_result["reasons"])
    fit_for_html = build_list_html(config_result["fit_for"])
    features_html = build_list_html(config_result["features"])
    cautions_html = build_list_html(config_result["cautions"])
    next_actions_html = build_list_html(config_result["next_actions"])
    legal_page_html    = build_legal_page_html(legal_news or [])
    legal_summary_html = build_legal_summary_panel(legal_news or [])
    security_trend_html = build_news_history_page_html(security_today or [], security_yesterday or [], security_older or [], "セキュリティ全体の流れ、注意喚起、制度動向、主要トレンドを見る入口タブです。")
    incident_html = build_news_history_page_html(incident_today or [], incident_yesterday or [], incident_older or [], "情報漏えい、不正アクセス、ランサムウェア被害など、実被害・事例寄りの情報を集約します。")
    vulnerability_html = build_news_history_page_html(vuln_today or [], vuln_yesterday or [], vuln_older or [], "CVE、ゼロデイ、脆弱性情報、パッチ、アップデート、緊急対策情報をまとめて確認します。")
    guideline_html = build_guideline_columns_html(guideline_content or {})

    html_doc = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_text(CONFIG.get("app_name", ""))}</title>
<style>
:root {{
    --bg:#000000;
    --panel:#0d0d0d;
    --panel2:#101010;
    --text:#ffffff;
    --sub:#b8c0cc;
    --muted:#7f8a99;
    --line:#1b2735;
    --blue:#0c4f90;
    --blue2:#4db4ff;
    --green:#1f7a43;
    --amber:#8a5b12;
}}
* {{ box-sizing:border-box; }}
html, body {{
    margin:0;
    padding:0;
    background:var(--bg);
    color:var(--text);
    font-family:"Segoe UI","Yu Gothic UI","Meiryo",sans-serif;
}}
.app {{
    max-width:1320px;
    margin:0 auto;
    padding:0 18px 28px;
}}
.fixed-header {{
    position:sticky;
    top:0;
    z-index:1000;
    background:rgba(0,0,0,.98);
    border-bottom:1px solid #111827;
    padding:8px 0 10px;
}}
.header-row {{
    display:flex;
    align-items:center;
    gap:12px;
    min-width:0;
    margin-bottom:10px;
}}
.title {{
    font-size:30px;
    font-weight:800;
    color:var(--blue2);
    line-height:1.1;
    margin:0;
    white-space:nowrap;
    flex:0 0 auto;
}}
.subtitle {{
    color:var(--sub);
    font-size:14px;
    flex:0 1 auto;
    min-width:0;
    white-space:nowrap;
}}
.timestamp {{
    color:var(--muted);
    font-size:12px;
    white-space:nowrap;
    flex:0 0 auto;
}}
.nav {{
    display:grid;
    grid-template-columns:repeat(5,minmax(0,1fr));
    gap:10px;
}}
.nav-btn {{
    width:100%;
    border:1px solid #16406d;
    background:linear-gradient(180deg,#0c4f90 0%,#0a3560 100%);
    color:#fff;
    padding:12px 14px;
    border-radius:10px;
    cursor:pointer;
    font-size:14px;
    font-weight:700;
}}
.nav-btn:hover {{
    background:linear-gradient(180deg,#1366b8 0%,#0e4173 100%);
    border-color:#2b7fcc;
}}
.nav-btn.active {{
    outline:2px solid #79c6ff;
}}
.content {{ padding-top:18px; }}
.page {{ display:none; }}
.page.active {{ display:block; }}
.section-title {{
    font-size:22px;
    font-weight:800;
    margin:0 0 12px;
}}
.metrics {{
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:14px;
    margin-bottom:18px;
}}
.metric, .panel {{
    background:linear-gradient(180deg,var(--panel2) 0%,var(--panel) 100%);
    border:1px solid var(--line);
    border-radius:14px;
    padding:18px;
}}
.metric {{ min-height:176px; }}
.metric-label {{
    font-size:14px;
    color:#d8e4f0;
    font-weight:700;
    margin-bottom:12px;
}}
.metric-value {{
    font-size:34px;
    color:var(--blue2);
    font-weight:800;
    margin-bottom:10px;
    line-height:1.1;
}}
.metric-desc, .info-list {{
    font-size:13px;
    color:var(--sub);
    line-height:1.7;
}}
.item-card {{
    display:grid;
    grid-template-columns:52px 1fr;
    gap:12px;
    align-items:start;
    padding:12px 0;
    border-bottom:1px solid #17202a;
}}
.item-card:last-child {{ border-bottom:none; }}
.item-index {{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    min-height:38px;
    min-width:52px;
    border-radius:10px;
    background:#0a3560;
    color:#dff3ff;
    font-weight:800;
}}
.item-title {{
    color:#8ad1ff;
    text-decoration:none;
    font-weight:700;
    line-height:1.55;
}}
.item-title:hover {{ text-decoration:underline; }}
.item-meta {{
    color:var(--muted);
    font-size:12px;
    margin-top:5px;
    line-height:1.5;
}}
.empty-box {{
    background:#0b0b0b;
    border:1px dashed #27415f;
    color:#c5d7ea;
    padding:16px;
    border-radius:12px;
}}
.info-list {{
    margin:0;
    padding-left:18px;
    font-size:14px;
    line-height:1.9;
}}
.history-table {{
    width:100%;
    border-collapse:collapse;
    margin-top:8px;
}}
.history-table th, .history-table td {{
    border-bottom:1px solid #1a2734;
    padding:10px 8px;
    text-align:left;
    font-size:13px;
}}
.history-table th {{
    color:#dbe9f6;
    font-weight:700;
}}
.history-table td {{
    color:#b8c0cc;
}}
.ai-top {{
    display:grid;
    grid-template-columns:1.1fr 0.9fr;
    gap:16px;
    margin-bottom:16px;
}}
.ai-grid {{
    display:grid;
    grid-template-columns:repeat(2,minmax(0,1fr));
    gap:16px;
    align-items:start;
}}
.badge {{
    display:inline-block;
    padding:7px 10px;
    border-radius:999px;
    background:#123c66;
    color:#dff3ff;
    font-size:12px;
    font-weight:700;
    margin-bottom:10px;
}}
.lead-name {{
    font-size:28px;
    font-weight:800;
    color:#4db4ff;
    margin-bottom:8px;
}}
.lead-text {{
    color:#b8c0cc;
    line-height:1.85;
    font-size:15px;
}}
.kv-box {{
    border:1px solid #24384c;
    border-radius:12px;
    padding:14px 16px;
    background:#0a0f15;
}}
.kv-title {{
    font-size:13px;
    font-weight:800;
    color:#d9ebfb;
    margin-bottom:8px;
}}
.notice-box {{
    border-left:4px solid #6dc8ff;
    padding:12px 14px;
    background:#09111a;
    border-radius:10px;
    color:#cfe6fb;
    line-height:1.8;
}}
.warn-box {{
    border-left:4px solid #d79b32;
    padding:12px 14px;
    background:#151108;
    border-radius:10px;
    color:#f3dfb2;
    line-height:1.8;
}}
.action-btn {{
    display:inline-block;
    margin-top:14px;
    padding:10px 14px;
    border-radius:10px;
    background:linear-gradient(180deg,#1f7a43 0%,#145b30 100%);
    color:#fff;
    text-decoration:none;
    font-weight:700;
    border:1px solid #23904f;
}}
.action-btn:hover {{
    background:linear-gradient(180deg,#2f9958 0%,#1a6f3d 100%);
}}

.guideline-grid {{
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:14px;
}}
.guideline-column {{
    background:#0d0d0d;
    border:1px solid #1b2735;
    border-radius:14px;
    padding:14px;
}}
.guideline-column-title {{
    font-size:18px;
    font-weight:800;
    color:#e8f4ff;
}}
.guideline-column-sub {{
    font-size:12px;
    color:#7f8a99;
    margin:4px 0 12px;
}}
.guideline-card {{
    background:#11161d;
    border:1px solid #1b2735;
    border-radius:12px;
    padding:12px;
    margin-bottom:10px;
}}
.guideline-card-title {{
    font-size:14px;
    font-weight:700;
    color:#d9ebfb;
    margin-bottom:6px;
    line-height:1.5;
}}
.guideline-card-desc {{
    font-size:12px;
    color:#b8c0cc;
    line-height:1.7;
    margin-bottom:8px;
}}
.guideline-card-meta {{
    font-size:11px;
    color:#7f8a99;
    margin-bottom:8px;
}}
.guideline-link {{
    display:inline-block;
    color:#8ad1ff;
    text-decoration:none;
    font-size:12px;
    font-weight:700;
}}
@media (max-width:980px) {{
    .metrics, .ai-top, .ai-grid, .guideline-grid {{ grid-template-columns:1fr; }}
    .nav {{
        grid-template-columns:repeat(3,minmax(0,1fr));
        gap:6px;
    }}
    .nav-btn {{
        padding:8px 6px;
        font-size:11px;
        border-radius:8px;
    }}
    .header-row {{
        flex-wrap:wrap;
        align-items:flex-start;
    }}
    .title, .subtitle, .timestamp {{
        white-space:normal;
    }}
    .app {{
        padding-left:12px;
        padding-right:12px;
    }}
}}
@media (max-width:480px) {{
    .nav {{
        grid-template-columns:repeat(2,minmax(0,1fr));
    }}
    .nav-btn {{
        padding:7px 4px;
        font-size:10px;
    }}
    .title {{ font-size:20px; }}
}}
</style>
<script>
function showPage(pageId, btn) {{
    document.querySelectorAll('.page').forEach(function(el) {{ el.classList.remove('active'); }});
    document.querySelectorAll('.nav-btn').forEach(function(el) {{ el.classList.remove('active'); }});
    document.getElementById(pageId).classList.add('active');
    btn.classList.add('active');
    window.scrollTo({{ top: 0, left: 0, behavior: 'auto' }});
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
}}
window.addEventListener('DOMContentLoaded', function() {{
    var firstBtn = document.querySelector('.nav-btn');
    if (firstBtn) firstBtn.classList.add('active');
}});
</script>
</head>
<body>
<div class="app">
  <div class="fixed-header">
    <div class="header-row">
      <div class="title">{safe_text(APP_ICON)} {safe_text(CONFIG.get("app_name", ""))}</div>
      <div class="subtitle">｜ {html.escape(CONFIG.get("subtitle", ""))}</div>
      <div class="timestamp">最終更新: {safe_text(generated_at)}</div>
    </div>
    <div class="nav">
      <button class="nav-btn" data-page="page-main"  onclick="showPage('page-main', this)">🏠 MAIN</button>
      <button class="nav-btn" data-page="page-legal" onclick="showPage('page-legal', this)">⚖️ 法規制情報</button>
      <button class="nav-btn" data-page="page-guideline" onclick="showPage('page-guideline', this)">🏭 自工会ガイドライン</button>
      <button class="nav-btn" data-page="page-security" onclick="showPage('page-security', this)">🔐 セキュリティ動向</button>
      <button class="nav-btn" data-page="page-incident" onclick="showPage('page-incident', this)">⚠️ インシデント / 攻撃事例</button>
      <button class="nav-btn" data-page="page-vuln" onclick="showPage('page-vuln', this)">🧩 脆弱性 / CVE</button>
      <button class="nav-btn" data-page="page-gov"   onclick="showPage('page-gov', this)">🛡 AIガバナンス</button>
      <button class="nav-btn" data-page="page-dev"   onclick="showPage('page-dev', this)">🚀 開発効率</button>
      <button class="nav-btn" data-page="page-ai"    onclick="showPage('page-ai', this)">🤖 推奨AI構成</button>
    </div>
  </div>

  <div class="content">
    <div id="page-main" class="page active">
      <div class="section-title">📊 エグゼクティブ・サマリー</div>
      <div class="notice-box" style="margin-bottom:18px; font-size:13px;">MAIN は全体の入口です。法規制の最新動向、AIリスク件数、開発記事件数、現在の推奨構成、過去ログをまとめて確認します。</div>

      <div class="panel" style="margin-bottom:18px;">
        <div class="section-title" style="font-size:18px; margin-bottom:4px;">⚖️ 法規制情報　最新動向</div>
        <div style="font-size:12px; color:#7f8a99; margin-bottom:12px;">自工会ガイドライン対応5法令　／　最新更新日時の新しい順</div>
        {legal_summary_html}
      </div>

      <div class="metrics">
        <div class="metric">
          <div class="metric-label">AIリスク件数</div>
          <div class="metric-value">{safe_text(risk_count if risk_count else "通知なし")}</div>
          <div class="metric-desc">AI関連の規制・脆弱性・漏洩ニュースの取得件数です。</div>
        </div>
        <div class="metric">
          <div class="metric-label">開発記事件数</div>
          <div class="metric-value">{safe_text(dev_count if dev_count else "通知なし")}</div>
          <div class="metric-desc">AI開発や活用に関する記事の取得件数です。</div>
        </div>
        <div class="metric">
          <div class="metric-label">推奨構成</div>
          <div class="metric-value">{safe_text(config_result["name"])}</div>
          <div class="metric-desc">記事傾向から自動判定した、今の優先構成です。</div>
        </div>
      </div>

      <div class="metrics" style="margin-top:18px;">
        <div class="metric">
          <div class="metric-label">セキュリティ動向</div>
          <div class="metric-value">{safe_text(security_count if security_count else "通知なし")}</div>
          <div class="metric-desc">注意喚起や全体動向の記事件数です。</div>
        </div>
        <div class="metric">
          <div class="metric-label">インシデント / 攻撃事例</div>
          <div class="metric-value">{safe_text(incident_count if incident_count else "通知なし")}</div>
          <div class="metric-desc">実被害や侵害事例として拾えた件数です。</div>
        </div>
        <div class="metric">
          <div class="metric-label">脆弱性 / CVE</div>
          <div class="metric-value">{safe_text(vuln_count if vuln_count else "通知なし")}</div>
          <div class="metric-desc">脆弱性・CVE・パッチ関連の記事件数です。</div>
        </div>
      </div>

      <div class="panel">
        <div class="section-title" style="font-size:18px; margin-bottom:10px;">過去ログ（SQLite）</div>
        <table class="history-table">
          <thead>
            <tr>
              <th>取得日時</th>
              <th>AIリスク</th>
              <th>開発記事</th>
              <th>セキュリティ動向</th>
              <th>インシデント</th>
              <th>脆弱性 / CVE</th>
              <th>構成</th>
            </tr>
          </thead>
          <tbody>
            {history_rows}
          </tbody>
        </table>
      </div>
    </div>


    <div id="page-guideline" class="page">
      <div class="section-title">🏭 自工会ガイドライン</div>
      {guideline_html}
    </div>

    <div id="page-security" class="page">
      <div class="section-title">🔐 セキュリティ動向</div>
      {security_trend_html}
    </div>

    <div id="page-incident" class="page">
      <div class="section-title">⚠️ インシデント / 攻撃事例</div>
      {incident_html}
    </div>

    <div id="page-vuln" class="page">
      <div class="section-title">🧩 脆弱性 / CVE</div>
      {vulnerability_html}
    </div>

    <div id="page-gov" class="page">
      <div class="section-title">🛡 AIガバナンス</div>
      <div class="panel">{risk_page_html}</div>
    </div>

    <div id="page-dev" class="page">
      <div class="section-title">🚀 AI開発効率</div>
      <div class="panel">{dev_html}</div>
    </div>

    <div id="page-ai" class="page">
      <div class="section-title">🤖 推奨AI構成</div>

      <div class="ai-top">
        <div class="panel">
          <div class="badge">現在の推奨構成</div>
          <div class="lead-name">{safe_text(config_result["name"])}</div>
          <div class="lead-text">{safe_text(config_result["summary"])}</div>
        </div>
        <div class="panel">
          <div class="section-title" style="font-size:18px; margin-bottom:10px;">一言でいうと</div>
          <div class="notice-box">{safe_text(config_result["plain_meaning"])}</div>
        </div>
      </div>

      <div class="ai-grid">
        <div class="panel">
          <div class="section-title" style="font-size:18px; margin-bottom:10px;">この構成が向いているケース</div>
          <ul class="info-list">{fit_for_html}</ul>
        </div>
        <div class="panel">
          <div class="section-title" style="font-size:18px; margin-bottom:10px;">特徴</div>
          <ul class="info-list">{features_html}</ul>
        </div>
        <div class="panel">
          <div class="section-title" style="font-size:18px; margin-bottom:10px;">注意点</div>
          <div class="warn-box">
            <ul class="info-list" style="margin:0;">{cautions_html}</ul>
          </div>
        </div>
        <div class="panel">
          <div class="section-title" style="font-size:18px; margin-bottom:10px;">判断理由</div>
          <ul class="info-list">{reasons_html}</ul>
        </div>
      </div>

      <div class="panel" style="margin-top:16px;">
        <div class="section-title" style="font-size:18px; margin-bottom:10px;">次にやること</div>
        <ul class="info-list">{next_actions_html}</ul>
        <a class="action-btn" href="https://learn.microsoft.com/ja-jp/azure/search/retrieval-augmented-generation-overview" target="_blank" rel="noopener noreferrer">Microsoft RAGドキュメント</a>
      </div>
    </div>

    <div id="page-legal" class="page">
      <div class="section-title">⚖️ 法規制動向</div>
      <div class="notice-box" style="margin-bottom:18px; font-size:13px;">
        自工会ガイドラインに基づく情報セキュリティ関連法令の最新動向を追跡します。改正・施行・通達などの情報が更新されます。
      </div>
      {legal_page_html}
    </div>
  </div>
</div>
</body>
</html>"""
    return html_doc


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    start = time.time()
    print("  🚀 並列取得: 7カテゴリ / 7 workers 同時実行")
    guideline_content = get_jama_guideline_content()
    with ThreadPoolExecutor(max_workers=7) as ex:
        f_risk = ex.submit(get_ai_risk_entries)
        f_dev = ex.submit(get_dev_articles)
        f_legal = ex.submit(get_all_legal_news)
        f_security = ex.submit(get_security_trend_entries)
        f_incident = ex.submit(get_incident_entries)
        f_vuln = ex.submit(get_vulnerability_entries)
        try:
            risk_entries = f_risk.result(timeout=MAIN_FUTURE_TIMEOUT)
            risk_entries.sort(key=lambda e: e.get("published_parsed") or (0,)*9, reverse=True)
        except TimeoutError:
            print(f"[WARN] AI Risk取得タイムアウト ({MAIN_FUTURE_TIMEOUT}s)")
            risk_entries = []
        except Exception as e:
            print(f"[WARN] AI Risk取得失敗: {e}")
            risk_entries = []
        try:
            dev_articles = f_dev.result(timeout=MAIN_FUTURE_TIMEOUT)
            dev_articles.sort(key=lambda a: a.get("published_at") or "", reverse=True)
        except TimeoutError:
            print(f"[WARN] Dev Articles取得タイムアウト ({MAIN_FUTURE_TIMEOUT}s)")
            dev_articles = []
        except Exception as e:
            print(f"[WARN] Dev Articles取得失敗: {e}")
            dev_articles = []
        try:
            legal_news = f_legal.result(timeout=MAIN_FUTURE_TIMEOUT)
        except TimeoutError:
            print(f"[WARN] 法規制ニュース取得タイムアウト ({MAIN_FUTURE_TIMEOUT}s)")
            legal_news = []
        except Exception as e:
            print(f"[WARN] 法規制ニュース取得失敗: {e}")
            legal_news = []
        try:
            security_trend_entries = f_security.result(timeout=MAIN_FUTURE_TIMEOUT)
        except TimeoutError:
            print(f"[WARN] セキュリティ動向取得タイムアウト ({MAIN_FUTURE_TIMEOUT}s)")
            security_trend_entries = []
        except Exception as e:
            print(f"[WARN] セキュリティ動向取得失敗: {e}")
            security_trend_entries = []
        try:
            incident_entries = f_incident.result(timeout=MAIN_FUTURE_TIMEOUT)
        except TimeoutError:
            print(f"[WARN] インシデント取得タイムアウト ({MAIN_FUTURE_TIMEOUT}s)")
            incident_entries = []
        except Exception as e:
            print(f"[WARN] インシデント取得失敗: {e}")
            incident_entries = []
        try:
            vulnerability_entries = f_vuln.result(timeout=MAIN_FUTURE_TIMEOUT)
        except TimeoutError:
            print(f"[WARN] 脆弱性取得タイムアウト ({MAIN_FUTURE_TIMEOUT}s)")
            vulnerability_entries = []
        except Exception as e:
            print(f"[WARN] 脆弱性取得失敗: {e}")
            vulnerability_entries = []

    generated_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    config_result = analyze_config(risk_entries, dev_articles)
    today_dev_articles, yesterday_dev_articles, fetched_older_dev_articles = split_dev_articles_by_day(dev_articles)
    border_dt = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    print(f"リスク記事: {len(risk_entries)}件 / 開発記事: {len(dev_articles)}件 / 法規制: {len(legal_news)}件 / セキュリティ動向: {len(security_trend_entries)}件 / インシデント: {len(incident_entries)}件 / 脆弱性: {len(vulnerability_entries)}件")

    con = init_db()
    save_run_history(
        con,
        generated_at,
        risk_entries,
        dev_articles,
        config_result,
        legal_news,
        security_entries=security_trend_entries,
        incident_entries=incident_entries,
        vuln_entries=vulnerability_entries,
    )
    save_extra_news_items(con, "security", security_trend_entries)
    save_extra_news_items(con, "incident", incident_entries)
    save_extra_news_items(con, "vulnerability", vulnerability_entries)
    history = get_recent_history(con, limit=8)
    risk_today, risk_yesterday, fetched_risk_older = split_risk_entries_by_day(risk_entries)
    security_today, security_yesterday, fetched_security_older = split_news_items_by_day(security_trend_entries)
    incident_today, incident_yesterday, fetched_incident_older = split_news_items_by_day(incident_entries)
    vuln_today, vuln_yesterday, fetched_vuln_older = split_news_items_by_day(vulnerability_entries)
    older_risk_history = get_older_risk_history(con, border_dt, 200)
    older_security_history = get_older_extra_news(con, "security", border_dt, 200)
    older_incident_history = get_older_extra_news(con, "incident", border_dt, 200)
    older_vuln_history = get_older_extra_news(con, "vulnerability", border_dt, 200)
    def merge_and_sort_news_items(*groups, limit=200):
        merged = []
        seen = set()
        for group in groups:
            for item in group or []:
                key = (
                    item.get("title", ""),
                    item.get("link") or "",
                    item.get("published") or item.get("published_at") or "",
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        merged.sort(key=lambda a: parse_any_datetime(a.get("published") or a.get("published_at") or "") or datetime.min.replace(tzinfo=JST), reverse=True)
        return merged[:limit]

    risk_older = merge_and_sort_news_items(fetched_risk_older, older_risk_history, limit=200)
    security_older = merge_and_sort_news_items(fetched_security_older, older_security_history, limit=200)
    incident_older = merge_and_sort_news_items(fetched_incident_older, older_incident_history, limit=200)
    vuln_older = merge_and_sort_news_items(fetched_vuln_older, older_vuln_history, limit=200)
    older_dev_history = get_older_dev_history(con, border_dt, 200)
    older_dev_articles = fetched_older_dev_articles + older_dev_history
    deduped_older_dev_articles = []
    seen_older = set()
    for article in older_dev_articles:
        key = (
            article.get("title", ""),
            article.get("link") or article.get("path") or "",
            article.get("published") or article.get("published_at") or "",
        )
        if key in seen_older:
            continue
        seen_older.add(key)
        deduped_older_dev_articles.append(article)
    deduped_older_dev_articles = deduped_older_dev_articles[:200]
    cleanup_db(con)
    con.close()

    html_text = build_html(
        risk_entries,
        today_dev_articles,
        yesterday_dev_articles,
        deduped_older_dev_articles,
        generated_at,
        history,
        config_result,
        legal_news,
        fetched_dev_count=len(dev_articles),
        risk_today=risk_today,
        risk_yesterday=risk_yesterday,
        risk_older=risk_older,
        security_today=security_today,
        security_yesterday=security_yesterday,
        security_older=security_older,
        incident_today=incident_today,
        incident_yesterday=incident_yesterday,
        incident_older=incident_older,
        vuln_today=vuln_today,
        vuln_yesterday=vuln_yesterday,
        vuln_older=vuln_older,
        guideline_content=guideline_content,
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8-sig") as f:
        f.write(html_text)

    print("=" * 46)
    print(f"  {CONFIG.get('app_name', '')}")
    print("=" * 46)
    print(f"DB: {DB_FILE}")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Recommended: {config_result['name']}")
    print(f"\n⏱ 処理時間: {time.time() - start:.1f}秒")
    print("Done.")


if __name__ == "__main__":
    main()