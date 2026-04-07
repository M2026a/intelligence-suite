from __future__ import annotations

import json
import html
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from domestic_travel import build_domestic_page
from global_travel import build_global_page

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

JST = ZoneInfo("Asia/Tokyo")
UPDATED_AT = datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_json(SHARED / "config.json")
THEMES = load_json(SHARED / "themes.json")
TABS = load_json(SHARED / "tabs.json")
SOURCES = load_json(SHARED / "sources.json")

APP_NAME = CONFIG["app_name"]
SUBTITLE = CONFIG.get("subtitle", "")

LOCATION_DATA = {
    "domestic": {
        "group_ja": "地方", "group_en": "Region",
        "sub_ja": "都道府県", "sub_en": "Prefecture",
        "city_ja": "市・エリア", "city_en": "City / Area",
        "regions": {
            "北海道": {"北海道": ["札幌", "函館", "小樽", "旭川", "富良野", "登別", "洞爺湖"]},
            "東北": {
                "青森県": ["青森", "弘前", "八戸"], "岩手県": ["盛岡", "平泉", "花巻"],
                "宮城県": ["仙台", "松島", "石巻"], "秋田県": ["秋田", "角館", "田沢湖"],
                "山形県": ["山形", "蔵王", "銀山温泉"], "福島県": ["福島", "会津若松", "郡山", "いわき"],
            },
            "関東": {
                "東京都": ["東京", "新宿", "渋谷", "浅草", "銀座", "上野"],
                "神奈川県": ["横浜", "みなとみらい", "鎌倉", "箱根", "川崎"],
                "埼玉県": ["大宮", "川越", "秩父"], "千葉県": ["千葉", "舞浜", "成田", "木更津"],
                "茨城県": ["水戸", "ひたちなか", "つくば"], "栃木県": ["宇都宮", "日光", "那須"],
                "群馬県": ["高崎", "草津", "伊香保"],
            },
            "中部": {
                "新潟県": ["新潟", "越後湯沢", "佐渡"], "富山県": ["富山", "黒部", "立山"],
                "石川県": ["金沢", "加賀", "和倉温泉"], "福井県": ["福井", "あわら", "敦賀"],
                "山梨県": ["甲府", "河口湖", "山中湖"], "長野県": ["長野", "松本", "軽井沢", "白馬"],
                "岐阜県": ["岐阜", "高山", "下呂", "白川郷"], "静岡県": ["静岡", "熱海", "伊豆", "浜松"],
                "愛知県": ["名古屋", "栄", "犬山", "常滑"],
            },
            "近畿": {
                "三重県": ["伊勢", "鳥羽", "志摩"], "滋賀県": ["大津", "彦根", "長浜"],
                "京都府": ["京都", "嵐山", "祇園", "天橋立"],
                "大阪府": ["大阪", "梅田", "難波", "天王寺", "新大阪"],
                "兵庫県": ["神戸", "姫路", "有馬温泉", "城崎温泉"],
                "奈良県": ["奈良", "吉野", "橿原"], "和歌山県": ["和歌山", "白浜", "那智勝浦", "高野山"],
            },
            "中国": {
                "鳥取県": ["鳥取", "倉吉", "境港"], "島根県": ["松江", "出雲", "石見銀山"],
                "岡山県": ["岡山", "倉敷"], "広島県": ["広島", "宮島", "尾道", "福山"],
                "山口県": ["山口", "下関", "萩", "岩国"],
            },
            "四国": {
                "徳島県": ["徳島", "鳴門", "祖谷"], "香川県": ["高松", "琴平", "小豆島"],
                "愛媛県": ["松山", "道後温泉", "今治"], "高知県": ["高知", "四万十", "足摺岬"],
            },
            "九州・沖縄": {
                "福岡県": ["福岡", "博多", "天神", "北九州"], "佐賀県": ["佐賀", "嬉野", "唐津"],
                "長崎県": ["長崎", "佐世保", "ハウステンボス"], "熊本県": ["熊本", "阿蘇", "黒川温泉"],
                "大分県": ["別府", "由布院", "大分"], "宮崎県": ["宮崎", "高千穂", "日南"],
                "鹿児島県": ["鹿児島", "指宿", "屋久島"], "沖縄県": ["那覇", "恩納村", "石垣", "宮古島"],
            },
        },
    },
    "international": {
        "group_ja": "地域", "group_en": "Area",
        "sub_ja": "国", "sub_en": "Country",
        "city_ja": "都市・エリア", "city_en": "City / Area",
        "regions": {
            "東アジア": {
                "韓国": ["ソウル", "釜山", "済州", "仁川"], "台湾": ["台北", "高雄", "台中"],
                "中国": ["上海", "北京", "広州", "深圳"], "香港": ["香港"], "マカオ": ["マカオ"],
            },
            "東南アジア": {
                "タイ": ["バンコク", "プーケット", "チェンマイ"], "シンガポール": ["シンガポール"],
                "ベトナム": ["ホーチミン", "ハノイ", "ダナン"],
                "マレーシア": ["クアラルンプール", "ペナン", "コタキナバル"],
                "インドネシア": ["バリ", "ジャカルタ"], "フィリピン": ["マニラ", "セブ", "ボラカイ"],
            },
            "南アジア・中東": {
                "インド": ["デリー", "ムンバイ", "ベンガルール"],
                "UAE": ["ドバイ", "アブダビ"], "トルコ": ["イスタンブール", "カッパドキア"], "カタール": ["ドーハ"],
            },
            "ヨーロッパ": {
                "フランス": ["パリ", "ニース", "リヨン"], "イギリス": ["ロンドン", "マンチェスター", "エディンバラ"],
                "イタリア": ["ローマ", "ミラノ", "ヴェネツィア"], "スペイン": ["バルセロナ", "マドリード", "セビリア"],
                "ドイツ": ["ベルリン", "ミュンヘン", "フランクフルト"], "スイス": ["チューリッヒ", "ジュネーブ"],
            },
            "北米": {
                "アメリカ": ["ニューヨーク", "ロサンゼルス", "ホノルル", "ラスベガス"],
                "カナダ": ["バンクーバー", "トロント", "モントリオール"],
            },
            "中南米": {
                "メキシコ": ["メキシコシティ", "カンクン"], "ブラジル": ["サンパウロ", "リオデジャネイロ"],
                "ペルー": ["リマ", "クスコ"],
            },
            "オセアニア": {
                "オーストラリア": ["シドニー", "メルボルン", "ケアンズ"],
                "ニュージーランド": ["オークランド", "クイーンズタウン"],
                "グアム": ["グアム"], "サイパン": ["サイパン"],
            },
            "アフリカ": {
                "エジプト": ["カイロ"], "モロッコ": ["マラケシュ", "カサブランカ"],
                "南アフリカ": ["ケープタウン", "ヨハネスブルグ"],
            },
        },
    },
}

