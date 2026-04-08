from __future__ import annotations

import json
import time
import requests
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import imp_base as core

ROOT       = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LOG_DIR    = ROOT / "logs"
SHARED_DIR = ROOT / "shared"
JST        = ZoneInfo("Asia/Tokyo")
CONFIG = json.loads((SHARED_DIR / "config.json").read_text(encoding="utf-8"))

APP_TITLE = CONFIG.get("app_name", "IdeaTrendEngine")
APP_SUBTITLE = CONFIG.get("subtitle", "")
HEADER_TITLE = APP_TITLE if not APP_SUBTITLE else f"{APP_TITLE}｜{APP_SUBTITLE}"

# ─────────────────────────────────────────
#  IM設定
# ─────────────────────────────────────────
IM_APP_NAME = CONFIG["im"]["app_name"]
IM_DB_PATH  = OUTPUT_DIR / CONFIG["im"]["db_name"]
IM_SOURCES       = core.SOURCES
IM_SEED_IDEAS    = core.SEED_IDEAS
IM_SOURCE_META   = core.SOURCE_META
IM_CATEGORY_RULES = core.CATEGORY_RULES

# ─────────────────────────────────────────
#  IT設定（sources_it.json / themes_it.json から読み込み）
# ─────────────────────────────────────────
IT_APP_NAME = CONFIG["it"]["app_name"]
IT_DB_PATH  = OUTPUT_DIR / CONFIG["it"]["db_name"]

HN_TOPSTORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL       = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
HN_MAX_ITEMS      = 20

def _fetch_hn_items() -> list[dict]:
    try:
        ids = requests.get(HN_TOPSTORIES_URL, timeout=8).json()[:HN_MAX_ITEMS]
    except Exception:
        return []
    items = []
    for item_id in ids:
        try:
            data = requests.get(HN_ITEM_URL.format(item_id=item_id), timeout=8).json()
            title = (data.get("title") or "").strip()
            url   = data.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
            if title:
                items.append({
                    "title": title, "url": url,
                    "source_name": "Hacker News", "source_type": "hackernews",
                    "raw_text": title,
                })
        except Exception:
            continue
    return items

def _load_it_sources() -> list[dict]:
    p    = SHARED_DIR / "sources_it.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    rows = []
    for page in data.get("pages", []):
        for item in page.get("items", []):
            source_type = item["name"].lower().replace(" ", "_")
            rows.append({"name": item["name"], "kind": "feed",
                         "source_type": source_type, "url": item["url"]})
    return rows

def _load_it_source_meta() -> dict:
    p    = SHARED_DIR / "sources_it.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    meta = {"other": {"region": "不明", "label": "不明"},
            "hackernews": {"region": "海外", "label": "海外"}}
    for page in data.get("pages", []):
        for item in page.get("items", []):
            source_type = item["name"].lower().replace(" ", "_")
            region = item.get("region", "不明")
            meta[source_type] = {"region": region, "label": region}
    return meta

def _load_it_category_rules() -> list[tuple]:
    p    = SHARED_DIR / "themes_it.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    rules = []
    for group in data.get("groups", []):
        cat = group.get("category", "その他")
        kws = [cat.lower(), cat]
        for sub in group.get("subcategories", []):
            kws.extend([sub.lower(), sub])
        rules.append((cat, kws))
    if not any(cat == "その他" for cat, _ in rules):
        rules.append(("その他", ["other", "misc", "general"]))
    return rules

IT_SOURCES        = _load_it_sources()
IT_SEED_IDEAS     = []
IT_SOURCE_META    = _load_it_source_meta()
IT_CATEGORY_RULES = _load_it_category_rules()

# ─────────────────────────────────────────
#  モード管理
# ─────────────────────────────────────────
CURRENT_SUFFIX   = "im"
CURRENT_APP_NAME = IM_APP_NAME
CURRENT_UPDATED_AT = ""

def current_stamp() -> str:
    return CURRENT_UPDATED_AT or datetime.now(JST).strftime("%Y-%m-%d %H:%M")

CSS_IM = core.CSS
CSS_IT = (
    core.CSS
    .replace("--ac:#a78bfa;", "--ac:#22d3ee;")
    .replace("--ac2:#34d399;", "--ac2:#38bdf8;")
)

def current_css() -> str:
    return CSS_IM if CURRENT_SUFFIX == "im" else CSS_IT

def output_name(name: str) -> str:
    p = Path(name)
    return f"{p.stem}_{CURRENT_SUFFIX}{p.suffix}"

