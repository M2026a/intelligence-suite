from __future__ import annotations

import html as htmllib
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ── パス設定 ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import domestic_news
import global_news

JST = ZoneInfo("Asia/Tokyo")
UPDATED_AT = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

# ── JSON 読み込み ─────────────────────────────────────────────────
def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

CONFIG  = load_json(SHARED / "config.json")
THEMES  = load_json(SHARED / "themes.json")
TABS    = load_json(SHARED / "tabs.json")
SOURCES = load_json(SHARED / "sources.json")

APP_NAME = CONFIG["app_name"]
SUBTITLE = CONFIG.get("subtitle", "")

# ── ニュースタブ定義 ──────────────────────────────────────────────
DOM_CATS = [
    ("dom_all",       "ALL",       "📋"),
    ("dom_latest",    "最新",         "🆕"),
    ("dom_event",     "イベント",     "🎉"),
    ("dom_ticket",    "チケット",     "🎫"),
    ("dom_spot",      "観光スポット", "🗺️"),
    ("dom_transport", "交通",         "🚃"),
    ("dom_weather",   "天気",         "🌤️"),
    ("dom_stay",      "宿泊",         "🏨"),
    ("dom_food",      "グルメ",       "🍜"),
    ("dom_notice",    "お知らせ",     "📢"),
]
GLB_CATS = [
    ("glb_all",       "ALL",       "📋"),
    ("glb_latest",    "最新",         "🆕"),
    ("glb_spot",      "観光スポット", "🗺️"),
    ("glb_safety",    "安全・入国情報","⚠️"),
    ("glb_transport", "航空・交通",   "✈️"),
    ("glb_hotel",     "ホテル・宿泊", "🏨"),
    ("glb_food",      "グルメ",       "🍜"),
    ("glb_info",      "旅行情報",     "📰"),
    ("glb_notice",    "お知らせ",     "📢"),
]
DOM_CAT_BY_ID  = {tid: cat for tid, cat, _ in DOM_CATS}
GLB_CAT_BY_ID  = {tid: cat for tid, cat, _ in GLB_CATS}

# 検索タブ（tabs.json から domestic_travel / global_travel を除外）
SEARCH_TABS = [p for p in TABS["pages"] if p["id"] not in ("domestic_travel", "global_travel")]
GENERAL_TAB = {"id": "overview", "label_ja": "総合", "icon": "📊"}

# ── 検索フォーム定義 ─────────────────────────────────────────────
SEARCH_TAB_FIELDS = {
    "transport": {"origin": True,  "dest": True, "depart": True,  "return": True,  "adults": True,  "keyword": False},
    "stay":      {"origin": False, "dest": True, "depart": True,  "return": True,  "adults": True,  "keyword": True },
    "package":   {"origin": True,  "dest": True, "depart": True,  "return": True,  "adults": True,  "keyword": False},
    "compare":   {"origin": True,  "dest": True, "depart": True,  "return": True,  "adults": True,  "keyword": False},
    "activity":  {"origin": False, "dest": True, "depart": True,  "return": False, "adults": False, "keyword": True },
    "food":      {"origin": False, "dest": True, "depart": False, "return": False, "adults": False, "keyword": True },
    "support":   {"origin": False, "dest": True, "depart": False, "return": False, "adults": False, "keyword": True },
    "ideas":     {"origin": False, "dest": True, "depart": True,  "return": False, "adults": False, "keyword": True },
}

QUICK_TAGS = {
    "transport": ["直行便", "LCC", "新幹線", "夜行バス", "レンタカー"],
    "stay":      ["温泉", "駅近", "高級ホテル", "コスパ", "朝食"],
    "package":   ["航空券+ホテル", "ツアー", "週末", "家族旅行"],
    "compare":   ["最安", "直行便", "新幹線", "高速バス", "夜行バス"],
    "activity":  ["絶景", "テーマパーク", "美術館", "子ども", "雨の日"],
    "food":      ["ラーメン", "寿司", "焼肉", "カフェ", "朝食", "ディナー"],
    "support":   ["駐車場", "Wi‑Fi", "eSIM", "保険", "両替"],
    "ideas":     ["ひとり旅", "デート", "温泉旅", "弾丸旅", "食べ歩き"],
}

FIELD_META = {
    "tripType":    ("旅行区分",      "Trip type"),
    "regionGroup": ("地域 / 地方",   "Area / Region"),
    "subArea":     ("国 / 都道府県", "Country / Prefecture"),
    "origin":      ("出発地",        "Origin"),
    "dest":        ("目的地",        "Destination"),
    "depart":      ("出発日",        "Depart"),
    "return":      ("帰着日",        "Return"),
    "adults":      ("大人",          "Adults"),
    "keyword":     ("キーワード",    "Keyword"),
}