AIRPORTS = {
    "東京": "TYO", "羽田": "HND", "成田": "NRT", "東京都": "TYO",
    "大阪": "OSA", "関西": "KIX", "伊丹": "ITM", "新大阪": "OSA", "大阪府": "OSA",
    "名古屋": "NGO", "中部": "NGO", "愛知県": "NGO",
    "札幌": "CTS", "北海道": "CTS", "仙台": "SDJ", "宮城県": "SDJ",
    "青森県": "AOJ", "岩手県": "HNA", "秋田県": "AXT", "山形県": "GAJ", "福島県": "FKS",
    "福岡": "FUK", "博多": "FUK", "福岡県": "FUK", "佐賀県": "HSG", "長崎県": "NGS",
    "熊本県": "KMJ", "大分県": "OIT", "宮崎県": "KMI", "鹿児島県": "KOJ",
    "沖縄県": "OKA", "那覇": "OKA",
    "新潟県": "KIJ", "富山県": "TOY", "石川県": "KMQ", "福井県": "KMQ",
    "山梨県": "HND", "長野県": "MMJ", "岐阜県": "NGO", "静岡県": "FSZ",
    "神奈川県": "HND", "埼玉県": "TYO", "千葉県": "NRT", "茨城県": "IBR",
    "栃木県": "TYO", "群馬県": "TYO", "三重県": "NGO", "滋賀県": "KIX",
    "京都府": "UKY", "兵庫県": "UKB", "奈良県": "KIX", "和歌山県": "KIX",
    "鳥取県": "TTJ", "島根県": "IZO", "岡山県": "OKJ", "広島県": "HIJ", "山口県": "UBJ",
    "徳島県": "TKS", "香川県": "TAK", "愛媛県": "MYJ", "高知県": "KCZ",
    "韓国": "SEL", "ソウル": "SEL", "仁川": "ICN", "金浦": "GMP", "釜山": "PUS", "済州": "CJU",
    "台湾": "TPE", "台北": "TPE", "高雄": "KHH", "台中": "RMQ",
    "中国": "BJS", "北京": "BJS", "上海": "SHA", "広州": "CAN", "深圳": "SZX",
    "香港": "HKG", "マカオ": "MFM",
    "タイ": "BKK", "バンコク": "BKK", "プーケット": "HKT", "チェンマイ": "CNX",
    "シンガポール": "SIN", "ベトナム": "SGN", "ホーチミン": "SGN", "ハノイ": "HAN", "ダナン": "DAD",
    "マレーシア": "KUL", "クアラルンプール": "KUL", "ペナン": "PEN", "コタキナバル": "BKI",
    "インドネシア": "DPS", "バリ": "DPS", "ジャカルタ": "CGK",
    "フィリピン": "MNL", "マニラ": "MNL", "セブ": "CEB",
    "フランス": "PAR", "パリ": "PAR", "ニース": "NCE", "リヨン": "LYS",
    "イギリス": "LON", "ロンドン": "LON", "マンチェスター": "MAN", "エディンバラ": "EDI",
    "イタリア": "ROM", "ローマ": "ROM", "ミラノ": "MIL", "ヴェネツィア": "VCE",
    "スペイン": "BCN", "バルセロナ": "BCN", "マドリード": "MAD", "セビリア": "SVQ",
    "ドイツ": "FRA", "ベルリン": "BER", "ミュンヘン": "MUC", "フランクフルト": "FRA",
    "スイス": "ZRH", "チューリッヒ": "ZRH", "ジュネーブ": "GVA",
    "アメリカ": "NYC", "ニューヨーク": "NYC", "ロサンゼルス": "LAX", "ホノルル": "HNL", "ラスベガス": "LAS",
    "カナダ": "YVR", "バンクーバー": "YVR", "トロント": "YYZ", "モントリオール": "YUL",
    "メキシコ": "MEX", "メキシコシティ": "MEX", "カンクン": "CUN",
    "ブラジル": "SAO", "サンパウロ": "SAO", "リオデジャネイロ": "RIO",
    "ペルー": "LIM", "リマ": "LIM", "クスコ": "CUZ",
    "オーストラリア": "SYD", "シドニー": "SYD", "メルボルン": "MEL", "ケアンズ": "CNS",
    "ニュージーランド": "AKL", "オークランド": "AKL", "クイーンズタウン": "ZQN",
    "グアム": "GUM", "サイパン": "SPN",
    "UAE": "DXB", "ドバイ": "DXB", "アブダビ": "AUH",
    "トルコ": "IST", "イスタンブール": "IST", "カッパドキア": "NAV",
    "カタール": "DOH", "ドーハ": "DOH",
    "インド": "DEL", "デリー": "DEL", "ムンバイ": "BOM", "ベンガルール": "BLR",
    "エジプト": "CAI", "カイロ": "CAI", "モロッコ": "RAK", "マラケシュ": "RAK", "カサブランカ": "CMN",
    "南アフリカ": "CPT", "ケープタウン": "CPT", "ヨハネスブルグ": "JNB",
}