def make_nav_items() -> list[tuple]:
    return [
        ("index_im.html",    "Latest"),
        ("pickup_im.html",   "Pickup"),
        ("focus_im.html",    "Focus"),
        ("themes_im.html",   "Themes"),
        ("sources_im.html",  "Sources"),
        ("analysis_im.html", "Analysis"),
        ("index_it.html",    "Latest"),
        ("pickup_it.html",   "Pickup"),
        ("focus_it.html",    "Focus"),
        ("themes_it.html",   "Themes"),
        ("sources_it.html",  "Sources"),
        ("analysis_it.html", "Analysis"),
    ]

def page_wrap(title: str, active_page: str, body: str) -> str:
    active = output_name(active_page)
    stem = Path(active_page).stem
    current_is_it = CURRENT_SUFFIX == "it"

    current_nav = []
    for href, label in core.NAV_ITEMS:
        if href.endswith(f"_{CURRENT_SUFFIX}.html"):
            classes = ["suite-btn", "itnav" if current_is_it else "imnav"]
            if href == active:
                classes.append("active")
            current_nav.append(f'<a href="{href}" class="{" ".join(classes)}">{label}</a>')

    category_im_classes = ["category-btn", "imnav"]
    category_it_classes = ["category-btn", "itnav"]
    if current_is_it:
        category_it_classes.append("active")
    else:
        category_im_classes.append("active")

    category_im = f'<a href="{stem}_im.html" class="{" ".join(category_im_classes)}">事例 💡</a>'
    category_it = f'<a href="{stem}_it.html" class="{" ".join(category_it_classes)}">TIPS 📘</a>'

    is_analysis = "analysis" in active
    lang_btn = "" if is_analysis else (
        '<div class="lang-switch">'
        '<button id="btn-lang-en" class="lang-btn lang-active" onclick="setPageLang(\'en\')">🌐 EN</button>'
        '<button id="btn-lang-ja" class="lang-btn" onclick="setPageLang(\'ja\')">🇯🇵 JA</button>'
        '</div>'
    )
    return f"""<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{core.esc(title)} - {core.esc(CURRENT_APP_NAME)}</title>
<style>{current_css()}
.topbar{{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:14px 24px 12px 16px;flex-wrap:wrap}}
.header-left{{display:flex;flex-direction:column;justify-content:center;min-width:0;flex:1 1 320px;padding-top:0}}
.header-title{{margin:0;font-size:24px;font-weight:800;line-height:1.18;white-space:normal;color:#f8fafc;letter-spacing:.01em;overflow-wrap:anywhere}}
.updated{{margin-top:3px;font-size:12px;font-weight:600;color:#9ba9bc;line-height:1.2}}
.header-right{{display:flex;align-items:flex-start;gap:14px;flex:1 1 720px;min-width:0;justify-content:flex-end;flex-wrap:wrap}}
.lang-switch{{display:flex;gap:6px;align-self:flex-start;flex:0 0 auto}}
.nav-shell{{display:flex;flex-direction:column;gap:8px;align-items:flex-start;padding:0;border:none;box-shadow:none;background:transparent;min-width:0;max-width:100%;flex:1 1 640px}}
.category-tabs,.nav-links{{display:flex;align-items:center;gap:6px;min-width:0;max-width:100%}}
.category-tabs{{flex:0 0 auto;flex-wrap:nowrap}}
.nav-links{{flex:1 1 100%;width:100%;flex-wrap:nowrap;overflow-x:auto;overflow-y:hidden;padding-bottom:2px;-webkit-overflow-scrolling:touch;scrollbar-width:none}}
.nav-links::-webkit-scrollbar{{display:none}}
.category-btn,.category-btn:hover,.category-btn:focus,.category-btn:visited,.suite-btn,.suite-btn:hover,.suite-btn:focus,.suite-btn:visited{{display:inline-flex;align-items:center;justify-content:center;min-height:28px;padding:4px 8px;border-radius:7px;text-decoration:none!important;font-size:12px;font-weight:700;line-height:1;color:#bfc7d5;background:transparent;border:1px solid transparent;box-shadow:none;transition:background .16s ease,color .16s ease,box-shadow .16s ease,transform .16s ease;vertical-align:middle;position:relative;top:0;flex:0 0 auto;white-space:nowrap}}
.category-btn{{padding:6px 10px;font-size:13px;border-radius:9px}}
.category-btn.imnav:hover,.suite-btn.imnav:hover{{color:#ffffff;background:rgba(196,161,255,.12);box-shadow:0 0 0 1px rgba(196,161,255,.24) inset}}
.category-btn.itnav:hover,.suite-btn.itnav:hover{{color:#ffffff;background:rgba(47,220,255,.12);box-shadow:0 0 0 1px rgba(47,220,255,.24) inset}}
.category-btn.imnav.active,.suite-btn.imnav.active{{background:#8f63db;color:#ffffff;box-shadow:0 0 0 1px rgba(255,255,255,.12) inset, 0 0 16px rgba(143,99,219,.42)}}
.category-btn.itnav.active,.suite-btn.itnav.active{{background:#0fb2d6;color:#ffffff;box-shadow:0 0 0 1px rgba(255,255,255,.12) inset, 0 0 16px rgba(15,178,214,.42)}}
.category-btn.imnav:not(.active),.category-btn.itnav:not(.active),.suite-btn.imnav:not(.active),.suite-btn.itnav:not(.active){{color:#c2cad7}}
.page{{min-height:calc(100vh - 86px);display:flex;flex-direction:column}}
.page-footer{{margin-top:auto;padding:16px 20px 18px;color:#9ba9bc;font-size:12px;text-align:left;opacity:.8}}
@media (max-width: 1320px){{
  .header-right{{justify-content:flex-start;flex-basis:100%;align-items:flex-start}}
  .nav-shell{{flex-basis:100%}}
}}
@media (max-width: 980px){{
  .topbar{{padding:14px 16px 12px}}
  .header-left,.header-right{{flex-basis:100%}}
  .header-title{{font-size:22px}}
  .nav-shell{{width:100%}}
  .category-tabs{{width:100%;justify-content:space-between}}
}}
@media (max-width: 768px){{
  .topbar{{gap:14px}}
  .header-title{{font-size:20px}}
  .header-right{{gap:10px;flex-direction:column;align-items:stretch}}
  .nav-shell{{width:100%;gap:10px}}
  .category-tabs{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;width:100%}}
  .nav-links{{gap:6px}}
  .suite-btn{{padding:4px 8px;font-size:12px;border-radius:6px}}
  .category-btn{{width:100%;justify-content:center}}
  .nav-links{{width:100%}}
  .lang-switch{{align-self:flex-start}}
}}
</style>
<script>
function setPageLang(lang){{
  document.querySelectorAll('.tl-en').forEach(function(e){{e.classList.toggle('hidden',lang==='ja');}});
  document.querySelectorAll('.tl-ja').forEach(function(e){{e.classList.toggle('hidden',lang==='en');}});
  document.querySelectorAll('.lang-btn').forEach(function(b){{b.classList.remove('lang-active');}});
  var a=document.getElementById('btn-lang-'+lang);
  if(a) a.classList.add('lang-active');
}}
</script>
</head><body>
<div class="topbar">
  <div class="header-left">
    <div class="header-title">{core.esc(HEADER_TITLE)}</div>
    <div class="updated">更新日時：{core.esc(current_stamp())}</div>
  </div>
  <div class="header-right">
    <div class="nav-shell">
      <div class="category-tabs">{category_im}{category_it}</div>
      <div class="nav-links">{''.join(current_nav)}</div>
    </div>{lang_btn}
  </div>
</div>
<div class="page">{body}<div class="page-footer">{core.esc(APP_TITLE)} — Generated at {core.esc(current_stamp())}</div></div>
</body></html>"""