LOCATION_DATA = {
    "domestic": {
        "group_ja": "地方", "group_en": "Region",
        "sub_ja": "都道府県", "sub_en": "Prefecture",
        "city_ja": "市・エリア", "city_en": "City / Area",
        "regions": {
            "北海道": {"北海道": ["札幌", "函館", "小樽", "旭川", "富良野", "登別", "洞爺湖"]},
            "東北": {"青森県": ["青森", "弘前", "八戸"], "岩手県": ["盛岡", "平泉", "花巻"], "宮城県": ["仙台", "松島", "石巻"], "秋田県": ["秋田", "角館", "田沢湖"], "山形県": ["山形", "蔵王", "銀山温泉"], "福島県": ["福島", "会津若松", "郡山", "いわき"]},
            "関東": {"東京都": ["東京", "新宿", "渋谷", "浅草", "銀座", "上野"], "神奈川県": ["横浜", "みなとみらい", "鎌倉", "箱根", "川崎"], "埼玉県": ["大宮", "川越", "秩父"], "千葉県": ["千葉", "舞浜", "成田", "木更津"], "茨城県": ["水戸", "ひたちなか", "つくば"], "栃木県": ["宇都宮", "日光", "那須"], "群馬県": ["高崎", "草津", "伊香保"]},
            "中部": {"新潟県": ["新潟", "越後湯沢", "佐渡"], "富山県": ["富山", "黒部", "立山"], "石川県": ["金沢", "加賀", "和倉温泉"], "福井県": ["福井", "あわら", "敦賀"], "山梨県": ["甲府", "河口湖", "山中湖"], "長野県": ["長野", "松本", "軽井沢", "白馬"], "岐阜県": ["岐阜", "高山", "下呂", "白川郷"], "静岡県": ["静岡", "熱海", "伊豆", "浜松"], "愛知県": ["名古屋", "栄", "犬山", "常滑"]},
            "近畿": {"三重県": ["伊勢", "鳥羽", "志摩"], "滋賀県": ["大津", "彦根", "長浜"], "京都府": ["京都", "嵐山", "祇園", "天橋立"], "大阪府": ["大阪", "梅田", "難波", "天王寺", "新大阪"], "兵庫県": ["神戸", "姫路", "有馬温泉", "城崎温泉"], "奈良県": ["奈良", "吉野", "橿原"], "和歌山県": ["和歌山", "白浜", "那智勝浦", "高野山"]},
            "中国": {"鳥取県": ["鳥取", "倉吉", "境港"], "島根県": ["松江", "出雲", "石見銀山"], "岡山県": ["岡山", "倉敷"], "広島県": ["広島", "宮島", "尾道", "福山"], "山口県": ["山口", "下関", "萩", "岩国"]},
            "四国": {"徳島県": ["徳島", "鳴門", "祖谷"], "香川県": ["高松", "琴平", "小豆島"], "愛媛県": ["松山", "道後温泉", "今治"], "高知県": ["高知", "四万十", "足摺岬"]},
            "九州・沖縄": {"福岡県": ["福岡", "博多", "天神", "北九州"], "佐賀県": ["佐賀", "嬉野", "唐津"], "長崎県": ["長崎", "佐世保", "ハウステンボス"], "熊本県": ["熊本", "阿蘇", "黒川温泉"], "大分県": ["別府", "由布院", "大分"], "宮崎県": ["宮崎", "高千穂", "日南"], "鹿児島県": ["鹿児島", "指宿", "屋久島"], "沖縄県": ["那覇", "恩納村", "石垣", "宮古島"]},
        },
    },
    "international": {
        "group_ja": "地域", "group_en": "Area",
        "sub_ja": "国", "sub_en": "Country",
        "city_ja": "都市・エリア", "city_en": "City / Area",
        "regions": {
            "東アジア": {"韓国": ["ソウル", "釜山", "済州", "仁川"], "台湾": ["台北", "高雄", "台中"], "中国": ["上海", "北京", "広州", "深圳"], "香港": ["香港"], "マカオ": ["マカオ"]},
            "東南アジア": {"タイ": ["バンコク", "プーケット", "チェンマイ"], "シンガポール": ["シンガポール"], "ベトナム": ["ホーチミン", "ハノイ", "ダナン"], "マレーシア": ["クアラルンプール", "ペナン", "コタキナバル"], "インドネシア": ["バリ", "ジャカルタ"], "フィリピン": ["マニラ", "セブ", "ボラカイ"]},
            "南アジア・中東": {"インド": ["デリー", "ムンバイ", "ベンガルール"], "UAE": ["ドバイ", "アブダビ"], "トルコ": ["イスタンブール", "カッパドキア"], "カタール": ["ドーハ"]},
            "ヨーロッパ": {"フランス": ["パリ", "ニース", "リヨン"], "イギリス": ["ロンドン", "マンチェスター", "エディンバラ"], "イタリア": ["ローマ", "ミラノ", "ヴェネツィア"], "スペイン": ["バルセロナ", "マドリード", "セビリア"], "ドイツ": ["ベルリン", "ミュンヘン", "フランクフルト"], "スイス": ["チューリッヒ", "ジュネーブ"]},
            "北米": {"アメリカ": ["ニューヨーク", "ロサンゼルス", "ホノルル", "ラスベガス"], "カナダ": ["バンクーバー", "トロント", "モントリオール"]},
            "中南米": {"メキシコ": ["メキシコシティ", "カンクン"], "ブラジル": ["サンパウロ", "リオデジャネイロ"], "ペルー": ["リマ", "クスコ"]},
            "オセアニア": {"オーストラリア": ["シドニー", "メルボルン", "ケアンズ"], "ニュージーランド": ["オークランド", "クイーンズタウン"], "グアム": ["グアム"], "サイパン": ["サイパン"]},
            "アフリカ": {"エジプト": ["カイロ"], "モロッコ": ["マラケシュ", "カサブランカ"], "南アフリカ": ["ケープタウン", "ヨハネスブルグ"]},
        },
    },
}

AIRPORTS = {
    "東京": "TYO", "羽田": "HND", "成田": "NRT", "東京都": "TYO", "大阪": "OSA", "関西": "KIX", "伊丹": "ITM", "新大阪": "OSA", "大阪府": "OSA", "名古屋": "NGO", "中部": "NGO", "愛知県": "NGO", "札幌": "CTS", "北海道": "CTS", "仙台": "SDJ", "宮城県": "SDJ", "青森県": "AOJ", "岩手県": "HNA", "秋田県": "AXT", "山形県": "GAJ", "福島県": "FKS", "福岡": "FUK", "博多": "FUK", "福岡県": "FUK", "佐賀県": "HSG", "長崎県": "NGS", "熊本県": "KMJ", "大分県": "OIT", "宮崎県": "KMI", "鹿児島県": "KOJ", "沖縄県": "OKA", "那覇": "OKA", "新潟県": "KIJ", "富山県": "TOY", "石川県": "KMQ", "福井県": "KMQ", "山梨県": "HND", "長野県": "MMJ", "岐阜県": "NGO", "静岡県": "FSZ", "神奈川県": "HND", "埼玉県": "TYO", "千葉県": "NRT", "茨城県": "IBR", "栃木県": "TYO", "群馬県": "TYO", "三重県": "NGO", "滋賀県": "KIX", "京都府": "UKY", "兵庫県": "UKB", "奈良県": "KIX", "和歌山県": "KIX", "鳥取県": "TTJ", "島根県": "IZO", "岡山県": "OKJ", "広島県": "HIJ", "山口県": "UBJ", "徳島県": "TKS", "香川県": "TAK", "愛媛県": "MYJ", "高知県": "KCZ",
    "韓国": "SEL", "ソウル": "SEL", "仁川": "ICN", "釜山": "PUS", "済州": "CJU", "台湾": "TPE", "台北": "TPE", "高雄": "KHH", "台中": "RMQ", "中国": "BJS", "北京": "BJS", "上海": "SHA", "広州": "CAN", "深圳": "SZX", "香港": "HKG", "マカオ": "MFM", "タイ": "BKK", "バンコク": "BKK", "プーケット": "HKT", "チェンマイ": "CNX", "シンガポール": "SIN", "ベトナム": "SGN", "ホーチミン": "SGN", "ハノイ": "HAN", "ダナン": "DAD", "マレーシア": "KUL", "クアラルンプール": "KUL", "ペナン": "PEN", "コタキナバル": "BKI", "インドネシア": "DPS", "バリ": "DPS", "ジャカルタ": "CGK", "フィリピン": "MNL", "マニラ": "MNL", "セブ": "CEB",
    "フランス": "PAR", "パリ": "PAR", "ニース": "NCE", "リヨン": "LYS", "イギリス": "LON", "ロンドン": "LON", "マンチェスター": "MAN", "エディンバラ": "EDI", "イタリア": "ROM", "ローマ": "ROM", "ミラノ": "MIL", "ヴェネツィア": "VCE", "スペイン": "BCN", "バルセロナ": "BCN", "マドリード": "MAD", "セビリア": "SVQ", "ドイツ": "FRA", "ベルリン": "BER", "ミュンヘン": "MUC", "フランクフルト": "FRA", "スイス": "ZRH", "チューリッヒ": "ZRH", "ジュネーブ": "GVA",
    "アメリカ": "NYC", "ニューヨーク": "NYC", "ロサンゼルス": "LAX", "ホノルル": "HNL", "ラスベガス": "LAS", "カナダ": "YVR", "バンクーバー": "YVR", "トロント": "YYZ", "モントリオール": "YUL", "メキシコ": "MEX", "メキシコシティ": "MEX", "カンクン": "CUN", "ブラジル": "SAO", "サンパウロ": "SAO", "リオデジャネイロ": "RIO", "ペルー": "LIM", "リマ": "LIM", "クスコ": "CUZ",
    "オーストラリア": "SYD", "シドニー": "SYD", "メルボルン": "MEL", "ケアンズ": "CNS", "ニュージーランド": "AKL", "オークランド": "AKL", "クイーンズタウン": "ZQN", "グアム": "GUM", "サイパン": "SPN", "UAE": "DXB", "ドバイ": "DXB", "アブダビ": "AUH", "トルコ": "IST", "イスタンブール": "IST", "カッパドキア": "NAV", "カタール": "DOH", "ドーハ": "DOH", "インド": "DEL", "デリー": "DEL", "ムンバイ": "BOM", "ベンガルール": "BLR", "エジプト": "CAI", "カイロ": "CAI", "モロッコ": "RAK", "マラケシュ": "RAK", "カサブランカ": "CMN", "南アフリカ": "CPT", "ケープタウン": "CPT", "ヨハネスブルグ": "JNB",
}