ORIGIN_SUGGESTIONS = [
    "東京", "羽田", "成田", "大阪", "関西", "伊丹", "新大阪", "名古屋", "中部",
    "札幌", "仙台", "福岡", "博多", "那覇", "ソウル", "釜山", "台北", "香港",
    "シンガポール", "バンコク", "パリ", "ロンドン", "ニューヨーク", "ロサンゼルス", "ホノルル",
]

QUICK_TAGS = {
    "index":     ["航空券", "ホテル", "観光", "グルメ", "駐車場", "eSIM"],
    "transport": ["直行便", "LCC", "新幹線", "夜行バス", "レンタカー"],
    "stay":      ["温泉", "駅近", "高級ホテル", "コスパ", "朝食"],
    "package":   ["航空券+ホテル", "ツアー", "週末", "家族旅行"],
    "compare":   ["最安", "直行便", "新幹線", "高速バス", "夜行バス"],
    "activity":  ["絶景", "テーマパーク", "美術館", "子ども", "雨の日"],
    "food":      ["ラーメン", "寿司", "焼肉", "カフェ", "朝食", "ディナー"],
    "support":   ["駐車場", "Wi‑Fi", "eSIM", "保険", "両替"],
    "ideas":     ["ひとり旅", "デート", "温泉旅", "弾丸旅", "食べ歩き"],
}

PAGE_FORM = {
    "index":     ["tripType", "regionGroup", "subArea", "origin", "dest", "depart", "return", "adults", "keyword"],
    "transport": ["tripType", "regionGroup", "subArea", "origin", "dest", "depart", "return", "adults"],
    "stay":      ["tripType", "regionGroup", "subArea", "dest", "depart", "return", "adults", "keyword"],
    "package":   ["tripType", "regionGroup", "subArea", "origin", "dest", "depart", "return", "adults"],
    "compare":   ["tripType", "regionGroup", "subArea", "origin", "dest", "depart", "return", "adults"],
    "activity":  ["tripType", "regionGroup", "subArea", "dest", "depart", "keyword"],
    "food":      ["tripType", "regionGroup", "subArea", "dest", "keyword"],
    "support":   ["tripType", "regionGroup", "subArea", "dest", "keyword"],
    "ideas":     ["tripType", "regionGroup", "subArea", "dest", "depart", "keyword"],
}

FIELD_META = {
    "tripType":    ("旅行区分",     "Trip type"),
    "regionGroup": ("地域 / 地方",  "Area / Region"),
    "subArea":     ("国 / 都道府県","Country / Prefecture"),
    "origin":      ("出発地",       "Origin"),
    "dest":        ("目的地",       "Destination"),
    "depart":      ("出発日",       "Depart"),
    "return":      ("帰着日",       "Return"),
    "adults":      ("大人",         "Adults"),
    "keyword":     ("キーワード",   "Keyword"),
}

# ── index専用タブ定義 ──────────────────────────────────────────────
INDEX_TABS = [
    {"id": "all",       "icon": "🔍", "label_ja": "すべて",   "label_en": "All"},
    {"id": "transport", "icon": "✈️", "label_ja": "移動",     "label_en": "Transport"},
    {"id": "stay",      "icon": "🏨", "label_ja": "宿泊",     "label_en": "Stay"},
    {"id": "package",   "icon": "🎫", "label_ja": "セット",   "label_en": "Packages"},
    {"id": "compare",   "icon": "⚖️", "label_ja": "比較",     "label_en": "Compare"},
    {"id": "activity",  "icon": "🎡", "label_ja": "観光",     "label_en": "Activities"},
    {"id": "food",      "icon": "🍽️", "label_ja": "食事",     "label_en": "Food"},
    {"id": "support",   "icon": "🧰", "label_ja": "サポート", "label_en": "Support"},
    {"id": "ideas",     "icon": "✨", "label_ja": "特集",     "label_en": "Ideas"},
]

# タブごとに有効なフィールド（False=グレーアウト）
INDEX_TAB_FIELDS = {
    "all":       {"origin": True,  "dest": True, "depart": True,  "return": True,  "adults": True,  "keyword": True},
    "transport": {"origin": True,  "dest": True, "depart": True,  "return": True,  "adults": True,  "keyword": False},
    "stay":      {"origin": False, "dest": True, "depart": True,  "return": True,  "adults": True,  "keyword": True},
    "package":   {"origin": True,  "dest": True, "depart": True,  "return": True,  "adults": True,  "keyword": False},
    "compare":   {"origin": True,  "dest": True, "depart": True,  "return": True,  "adults": True,  "keyword": False},
    "activity":  {"origin": False, "dest": True, "depart": True,  "return": False, "adults": False, "keyword": True},
    "food":      {"origin": False, "dest": True, "depart": False, "return": False, "adults": False, "keyword": True},
    "support":   {"origin": False, "dest": True, "depart": False, "return": False, "adults": False, "keyword": True},
    "ideas":     {"origin": False, "dest": True, "depart": True,  "return": False, "adults": False, "keyword": True},
}