# ─────────────────────────────────────────
#  ページ生成（h1なし・説明文あり）
# ─────────────────────────────────────────
def gen_index(conn) -> str:
    ideas = core.query_ideas(conn, "collected_at DESC")
    stats = core.get_stats(conn)
    rows  = "".join(core.idea_row(i) for i in ideas) or '<tr><td colspan="7" class="empty">データなし</td></tr>'
    cats      = [""] + [r["category"] for r in stats["categories"]]
    diffs     = ["", "低", "中", "高"]
    src_types = [""] + [s["source_type"] for s in core.SOURCES]
    regions   = ["", "日本", "海外", "不明"]
    cat_opts    = "".join(f'<option value="{core.esc(c)}">{core.esc(c) or "全カテゴリ"}</option>' for c in cats)
    diff_opts   = "".join(f'<option value="{core.esc(d)}">{core.esc(d) or "全難易度"}</option>' for d in diffs)
    src_opts    = "".join(f'<option value="{core.esc(s)}">{core.esc(s) or "全ソース"}</option>' for s in src_types)
    region_opts = "".join(f'<option value="{core.esc(r)}">{core.esc(r) or "全地域"}</option>' for r in regions)
    sub = "収集した改善事例・TIPSを新着順で確認できます" if CURRENT_SUFFIX == "im" else "収集した技術情報を新着順で確認できます"
    body = f"""
<div class="sub">{sub}</div>
<div class="stats">
  <div class="stat"><div class="lbl">総件数</div><div class="val">{stats["total"]}</div></div>
  <div class="stat"><div class="lbl">平均スコア</div><div class="val">{stats["avg_score"]}</div></div>
  <div class="stat"><div class="lbl">お気に入り</div><div class="val">{stats["favorites"]}</div></div>
  <div class="stat"><div class="lbl">カテゴリ数</div><div class="val">{len(stats["categories"])}</div></div>
</div>
<div class="filter-row">
  <input id="q" class="search-bar" placeholder="キーワード検索..." oninput="filterTable()">
  <select id="cat" onchange="filterTable()">{cat_opts}</select>
  <select id="diff" onchange="filterTable()">{diff_opts}</select>
  <select id="src" onchange="filterTable()">{src_opts}</select>
  <select id="region" onchange="filterTable()">{region_opts}</select>
</div>
<div class="table-wrap"><table>
<thead><tr><th>タイトル</th><th>カテゴリ</th><th>難易度</th><th>スコア</th><th>インパクト</th><th>ソース</th><th>収集日</th></tr></thead>
<tbody id="ideaBody">{rows}</tbody>
</table></div>
<div id="emptyMsg" class="empty hidden">条件に一致するネタがありません。</div>
{core.FILTER_SCRIPT}"""
    return page_wrap("Latest", "index.html", body)