ORIGIN_SUGGESTIONS = ["東京", "羽田", "成田", "大阪", "関西", "伊丹", "新大阪", "名古屋", "中部", "札幌", "仙台", "福岡", "博多", "那覇", "ソウル", "釜山", "台北", "香港", "シンガポール", "バンコク", "パリ", "ロンドン", "ニューヨーク", "ロサンゼルス", "ホノルル"]


# ── ヘルパー関数 ──────────────────────────────────────────────────
def e(text: str) -> str:
    return htmllib.escape(str(text))


def render_field(name: str, add_data_field: bool = False) -> str:
    ja, en = FIELD_META[name]
    if name == "tripType":
        control = '<select id="tripType"><option value="">選択してください</option><option value="domestic">国内</option><option value="international">海外</option></select>'
    elif name == "regionGroup":
        control = '<select id="regionGroup" disabled><option value="">旅行区分を先に選択</option></select>'
    elif name == "subArea":
        control = '<select id="subArea" disabled><option value="">地域/地方を選択</option></select>'
    elif name == "origin":
        control = '<input id="originInput" list="originList" value="" placeholder="例: 東京, 羽田, 大阪">'
    elif name == "dest":
        control = '<input id="destInput" list="destList" value="" placeholder="例: 新宿, 札幌, ソウル, パリ">'
    elif name == "depart":
        control = '<input id="departInput" type="date" value="">'
    elif name == "return":
        control = '<input id="returnInput" type="date" value="">'
    elif name == "adults":
        control = '<input id="adultsInput" type="number" min="1" max="9" value="" placeholder="1">'
    else:
        control = '<input id="keywordInput" value="" placeholder="例: 温泉, ラーメン, 直行便">'

    data_attr = f' data-field="{name}"' if add_data_field else ""
    extra_attrs = ""
    if name == "regionGroup":
        extra_attrs = ' id="regionGroupWrap"'
        label_html = '<span id="regionGroupLabelJa">地域 / 地方</span><span id="regionGroupLabelEn">Area / Region</span>'
    elif name == "subArea":
        extra_attrs = ' id="subAreaWrap"'
        label_html = '<span id="subAreaLabelJa">国 / 都道府県</span><span id="subAreaLabelEn">Country / Prefecture</span>'
    elif name == "dest":
        extra_attrs = ' id="destWrap"'
        label_html = '<span id="destLabelJa">目的地</span><span id="destLabelEn">Destination</span>'
    else:
        label_html = f'<span class="ja">{ja}</span><span class="en">{en}</span>'

    return f'<label{extra_attrs}{data_attr}>{label_html}{control}</label>'


def all_provider_buttons() -> str:
    """全検索タブのプロバイダーボタン（data-page属性付き）"""
    buttons = []
    for page_id, items in SOURCES.items():
        for item in items:
            for provider in item.get("providers", []):
                kind_attr = f' data-kind="{e(provider["kind"])}"' if provider.get("kind") else ""
                buttons.append(
                    f'<a class="provider-btn" data-region="{provider.get("region","both")}"'
                    f' data-page="{page_id}"{kind_attr}'
                    f' data-url="{e(provider["url"])}"'
                    f' href="#" target="_blank" rel="noopener">{e(provider["name"])}</a>'
                )
    return "".join(buttons)


def render_stat_rows(counter: Counter, limit: int = 5) -> str:
    if not counter:
        return "<div class='empty-small'>データなし</div>"
    top = counter.most_common(limit)
    max_count = top[0][1] if top else 1
    rows = []
    for name, count in top:
        width = max(12, int((count / max_count) * 100)) if max_count else 12
        rows.append(
            f"<div class='stat-row'><div class='stat-row-head'><span class='stat-label'>{e(name)}</span><strong class='stat-value'>{count}</strong></div><div class='stat-bar'><span style=\"width:{width}%\"></span></div></div>"
        )
    return ''.join(rows)


def render_stat_block(title: str, counter: Counter, limit: int = 5) -> str:
    return f"<div class='summary-block'><h3>{e(title)}</h3><div class='stat-rows'>{render_stat_rows(counter, limit)}</div></div>"