def nav_html(active_id: str) -> str:
    items = []
    for page in TABS["pages"]:
        cls = "nav-link active" if page["id"] == active_id else "nav-link"
        items.append(
            f'<a class="{cls}" href="{page["id"]}.html">'
            f'<span class="icon">{html.escape(page["icon"])}</span>'
            f'<span class="ja">{html.escape(page["label_ja"])}</span>'
            f'<span class="en">{html.escape(page["label_en"])}</span></a>'
        )
    return "".join(items)


def render_field(name: str, add_data_field: bool = False) -> str:
    ja, en = FIELD_META[name]
    if name == "tripType":
        control = (
            '<select id="tripType">'
            '<option value="">選択してください</option>'
            '<option value="domestic">国内</option>'
            '<option value="international">海外</option>'
            '</select>'
        )
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

    attrs = ""
    data_attr = f' data-field="{name}"' if add_data_field else ""
    if name == "regionGroup":
        attrs = ' id="regionGroupWrap"'
        label_html = '<span id="regionGroupLabelJa">地域 / 地方</span><span id="regionGroupLabelEn">Area / Region</span>'
    elif name == "subArea":
        attrs = ' id="subAreaWrap"'
        label_html = '<span id="subAreaLabelJa">国 / 都道府県</span><span id="subAreaLabelEn">Country / Prefecture</span>'
    elif name == "dest":
        attrs = ' id="destWrap"'
        label_html = '<span id="destLabelJa">目的地</span><span id="destLabelEn">Destination</span>'
    else:
        label_html = f'<span class="ja">{ja}</span><span class="en">{en}</span>'

    return f'<label{attrs}{data_attr}>{label_html}{control}</label>'


def action_buttons(page_id: str) -> str:
    buttons = []
    for item in SOURCES.get(page_id, []):
        for provider in item.get("providers", []):
            extra = ""
            if provider.get("kind"):
                extra += f' data-kind="{html.escape(provider["kind"])}"'
            buttons.append(
                f'<a class="provider-btn" data-region="{provider.get("region", "both")}"'
                f'{extra} data-url="{html.escape(provider["url"], quote=True)}" '
                f'href="#" target="_blank" rel="noopener">{html.escape(provider["name"])}</a>'
            )
    return "".join(buttons)


def index_all_buttons() -> str:
    """indexページ用：全ソースのボタンにdata-page属性を付与"""
    buttons = []
    for page_id, items in SOURCES.items():
        for item in items:
            for provider in item.get("providers", []):
                extra = ""
                if provider.get("kind"):
                    extra += f' data-kind="{html.escape(provider["kind"])}"'
                buttons.append(
                    f'<a class="provider-btn" data-region="{provider.get("region", "both")}"'
                    f' data-page="{page_id}"{extra} '
                    f'data-url="{html.escape(provider["url"], quote=True)}" '
                    f'href="#" target="_blank" rel="noopener">{html.escape(provider["name"])}</a>'
                )
    return "".join(buttons)


def compare_switcher() -> str:
    return '''
    <div class="kind-switch" id="compareSwitch">
      <button class="kind-btn active" type="button" data-kind="flight"><span class="ja">航空券</span><span class="en">Flights</span></button>
      <button class="kind-btn" type="button" data-kind="rail"><span class="ja">新幹線・鉄道</span><span class="en">Rail</span></button>
      <button class="kind-btn" type="button" data-kind="bus"><span class="ja">高速バス</span><span class="en">Bus</span></button>
    </div>
    '''


def index_page_panel() -> str:
    """indexページ専用パネル：タブ式フルランチャー"""
    tab_btns = "".join(
        f'<button class="index-tab-btn{"  active" if t["id"] == "all" else ""}" '
        f'data-tab="{t["id"]}" type="button">'
        f'{t["icon"]} <span class="ja">{t["label_ja"]}</span>'
        f'<span class="en">{t["label_en"]}</span></button>'
        for t in INDEX_TABS
    )
    all_fields = ["tripType", "regionGroup", "subArea", "origin", "dest", "depart", "return", "adults", "keyword"]
    fields = "".join(render_field(name, add_data_field=True) for name in all_fields)
    origin_options = "".join(f'<option value="{html.escape(c)}"></option>' for c in ORIGIN_SUGGESTIONS)
    tags = "".join(
        f'<button class="chip-btn" type="button" onclick="setKeyword({json.dumps(tag, ensure_ascii=False)})">{html.escape(tag)}</button>'
        for tag in QUICK_TAGS.get("index", [])
    )
    return f'''
    <section class="panel card">
      <h2>🔍 <span class="ja">まとめ検索</span><span class="en">Quick Search</span></h2>
      <div class="index-tab-wrap">{tab_btns}</div>
      <div class="search-grid">{fields}</div>
      <div class="control-row">
        <button class="secondary-btn" type="button" onclick="resetForm()"><span class="ja">リセット</span><span class="en">Reset</span></button>
      </div>
      <div class="provider-row">{index_all_buttons()}</div>
      <div class="helper-line"><span class="ja">検索先:</span><span class="en">Search target:</span> <strong id="currentTarget">未入力</strong></div>
    </section>
    <section class="chips-panel card">
      <div class="section-label ja">よく使う候補</div>
      <div class="section-label en">Quick picks</div>
      <div class="chip-list">{tags}</div>
    </section>
    <datalist id="originList">{origin_options}</datalist>
    <datalist id="destList"></datalist>
    '''