def set_mode(mode: str) -> None:
    global CURRENT_SUFFIX, CURRENT_APP_NAME, CURRENT_UPDATED_AT
    if mode == "im":
        CURRENT_SUFFIX   = "im"
        CURRENT_APP_NAME = IM_APP_NAME
        core.APP_NAME    = IM_APP_NAME
        core.DB_PATH     = IM_DB_PATH
        core.SOURCES     = IM_SOURCES
        core.SEED_IDEAS  = IM_SEED_IDEAS
        core.SOURCE_META = IM_SOURCE_META
        core.CATEGORY_RULES = IM_CATEGORY_RULES
    elif mode == "it":
        CURRENT_SUFFIX   = "it"
        CURRENT_APP_NAME = IT_APP_NAME
        core.APP_NAME    = IT_APP_NAME
        core.DB_PATH     = IT_DB_PATH
        core.SOURCES     = IT_SOURCES
        core.SEED_IDEAS  = IT_SEED_IDEAS
        core.SOURCE_META = IT_SOURCE_META
        core.CATEGORY_RULES = IT_CATEGORY_RULES
    else:
        raise ValueError(mode)
    core.NAV_ITEMS  = make_nav_items()
    core.CSS        = current_css()
    core.page_wrap  = page_wrap
    core.gen_index  = gen_index

def run_mode(mode: str) -> None:
    global CURRENT_UPDATED_AT
    set_mode(mode)
    conn = core.open_db()
    try:
        core.run_collection(conn)
        cache = json.loads(core.TRANSLATE_CACHE_FILE.read_text(encoding="utf-8")) if core.TRANSLATE_CACHE_FILE.exists() else {}
        # HN（ITモードのみ）
        if mode == "it":
            print("  [HN] Hacker News 取得中...")
            hn_items = _fetch_hn_items()
            if hn_items:
                f, i, u = core.enrich_and_store(conn, hn_items)
                print(f"  ✓ Hacker News {f}件")
        core.translate_items(conn, cache)
        core.refresh_all_categories(conn)
        print(f"[{mode}] HTML生成中...")
        latest = core.get_stats(conn)["latest"]
        CURRENT_UPDATED_AT = (
            core.format_dt(latest["started_at"] if latest else None)
            or datetime.now(JST).strftime("%Y-%m-%d %H:%M")
        )
        pages = {
            output_name("index.html"):    core.gen_index(conn),
            output_name("pickup.html"):   core.gen_pickup(conn),
            output_name("focus.html"):    core.gen_focus(conn),
            output_name("themes.html"):   core.gen_themes(conn),
            output_name("sources.html"):  core.gen_sources(conn),
            output_name("analysis.html"): core.gen_analysis(conn),
        }
        for filename, html in pages.items():
            path = OUTPUT_DIR / filename
            path.write_text(html, encoding="utf-8")
            print(f"  ✓ {filename}")
    finally:
        conn.close()

def main() -> None:
    start = time.time()
    print("=" * 55)
    print(f"  {APP_TITLE}")
    print("=" * 55)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    print("\n[Part 1] 改善ネタエンジン")
    run_mode("im")

    print("\n[Part 2] ITトレンド構造ダッシュボード")
    run_mode("it")

    print(f"\n⏱ 処理時間: {time.time() - start:.1f}秒")
    print()
    print("[DONE] 生成完了")
    print(f"  im: {OUTPUT_DIR / 'index_im.html'}")
    print(f"  it: {OUTPUT_DIR / 'index_it.html'}")

if __name__ == "__main__":
    main()