# ── HTML ビルダー ─────────────────────────────────────────────────
def build_page(
    dom_items: list[dict], dom_summary: dict, dom_fetched: int, dom_inserted: int, dom_errors: list[str],
    glb_items: list[dict], glb_summary: dict, glb_fetched: int, glb_inserted: int, glb_errors: list[str],
) -> str:
    # ── JS 用データ
    airports_json  = json.dumps(AIRPORTS, ensure_ascii=False)
    location_json  = json.dumps(LOCATION_DATA, ensure_ascii=False)
    search_fields_json = json.dumps(SEARCH_TAB_FIELDS, ensure_ascii=False)
    quick_tags_json = json.dumps(QUICK_TAGS, ensure_ascii=False)
    dom_items_json = json.dumps(dom_items, ensure_ascii=False)
    glb_items_json = json.dumps(glb_items, ensure_ascii=False)
    glb_regions_json = json.dumps(global_news.REGIONS, ensure_ascii=False)
    origin_opts = "".join(f'<option value="{e(c)}"></option>' for c in ORIGIN_SUGGESTIONS)

    # ── タブバー HTML
    # 検索タブ行
    search_tab_btns = "".join(
        f'<button class="tab-btn search-tab{" active" if i == 0 else ""}" data-tab="{p["id"]}" data-type="search">'
        f'{e(p["icon"])} {e(p["label_ja"])}</button>'
        for i, p in enumerate(SEARCH_TABS)
    )
    # 国内ニュースタブ
    dom_tab_btns = "".join(
        f'<button class="tab-btn dom-tab" data-tab="{tid}" data-cat="{e(cat)}" data-type="dom">'
        f'{icon} {e(cat)}</button>'
        for tid, cat, icon in DOM_CATS
    )
    # 海外ニュースタブ
    glb_tab_btns = "".join(
        f'<button class="tab-btn glb-tab" data-tab="{tid}" data-cat="{e(cat)}" data-type="glb">'
        f'{icon} {e(cat)}</button>'
        for tid, cat, icon in GLB_CATS
    )

    # ── 検索セクションのフィールドHTML（全フィールド、data-field付き）
    all_fields = ["tripType", "regionGroup", "subArea", "origin", "dest", "depart", "return", "adults", "keyword"]
    fields_html = "".join(render_field(f, add_data_field=True) for f in all_fields)

    # ── プロバイダーボタン
    all_provider_btns = all_provider_buttons()

    # ── 総合タブ UI
    def overview_stat_rows(counter: Counter, limit: int = 5) -> str:
        items = [(str(name).strip(), int(count)) for name, count in counter.most_common(limit) if str(name).strip()]
        if not items:
            return "<div class='empty-small'>データなし</div>"
        maxv = max(count for _, count in items) or 1
        rows = []
        for name, count in items:
            width = max(8, round(count / maxv * 100))
            rows.append(
                f"<div class='stat-row'><div class='stat-row-head'><span class='stat-label'>{e(name)}</span><strong class='stat-value'>{count}</strong></div><div class='stat-bar'><span style='width:{width}%'></span></div></div>"
            )
        return "".join(rows)

    overview_tab_btn = '<button class="tab-btn overview-tab" data-tab="overview" data-type="overview">📊 総合</button>'

    total_count = int(dom_summary.get("total", 0)) + int(glb_summary.get("total", 0))
    total_new = int(dom_summary.get("new_count", 0)) + int(glb_summary.get("new_count", 0))
    total_fetched = int(dom_fetched) + int(glb_fetched)
    total_inserted = int(dom_inserted) + int(glb_inserted)

    overview_kpis = "".join([
        f"<div class='hero-card'><span>総件数</span><strong>{total_count}</strong></div>",
        f"<div class='hero-card'><span>新着</span><strong>{total_new}</strong></div>",
        f"<div class='hero-card'><span>今回取得</span><strong>{total_inserted}</strong></div>",
        f"<div class='hero-card'><span>取得総数</span><strong>{total_fetched}</strong></div>",
    ])

    overview_domestic = (
        "<section class='summary-section'>"
        "<h2 class='summary-section-title'>🗾 国内サマリー</h2>"
        "<div class='summary-grid'>"
        f"<div class='summary-block'><h3>カテゴリ TOP5</h3><div class='stat-rows'>{overview_stat_rows(dom_summary.get('categories', Counter()))}</div></div>"
        f"<div class='summary-block'><h3>都道府県 TOP5</h3><div class='stat-rows'>{overview_stat_rows(dom_summary.get('prefectures', Counter()))}</div></div>"
        f"<div class='summary-block'><h3>取得元 TOP5</h3><div class='stat-rows'>{overview_stat_rows(dom_summary.get('sources', Counter()))}</div></div>"
        "</div>"
        "</section>"
    )

    overview_global = (
        "<section class='summary-section'>"
        "<h2 class='summary-section-title'>🌍 海外サマリー</h2>"
        "<div class='summary-grid'>"
        f"<div class='summary-block'><h3>カテゴリ TOP5</h3><div class='stat-rows'>{overview_stat_rows(glb_summary.get('categories', Counter()))}</div></div>"
        f"<div class='summary-block'><h3>地域 TOP5</h3><div class='stat-rows'>{overview_stat_rows(glb_summary.get('regions', Counter()))}</div></div>"
        f"<div class='summary-block'><h3>国・エリア TOP5</h3><div class='stat-rows'>{overview_stat_rows(glb_summary.get('countries', Counter()))}</div></div>"
        f"<div class='summary-block'><h3>取得元 TOP5</h3><div class='stat-rows'>{overview_stat_rows(glb_summary.get('sources', Counter()))}</div></div>"
        "</div>"
        "</section>"
    )

    # ── CSS
    css = """
:root{
  --bg:#09111f;--panel:#121d31;--line:#24324b;--text:#ecf3ff;--muted:#9db0cb;
  --acc-search:#64b5ff;--acc-dom:#5dd8a0;--acc-glb:#f0a952;--acc-over:#d9d36b;
  --acc2:#82e2c0;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:linear-gradient(180deg,#0b1020,#12182d);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Hiragino Sans','Yu Gothic UI',Meiryo,sans-serif;overflow-x:hidden}
a{color:#9cc1ff;text-decoration:none} a:hover{text-decoration:underline}
.wrap{max-width:1480px;margin:0 auto;padding:14px 20px;}
/* ─ Topbar ─ */
.topbar{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.title h1{font-size:22px;font-weight:800;display:flex;flex-wrap:wrap;align-items:baseline;gap:0}
.title-suffix{color:var(--muted);font-size:14px;font-weight:500}
.sub{margin-top:4px;color:var(--muted);font-size:12px}
.toggle{border:1px solid var(--line);background:#101a2b;color:var(--text);padding:10px 14px;border-radius:999px;font-weight:700;cursor:pointer}
/* ─ Tab rows ─ */
.tab-rows{display:flex;flex-direction:column;gap:0;margin-bottom:20px;border-radius:18px;overflow:hidden;border:1px solid var(--line)}
.tab-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:10px 14px;background:rgba(18,29,49,.9)}
.tab-row+.tab-row{border-top:1px solid var(--line)}
.tab-row-label{font-size:11px;font-weight:800;color:var(--muted);white-space:nowrap;padding:5px 9px;border-radius:7px;background:rgba(255,255,255,.06);letter-spacing:.04em;border:1px solid var(--line)}
.tab-row-sep{font-size:11px;font-weight:800;color:var(--muted);white-space:nowrap;padding:5px 9px;border-radius:7px;background:rgba(255,255,255,.06);letter-spacing:.04em;border:1px solid var(--line);margin-left:6px}
.tab-btn{background:transparent;border:1px solid var(--line);border-radius:999px;color:var(--muted);cursor:pointer;font-size:13px;padding:7px 14px;white-space:nowrap;transition:all .15s;line-height:1}
.tab-btn:hover{border-color:#5a7ab5;color:var(--text)}
.overview-tab.active{background:linear-gradient(135deg,var(--acc-over),#d3b64e);border-color:transparent;color:#06111d;font-weight:700}
.search-tab.active{background:linear-gradient(135deg,var(--acc-search),#4a8fe8);border-color:transparent;color:#06111d;font-weight:700}
.dom-tab.active{background:linear-gradient(135deg,var(--acc-dom),#36c87a);border-color:transparent;color:#06111d;font-weight:700}
.glb-tab.active{background:linear-gradient(135deg,var(--acc-glb),#e8856e);border-color:transparent;color:#06111d;font-weight:700}
/* ─ Content sections ─ */
.content-section{display:none} .content-section.active{display:block}
/* ─ Cards ─ */
.card{background:rgba(18,29,49,.97);border:1px solid var(--line);border-radius:22px;box-shadow:0 10px 28px rgba(0,0,0,.18)}
.panel{padding:20px 22px;margin-bottom:16px}
.panel h2{font-size:20px;margin-bottom:16px}
/* ─ Search form ─ */
.search-grid{display:flex;flex-wrap:wrap;gap:10px;align-items:end}
label{display:flex;flex-direction:column;gap:7px;font-size:13px;color:var(--muted);flex:1 1 160px;min-width:110px}
label:has(select){flex:0 1 155px;max-width:175px}
label.field-dimmed{opacity:.3;pointer-events:none}
input,select{width:100%;padding:11px 12px;border-radius:14px;border:1px solid var(--line);background:#0d1525;color:var(--text);font-size:13px}
input::placeholder{color:#718096}
.control-row{display:flex;justify-content:flex-end;margin-top:12px}
.secondary-btn{display:inline-flex;align-items:center;padding:11px 18px;border-radius:14px;border:1px solid var(--line);font-weight:700;background:#202847;color:var(--text);cursor:pointer;transition:.15s ease}
.secondary-btn:hover{background:#2d3a5e}
.compare-switch{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 16px}
.kind-btn{display:inline-flex;align-items:center;padding:10px 16px;border-radius:14px;border:1px solid var(--line);font-weight:700;background:#202847;color:var(--text);cursor:pointer;transition:.15s}
.kind-btn.active{background:linear-gradient(135deg,var(--acc-search),#4a8fe8);color:#06111d;border-color:transparent}
.provider-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}
.provider-btn{display:inline-flex;align-items:center;padding:11px 16px;border-radius:14px;border:1px solid var(--line);font-weight:700;background:#7cb2ff;color:#06111d;cursor:pointer;transition:.15s ease;text-decoration:none}
.provider-btn:hover{transform:translateY(-1px);text-decoration:none}
.provider-btn.disabled{opacity:.4;pointer-events:none;background:#1b2440;color:#91a0bd}
.helper-line{margin-top:14px;color:var(--muted);font-size:13px;text-align:right}
.helper-line strong{color:var(--text)}
.chips-panel{padding:18px 22px;margin-bottom:16px}
.chip-list{display:flex;gap:8px;flex-wrap:wrap}
.chip-btn{background:#192035;border:1px solid var(--line);color:var(--text);border-radius:999px;padding:6px 14px;font-size:12px;cursor:pointer;transition:.15s}
.chip-btn:hover{background:#253060;border-color:var(--acc-search)}
/* ─ News sections ─ */
.news-layout{display:block}
.news-main.card{padding:18px 20px}
.news-ctrl{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.news-ctrl input,.news-ctrl select{width:auto;flex:1 1 160px;padding:10px 12px}
.news-ctrl .reset-btn{padding:10px 16px;border-radius:14px;border:1px solid var(--line);background:#202847;color:var(--text);cursor:pointer;white-space:nowrap;font-size:13px}
.news-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}
.news-chip{background:#192035;border:1px solid var(--line);color:var(--text);border-radius:999px;padding:4px 12px;font-size:12px;cursor:pointer;transition:.15s}
.news-chip:hover{background:#253060}
.news-summary{color:var(--muted);font-size:13px;margin-bottom:12px}
.news-list{display:flex;flex-direction:column;gap:10px}
.news-item{border:1px solid var(--line);border-radius:16px;background:rgba(13,21,37,.9);padding:14px}
.news-item-title{font-size:15px;font-weight:700;margin-bottom:8px;line-height:1.4}
.news-item-meta{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.badge{display:inline-flex;align-items:center;border-radius:999px;font-size:11px;padding:4px 9px;border:1px solid var(--line);background:#132042;color:var(--text)}
.badge.new{background:rgba(255,112,150,.18);border-color:rgba(255,112,150,.45)}
.badge.cat{background:rgba(100,181,255,.18);border-color:rgba(100,181,255,.45)}
.badge.loc{background:rgba(93,216,160,.15);border-color:rgba(93,216,160,.35)}
.badge.lang-en{background:rgba(255,200,80,.12);border-color:rgba(255,200,80,.35)}
.news-item-body{color:#d9e3ff;font-size:13px;line-height:1.6;margin-bottom:8px}
.news-item-sub{color:var(--muted);font-size:12px}
.news-hero-row{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
.hero-card{background:rgba(18,29,49,.97);border:1px solid var(--line);border-radius:14px;padding:12px 14px}
.hero-card span{display:block;color:var(--muted);font-size:11px;margin-bottom:6px}
.hero-card strong{font-size:20px}
.summary-section{margin-bottom:18px}
.summary-section-title{font-size:16px;font-weight:800;margin:0 0 10px 2px}
.summary-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.summary-block{background:rgba(18,29,49,.97);border:1px solid var(--line);border-radius:18px;padding:16px}
.summary-block h3{font-size:14px;margin-bottom:12px;color:var(--muted)}
.stat-rows{display:grid;gap:10px}
.stat-row{display:grid;gap:6px}
.stat-row-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.stat-label{font-size:12px;color:#dce7ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat-value{font-size:15px}
.stat-bar{height:8px;border-radius:999px;background:#0f1730;overflow:hidden;border:1px solid var(--line)}
.stat-bar span{display:block;height:100%;border-radius:999px;background:linear-gradient(135deg,var(--acc-search),var(--acc2))}
.empty-small{color:var(--muted);font-size:12px}
.tr-en{display:none} body.en-mode .tr-ja{display:none !important} body.en-mode .tr-en{display:inline !important}
.en{display:none} body.en-mode .ja{display:none !important} body.en-mode .en{display:initial !important}
@media(max-width:900px){.summary-grid{grid-template-columns:1fr}}
@media(max-width:640px){.title h1{font-size:20px} .tab-btn{font-size:12px;padding:6px 11px} .news-hero-row{grid-template-columns:repeat(2,1fr)}}
"""

    # ── JavaScript
    js = f"""
const AIRPORTS = {airports_json};
const LOCATION_DATA = {location_json};
const SEARCH_TAB_FIELDS = {search_fields_json};
const QUICK_TAGS = {quick_tags_json};
const DOM_ITEMS = {dom_items_json};
const GLB_ITEMS = {glb_items_json};
const GLB_REGIONS = {glb_regions_json};

let currentSearchTab = '{SEARCH_TABS[0]["id"]}';
let currentCompareKind = 'flight';
let domActiveTab = 'ALL';
let glbActiveTab = 'ALL';

function qs(id) {{ return document.getElementById(id); }}

// ══ タブ切り替え ══════════════════════════════════════════════════
function switchTab(tabId) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`[data-tab="${{tabId}}"]`);
  if (btn) btn.classList.add('active');

  const type = btn?.dataset.type;
  document.getElementById('section-overview').classList.toggle('active', type === 'overview');
  document.getElementById('section-search').classList.toggle('active', type === 'search');
  document.getElementById('section-dom-news').classList.toggle('active', type === 'dom');
  document.getElementById('section-glb-news').classList.toggle('active', type === 'glb');

  if (type === 'overview') {{
    return;
  }} else if (type === 'search') {{
    currentSearchTab = tabId;
    applySearchTab(tabId);
  }} else if (type === 'dom') {{
    domActiveTab = btn?.dataset.cat || 'ALL';
    renderDomestic();
  }} else if (type === 'glb') {{
    glbActiveTab = btn?.dataset.cat || 'ALL';
    renderGlobal();
  }}
}}

// ══ 検索タブ制御 ══════════════════════════════════════════════════
function applySearchTab(tabId) {{
  const page = document.querySelector(`[data-tab="${{tabId}}"]`);
  const title = page ? page.textContent.trim() : tabId;
  const titleEl = qs('searchPanelTitle');
  if (titleEl) titleEl.textContent = title;

  const fields = SEARCH_TAB_FIELDS[tabId] || {{}};
  const fieldMap = [
    {{key:'origin',  id:'originInput'}},
    {{key:'dest',    id:'destInput'}},
    {{key:'depart',  id:'departInput'}},
    {{key:'return',  id:'returnInput'}},
    {{key:'adults',  id:'adultsInput'}},
    {{key:'keyword', id:'keywordInput'}},
  ];
  fieldMap.forEach(f => {{
    const el = qs(f.id);
    if (!el) return;
    const label = el.closest('label');
    const active = fields[f.key] !== false;
    if (label) label.classList.toggle('field-dimmed', !active);
    el.disabled = !active;
  }});

  // compare スイッチャー表示切替
  const cmp = qs('compareSwitch');
  if (cmp) cmp.style.display = tabId === 'compare' ? 'flex' : 'none';

  // チップ更新
  const chipList = qs('searchChipList');
  if (chipList) {{
    const tags = QUICK_TAGS[tabId] || [];
    chipList.innerHTML = tags.map(tag =>
      `<button class="chip-btn" onclick="setKeyword(${{JSON.stringify(tag)}})">${{tag}}</button>`
    ).join('');
  }}

  applyLinks();
}}

// ══ 検索リンク制御 ════════════════════════════════════════════════
function fillOptions(select, values, placeholder) {{
  if (!select) return;
  const cur = select.value;
  select.innerHTML = '';
  const first = document.createElement('option');
  first.value = ''; first.textContent = placeholder;
  select.appendChild(first);
  values.forEach(v => {{
    const op = document.createElement('option');
    op.value = v; op.textContent = v;
    select.appendChild(op);
  }});
  if (values.includes(cur)) select.value = cur;
}}

function typeData() {{
  const type = qs('tripType') ? qs('tripType').value : '';
  return LOCATION_DATA[type] || null;
}}

function allSubAreas(data, regionName='') {{
  if (!data) return [];
  const regions = data.regions || {{}};
  const entries = regionName && regions[regionName] ? {{[regionName]: regions[regionName]}} : regions;
  const out = [];
  Object.values(entries).forEach(group => Object.keys(group).forEach(name => out.push(name)));
  return [...new Set(out)];
}}

function cityOptions(data, regionName='', subName='') {{
  if (!data) return [];
  const out = [];
  const regions = data.regions || {{}};
  const targetRegions = regionName && regions[regionName] ? {{[regionName]: regions[regionName]}} : regions;
  Object.entries(targetRegions).forEach(([_, group]) => {{
    Object.entries(group).forEach(([sub, cities]) => {{
      if (!subName || subName === sub) {{ out.push(sub); cities.forEach(c => out.push(c)); }}
    }});
  }});
  return [...new Set(out)];
}}

function updateGeoLabels() {{
  const data = typeData();
  if (qs('regionGroupLabelJa')) qs('regionGroupLabelJa').textContent = data ? data.group_ja : '地域 / 地方';
  if (qs('regionGroupLabelEn')) qs('regionGroupLabelEn').textContent = data ? data.group_en : 'Area / Region';
  if (qs('subAreaLabelJa')) qs('subAreaLabelJa').textContent = data ? data.sub_ja : '国 / 都道府県';
  if (qs('subAreaLabelEn')) qs('subAreaLabelEn').textContent = data ? data.sub_en : 'Country / Prefecture';
  if (qs('destLabelJa')) qs('destLabelJa').textContent = data ? data.city_ja : '目的地';
  if (qs('destLabelEn')) qs('destLabelEn').textContent = data ? data.city_en : 'Destination';
}}

function updateRegionGroup(resetSub) {{
  const sel = qs('regionGroup');
  const data = typeData();
  if (!sel) return;
  if (!data) {{ sel.disabled = true; fillOptions(sel, [], '旅行区分を先に選択'); updateDestList(); return; }}
  sel.disabled = false;
  fillOptions(sel, Object.keys(data.regions || {{}}), data.group_ja + 'を選択');
  if (resetSub) qs('regionGroup').dispatchEvent(new Event('change'));
}}

function updateSubArea(resetDest) {{
  const sub = qs('subArea');
  const data = typeData();
  if (!sub) return;
  if (!data) {{ sub.disabled = true; fillOptions(sub, [], '地域/地方を選択'); updateDestList(); return; }}
  sub.disabled = false;
  const regionValue = qs('regionGroup') ? qs('regionGroup').value : '';
  fillOptions(sub, allSubAreas(data, regionValue), data.sub_ja + 'を選択');
  if (resetDest && qs('destInput')) qs('destInput').value = '';
  updateDestList();
}}

function updateDestList() {{
  const list = qs('destList');
  if (!list) return;
  const data = typeData();
  const regionValue = qs('regionGroup') ? qs('regionGroup').value : '';
  const subValue = qs('subArea') ? qs('subArea').value : '';
  list.innerHTML = cityOptions(data, regionValue, subValue).map(v => `<option value="${{v}}"></option>`).join('');
}}

function airportCode(name) {{
  const v = (name || '').trim();
  if (!v) return '';
  if (AIRPORTS[v]) return AIRPORTS[v];
  if (/^[A-Za-z]{{3}}$/.test(v)) return v.toUpperCase();
  return '';
}}

function effectiveDest() {{
  const manual = qs('destInput') ? qs('destInput').value.trim() : '';
  const sub = qs('subArea') ? qs('subArea').value.trim() : '';
  const region = qs('regionGroup') ? qs('regionGroup').value.trim() : '';
  return manual || sub || region;
}}

function searchState() {{
  return {{
    tripType: qs('tripType') ? qs('tripType').value : '',
    origin: qs('originInput') ? qs('originInput').value.trim() : '',
    dest: effectiveDest(),
    depart: qs('departInput') ? qs('departInput').value : '',
    ret: qs('returnInput') ? qs('returnInput').value : '',
    adults: qs('adultsInput') && qs('adultsInput').value ? qs('adultsInput').value : '1',
    keyword: qs('keywordInput') ? qs('keywordInput').value.trim() : '',
    children: '0',
  }};
}}

function replaceParams(url, st) {{
  const map = {{
    '{{origin_air}}': airportCode(st.origin),
    '{{dest_air}}':   airportCode(st.dest),
    '{{origin_query}}': encodeURIComponent(st.origin),
    '{{dest_query}}':   encodeURIComponent(st.dest),
    '{{depart}}':     st.depart,
    '{{return}}':     st.ret,
    '{{adults}}':     st.adults,
    '{{children}}':   st.children,
    '{{keyword_query}}': encodeURIComponent(st.keyword || st.dest),
  }};
  let out = url;
  Object.keys(map).forEach(k => out = out.split(k).join(map[k]));
  return out;
}}

function hasMinimumInput(btn, st) {{
  const tabId = currentSearchTab;
  const needsOrigin = ['transport','package','compare'].includes(tabId);
  if (needsOrigin && !st.origin) return false;
  if (!st.dest) return false;
  if (btn.dataset.kind === 'flight') return !!(airportCode(st.origin) && airportCode(st.dest));
  return true;
}}

function applyLinks() {{
  const st = searchState();
  const line = qs('currentTarget');
  if (line) line.textContent = st.dest || st.origin || '未入力';

  document.querySelectorAll('.provider-btn').forEach(btn => {{
    const region = btn.dataset.region || 'both';
    const allowedRegion = !st.tripType || region === 'both' || region === st.tripType;
    const allowedPage = btn.dataset.page === currentSearchTab;
    const allowedKind = !btn.dataset.kind || btn.dataset.kind === currentCompareKind;
    const allowedInput = hasMinimumInput(btn, st);

    btn.style.display = (allowedRegion && allowedPage && allowedKind) ? '' : 'none';
    const enabled = allowedRegion && allowedKind && allowedInput;
    btn.classList.toggle('disabled', !enabled);
    if (enabled) {{ btn.href = replaceParams(btn.dataset.url, st); btn.setAttribute('target','_blank'); }}
    else {{ btn.href = '#'; btn.removeAttribute('target'); }}
  }});
}}

function setKeyword(v) {{
  if (qs('keywordInput')) qs('keywordInput').value = v;
  applyLinks();
}}

function resetForm() {{
  ['tripType','regionGroup','subArea','originInput','destInput','departInput','returnInput','adultsInput','keywordInput'].forEach(id => {{
    const el = qs(id); if (el) el.value = '';
  }});
  currentCompareKind = 'flight';
  document.querySelectorAll('.kind-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.kind === 'flight'));
  updateGeoLabels(); updateRegionGroup(true); applyLinks();
}}

// ── 入力イベント
if (qs('tripType')) qs('tripType').addEventListener('change', () => {{
  if (qs('regionGroup')) qs('regionGroup').value = '';
  if (qs('subArea')) qs('subArea').value = '';
  if (qs('destInput')) qs('destInput').value = '';
  updateGeoLabels(); updateRegionGroup(false); updateSubArea(true); applyLinks();
}});
if (qs('regionGroup')) qs('regionGroup').addEventListener('change', () => {{ if (qs('subArea')) qs('subArea').value = ''; updateSubArea(true); applyLinks(); }});
if (qs('subArea')) qs('subArea').addEventListener('change', () => {{ updateDestList(); applyLinks(); }});
['originInput','destInput','departInput','returnInput','adultsInput','keywordInput'].forEach(id => {{
  const el = qs(id);
  if (el) {{ el.addEventListener('input', applyLinks); el.addEventListener('change', applyLinks); }}
}});
document.querySelectorAll('.kind-btn').forEach(btn => btn.addEventListener('click', () => {{
  currentCompareKind = btn.dataset.kind;
  document.querySelectorAll('.kind-btn').forEach(el => el.classList.toggle('active', el === btn));
  applyLinks();
}}));

// ══ 国内ニュース ══════════════════════════════════════════════════
function renderDomestic() {{
  const q = (qs('domSearch')?.value || '').trim().toLowerCase();
  const pref = qs('domPref')?.value || '';
  const region = qs('domRegion')?.value || '';
  const src = qs('domSource')?.value || '';
  const filtered = DOM_ITEMS.filter(item => {{
    if (domActiveTab === '最新' && !item.is_new) return false;
    if (domActiveTab !== 'ALL' && domActiveTab !== '最新' && item.category !== domActiveTab) return false;
    if (q) {{ const bag = `${{item.title}} ${{item.summary}} ${{item.source_name}}`.toLowerCase(); if (!bag.includes(q)) return false; }}
    if (pref && item.prefecture !== pref) return false;
    if (region && item.region !== region) return false;
    if (src && item.source_name !== src) return false;
    return true;
  }});
  const summary = qs('domSummary');
  if (summary) summary.textContent = `${{filtered.length}}件表示 / 総件数 ${{DOM_ITEMS.length}}件`;
  const list = qs('domList');
  if (!list) return;
  if (!filtered.length) {{ list.innerHTML = "<div class='news-item'><div class='news-item-title'>該当データがありません</div></div>"; return; }}
  list.innerHTML = filtered.map(item => {{
    const tags = (item.tags || '').split(',').map(v => v.trim()).filter(Boolean).slice(0,3);
    const badges = [
      item.is_new ? "<span class='badge new'>NEW</span>" : '',
      `<span class='badge cat'>${{item.category}}</span>`,
      `<span class='badge loc'>${{item.prefecture}}</span>`,
      ...tags.map(t => `<span class='badge'>${{t}}</span>`)
    ].join('');
    return `<article class='news-item'><div class='news-item-title'><a href="${{item.link}}" target="_blank" rel="noopener">${{item.title}}</a></div><div class='news-item-meta'>${{badges}}</div><div class='news-item-body'>${{item.summary||''}}</div><div class='news-item-sub'>${{item.source_name}} ｜ ${{item.published_at||''}}</div></article>`;
  }}).join('');
}}

// 国内フィルタ select の populate
(function() {{
  function uniq(arr) {{ return [...new Set(arr.filter(Boolean))]; }}
  function fill(sel, vals) {{ uniq(vals).sort((a,b) => a.localeCompare(b,'ja')).forEach(v => {{ const o=document.createElement('option'); o.value=v; o.textContent=v; sel.appendChild(o); }}); }}
  const REGION_ORDER = ["北海道","東北","関東","中部","近畿","中国","四国","九州","沖縄"];
  const domPref = qs('domPref'); if(domPref) fill(domPref, DOM_ITEMS.map(x=>x.prefecture));
  const domRegion = qs('domRegion');
  if (domRegion) {{
    const existing = new Set(DOM_ITEMS.map(x=>x.region).filter(Boolean));
    REGION_ORDER.forEach(r => {{ if(!existing.has(r)) return; const o=document.createElement('option'); o.value=r; o.textContent=r; domRegion.appendChild(o); }});
  }}
  const domSource = qs('domSource'); if(domSource) fill(domSource, DOM_ITEMS.map(x=>x.source_name));
}})();
['domSearch','domPref','domRegion','domSource'].forEach(id => {{
  const el = qs(id);
  if (el) {{ el.addEventListener('input', renderDomestic); el.addEventListener('change', renderDomestic); }}
}});
qs('domReset')?.addEventListener('click', () => {{
  ['domSearch','domPref','domRegion','domSource'].forEach(id => {{ const el=qs(id); if(el) el.value=''; }});
  renderDomestic();
}});

// ══ 海外ニュース ══════════════════════════════════════════════════
function renderGlobal() {{
  const q = (qs('glbSearch')?.value || '').trim().toLowerCase();
  const region = qs('glbRegion')?.value || '';
  const country = qs('glbCountry')?.value || '';
  const src = qs('glbSource')?.value || '';
  const filtered = GLB_ITEMS.filter(item => {{
    if (glbActiveTab === '最新' && !item.is_new) return false;
    if (glbActiveTab !== 'ALL' && glbActiveTab !== '最新' && item.category !== glbActiveTab) return false;
    if (q) {{ const bag = `${{item.title}} ${{item.title_ja||''}} ${{item.summary}} ${{item.source_name}}`.toLowerCase(); if (!bag.includes(q)) return false; }}
    if (region && item.region !== region) return false;
    if (country && item.country !== country) return false;
    if (src && item.source_name !== src) return false;
    return true;
  }});
  const summary = qs('glbSummary');
  if (summary) summary.textContent = `${{filtered.length}}件表示 / 総件数 ${{GLB_ITEMS.length}}件`;
  const list = qs('glbList');
  if (!list) return;
  if (!filtered.length) {{ list.innerHTML = "<div class='news-item'><div class='news-item-title'>該当データがありません</div></div>"; return; }}
  list.innerHTML = filtered.map(item => {{
    const tags = (item.tags || '').split(',').map(v => v.trim()).filter(Boolean).slice(0,3);
    const isEnWithTr = item.lang === 'en' && !!item.title_ja;
    const titleHtml = isEnWithTr ? `<span class='tr-ja'>${{item.title_ja}}</span><span class='tr-en'>${{item.title}}</span>` : item.title;
    const summaryText = item.summary || '';
    const summaryHtml = (isEnWithTr && item.summary_ja) ? `<span class='tr-ja'>${{item.summary_ja}}</span><span class='tr-en'>${{summaryText}}</span>` : summaryText;
    const badges = [
      item.is_new ? "<span class='badge new'>NEW</span>" : '',
      `<span class='badge cat'>${{item.category}}</span>`,
      `<span class='badge loc'>${{item.country}}</span>`,
      item.lang === 'en' ? "<span class='badge lang-en'>EN</span>" : '',
      ...tags.map(t => `<span class='badge'>${{t}}</span>`)
    ].join('');
    return `<article class='news-item'><div class='news-item-title'><a href="${{item.link}}" target="_blank" rel="noopener">${{titleHtml}}</a></div><div class='news-item-meta'>${{badges}}</div><div class='news-item-body'>${{summaryHtml}}</div><div class='news-item-sub'>${{item.source_name}} ｜ ${{item.published_at||''}}</div></article>`;
  }}).join('');
}}

(function() {{
  function uniq(arr) {{ return [...new Set(arr.filter(Boolean))]; }}
  function fill(sel, vals) {{ uniq(vals).sort((a,b) => a.localeCompare(b,'ja')).forEach(v => {{ const o=document.createElement('option'); o.value=v; o.textContent=v; sel.appendChild(o); }}); }}
  const glbRegion = qs('glbRegion');
  if (glbRegion) {{
    const existing = new Set(GLB_ITEMS.map(x=>x.region).filter(Boolean));
    GLB_REGIONS.forEach(r => {{ if(!existing.has(r)) return; const o=document.createElement('option'); o.value=r; o.textContent=r; glbRegion.appendChild(o); }});
  }}
  const glbCountry = qs('glbCountry'); if(glbCountry) fill(glbCountry, GLB_ITEMS.map(x=>x.country));
  const glbSource = qs('glbSource'); if(glbSource) fill(glbSource, GLB_ITEMS.map(x=>x.source_name));
}})();
['glbSearch','glbRegion','glbCountry','glbSource'].forEach(id => {{
  const el = qs(id);
  if (el) {{ el.addEventListener('input', renderGlobal); el.addEventListener('change', renderGlobal); }}
}});
qs('glbReset')?.addEventListener('click', () => {{
  ['glbSearch','glbRegion','glbCountry','glbSource'].forEach(id => {{ const el=qs(id); if(el) el.value=''; }});
  renderGlobal();
}});

// ══ 共通 ══════════════════════════════════════════════════════════
function toggleLang() {{
  document.body.classList.toggle('en-mode');
  localStorage.setItem('tsp-lang', document.body.classList.contains('en-mode') ? 'en' : 'ja');
}}
if (localStorage.getItem('tsp-lang') === 'en') document.body.classList.add('en-mode');

document.querySelectorAll('.tab-btn').forEach(btn => btn.addEventListener('click', () => {{ const tabId = btn.dataset.tab; if (tabId) switchTab(tabId); }}));

// 初期化
updateGeoLabels();
updateRegionGroup(false);
updateSubArea(false);
applyLinks();
renderDomestic();
renderGlobal();
switchTab('overview');
"""

    # ── HTML 組み立て
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{e(APP_NAME)} | {e(SUBTITLE)}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">

  <!-- ── Topbar ── -->
  <div class="topbar">
    <div class="title">
      <h1>{e(APP_NAME)}<span class="title-suffix"> ｜ {e(SUBTITLE)}</span></h1>
      <div class="sub">更新日時：{e(UPDATED_AT)}</div>
    </div>
    <div class="actions">
      <button class="toggle" onclick="toggleLang()">JP / EN</button>
    </div>
  </div>

  <!-- ── Tab rows ── -->
  <div class="tab-rows">
    <div class="tab-row">
      {overview_tab_btn}
      <span class="tab-row-sep">🗾 国内</span>
      {dom_tab_btns}
      <span class="tab-row-sep">🌍 海外</span>
      {glb_tab_btns}
    </div>
    <div class="tab-row">
      <span class="tab-row-label">🔍 検索</span>
      {search_tab_btns}
    </div>
  </div>


  <!-- ══ 総合セクション ══ -->
  <div id="section-overview" class="content-section active">
    <div class="news-hero-row">
      {overview_kpis}
    </div>
    {overview_domestic}
    {overview_global}
  </div>

  <!-- ══ 検索セクション ══ -->
  <div id="section-search" class="content-section">
    <div class="panel card">
      <h2 id="searchPanelTitle">{e(SEARCH_TABS[0]["icon"])} {e(SEARCH_TABS[0]["label_ja"])}</h2>
      <!-- compare switcher -->
      <div id="compareSwitch" class="compare-switch" style="display:none">
        <button class="kind-btn active" type="button" data-kind="flight"><span class="ja">航空券</span><span class="en">Flights</span></button>
        <button class="kind-btn" type="button" data-kind="rail"><span class="ja">新幹線・鉄道</span><span class="en">Rail</span></button>
        <button class="kind-btn" type="button" data-kind="bus"><span class="ja">高速バス</span><span class="en">Bus</span></button>
      </div>
      <div class="search-grid">{fields_html}</div>
      <div class="control-row">
        <button class="secondary-btn" type="button" onclick="resetForm()"><span class="ja">リセット</span><span class="en">Reset</span></button>
      </div>
      <div class="provider-row">{all_provider_btns}</div>
      <div class="helper-line"><span class="ja">検索先:</span><span class="en">Search target:</span> <strong id="currentTarget">未入力</strong></div>
    </div>
    <div class="chips-panel card">
      <div class="chip-list" id="searchChipList"></div>
    </div>
    <datalist id="originList">{origin_opts}</datalist>
    <datalist id="destList"></datalist>
  </div>

  <!-- ══ 国内ニュースセクション ══ -->
  <div id="section-dom-news" class="content-section">
    <div class="news-layout">
      <div class="news-main card">
        <!-- フィルタコントロール -->
        <div class="news-ctrl">
          <input id="domSearch" type="text" placeholder="キーワード検索">
          <select id="domPref"><option value="">都道府県: 全件</option></select>
          <select id="domRegion"><option value="">地方: 全件</option></select>
          <select id="domSource"><option value="">取得元: 全件</option></select>
          <button id="domReset" class="reset-btn">リセット</button>
        </div>
        <div id="domSummary" class="news-summary"></div>
        <div id="domList" class="news-list"></div>
      </div>
    </div>
  </div>

  <!-- ══ 海外ニュースセクション ══ -->
  <div id="section-glb-news" class="content-section">
    <div class="news-layout">
      <div class="news-main card">
        <div class="news-ctrl">
          <input id="glbSearch" type="text" placeholder="キーワード検索">
          <select id="glbRegion"><option value="">地域: 全件</option></select>
          <select id="glbCountry"><option value="">国: 全件</option></select>
          <select id="glbSource"><option value="">取得元: 全件</option></select>
          <button id="glbReset" class="reset-btn">リセット</button>
        </div>
        <div id="glbSummary" class="news-summary"></div>
        <div id="glbList" class="news-list"></div>
      </div>
    </div>
  </div>

  <div style="margin-top:20px;color:var(--muted);font-size:12px;padding:0 4px">
    {e(APP_NAME)} — Generated at {e(UPDATED_AT)}
  </div>