def page_panel(page_id: str) -> str:
    page = next(p for p in TABS["pages"] if p["id"] == page_id)
    fields = "".join(render_field(name) for name in PAGE_FORM[page_id])
    origin_options = "".join(f'<option value="{html.escape(c)}"></option>' for c in ORIGIN_SUGGESTIONS)
    tags = "".join(
        f'<button class="chip-btn" type="button" onclick="setKeyword({json.dumps(tag, ensure_ascii=False)})">{html.escape(tag)}</button>'
        for tag in QUICK_TAGS.get(page_id, [])
    )
    compare_html = compare_switcher() if page_id == "compare" else ""
    note_html = (
        '<div class="compare-note ja">同じ条件で各サイトを開いて比較できます。</div>'
        '<div class="compare-note en">Open each site with the same conditions for side-by-side comparison.</div>'
        if page_id == "compare" else ""
    )
    return f'''
    <section class="panel card">
      <h2>{html.escape(page["icon"])} <span class="ja">{html.escape(page["title_ja"])}</span><span class="en">{html.escape(page["title_en"])}</span></h2>
      {compare_html}
      <div class="search-grid">{fields}</div>
      {note_html}
      <div class="control-row">
        <button class="secondary-btn" type="button" onclick="resetForm()"><span class="ja">リセット</span><span class="en">Reset</span></button>
      </div>
      <div class="provider-row">{action_buttons(page_id)}</div>
      <div class="helper-line"><span class="ja">検索先:</span><span class="en">Search target:</span> <strong id="currentTarget">未入力</strong></div>
    </section>
    <section class="chips-panel card">
      <div class="section-label ja">よく使う候補</div>
      <div class="section-label en">Quick picks</div>
      <div class="chip-list">{tags}</div>
    </section>
    <datalist id="originList">{origin_options}</datalist>
    <datalist id="destList"></datalist>
    '''


