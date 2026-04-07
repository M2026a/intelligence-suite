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
CATEGORY_ORDER = ["すべて", "最新", "観光スポット", "安全・入国情報", "航空・交通", "ホテル・宿泊", "グルメ", "旅行情報", "お知らせ"]


def escape_html(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


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
    # 既存DBへのマイグレーション（カラムが無ければ追加）
    for col_def in [
        "title_ja TEXT NOT NULL DEFAULT ''",
        "summary_ja TEXT NOT NULL DEFAULT ''",
    ]:
        col_name = col_def.split()[0]
        try:
            cur.execute(f"ALTER TABLE items ADD COLUMN {col_def}")
        except Exception:
            pass  # すでに存在する場合は無視
    conn.commit()
    conn.close()


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
        if country in text:
            return country
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
            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "published_at": parse_datetime(published_raw),
                "source_name": source["name"],
                "lang": source.get("lang", "ja"),
                "default_category": source.get("category", "お知らせ")
            })
    return items


def _translate_text(text: str) -> str:
    """テキストを日本語に翻訳する（失敗時は空文字）"""
    if not text or not _TRANSLATOR_AVAILABLE:
        return ""
    for attempt in range(TRANSLATE_RETRIES + 1):
        try:
            result = GoogleTranslator(source="auto", target="ja").translate(text[:4500])
            return result or ""
        except Exception:
            if attempt < TRANSLATE_RETRIES:
                time.sleep(1)
    return ""


def translate_pending_items() -> int:
    """lang='en' かつ title_ja が未翻訳のアイテムを翻訳してDBに保存する"""
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

    def _translate_row(row: tuple) -> tuple:
        rid, title, summary = row
        return rid, _translate_text(title), _translate_text(summary or "")

    results: list[tuple] = []
    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as executor:
        futs = {executor.submit(_translate_row, row): row[0] for row in rows}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                print(f"    [WARN] 翻訳エラー: {e}", flush=True)

    conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
    cur.executemany(
        "UPDATE items SET title_ja=?, summary_ja=? WHERE id=?",
        [(r[1], r[2], r[0]) for r in results]
    )
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
    print(f"    Global: refreshing feeds... ({total_sources} sources / {max_workers} workers, {len(existing_links)} items in DB)", flush=True)

    def _fetch(idx: int, source: dict):
        return idx, source.get("name", f"source{idx}"), fetch_rss_source(source)

    future_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for idx, source in enumerate(SOURCES, start=1):
            source_name = source.get("name", f"source{idx}")
            print(f"    Global: queue {idx}/{total_sources} - {source_name}", flush=True)
            future_map[executor.submit(_fetch, idx, source)] = (idx, source_name)

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
                    insert_batch.append((
                        row["title"], subtitle, row["summary"], row["link"],
                        row["source_name"], category, country, region,
                        row.get("lang", "ja"), ", ".join(tags), published_at, now_str, is_new,
                        "", ""  # title_ja, summary_ja（翻訳は後で実行）
                    ))
                if insert_batch:
                    cur.executemany(
                        "INSERT OR IGNORE INTO items "
                        "(title, subtitle, summary, link, source_name, category, country, region, "
                        "lang, tags, published_at, fetched_at, is_new, title_ja, summary_ja) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        insert_batch
                    )
                    inserted_count += len(insert_batch)
                    added = len(insert_batch)
                else:
                    added = 0
                print(f"    Global: done {completed}/{total_sources} - {source_name} (fetched {len(rows)}, new {len(new_rows)}, inserted {added})", flush=True)
            except Exception as exc:
                msg = f"{source_name}: {exc}"
                errors.append(msg)
                print(f"    [WARN] {completed}/{total_sources} {msg}", flush=True)

    conn.commit(); conn.close()
    print(f"    Global: refresh complete (fetched {fetched_count}, inserted {inserted_count}, warnings {len(errors)})", flush=True)

    # 英語記事を翻訳
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
        "sources": Counter(item["source_name"] for item in items)
    }