</div><!-- /wrap -->
<script>{js}</script>
</body>
</html>"""


# ── メイン ────────────────────────────────────────────────────────
def main() -> None:
    t0 = time.monotonic()
    print("==============================================")
    print(f"  {APP_NAME}")
    print("==============================================")
    print("[1/3] Fetching news data in parallel...", flush=True)

    dom_result = glb_result = None

    def _fetch_dom():
        return domestic_news.fetch_and_get_data(refresh=True)

    def _fetch_glb():
        return global_news.fetch_and_get_data(refresh=True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        fut_dom = executor.submit(_fetch_dom)
        fut_glb = executor.submit(_fetch_glb)
        dom_result = fut_dom.result()
        glb_result = fut_glb.result()

    dom_items, dom_summary, dom_fetched, dom_inserted, dom_errors = dom_result
    glb_items, glb_summary, glb_fetched, glb_inserted, glb_errors = glb_result

    print(f"  Domestic: {dom_summary['total']} items | Global: {glb_summary['total']} items", flush=True)

    print("[2/3] Building index.html...", flush=True)
    html = build_page(
        dom_items, dom_summary, dom_fetched, dom_inserted, dom_errors,
        glb_items, glb_summary, glb_fetched, glb_inserted, glb_errors,
    )

    out_path = OUTPUT_DIR / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[3/3] Wrote: {out_path}", flush=True)
    print(f"\n⏱ 処理時間: {time.monotonic() - t0:.1f}秒", flush=True)


if __name__ == "__main__":
    main()