def build_page(page_id: str) -> str:
    if page_id == 'domestic_travel':
        return build_domestic_page(app_name=APP_NAME, subtitle=SUBTITLE, updated_at=UPDATED_AT, nav_html=nav_html(page_id), refresh=True)
    if page_id == 'global_travel':
        return build_global_page(app_name=APP_NAME, subtitle=SUBTITLE, updated_at=UPDATED_AT, nav_html=nav_html(page_id))
    page = next(p for p in TABS["pages"] if p["id"] == page_id)
    airports_json = json.dumps(AIRPORTS, ensure_ascii=False)
    location_json = json.dumps(LOCATION_DATA, ensure_ascii=False)
    title_suffix_html = f'<span class="title-suffix"> ｜ {html.escape(SUBTITLE)}</span>' if SUBTITLE else ""

    panel_html = index_page_panel() if page_id == "index" else page_panel(page_id)

    # index専用JS（タブ制御）
    index_tab_fields_json = json.dumps(INDEX_TAB_FIELDS, ensure_ascii=False)
    index_js = f"""
const INDEX_TAB_FIELDS = {index_tab_fields_json};
let currentIndexTab = 'all';
function applyIndexTab(tabId) {{
  currentIndexTab = tabId;
  document.querySelectorAll('.index-tab-btn').forEach(btn => {{
    btn.classList.toggle('active', btn.dataset.tab === tabId);
  }});
  const fields = INDEX_TAB_FIELDS[tabId] || INDEX_TAB_FIELDS.all;
  const fieldMap = [
    {{key:'origin',  id:'originInput'}},
    {{key:'dest',    id:'destInput'}},
    {{key:'depart',  id:'departInput'}},
    {{key:'return',  id:'returnInput'}},
    {{key:'adults',  id:'adultsInput'}},
    {{key:'keyword', id:'keywordInput'}},
  ];
  fieldMap.forEach(function(f) {{
    const el = qs(f.id);
    if (!el) return;
    const label = el.closest('label');
    const active = fields[f.key] !== false;
    if (label) label.classList.toggle('field-dimmed', !active);
    el.disabled = !active;
    if (!active && el.value) el.dataset.savedVal = el.value;
  }});
  document.querySelectorAll('.provider-btn[data-page]').forEach(btn => {{
    btn.dataset.tabHidden = (tabId !== 'all' && btn.dataset.page !== tabId) ? '1' : '';
  }});
  applyLinks();
}}
document.querySelectorAll('.index-tab-btn').forEach(btn => {{
  btn.addEventListener('click', function() {{ applyIndexTab(this.dataset.tab); }});
}});
""" if page_id == "index" else ""

    return f'''<!doctype html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(APP_NAME)} | {html.escape(page['label_ja'])}</title>
<style>
:root{{--accent:{THEMES['accent']};--accent2:{THEMES['accent2']};--bg:{THEMES['bg']};--panel:{THEMES['panel']};--line:{THEMES['line']};--text:{THEMES['text']};--muted:{THEMES['muted']};}}
*{{box-sizing:border-box}}
body{{margin:0;background:linear-gradient(180deg,#0b1020,#12182d);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Hiragino Sans','Yu Gothic UI',Meiryo,sans-serif}}
a{{color:inherit;text-decoration:none}}
.wrap{{max-width:1480px;width:min(1480px,calc(100% - 40px));margin:0 auto;padding:14px 20px}}
.topbar{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:12px}}
.title h1{{margin:0;font-size:22px;font-weight:800;display:flex;flex-wrap:wrap;align-items:baseline;gap:0}}
.title-suffix{{color:var(--muted);font-size:14px;font-weight:500}}
.sub{{margin-top:4px;color:var(--muted);font-size:12px}}
.actions{{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}}
.toggle{{border:1px solid var(--line);background:#101a2b;color:var(--text);padding:10px 14px;border-radius:999px;font-weight:700;cursor:pointer}}
.nav{{display:flex;gap:8px;flex-wrap:wrap;padding:8px 0 18px;margin-bottom:8px;border-bottom:1px solid var(--line)}}
.nav-link{{flex:0 0 auto;display:flex;gap:6px;align-items:center;padding:9px 13px;border-radius:14px;background:#101a2b;border:1px solid var(--line);color:var(--muted);font-size:13px}}
.nav-link.active{{color:#06111d;background:linear-gradient(135deg,var(--accent),var(--accent2));border-color:transparent}}
.nav-ext{{border-color:#3d5a8a;color:#7aadff}}
.card{{background:rgba(18,29,49,.97);border:1px solid var(--line);border-radius:22px;box-shadow:0 10px 28px rgba(0,0,0,.18)}}
.panel{{padding:20px 22px;margin:22px 6px 16px}}
.panel h2{{margin:0 0 14px;font-size:22px}}
.search-grid{{display:flex;flex-wrap:wrap;gap:10px;align-items:end}}
label{{display:flex;flex-direction:column;gap:7px;font-size:13px;color:var(--muted);transition:opacity .2s;flex:1 1 160px;min-width:110px}}
label:has(select){{flex:0 1 155px;max-width:175px}}
label.field-dimmed{{opacity:.3;pointer-events:none}}
label.field-dimmed input,label.field-dimmed select{{color:#4a5568;cursor:not-allowed}}
input,select{{width:100%;padding:11px 12px;border-radius:14px;border:1px solid var(--line);background:#0d1525;color:var(--text)}}
input::placeholder{{color:#718096}}
.control-row{{display:flex;justify-content:flex-end;margin-top:12px}}
.provider-row{{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}}
.provider-btn,.secondary-btn,.chip-btn,.kind-btn,.index-tab-btn{{display:inline-flex;align-items:center;justify-content:center;text-align:center;padding:11px 15px;border-radius:14px;border:1px solid var(--line);font-weight:700;min-height:46px;background:#7cb2ff;color:#06111d;cursor:pointer;transition:.15s ease}}
.secondary-btn,.chip-btn,.kind-btn{{background:#202847;color:var(--text)}}
.index-tab-btn{{background:#192035;color:var(--muted);font-size:13px;min-height:38px;padding:8px 13px}}
.index-tab-btn.active{{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#06111d;border-color:transparent}}
.provider-btn:hover,.secondary-btn:hover,.chip-btn:hover,.kind-btn:hover,.index-tab-btn:hover{{transform:translateY(-1px)}}
.provider-btn.disabled{{opacity:.45;pointer-events:none;background:#1b2440;color:#91a0bd}}
.index-tab-wrap{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid var(--line)}}
.kind-switch{{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 16px}}
.kind-btn.active{{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#06111d;border-color:transparent}}
.compare-note{{margin-top:-2px;margin-bottom:8px;color:var(--muted);font-size:13px}}
.helper-line{{margin-top:14px;text-align:right;color:var(--muted)}}
.helper-line strong{{color:var(--text)}}
.chips-panel{{padding:18px 22px;margin:0 6px 18px}}
.section-label{{margin-bottom:12px;font-weight:700}}
.chip-list{{display:flex;gap:10px;flex-wrap:wrap}}
.footer{{margin-top:18px;color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:0 6px}}
.en{{display:none}} body.en-mode .ja{{display:none !important}} body.en-mode .en{{display:initial !important}}
@media (max-width:760px){{.topbar{{flex-direction:column}} .actions{{justify-content:flex-start}} .helper-line{{text-align:left}} .control-row{{justify-content:flex-start}} .title h1{{font-size:20px}} .title-suffix{{font-size:12px}}}}
</style>
</head>
<body data-page="{page_id}">
<div class="wrap">
  <div class="topbar">
    <div class="title">
      <h1>{html.escape(APP_NAME)}{title_suffix_html}</h1>
      <div class="sub">更新日時：{UPDATED_AT}</div>
    </div>
    <div class="actions">
      <button class="toggle" onclick="toggleLang()">JP / EN</button>
    </div>
  </div>
  <nav class="nav">{nav_html(page_id)}</nav>
  {panel_html}
  <div class="footer">
    <div class="ja">条件を入れて各外部サイトを比較できます。</div>
    <div class="en">Use your conditions to open and compare each external site.</div>
    <div class="ja">都市・市区町村は自由入力でも使えます。</div>
    <div class="en">Cities and areas also support free-form input.</div>
  </div>
</div>
<script>
const AIRPORTS = {airports_json};
const LOCATION_DATA = {location_json};
let currentCompareKind = 'flight';
function qs(id){{return document.getElementById(id)}}
function fillOptions(select, values, placeholder){{
  if(!select) return;
  const current = select.value;
  select.innerHTML = '';
  const first = document.createElement('option');
  first.value = '';
  first.textContent = placeholder;
  select.appendChild(first);
  values.forEach(v => {{
    const op = document.createElement('option');
    op.value = v;
    op.textContent = v;
    select.appendChild(op);
  }});
  if(values.includes(current)) select.value = current;
}}
function typeData(){{
  const type = qs('tripType') ? qs('tripType').value : '';
  return LOCATION_DATA[type] || null;
}}
function allSubAreas(data, regionName=''){{
  if(!data) return [];
  const regions = data.regions || {{}};
  const entries = regionName && regions[regionName] ? {{[regionName]: regions[regionName]}} : regions;
  const out = [];
  Object.values(entries).forEach(group => Object.keys(group).forEach(name => out.push(name)));
  return Array.from(new Set(out));
}}
function cityOptions(data, regionName='', subName=''){{
  if(!data) return [];
  const out = [];
  const regions = data.regions || {{}};
  const targetRegions = regionName && regions[regionName] ? {{[regionName]: regions[regionName]}} : regions;
  Object.entries(targetRegions).forEach(([_, group]) => {{
    Object.entries(group).forEach(([sub, cities]) => {{
      if(!subName || subName === sub) {{
        out.push(sub);
        cities.forEach(city => out.push(city));
      }}
    }})
  }})
  return Array.from(new Set(out));
}}
function updateGeoLabels(){{
  const data = typeData();
  const regionJa = data ? data.group_ja : '地域 / 地方';
  const regionEn = data ? data.group_en : 'Area / Region';
  const subJa = data ? data.sub_ja : '国 / 都道府県';
  const subEn = data ? data.sub_en : 'Country / Prefecture';
  const destJa = data ? data.city_ja : '目的地';
  const destEn = data ? data.city_en : 'Destination';
  if(qs('regionGroupLabelJa')) qs('regionGroupLabelJa').textContent = regionJa;
  if(qs('regionGroupLabelEn')) qs('regionGroupLabelEn').textContent = regionEn;
  if(qs('subAreaLabelJa')) qs('subAreaLabelJa').textContent = subJa;
  if(qs('subAreaLabelEn')) qs('subAreaLabelEn').textContent = subEn;
  if(qs('destLabelJa')) qs('destLabelJa').textContent = destJa;
  if(qs('destLabelEn')) qs('destLabelEn').textContent = destEn;
  if(qs('destInput')) {{
    qs('destInput').placeholder = data ? (data.city_ja === '市・エリア' ? '例: 新宿, 札幌, 博多' : '例: ソウル, パリ, 台北') : '旅行区分を選ぶと候補が絞れます';
  }}
}}
function updateRegionGroup(resetSub){{
  const sel = qs('regionGroup');
  const data = typeData();
  if(!sel) return;
  if(!data) {{
    sel.disabled = true;
    fillOptions(sel, [], '旅行区分を先に選択');
    if(qs('subArea')) {{ qs('subArea').disabled = true; fillOptions(qs('subArea'), [], '地域/地方を選択'); }}
    updateDestList();
    return;
  }}
  sel.disabled = false;
  fillOptions(sel, Object.keys(data.regions || {{}}), data.group_ja + 'を選択');
  if(resetSub) qs('regionGroup').dispatchEvent(new Event('change'));
}}
function updateSubArea(resetDest){{
  const sub = qs('subArea');
  const data = typeData();
  if(!sub) return;
  if(!data) {{
    sub.disabled = true;
    fillOptions(sub, [], '地域/地方を選択');
    updateDestList();
    return;
  }}
  sub.disabled = false;
  const regionValue = qs('regionGroup') ? qs('regionGroup').value : '';
  const options = allSubAreas(data, regionValue);
  fillOptions(sub, options, data.sub_ja + 'を選択');
  if(resetDest && qs('destInput')) qs('destInput').value = '';
  updateDestList();
}}
function updateDestList(){{
  const list = qs('destList');
  if(!list) return;
  const data = typeData();
  const regionValue = qs('regionGroup') ? qs('regionGroup').value : '';
  const subValue = qs('subArea') ? qs('subArea').value : '';
  const values = cityOptions(data, regionValue, subValue);
  list.innerHTML = values.map(v => `<option value="${{v}}"></option>`).join('');
}}
function airportCode(name){{
  const value = (name || '').trim();
  if(!value) return '';
  if(AIRPORTS[value]) return AIRPORTS[value];
  if(/^[A-Za-z]{{3}}$/.test(value)) return value.toUpperCase();
  return '';
}}
function effectiveDest(){{
  const manual = qs('destInput') ? qs('destInput').value.trim() : '';
  const sub = qs('subArea') ? qs('subArea').value.trim() : '';
  const region = qs('regionGroup') ? qs('regionGroup').value.trim() : '';
  return manual || sub || region;
}}
function state(){{
  return {{
    tripType: qs('tripType') ? qs('tripType').value : '',
    origin: qs('originInput') ? qs('originInput').value.trim() : '',
    dest: effectiveDest(),
    depart: qs('departInput') ? qs('departInput').value : '',
    ret: qs('returnInput') ? qs('returnInput').value : '',
    adults: qs('adultsInput') && qs('adultsInput').value ? qs('adultsInput').value : '1',
    keyword: qs('keywordInput') ? qs('keywordInput').value.trim() : '',
    children: '0'
  }};
}}
function replaceParams(url, st){{
  const map = {{
    '{{origin_air}}': airportCode(st.origin),
    '{{dest_air}}': airportCode(st.dest),
    '{{origin_query}}': encodeURIComponent(st.origin),
    '{{dest_query}}': encodeURIComponent(st.dest),
    '{{depart}}': st.depart,
    '{{return}}': st.ret,
    '{{adults}}': st.adults,
    '{{children}}': st.children,
    '{{keyword_query}}': encodeURIComponent(st.keyword || st.dest)
  }};
  let out = url;
  Object.keys(map).forEach(k => out = out.split(k).join(map[k]));
  return out;
}}
function compareEnabled(btn){{
  if(!btn.dataset.kind) return true;
  return btn.dataset.kind === currentCompareKind;
}}
function pageNeedsOrigin(pageId){{
  return ['transport', 'package', 'compare'].includes(pageId);
}}
function pageNeedsDest(pageId){{
  return ['transport', 'stay', 'package', 'compare', 'activity', 'food', 'support', 'ideas'].includes(pageId);
}}
function hasMinimumInput(btn, st){{
  const pageId = document.body.dataset.page;
  if(pageId === 'index') {{
    const tab = typeof currentIndexTab !== 'undefined' ? currentIndexTab : 'all';
    if(tab === 'all') {{
      if(btn.dataset.kind === 'flight') return !!(airportCode(st.origin) && airportCode(st.dest));
      return !!st.dest;
    }}
    const needsOrigin = ['transport','package','compare'].includes(tab);
    if(needsOrigin && !st.origin) return false;
    if(!st.dest) return false;
    if(btn.dataset.kind === 'flight') return !!(airportCode(st.origin) && airportCode(st.dest));
    return true;
  }}
  if(pageNeedsOrigin(pageId) && !st.origin) return false;
  if(pageNeedsDest(pageId) && !st.dest) return false;
  if(btn.dataset.kind === 'flight') return !!(airportCode(st.origin) && airportCode(st.dest));
  return true;
}}
function applyLinks(){{
  const st = state();
  const line = qs('currentTarget');
  if(line) line.textContent = st.dest || st.origin || '未入力';
  document.querySelectorAll('.provider-btn').forEach(btn => {{
    const region = btn.dataset.region || 'both';
    const allowedRegion = !st.tripType ? true : (region === 'both' || region === st.tripType);
    const allowedCompare = compareEnabled(btn);
    const allowedInput = hasMinimumInput(btn, st);
    const tabHidden = btn.dataset.tabHidden === '1';
    const allowed = allowedRegion && allowedCompare && allowedInput;
    btn.classList.toggle('disabled', !allowed);
    if(allowed) {{
      btn.href = replaceParams(btn.dataset.url, st);
      btn.setAttribute('target', '_blank');
    }} else {{
      btn.href = '#';
      btn.removeAttribute('target');
    }}
    btn.style.display = (allowedRegion && allowedCompare && !tabHidden) ? 'inline-flex' : 'none';
  }});
}}
function setKeyword(v){{
  if(qs('keywordInput')) qs('keywordInput').value = v;
  applyLinks();
}}
function resetForm(){{
  ['tripType','regionGroup','subArea','originInput','destInput','departInput','returnInput','adultsInput','keywordInput'].forEach(id => {{
    const el = qs(id);
    if(el) el.value = '';
  }});
  currentCompareKind = 'flight';
  document.querySelectorAll('.kind-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.kind === currentCompareKind));
  updateGeoLabels();
  updateRegionGroup(true);
  applyLinks();
}}
function toggleLang(){{
  document.body.classList.toggle('en-mode');
  localStorage.setItem('tsp-lang', document.body.classList.contains('en-mode') ? 'en' : 'ja');
}}
if(qs('tripType')) qs('tripType').addEventListener('change', () => {{
  if(qs('regionGroup')) qs('regionGroup').value = '';
  if(qs('subArea')) qs('subArea').value = '';
  if(qs('destInput')) qs('destInput').value = '';
  updateGeoLabels();
  updateRegionGroup(false);
  updateSubArea(true);
  applyLinks();
}});
if(qs('regionGroup')) qs('regionGroup').addEventListener('change', () => {{
  if(qs('subArea')) qs('subArea').value = '';
  updateSubArea(true);
  applyLinks();
}});
if(qs('subArea')) qs('subArea').addEventListener('change', () => {{ updateDestList(); applyLinks(); }});
['originInput','destInput','departInput','returnInput','adultsInput','keywordInput'].forEach(id => {{
  const el = qs(id);
  if(el) {{ el.addEventListener('input', applyLinks); el.addEventListener('change', applyLinks); }}
}});
document.querySelectorAll('.kind-btn').forEach(btn => btn.addEventListener('click', () => {{
  currentCompareKind = btn.dataset.kind;
  document.querySelectorAll('.kind-btn').forEach(el => el.classList.toggle('active', el === btn));
  applyLinks();
}}));
if(localStorage.getItem('tsp-lang') === 'en') document.body.classList.add('en-mode');
{index_js}
// 年4桁入力後に自動で月フィールドへ移動
(function(){{
  ['departInput','returnInput'].forEach(function(id){{
    var el=qs(id);
    if(!el) return;
    var dc=0;
    el.addEventListener('keydown',function(e){{
      if(/^[0-9]$/.test(e.key)){{
        dc++;
        if(dc===4){{
          var self=this;
          setTimeout(function(){{
            self.dispatchEvent(new KeyboardEvent('keydown',{{key:'ArrowRight',keyCode:39,bubbles:true}}));
          }},30);
          dc=0;
        }}
      }} else if(e.key!=='Backspace'&&e.key!=='Delete'){{
        dc=0;
      }}
    }});
  }});
}})();
updateGeoLabels();
updateRegionGroup(false);
updateSubArea(false);
applyLinks();
</script>
</body>
</html>'''


