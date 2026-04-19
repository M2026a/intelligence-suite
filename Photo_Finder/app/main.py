from __future__ import annotations

import json
import time
import html as htmllib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 PhotoFinder"

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
SHARED_DIR = ROOT / "shared"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

try:
    JST = ZoneInfo("Asia/Tokyo")
except Exception:
    JST = timezone(timedelta(hours=9))
UPDATED_AT = datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_json(SHARED_DIR / "config.json")
CATEGORIES_DATA = load_json(SHARED_DIR / "categories.json")

APP_NAME = CONFIG["app_name"]
APP_ICON = CONFIG["app_icon"]
APP_SUB = CONFIG["subtitle"]

REGION_PREFECTURES = {
    "北海道": ["北海道"],
    "東北": ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    "関東": ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"],
    "中部": ["新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県"],
    "関西": ["滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    "中国": ["鳥取県", "島根県", "岡山県", "広島県", "山口県"],
    "四国": ["徳島県", "香川県", "愛媛県", "高知県"],
    "九州・沖縄": ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"],
}

PREFECTURE_AREAS = {
    "北海道": ["札幌", "すすきの", "函館", "旭川", "小樽"],
    "青森県": ["青森", "弘前", "八戸"],
    "岩手県": ["盛岡", "一関", "北上"],
    "宮城県": ["仙台", "国分町", "石巻"],
    "秋田県": ["秋田", "大館", "横手"],
    "山形県": ["山形", "米沢", "鶴岡"],
    "福島県": ["福島", "郡山", "いわき", "会津若松"],
    "茨城県": ["水戸", "つくば", "土浦"],
    "栃木県": ["宇都宮", "小山", "那須塩原"],
    "群馬県": ["高崎", "前橋", "伊勢崎"],
    "埼玉県": ["大宮", "浦和", "川越", "川口", "所沢"],
    "千葉県": ["千葉", "船橋", "柏", "成田", "木更津"],
    "東京都": ["新宿", "渋谷", "池袋", "上野", "銀座", "東京駅", "吉祥寺", "八王子"],
    "神奈川県": ["横浜", "みなとみらい", "川崎", "藤沢", "鎌倉"],
    "新潟県": ["新潟", "長岡", "上越"],
    "富山県": ["富山", "高岡"],
    "石川県": ["金沢", "小松"],
    "福井県": ["福井", "敦賀"],
    "山梨県": ["甲府", "河口湖"],
    "長野県": ["長野", "松本", "軽井沢"],
    "岐阜県": ["岐阜", "大垣", "高山"],
    "静岡県": ["静岡", "浜松", "熱海", "沼津"],
    "愛知県": ["名古屋", "栄", "名古屋駅", "金山", "豊橋", "岡崎", "豊田市"],
    "三重県": ["津", "四日市", "伊勢"],
    "滋賀県": ["大津", "草津", "彦根"],
    "京都府": ["京都", "河原町", "烏丸", "祇園", "京都駅"],
    "大阪府": ["梅田", "難波", "心斎橋", "天王寺", "新大阪", "京橋"],
    "兵庫県": ["神戸", "三宮", "姫路", "西宮"],
    "奈良県": ["奈良", "橿原"],
    "和歌山県": ["和歌山", "白浜"],
    "鳥取県": ["鳥取", "米子"],
    "島根県": ["松江", "出雲"],
    "岡山県": ["岡山", "倉敷"],
    "広島県": ["広島", "福山", "宮島口"],
    "山口県": ["山口", "下関", "宇部"],
    "徳島県": ["徳島", "鳴門"],
    "香川県": ["高松", "丸亀"],
    "愛媛県": ["松山", "今治"],
    "高知県": ["高知", "四万十"],
    "福岡県": ["博多", "天神", "中洲", "小倉"],
    "佐賀県": ["佐賀", "唐津"],
    "長崎県": ["長崎", "佐世保"],
    "熊本県": ["熊本", "阿蘇"],
    "大分県": ["大分", "別府", "湯布院"],
    "宮崎県": ["宮崎", "都城"],
    "鹿児島県": ["鹿児島", "天文館", "霧島"],
    "沖縄県": ["那覇", "国際通り", "北谷", "石垣"],
}


def e(text: str) -> str:
    return htmllib.escape(str(text))


def page_index() -> str:
    cats = CATEGORIES_DATA["categories"]
    first_cat = cats[0]

    cats_js = json.dumps(cats, ensure_ascii=False)
    region_js = json.dumps(REGION_PREFECTURES, ensure_ascii=False)
    area_js = json.dumps(PREFECTURE_AREAS, ensure_ascii=False)

    tab_btns = "".join(
        '<button class="tab-btn{active}" data-cat="{cid}" onclick="switchCat(\'{cid}\')">{icon} {label}</button>'.format(
            active=" active" if i == 0 else "",
            cid=e(c["id"]),
            icon=c["icon"],
            label=e(c["label"]),
        )
        for i, c in enumerate(cats)
    )

    region_opts = '<option value=""></option>' + "".join(
        '<option value="{r}">{r}</option>'.format(r=e(r)) for r in REGION_PREFECTURES
    )

    subcat_opts = '<option value=""></option>' + "".join(
        '<option value="{s}">{s}</option>'.format(s=e(s))
        for s in first_cat["subcategories"]
    )

    initial_src_btns = "".join(
        '<a class="btn" href="{href}" target="_blank" rel="noopener" data-template="{tpl}">{name}</a>'.format(
            href=e(src["url_template"].replace("{query}", "")),
            tpl=e(src["url_template"]),
            name=e(src["name"]),
        )
        for src in first_cat["sources"]
    )

    initial_shortcuts = "".join(
        '<button class="btn alt" onclick="setSubcat(\'{s}\')">{s}</button>'.format(s=e(s))
        for s in first_cat["subcategories"]
    )

    has_scenes = bool(first_cat.get("scenes"))
    initial_scenes = ""
    if has_scenes:
        initial_scenes = "".join(
            '<button class="btn alt" onclick="setScene(\'{s}\')">{s}</button>'.format(s=e(s))
            for s in first_cat["scenes"]
        )
    scenes_display = "block" if has_scenes else "none"

    css = (
        ":root{"
        "--bg:#0b1020;--panel:#121934;--line:#273052;"
        "--text:#eef3ff;--muted:#aeb8d9;--accent:#6ea8ff;"
        "}"
        "*{box-sizing:border-box;margin:0;padding:0}"
        "body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Meiryo,sans-serif;font-size:14px;overflow-x:hidden}"
        "a{color:#cfe0ff;text-decoration:none}"
        "a:hover{text-decoration:underline}"
        "header{position:sticky;top:0;z-index:50;background:rgba(13,16,22,.96);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}"
        ".wrap{max-width:1480px;width:100%;margin:0 auto;padding:18px 20px;overflow-x:hidden;box-sizing:border-box}"
        ".top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}"
        ".title h1{margin:0;font-size:22px;font-weight:800;color:var(--text)}"
        ".title h1 span{font-size:16px;font-weight:400;color:var(--muted)}"
        ".title .sub{margin-top:4px;color:var(--muted);font-size:12px}"
        ".tab-wrap{margin-top:14px;overflow-x:auto;white-space:nowrap;box-sizing:border-box}"
        ".tab-wrap::-webkit-scrollbar{height:4px}"
        ".tab-wrap::-webkit-scrollbar-thumb{background:var(--line);border-radius:2px}"
        ".tabs{display:flex;gap:6px;flex-wrap:wrap}"
        ".tab-btn{background:transparent;border:1px solid var(--line);border-radius:999px;color:var(--muted);cursor:pointer;font-size:13px;padding:6px 14px;white-space:nowrap;transition:all .15s}"
        ".tab-btn:hover{border-color:var(--accent);color:var(--text)}"
        ".tab-btn.active{background:var(--accent);border-color:var(--accent);color:#07111f;font-weight:700}"
        ".card{background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:14px}"
        ".card-title{font-size:18px;font-weight:800;margin-bottom:14px}"
        ".controls{display:grid;grid-template-columns:140px 160px 260px 160px 160px 120px;gap:10px;align-items:end}"
        "label{display:block;font-weight:700;margin-bottom:5px;font-size:13px}"
        "input,select{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:#0a1024;color:var(--text);font-size:13px}"
        ".action-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:14px}"
        ".src-btns{display:flex;gap:10px;flex-wrap:wrap}"
        ".query-display{color:var(--muted);font-size:13px;margin-left:auto}"
        ".query-display strong{color:var(--text)}"
        ".shortcut-label{font-size:12px;color:var(--muted);font-weight:700;margin-bottom:8px}"
        ".btnrow{display:flex;flex-wrap:wrap;gap:8px}"
        ".btn{display:inline-block;padding:9px 14px;border-radius:10px;background:var(--accent);color:#06111d;font-weight:700;border:none;cursor:pointer;text-decoration:none;font-size:13px}"
        ".btn.alt{background:rgba(255,255,255,.07);color:var(--text);border:1px solid var(--line)}"
        ".btn.alt:hover{background:rgba(255,255,255,.13);text-decoration:none}"
        ".btn:hover{text-decoration:none}"
        ".reset-btn{background:rgba(255,255,255,.07);border:1px solid var(--line);border-radius:10px;color:var(--text);cursor:pointer;font-size:13px;padding:10px 16px;white-space:nowrap}"
        ".shortcuts-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}"
        ".shortcuts-grid>div{display:flex;flex-direction:column}"
        ".bottom-meta{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;color:var(--muted);font-size:12px;padding:0 4px}"
        ".bottom-meta a{color:var(--muted)}"
        ".bottom-meta a:hover{color:var(--text)}"
        ".footer{margin:24px 0 0;padding:14px 0;color:var(--muted);font-size:12px;width:100%;box-sizing:border-box}"
        ".footer-inner{max-width:1480px;width:100%;margin:0 auto;padding:0 20px;box-sizing:border-box}"
        "@media(max-width:800px){"
        ".controls{grid-template-columns:1fr}"
        ".shortcuts-grid{grid-template-columns:1fr}"
        ".query-display{margin-left:0}"
        "}"
    )

    js = (
        "const CATEGORIES = " + cats_js + ";\n"
        "const REGION_PREFECTURES = " + region_js + ";\n"
        "const PREFECTURE_AREAS = " + area_js + ";\n"
        "let currentCat = CATEGORIES[0];\n"
        "\n"
        "function switchCat(catId) {\n"
        "  currentCat = CATEGORIES.find(c => c.id === catId) || CATEGORIES[0];\n"
        "  document.querySelectorAll('.tab-btn').forEach(btn => {\n"
        "    btn.classList.toggle('active', btn.dataset.cat === catId);\n"
        "  });\n"
        "  document.getElementById('catTitle').textContent = currentCat.icon + ' ' + currentCat.label;\n"
        "  const subcatSel = document.getElementById('subcat');\n"
        "  subcatSel.innerHTML = '<option value=\"\"></option>' +\n"
        "    currentCat.subcategories.map(s => `<option value=\"${esc(s)}\">${esc(s)}</option>`).join('');\n"
        "  const sceneSel = document.getElementById('scene');\n"
        "  if (sceneSel) {\n"
        "    sceneSel.innerHTML = '<option value=\"\"></option>' +\n"
        "      ((currentCat.scenes || []).map(s => `<option value=\"${esc(s)}\">${esc(s)}</option>`).join(''));\n"
        "  }\n"
        "  document.getElementById('subcatBtns').innerHTML =\n"
        "    currentCat.subcategories.map(s =>\n"
        "      `<button class=\"btn alt\" onclick=\"setSubcat('${esc(s)}')\">${esc(s)}</button>`\n"
        "    ).join('');\n"
        "  const sceneArea = document.getElementById('sceneArea');\n"
        "  const sceneBtns = document.getElementById('sceneBtns');\n"
        "  if (currentCat.scenes && currentCat.scenes.length > 0) {\n"
        "    sceneArea.style.display = 'block';\n"
        "    sceneBtns.innerHTML = currentCat.scenes.map(s =>\n"
        "      `<button class=\"btn alt\" onclick=\"setScene('${esc(s)}')\">${esc(s)}</button>`\n"
        "    ).join('');\n"
        "  } else {\n"
        "    sceneArea.style.display = 'none';\n"
        "    sceneBtns.innerHTML = '';\n"
        "  }\n"
        "  applyLinks();\n"
        "  updateTopLinks();\n"
        "}\n"
        "\n"
        "function esc(s) {\n"
        "  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&#39;');\n"
        "}\n"
        "\n"
        "function buildQuery() {\n"
        "  const region = document.getElementById('region').value.trim();\n"
        "  const pref = document.getElementById('pref').value.trim();\n"
        "  const area = document.getElementById('area').value.trim();\n"
        "  const subcat = document.getElementById('subcat').value.trim();\n"
        "  const scene = document.getElementById('scene').value.trim();\n"
        "  const place = [pref || region, area].filter(Boolean).join(' ');\n"
        "  return [place, subcat, scene].filter(Boolean).join(' ').trim();\n"
        "}\n"
        "\n"
        "function applyLinks() {\n"
        "  const query = buildQuery();\n"
        "  document.getElementById('queryPreview').textContent = query || '未入力';\n"
        "  document.getElementById('srcBtns').innerHTML = currentCat.sources.map(src => {\n"
        "    const href = src.url_template.replace('{query}', encodeURIComponent(query));\n"
        "    return `<a class=\"btn\" href=\"${href}\" target=\"_blank\" rel=\"noopener\">${esc(src.name)}</a>`;\n"
        "  }).join('');\n"
        "}\n"
        "\n"
        "function updatePrefectures() {\n"
        "  const region = document.getElementById('region').value;\n"
        "  const prefSel = document.getElementById('pref');\n"
        "  const prefs = REGION_PREFECTURES[region] || [];\n"
        "  prefSel.innerHTML = '<option value=\"\"></option>' +\n"
        "    prefs.map(p => `<option value=\"${esc(p)}\">${esc(p)}</option>`).join('');\n"
        "  updateAreaHints(true);\n"
        "}\n"
        "\n"
        "function updateAreaHints(resetArea) {\n"
        "  const pref = document.getElementById('pref').value;\n"
        "  const areaList = document.getElementById('area-list');\n"
        "  const areaInput = document.getElementById('area');\n"
        "  const areas = PREFECTURE_AREAS[pref] || [];\n"
        "  areaList.innerHTML = areas.map(a => `<option value=\"${esc(a)}\">`).join('');\n"
        "  if (resetArea) areaInput.value = '';\n"
        "  applyLinks();\n"
        "}\n"
        "\n"
        "function setSubcat(val) {\n"
        "  document.getElementById('subcat').value = val;\n"
        "  applyLinks();\n"
        "}\n"
        "\n"
        "function setScene(val) {\n"
        "  const scene = document.getElementById('scene');\n"
        "  if (scene) {\n"
        "    scene.value = val;\n"
        "  }\n"
        "  applyLinks();\n"
        "}\n"
        "\n"
        "function updateTopLinks() {\n"
        "  document.getElementById('topLinks').innerHTML =\n"
        "    currentCat.sources.map((src, i) => {\n"
        "      const href = src.url_template.replace('{query}', '');\n"
        "      return (i > 0 ? ' / ' : '') +\n"
        "        `<a href=\"${href}\" target=\"_blank\" rel=\"noopener\">${esc(src.name)}</a>`;\n"
        "    }).join('');\n"
        "}\n"
        "\n"
        "function clearInputs() {\n"
        "  document.getElementById('region').selectedIndex = 0;\n"
        "  updatePrefectures();\n"
        "  document.getElementById('pref').selectedIndex = 0;\n"
        "  document.getElementById('area').value = '';\n"
        "  document.getElementById('subcat').selectedIndex = 0;\n"
        "  const scene = document.getElementById('scene');\n"
        "  if (scene) scene.selectedIndex = 0;\n"
        "  applyLinks();\n"
        "}\n"
        "\n"
        "document.getElementById('region').addEventListener('change', applyLinks);\n"
        "document.getElementById('pref').addEventListener('change', applyLinks);\n"
        "document.getElementById('area').addEventListener('input', applyLinks);\n"
        "document.getElementById('area').addEventListener('change', applyLinks);\n"
        "document.getElementById('subcat').addEventListener('change', applyLinks);\n"
        "document.getElementById('scene').addEventListener('change', applyLinks);\n"
        "\n"
        "switchCat(CATEGORIES[0].id);\n"
    )

    return (
        "<!doctype html>\n"
        "<html lang=\"ja\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        "<title>" + e(APP_NAME) + "</title>\n"
        "<style>" + css + "</style>\n"
        "</head>\n"
        "<body>\n"
        "<header>\n"
        "<div class=\"wrap\">\n"
        "  <div class=\"top\">\n"
        "    <div class=\"title\">\n"
        "      <h1>" + e(APP_ICON) + " " + e(APP_NAME) + "<span>｜ " + e(APP_SUB) + "</span></h1>\n"
        "      <div class=\"sub\">更新日時：" + e(UPDATED_AT) + "</div>\n"
        "    </div>\n"
        "  </div>\n"
        "  <div class=\"tab-wrap\"><div class=\"tabs\">" + tab_btns + "</div></div>\n"
        "</div>\n"
        "</header>\n"
        "\n"
        "<div class=\"wrap\">\n"
        "  <div class=\"card\">\n"
        "    <div class=\"card-title\" id=\"catTitle\">" + e(first_cat["icon"]) + " " + e(first_cat["label"]) + "</div>\n"
        "    <div class=\"controls\">\n"
        "      <div><label for=\"region\">地方</label>"
        "<select id=\"region\" onchange=\"updatePrefectures()\">" + region_opts + "</select></div>\n"
        "      <div><label for=\"pref\">都道府県</label>"
        "<select id=\"pref\" onchange=\"updateAreaHints(true)\"><option value=\"\"></option></select></div>\n"
        "      <div><label for=\"area\">エリア・駅・市</label>"
        "<input id=\"area\" list=\"area-list\" placeholder=\"例: 名古屋港 / 白川郷 / 伊良湖\">"
        "<datalist id=\"area-list\"></datalist></div>\n"
        "      <div><label for=\"subcat\">被写体</label>"
        "<select id=\"subcat\" onchange=\"applyLinks()\">" + subcat_opts + "</select></div>\n"
        "      <div><label for=\"scene\">撮影条件</label>"
        "<select id=\"scene\" onchange=\"applyLinks()\"><option value=\"\"></option></select></div>\n"
        "      <div style=\"display:flex;align-items:flex-end\">"
        "<button class=\"reset-btn\" onclick=\"clearInputs()\">リセット</button></div>\n"
        "    </div>\n"
        "    <div class=\"action-row\">\n"
        "      <div class=\"src-btns\" id=\"srcBtns\">" + initial_src_btns + "</div>\n"
        "      <div class=\"query-display\">現在の検索条件：<strong id=\"queryPreview\">未入力</strong></div>\n"
        "    </div>\n"
        "  </div>\n"
        "\n"
        "  <div class=\"card\">\n"
        "    <div class=\"shortcuts-grid\">\n"
        "      <div>\n"
        "        <div class=\"shortcut-label\">被写体から選ぶ</div>\n"
        "        <div class=\"btnrow\" id=\"subcatBtns\">" + initial_shortcuts + "</div>\n"
        "      </div>\n"
        "      <div id=\"sceneArea\" style=\"display:" + scenes_display + "\">\n"
        "        <div class=\"shortcut-label\">撮影条件から選ぶ</div>\n"
        "        <div class=\"btnrow\" id=\"sceneBtns\">" + initial_scenes + "</div>\n"
        "      </div>\n"
        "    </div>\n"
        "  </div>\n"
        "\n"
        "  <div class=\"bottom-meta\">\n"
        "    <div>撮影可否・立入可否・私有地情報は現地案内を確認</div>\n"
        "    <div>各参照先：<span id=\"topLinks\"></span></div>\n"
        "  </div>\n"
        "\n"
        "</div>\n"
        "\n"
        "<div class=\"footer\"><div class=\"footer-inner\">" + e(APP_NAME) + " — 更新日時 " + e(UPDATED_AT) + "</div></div>\n"
        "\n"
        "<script>\n" + js + "</script>\n"
        "</body>\n"
        "</html>"
    )


def write_page(filename: str, content: str) -> None:
    (OUTPUT_DIR / filename).write_text(content, encoding="utf-8")


def main() -> None:
    start = time.time() 
    print("==============================================")
    print("  Photo Finder v0.0.1")
    print("==============================================")
    print("[1/3] Installing requirements...")
    print("[2/3] Running...")

    write_page("index.html", page_index())

    print("[3/3] Opening dashboard...")
    print(f"\n⏱ 処理時間: {time.time() - start:.1f}秒")
    print("  - output/index.html")


if __name__ == "__main__":
    main()