def top_badges(counter: Counter, limit: int = 6) -> str:
    if not counter: return "<div class='empty-small'>データなし</div>"
    return "".join(
        f"<div class='mini-stat'><span>{escape_html(name)}</span><strong>{count}</strong></div>"
        for name, count in counter.most_common(limit)
    )


def build_html(items: list[dict], summary: dict, fetched_count: int, inserted_count: int, errors: list[str], *, app_name: str, subtitle: str, nav_html: str) -> str:
    items_json = json.dumps(items, ensure_ascii=False)
    regions_json = json.dumps(REGIONS, ensure_ascii=False)
    category_tabs = [c for c in CATEGORY_ORDER if c == "すべて" or c == "最新" or summary["categories"].get(c)]
    tab_buttons = "".join(
        f"<button class='tab-btn{' active' if i == 0 else ''}' data-tab='{escape_html(cat)}'>{escape_html(cat)}</button>"
        for i, cat in enumerate(category_tabs)
    )
    info_cards = (
        f"<div class='hero-card'><span>総件数</span><strong>{summary['total']}</strong></div>"
        f"<div class='hero-card'><span>新着</span><strong>{summary['new_count']}</strong></div>"
        f"<div class='hero-card'><span>今回取得</span><strong>{inserted_count}</strong></div>"
        f"<div class='hero-card'><span>取得総数</span><strong>{fetched_count}</strong></div>"
    )
    error_html = (
        "<div class='panel warn'><h3>取得時の注意</h3><ul>"
        + "".join(f"<li>{escape_html(err)}</li>" for err in errors)
        + "</ul></div>"
    ) if errors else ""
    empty_notice = "" if items else (
        "<div class='panel'><h3>初回起動メモ</h3>"
        "<p>この画面は取得後に自動更新されます。ネット接続がない場合やRSS元の応答がない場合は件数が0になります。</p></div>"
    )

    return f"""<!DOCTYPE html><html lang='ja'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'><title>{escape_html(app_name)} | 海外旅行</title><style>
:root {{--bg:#0b1020; --panel:#141b32; --line:#2f3b63; --text:#edf2ff; --muted:#9fb0d7; --accent:#72a7ff; --accent2:#7ef0c2;}}
* {{ box-sizing:border-box; }} body {{ margin:0; font-family:"Segoe UI","Meiryo",sans-serif; background:linear-gradient(180deg,#0b1020,#12182d); color:var(--text); }} a {{ color:#9cc1ff; text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.wrap {{ max-width:1480px; width:min(1480px,calc(100% - 40px)); margin:0 auto; padding:14px 20px; }} .topbar {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; margin-bottom:16px; }} .title h1 {{ margin:0; font-size:22px; font-weight:800; display:flex; flex-wrap:wrap; align-items:baseline; gap:0; }} .title-suffix {{ color:var(--muted); font-size:14px; font-weight:500; }} .sub {{ margin-top:4px; color:var(--muted); font-size:12px; }} .nav {{ display:flex; gap:8px; flex-wrap:wrap; padding:8px 0 14px; margin-bottom:14px; border-bottom:1px solid var(--line); }} .nav .nav-link {{ display:inline-flex; gap:6px; align-items:center; padding:9px 13px; border-radius:14px; background:#101a2b; border:1px solid var(--line); color:var(--muted); font-size:13px; }} .nav .nav-link.active {{ color:#06111d; background:linear-gradient(135deg,var(--accent),var(--accent2)); border-color:transparent; }}
.hero-card,.panel {{ background:rgba(20,27,50,.95); border:1px solid var(--line); border-radius:16px; box-shadow:0 8px 24px rgba(0,0,0,.18); }} .stats-grid {{ display:grid; grid-template-columns:1fr; gap:10px; }} .hero-card {{ padding:14px 16px; }} .hero-card span {{ display:block; color:var(--muted); font-size:13px; margin-bottom:8px; }} .hero-card strong {{ font-size:24px; }} .layout {{ display:grid; grid-template-columns:1fr 320px; gap:16px; align-items:start; }} .panel {{ padding:16px; }} .controls {{ display:grid; grid-template-columns:1.4fr 1fr 1fr 1fr auto; gap:10px; margin-bottom:14px; }} input,select,button {{ border-radius:12px; border:1px solid var(--line); background:#0f1730; color:var(--text); padding:12px 14px; font-size:14px; }} button {{ cursor:pointer; }} .tabbar {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; }} .tab-btn {{ background:#0f1730; }} .tab-btn.active {{ background:linear-gradient(135deg,var(--accent),#5c85ff); color:white; border-color:#7caeff; }} .tab-summary {{ color:var(--muted); font-size:13px; margin-bottom:10px; }} .list {{ display:flex; flex-direction:column; gap:10px; }} .item {{ border:1px solid var(--line); border-radius:14px; background:rgba(15,23,48,.85); padding:14px; }} .item-title {{ font-size:17px; font-weight:700; margin-bottom:8px; line-height:1.4; }} .item-meta {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px; }} .badge {{ display:inline-flex; align-items:center; gap:6px; border-radius:999px; font-size:12px; padding:5px 9px; border:1px solid var(--line); color:var(--text); background:#132042; }} .badge.new {{ background:rgba(255,112,150,.18); border-color:rgba(255,112,150,.45); }} .badge.category {{ background:rgba(114,167,255,.18); border-color:rgba(114,167,255,.45); }} .badge.country {{ background:rgba(126,240,194,.15); border-color:rgba(126,240,194,.35); }} .badge.lang-en {{ background:rgba(255,200,80,.12); border-color:rgba(255,200,80,.35); }} .item-summary {{ color:#d9e3ff; font-size:14px; line-height:1.65; margin-bottom:10px; }} .item-sub {{ color:var(--muted); font-size:13px; }} .side-grid {{ display:grid; gap:16px; align-content:start; align-self:start; }} .side-panel {{ display:flex; flex-direction:column; }} .panel h2,.panel h3 {{ margin:0 0 12px 0; }} .mini-grid {{ display:grid; gap:8px; }} .mini-stat {{ display:flex; align-items:center; justify-content:space-between; gap:10px; padding:10px 12px; border:1px solid var(--line); border-radius:12px; background:#0f1730; }} .mini-stat span {{ color:#dce7ff; font-size:13px; }} .mini-stat strong {{ font-size:18px; }} .footer-note,.empty-small {{ color:var(--muted); font-size:12px; }} .warn {{ border-color:rgba(255,189,102,.5); }} .warn ul {{ padding-left:18px; margin:0; color:#ffe5b3; }}
.chip-row {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; align-items:center; }} .chip-label {{ color:var(--muted); font-size:12px; white-space:nowrap; margin-right:4px; }} .chip-btn {{ background:#192035; border:1px solid var(--line); color:var(--text); border-radius:999px; padding:5px 12px; font-size:12px; cursor:pointer; transition:.15s; }} .chip-btn:hover {{ background:#253060; border-color:var(--accent); }}
.tr-en {{ display:none; }} body.en-mode .tr-ja {{ display:none !important; }} body.en-mode .tr-en {{ display:inline !important; }}
.en{{display:none}} body.en-mode .ja{{display:none !important}} body.en-mode .en{{display:initial !important}} @media (max-width:1100px) {{ .layout {{ grid-template-columns:1fr; }} }} @media (max-width:900px) {{ .controls {{ grid-template-columns:1fr 1fr; }} }} @media (max-width:640px) {{ .controls {{ grid-template-columns:1fr; }} .title h1 {{ font-size:22px; }} .title-suffix{{font-size:13px}} }}
</style></head><body><div class='wrap'><div class='topbar'><div class='title'><h1>{escape_html(app_name)}<span class='title-suffix'> ｜ {escape_html(subtitle)}</span></h1><div class='sub'>更新日時：{escape_html(summary['latest_time'])}</div></div><div class='actions'><button class='toggle' type='button' onclick='toggleLang()'>JP / EN</button></div></div><nav class='nav'>{nav_html}</nav><div class='layout'><div><div class='panel'><div class='tabbar'>{tab_buttons}</div><div class='chip-row'><span class='chip-label ja'>キーワード候補:</span><span class='chip-label en'>Quick picks:</span><button class='chip-btn' onclick="setSearch('ビーチ')">🏖️ ビーチ</button><button class='chip-btn' onclick="setSearch('世界遺産')">🏛️ 世界遺産</button><button class='chip-btn' onclick="setSearch('グルメ')">🍜 グルメ</button><button class='chip-btn' onclick="setSearch('安全情報')">⚠️ 安全情報</button><button class='chip-btn' onclick="setSearch('ハネムーン')">💍 ハネムーン</button><button class='chip-btn' onclick="setSearch('バックパック')">🎒 バックパック</button><button class='chip-btn' onclick="setSearch('トレッキング')">🥾 トレッキング</button><button class='chip-btn' onclick="setSearch('リゾート')">🌴 リゾート</button></div><div class='controls'><input id='searchBox' type='text' placeholder='検索（タイトル / 要約 / 取得元）'><select id='regionFilter'><option value=''>地域: すべて</option></select><select id='countryFilter'><option value=''>国: すべて</option></select><select id='sourceFilter'><option value=''>取得元: すべて</option></select><button id='resetBtn'>リセット</button></div><div id='tabSummary' class='tab-summary'></div><div id='list' class='list'></div><div class='footer-note' style='margin-top:12px;'>カテゴリ / 地域 / 国 / 取得元で絞り込めます。英語記事には EN バッジが付きます。JP/EN ボタンで記事の表示言語を切り替えられます。</div></div>{error_html}{empty_notice}</div><div class='side-grid'><section class='panel side-panel'><div class='stats-grid'>{info_cards}</div></section><section class='panel side-panel'><h2>カテゴリ件数</h2><div class='mini-grid'>{top_badges(summary['categories'])}</div></section><section class='panel side-panel'><h2>地域件数</h2><div class='mini-grid'>{top_badges(summary['regions'])}</div></section><section class='panel side-panel'><h2>国・エリア件数</h2><div class='mini-grid'>{top_badges(summary['countries'])}</div></section><section class='panel side-panel'><h2>取得元件数</h2><div class='mini-grid'>{top_badges(summary['sources'])}</div></section></div></div></div><script>
const items = {items_json};
const REGION_ORDER = {regions_json};
const tabs = document.querySelectorAll('.tab-btn');
const searchBox = document.getElementById('searchBox');
const regionFilter = document.getElementById('regionFilter');
const countryFilter = document.getElementById('countryFilter');
const sourceFilter = document.getElementById('sourceFilter');
const resetBtn = document.getElementById('resetBtn');
const list = document.getElementById('list');
const tabSummary = document.getElementById('tabSummary');
let activeTab = 'すべて';
function uniq(values) {{ return [...new Set(values.filter(Boolean))]; }}
function fillSelect(select, values) {{ uniq(values).sort((a,b)=>a.localeCompare(b,'ja')).forEach(v => {{ const o=document.createElement('option'); o.value=v; o.textContent=v; select.appendChild(o); }}); }}
function fillRegionSelect() {{ const existing = new Set(items.map(x=>x.region).filter(Boolean)); REGION_ORDER.forEach(region => {{ if (!existing.has(region)) return; const o=document.createElement('option'); o.value=region; o.textContent=region; regionFilter.appendChild(o); }}); }}
fillRegionSelect();
fillSelect(countryFilter, items.map(x=>x.country));
fillSelect(sourceFilter, items.map(x=>x.source_name));
function matchesTab(item) {{ if(activeTab==='すべて') return true; if(activeTab==='最新') return !!item.is_new; return item.category===activeTab; }}
function matchesFilters(item) {{ const q=(searchBox.value||'').trim().toLowerCase(); if(q) {{ const bag=`${{item.title}} ${{item.title_ja}} ${{item.summary}} ${{item.source_name}}`.toLowerCase(); if(!bag.includes(q)) return false; }} if(regionFilter.value && item.region!==regionFilter.value) return false; if(countryFilter.value && item.country!==countryFilter.value) return false; if(sourceFilter.value && item.source_name!==sourceFilter.value) return false; return true; }}
function render() {{
  const filtered=items.filter(item=>matchesTab(item)&&matchesFilters(item));
  tabSummary.textContent=`${{filtered.length}}件表示 / 総件数 ${{items.length}}件`;
  if(!filtered.length) {{ list.innerHTML="<div class='item'><div class='item-title'>該当データがありません</div><div class='item-sub'>条件をゆるめて再度お試しください。</div></div>"; return; }}
  list.innerHTML=filtered.map(item=>{{
    const tags=(item.tags||'').split(',').map(v=>v.trim()).filter(Boolean).slice(0,4);
    const isEnWithTr = item.lang==='en' && !!item.title_ja;
    const titleHtml = isEnWithTr
      ? `<span class='tr-ja'>${{item.title_ja}}</span><span class='tr-en'>${{item.title}}</span>`
      : item.title;
    const summaryText = item.summary || '要約なし';
    const summaryHtml = (isEnWithTr && item.summary_ja)
      ? `<span class='tr-ja'>${{item.summary_ja}}</span><span class='tr-en'>${{summaryText}}</span>`
      : summaryText;
    const langBadge = item.lang==='en' ? "<span class='badge lang-en'>EN</span>" : '';
    const badges=[
      item.is_new ? "<span class='badge new'>NEW</span>" : '',
      `<span class='badge category'>${{item.category}}</span>`,
      `<span class='badge country'>${{item.country}}</span>`,
      langBadge,
      ...tags.map(tag=>`<span class='badge'>${{tag}}</span>`)
    ].join('');
    const published=item.published_at||item.fetched_at||'';
    return `<article class='item'><div class='item-title'><a href="${{item.link}}" target="_blank" rel="noopener">${{titleHtml}}</a></div><div class='item-meta'>${{badges}}</div><div class='item-summary'>${{summaryHtml}}</div><div class='item-sub'>${{item.subtitle}} ｜ ${{item.source_name}} ｜ ${{published}}</div></article>`;
  }}).join('');
}}
tabs.forEach(btn=>btn.addEventListener('click',()=>{{ tabs.forEach(x=>x.classList.remove('active')); btn.classList.add('active'); activeTab=btn.dataset.tab; render(); }}));
[searchBox,regionFilter,countryFilter,sourceFilter].forEach(el=>{{ el.addEventListener('input',render); el.addEventListener('change',render); }});
resetBtn.addEventListener('click',()=>{{ searchBox.value=''; regionFilter.value=''; countryFilter.value=''; sourceFilter.value=''; activeTab='すべて'; tabs.forEach((x,idx)=>x.classList.toggle('active',idx===0)); render(); }});
function setSearch(v) {{ searchBox.value=v; render(); }}
function toggleLang() {{
  document.body.classList.toggle('en-mode');
  localStorage.setItem('tsp-lang', document.body.classList.contains('en-mode') ? 'en' : 'ja');
}}
if(localStorage.getItem('tsp-lang')==='en') document.body.classList.add('en-mode');
render();
</script></body></html>"""


def build_global_page(*, app_name: str, subtitle: str, updated_at: str, nav_html: str) -> str:
    try:
        fetched_count, inserted_count, errors = refresh_global_data()
    except Exception as exc:
        fetched_count, inserted_count, errors = 0, 0, [str(exc)]
    items = read_items()
    summary = summarize(items)
    return build_html(items, summary, fetched_count, inserted_count, errors, app_name=app_name, subtitle=subtitle, nav_html=nav_html)