def main() -> None:
    t0 = time.monotonic()
    pages = TABS["pages"]
    total = len(pages)
    print(f"  - Loading config... ({total} pages scheduled)", flush=True)

    results: dict[str, str] = {}
    build_errors: list[str] = []

    def _build(page: dict) -> tuple[str, str]:
        pid = page["id"]
        return pid, build_page(pid)

    # domestic_travel は内部で RSS 並列取得を行うため、他のページと同時実行して待ち時間を有効活用
    max_workers = min(4, total)
    print(f"  - Page generation: {max_workers} workers", flush=True)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_build, page): page["id"] for page in pages}
        completed = 0
        for future in as_completed(future_map):
            completed += 1
            page_id = future_map[future]
            try:
                pid, html_text = future.result()
                results[pid] = html_text
                print(f"  - [{completed}/{total}] Built: {pid}.html", flush=True)
            except Exception as exc:
                build_errors.append(f"{page_id}: {exc}")
                print(f"  [WARN] [{completed}/{total}] {page_id}.html failed → {exc}", flush=True)

    print(f"  - Writing {len(results)} pages...", flush=True)
    for page_id, html_text in results.items():
        (OUTPUT_DIR / f"{page_id}.html").write_text(html_text, encoding="utf-8")
        print(f"  - Wrote: {page_id}.html", flush=True)

    elapsed = time.monotonic() - t0
    print(f"[{UPDATED_AT}] Done. {len(results)}/{total} pages in {elapsed:.1f}s.", flush=True)
    if build_errors:
        for err in build_errors:
            print(f"  [WARN] {err}", flush=True)


if __name__ == "__main__":
    main()
