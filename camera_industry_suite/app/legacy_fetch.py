
from __future__ import annotations
import subprocess, sys, json, re, html, hashlib, sqlite3, time, unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

def ensure_package(import_name: str, package_name: str | None = None) -> None:
    package = package_name or import_name
    try:
        __import__(import_name)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

ensure_package("feedparser")
ensure_package("requests")
ensure_package("deep_translator", "deep-translator")

import feedparser, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared"
APPDIR = Path(__file__).parent
OUTPUT = ROOT / "output"
LOGS = ROOT / "logs"
OUTPUT.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)

JST = ZoneInfo("Asia/Tokyo")

CONFIG = json.loads((SHARED / "config.json").read_text(encoding="utf-8"))
APP_NAME = CONFIG["app_name"]
DB_FILE = OUTPUT / CONFIG["db_name"]
CACHE_FILE = OUTPUT / "translate_cache.json"
HISTORY_DAYS = int(CONFIG.get("history_days", 21))
NEW_HOURS = int(CONFIG.get("new_badge_hours", 8))
MAX_ITEMS = int(CONFIG.get("max_items_per_source", 25))
TRANSLATE_TOP_N = int(CONFIG.get("translate_top_n", 20))
USER_AGENT = "Mozilla/5.0"
APP_KIND = "camera" if "camera" in APP_NAME.lower() else "pc"
CAMERA_CORE_TERMS = ["camera","lens","mirrorless","dslr","photography","photo","firmware","sensor","autofocus","lumix","eos","nikkor","alpha","gfx","x100","z mount","rf mount","mft","micro four thirds","full frame","カメラ","レンズ","ミラーレス","一眼","写真","撮影","センサー","af","ファームウェア","lumix"]
PC_CORE_TERMS = ["pc","laptop","notebook","desktop","workstation","gaming pc","mini pc","cpu","processor","gpu","graphics","chip","ai pc","npu","motherboard","ssd","windows","linux","surface","thinkpad","macbook","geforce","rtx","radeon","ryzen","core ultra","snapdragon x","ノートpc","デスクトップ","cpu","gpu","半導体","チップ","windows","ai pc","npu","ワークステーション","ゲーミングpc"]
NO_DATE_LARGE_FEED_THRESHOLD = 100
BROAD_SOURCE_LIMIT = 120
PC_NEGATIVE_TERMS = ["smartphone","phone","android phone","earbuds","headphones","smartwatch","watch","wearable","vacuum","robot vacuum","projector","camera phone","car audio","soundbar","tv","television","monitor arm","mwc","xiaomi 15","galaxy s","iphone","pixel 9","oneplus","イヤホン","ヘッドホン","スマートウォッチ","スマホ","スマートフォン","タブレット単体","掃除機","テレビ"]
CAMERA_NEGATIVE_TERMS = ["washing machine","refrigerator","air conditioner","microwave","tv","television","美容","冷蔵庫","洗濯機","エアコン","電子レンジ","テレビ","住宅設備"]



def get_source_history_days(src: dict) -> int:
    name = src.get("name", "")
    days = HISTORY_DAYS
    if APP_KIND == "camera":
        if src.get("official") and normalize_region(src.get("region", "Global")) == "JP":
            days = max(days, 180)
        if "ソニー α 国内" in name:
            days = max(days, 720)
        if "Panasonic" in name:
            days = max(days, 240)
    else:
        if src.get("official"):
            days = max(days, 60)
    return days

def negative_hit(text: str) -> bool:
    s = lower(text)
    negs = CAMERA_NEGATIVE_TERMS if APP_KIND == "camera" else PC_NEGATIVE_TERMS
    return any(t in s for t in negs)

def is_broad_source(source_name: str) -> bool:
    s = lower(source_name)
    if APP_KIND == "pc":
        return any(k in s for k in ["notebookcheck", "tom's hardware", "ars technica"])
    return any(k in s for k in ["petapixel", "photo rumors"])

def get_source_accept_cap(src: dict, raw_feed_count: int) -> int | None:
    name = lower(src.get("name", ""))
    if APP_KIND == "pc":
        if "notebookcheck" in name:
            return 60
        if "tom's hardware" in name:
            return 32
        if "ars technica" in name:
            return 10
        if "lenovo" in name:
            return 20
    else:
        if "ソニー α 国内" in src.get("name", ""):
            return 18
        if "panasonic" in name:
            return 18
        if "デジカメ watch" in name:
            return 20
    if raw_feed_count > 250:
        return min(BROAD_SOURCE_LIMIT, 80)
    return None

def source_specific_brand_bonus(text: str, source_name: str) -> bool:
    s = lower(text)
    n = lower(source_name)
    mapping = {
        "ソニー α 国内": ["α", "alpha", "vlogcam", "fx3", "fx30", "a7", "a9", "a1", "a6700", "a7c", "zv-e", "zv-e1", "zv-e10", "ilce-", "ilme-", "fe ", "eマウント", "e mount", "g master", "g lens", "sel", "交換レンズ", "デジタル一眼カメラ", "ミラーレス一眼", "シネマライン", "シネマカメラ", "クリエイター", "イメージセンサー", "撮像", "動画制作", "映像制作"],
        "panasonic newsroom japan": ["lumix", "ルミックス", "dc-s", "dc-g", "dc-gh", "dc-gh", "gh", "gh7", "g9", "g100", "s1", "s1r", "s1rii", "s5", "s9", "tz", "fz", "hc-x", "ag-", "aw-ue", "ak-", "bs1h", "bgh1", "eva1", "varicam", "leica dg", "lマウント", "l mount", "マイクロフォーサーズ", "mft", "フルサイズミラーレス", "デジタルカメラ", "デジタル一眼", "ミラーレス一眼", "交換レンズ", "業務用カメラ", "シネマカメラ", "映像制作", "動画撮影", "映像ソリューション", "放送", "業務用映像", "ライブ配信"],
        "pc watch": ["windows", "cpu", "gpu", "ノートpc", "デスクトップ", "ai pc", "ssd", "半導体", "intel", "amd", "nvidia", "geforce", "radeon", "ryzen", "core ultra", "snapdragon x", "surface", "thinkpad"],
        "窓の杜": ["windows", "アップデート", "セキュリティ", "ソフト", "アプリ", "ブラウザ", "microsoft", "explorer", "copilot", "edge", "office", "onedrive"],
        "itmedia pc user": ["pc", "ノートpc", "デスクトップ", "windows", "cpu", "gpu", "レビュー", "実機", "surface", "thinkpad", "macbook", "ゲーミングpc", "mini pc"],
    }
    for key, vals in mapping.items():
        if key.lower() == n:
            return any(v.lower() in s for v in vals)
    return False

def get_core_terms():
    return CAMERA_CORE_TERMS if APP_KIND == "camera" else PC_CORE_TERMS

def source_hint_keywords(source_name: str):
    s = lower(source_name)
    if APP_KIND == "camera":
        mapping = {
            "nikon": ["nikon","nikkor","zf","z6","z8","z9"],
            "canon": ["canon","eos","rf"],
            "sony": ["sony","alpha","α","a7","a9","a1","fx3","fx30","vlogcam","zv-e","zv-e1","zv-e10","ilce-","fe ","e mount","eマウント","g master","g lens","sel"],
            "panasonic": ["panasonic","lumix","ルミックス","gh","gh7","g9","g100","s1","s1r","s1rii","s5","s9","dc-s","dc-g","l mount","lマウント","マイクロフォーサーズ","フルサイズミラーレス","デジタルカメラ","交換レンズ","業務用カメラ","シネマカメラ","映像制作","動画撮影","ライブ配信"],
            "fuji": ["fujifilm","x100","gfx","x-t","x-h"],
            "43": ["om system","olympus","micro four thirds","mft","zuiko"],
            "leica": ["leica","q3","sl3","m11"],
            "photo rumors": ["camera","lens","canon","nikon","sony","fujifilm","leica","sigma","tamron","om system","panasonic","ricoh"],
            "dpreview": ["camera","lens","photography"],
            "petapixel": ["camera","lens","photography"]
        }
    else:
        mapping = {
            "windows": ["windows","surface","copilot+","copilot pc"],
            "nvidia": ["nvidia","geforce","rtx","gpu","graphics"],
            "intel": ["intel","core ultra","xeon","arc","processor"],
            "lenovo": ["lenovo","thinkpad","thinkbook","legion","yoga","ideapad"],
            "amd": ["amd","ryzen","radeon","epyc","threadripper"],
            "pc watch": ["pc","ノートpc","デスクトップ","cpu","gpu","ssd","windows","ai pc","半導体"],
            "itmedia": ["pc","ノートpc","デスクトップ","windows","cpu","gpu","レビュー","実機"],
            "窓の杜": ["windows","アプリ","ソフト","セキュリティ","アップデート","ブラウザ","microsoft","explorer"],
            "ars technica": ["pc","windows","linux","cpu","gpu","chip"],
            "tom's hardware": ["pc","cpu","gpu","graphics","laptop","desktop","ssd","monitor"],
            "notebookcheck": ["laptop","notebook","gaming laptop","cpu","gpu","ryzen","core ultra","snapdragon x","macbook","surface","thinkpad","zenbook","rog","workstation","mini pc"],
            "videocardz": ["gpu","graphics","rtx","radeon","geforce"]
        }
    for k, vals in mapping.items():
        if k in s:
            return vals
    return []

def is_relevant_item(text: str, categories: list[str], brand_hits: list[str], source_name: str, source_type: str) -> bool:
    s = lower(text)
    core_hit = any(t in s for t in get_core_terms())
    category_hit = any(c != "その他" for c in categories)
    brand_hit = any(b != "その他" for b in brand_hits)
    hint_terms = source_hint_keywords(source_name)
    hint_hit = any(lower(t) in s for t in hint_terms) if hint_terms else False
    source_bonus = source_specific_brand_bonus(text, source_name)
    broad = is_broad_source(source_name)
    neg = negative_hit(text)
    if neg and not brand_hit:
        return False
    if APP_KIND == "camera":
        if source_type == 'rumor':
            return core_hit or brand_hit or hint_hit or source_bonus or 'rumor' in s or 'リーク' in s or '噂' in s
        if 'ソニー α 国内' in source_name:
            sony_title_ok = source_bonus or hint_hit or brand_hit or core_hit or ('sony' in s and ('α' in text or 'alpha' in s))
            sony_extra = any(k in s for k in ['vlogcam','zv-e','zv-e1','zv-e10','ilce-','ilme-','sel','g master','eマウント','交換レンズ','デジタル一眼','ミラーレス一眼','シネマライン','シネマカメラ','映像制作','動画制作','撮像','クリエイター'])
            return sony_title_ok or sony_extra
        if 'Panasonic Newsroom Japan' in source_name:
            pana_strong = any(k in s for k in ['lumix','ルミックス','dc-s','dc-g','dc-gh','gh7','g9','g100','s1','s1r','s1rii','s5','s5ii','s5iix','s9','tz','fz','leica dg','leica dc','lマウント','l mount','マイクロフォーサーズ','mft','フルサイズミラーレス','交換レンズ'])
            pana_video = any(k in s for k in ['hc-x','ag-','aw-ue','ak-','bs1h','bgh1','eva1','varicam','proav','broadcast','cinema','camcorder','業務用カメラ','シネマカメラ','映像ソリューション','業務用映像','動画撮影','映像制作','放送','配信','ライブ配信','ライブ制作'])
            pana_imaging = any(k in s for k in ['カメラ','写真','撮影','撮像','イメージング','ミラーレス一眼','デジタル一眼','デジタルカメラ','クリエイター','映像','レンズ'])
            pana_url_hint = any(k in s for k in ['/lumix/','lumix','/imaging/','/proav/','/broadcast/','/camera/','/camcorder/'])
            if source_bonus or hint_hit or pana_strong or pana_video or pana_imaging or pana_url_hint:
                return True
            return (brand_hit and (category_hit or hint_hit or pana_imaging or pana_video or pana_url_hint)) or (core_hit and (category_hit or hint_hit or pana_imaging or pana_video or pana_url_hint))
        if broad:
            return (core_hit or brand_hit or source_bonus) and (category_hit or hint_hit or brand_hit)
        return (core_hit and (category_hit or brand_hit or hint_hit or source_bonus)) or (brand_hit and (category_hit or hint_hit or source_bonus)) or (hint_hit and (category_hit or brand_hit or source_bonus)) or source_bonus
    else:
        if source_type == 'rumor':
            return (core_hit or brand_hit or hint_hit or source_bonus) and not neg
        if broad:
            return (core_hit or brand_hit or source_bonus) and not neg
        return ((core_hit and (category_hit or brand_hit or hint_hit or source_bonus)) or (brand_hit and (category_hit or hint_hit or source_bonus)) or (hint_hit and (category_hit or brand_hit or source_bonus)) or source_bonus) and not neg

def log(msg:str): print(msg, flush=True)

def fetch_feed_content(src: dict):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    url = src["url"]
    tried = []
    candidates = [url]
    if src.get("name") == "VideoCardz":
        candidates = [url, "https://videocardz.com/feed", "https://videocardz.com/rss-feed"]
    last_err = None
    for candidate in list(dict.fromkeys(candidates)):
        tried.append(candidate)
        try:
            r = requests.get(candidate, headers={**headers, "Referer": "https://videocardz.com/" if "videocardz.com" in candidate else "https://www.google.com/"}, timeout=20)
            r.raise_for_status()
            return candidate, r.content
        except Exception as e:
            last_err = e
            continue
    raise last_err

def load_json(path, default):
    if Path(path).exists():
        try: return json.loads(Path(path).read_text(encoding="utf-8"))
        except: pass
    return default

def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def allowed(link, domains):
    host=(urlparse(link).hostname or "").lower()
    if host.startswith("www."): host=host[4:]
    for d in domains or []:
        d=d.lower()
        if d.startswith("www."): d=d[4:]
        if host==d or host.endswith("."+d): return True
    return not domains

def is_new(iso):
    if not iso: return False
    try:
        return datetime.now(JST) - datetime.fromisoformat(iso) <= timedelta(hours=NEW_HOURS)
    except: return False

def lower(x): return (x or "").lower()

def normalize_region(value: str) -> str:
    s = lower(value)
    if s in ('jp', 'japan', '日本', 'domestic', 'japanese'):
        return 'JP'
    return 'Global'

def classify(text, defs):
    hits=[]
    s=lower(text)
    for k,v in defs.items():
        kws=[lower(x) for x in v.get("keywords_en",[])+v.get("keywords_ja",[])]
        if any(w and w in s for w in kws):
            hits.append(k)
    return hits or ["その他"]

def brands(text, defs):
    hits=[]
    s=lower(text)
    for k,v in defs.items():
        if any(lower(w) in s for w in v):
            hits.append(k)
    return hits or ["その他"]

def normalize_dedup_text(text: str) -> str:
    s = unicodedata.normalize("NFKC", lower(text or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠ー]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def make_content_key(item: dict) -> str:
    title_key = normalize_dedup_text(item.get("title", ""))
    summary_key = normalize_dedup_text(item.get("summary", ""))
    title_key = re.sub(r"\b(news|newsroom|official|press|release|watch|impress|itmedia|dpreview|petapixel|videocardz|ars|technica|notebookcheck|rumors?|blog|storyhub|review|hands on|gallery)\b", " ", title_key)
    title_key = re.sub(r"\s+", " ", title_key).strip()
    if len(title_key) < 16:
        title_key = (title_key + " " + " ".join(summary_key.split()[:12])).strip()
    day = ""
    pub = item.get("pub_dt", "")
    if pub:
        day = pub[:10]
    title_tokens = title_key.split()[:18]
    summary_tokens = summary_key.split()[:12]
    return f"{day}|{' '.join(title_tokens)}|{' '.join(summary_tokens)}"[:220]

def choose_better_item(a: dict, b: dict) -> dict:
    def rank(x: dict):
        return (
            int(bool(x.get("official"))),
            int(x.get("score", 0)),
            int(bool(x.get("is_new"))),
            x.get("pub_dt", ""),
            len(x.get("summary", "")),
        )
    return a if rank(a) >= rank(b) else b

def make_source_key(link: str) -> str:
    host = (urlparse(link).hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    path = re.sub(r'/+$', '', urlparse(link).path or '')
    return f"{host}{path}"

def dedup_items(items: list[dict]) -> tuple[list[dict], int, int]:
    raw_count = len(items)
    by_link = {}
    for item in items:
        key = make_source_key(item.get("link", ""))
        if not key:
            continue
        by_link[key] = choose_better_item(by_link[key], item) if key in by_link else item
    link_unique = list(by_link.values())
    by_content = {}
    for item in link_unique:
        key = make_content_key(item)
        by_content[key] = choose_better_item(by_content[key], item) if key in by_content else item
    content_unique = list(by_content.values())
    content_unique.sort(key=lambda x: (x.get("score", 0), x.get("pub_dt", "")), reverse=True)
    return content_unique, raw_count, len(link_unique)

def fetch_all():
    sources = json.loads((SHARED/"sources.json").read_text(encoding="utf-8"))
    themes = json.loads((SHARED/"legacy_camera_themes.json").read_text(encoding="utf-8"))["themes"]
    brand_defs = json.loads((SHARED/"brands.json").read_text(encoding="utf-8"))
    items=[]
    source_stats=[]
    total_sources=len(sources)
    max_workers = min(8, total_sources)
    log(f"    🚀 並列取得: {total_sources} ソース / {max_workers} workers")

    def _fetch_one(idx_src):
        idx, src = idx_src
        log(f"    [{idx}/{total_sources}] {src['name']} 取得開始")
        try:
            r=requests.get(src["url"], headers={"User-Agent":USER_AGENT}, timeout=20)
            r.raise_for_status()
            feed=feedparser.parse(r.content)
            return idx, src, feed, None
        except Exception as e:
            log(f"[WARN] [{idx}/{total_sources}] {src['name']} ERR {e}")
            return idx, src, None, e

    fetch_results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_fetch_one, (i, s)): i for i, s in enumerate(sources, start=1)}
        for f in as_completed(futs):
            idx, src, feed, err = f.result()
            fetch_results[idx] = (src, feed, err)

    for idx in sorted(fetch_results):
        src, feed, err = fetch_results[idx]
        if err is not None:
            source_stats.append({"name":src["name"],"region":normalize_region(src.get("region","Global")),"source_type":src.get("source_type","media"),"raw_feed":0,"accepted":0,"status":"error"})
            continue

        raw_feed_count = len(getattr(feed, "entries", []))
        accepted = 0
        skipped_missing_date = 0
        skipped_irrelevant = 0
        skipped_old = 0
        skipped_cap = 0
        entries = list(getattr(feed, "entries", []))
        source_history_days = get_source_history_days(src)
        source_cap = get_source_accept_cap(src, raw_feed_count)
        if "ソニー α 国内" in src["name"]:
            source_history_days = 9999
        if "Panasonic Newsroom Japan" in src["name"]:
            source_history_days = max(source_history_days, 365)
        for entry in entries:
            title=clean_text(getattr(entry,"title",""))
            summary=clean_text(getattr(entry,"summary",getattr(entry,"description","")))
            link=getattr(entry,"link","")
            if not title or not link or not allowed(link, src.get("allowed_domains",[])):
                continue
            pub_dt=None
            try:
                if getattr(entry,"published_parsed",None):
                    pub_dt=datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).astimezone(JST)
                elif getattr(entry,"updated_parsed",None):
                    pub_dt=datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc).astimezone(JST)
            except:
                pass
            if raw_feed_count >= NO_DATE_LARGE_FEED_THRESHOLD and not pub_dt and 'ソニー α 国内' not in src['name'] and 'Panasonic Newsroom Japan' not in src['name']:
                skipped_missing_date += 1
                continue
            if pub_dt:
                age_days = (datetime.now(JST)-pub_dt).days
                if "ソニー α 国内" in src["name"]:
                    # Sony国内RSSは公開日フィールドが古いアーカイブ時刻になることがあるため、期間外では落とさない
                    pass
                elif "Panasonic Newsroom Japan" in src["name"]:
                    if age_days > source_history_days:
                        skipped_old += 1
                        continue
                elif age_days > source_history_days:
                    skipped_old += 1
                    continue
            text = title + " " + summary + " " + link + " " + src.get("name", "")
            cats = classify(text, themes)
            b_hits = brands(text, brand_defs)
            if not is_relevant_item(text, cats, b_hits, src["name"], src.get("source_type","media")):
                skipped_irrelevant += 1
                continue
            if source_cap is not None and accepted >= source_cap:
                skipped_cap += 1
                continue
            item={
                "id":"item-"+hashlib.md5(link.encode()).hexdigest()[:12],
                "source":src["name"],
                "lang":src.get("lang","en"),
                "region":normalize_region(src.get("region","Global")),
                "official":bool(src.get("official",False)),
                "source_type":src.get("source_type","media"),
                "title":title,
                "title_ja":title,
                "summary":summary[:320],
                "summary_ja":summary[:320],
                "link":link,
                "published":pub_dt.strftime("%Y-%m-%d %H:%M") if pub_dt else "",
                "pub_dt":pub_dt.isoformat() if pub_dt else "",
                "categories":cats,
                "brands":b_hits,
            }
            item["is_new"]=is_new(item["pub_dt"])
            score=1 + (2 if item["official"] else 0) + (1 if item["source_type"]=="rumor" else 0) + (1 if item["is_new"] else 0) + (1 if any(c != "その他" for c in cats) else 0) + (1 if any(b != "その他" for b in b_hits) else 0)
            item["score"]=min(score,7)
            items.append(item)
            accepted += 1

        source_stats.append({
            "name": src["name"],
            "region": normalize_region(src.get("region","Global")),
            "source_type": src.get("source_type","media"),
            "raw_feed": raw_feed_count,
            "accepted": accepted,
            "status": "ok",
            "skipped_missing_date": skipped_missing_date,
            "skipped_irrelevant": skipped_irrelevant,
            "skipped_old": skipped_old,
            "skipped_cap": skipped_cap,
        })
        log(f"    [{idx}/{total_sources}] {src['name']} feed={raw_feed_count} 採用={accepted} 除外(日付なし)={skipped_missing_date} 除外(期間外)={skipped_old} 除外(関連外)={skipped_irrelevant} 除外(調整)={skipped_cap}")

    items, raw_count, link_unique_count = dedup_items(items)
    log(f"  Accepted before dedup: {raw_count}")
    log(f"  Unique by link: {link_unique_count}")
    log(f"  Unique by content: {len(items)}")
    # translate
    cache=load_json(CACHE_FILE,{})
    trans_targets = [x for x in items if x["lang"] != "ja"][:TRANSLATE_TOP_N]
    if trans_targets:
        log(f"  Progress 2/3 | 翻訳準備中... 対象={len(trans_targets)}")
    tr=GoogleTranslator(source="auto", target="ja")
    n=0
    for x in trans_targets:
        kt="t:"+hashlib.md5(x["title"].encode()).hexdigest()
        ks="s:"+hashlib.md5(x["summary"].encode()).hexdigest()
        try:
            if kt not in cache: cache[kt]=tr.translate(x["title"])
            if ks not in cache: cache[ks]=tr.translate(x["summary"])
            x["title_ja"]=cache.get(kt,x["title"])
            x["summary_ja"]=cache.get(ks,x["summary"])
        except:
            pass
        n+=1
        if n == 1 or n % 5 == 0 or n == len(trans_targets):
            log(f"  Progress 2/3 | 翻訳中... {n}/{len(trans_targets)}")
    if trans_targets:
        log("  Progress 2/3 | 翻訳キャッシュ保存中...")
    save_json(CACHE_FILE,cache)
    feed_total = sum(x["raw_feed"] for x in source_stats if x["status"]=="ok")
    accepted_total = sum(x["accepted"] for x in source_stats if x["status"]=="ok")
    stats={
        "accepted_before_dedup": raw_count,
        "feed_total": feed_total,
        "accepted_total": accepted_total,
        "display_total": len(items),
        "unique_by_link": link_unique_count,
        "unique_by_content": len(items),
        "source_total_check": accepted_total,
        "app_kind": APP_KIND,
        "source_stats": source_stats,
    }
    return items, themes, brand_defs, stats

def init_db():
    con=sqlite3.connect(DB_FILE)
    cur=con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, source TEXT, lang TEXT, region TEXT, source_type TEXT, title TEXT, title_ja TEXT, summary TEXT, summary_ja TEXT, link TEXT, published TEXT, pub_dt TEXT, categories TEXT, brands TEXT, is_new INTEGER, score INTEGER)")
    con.commit()
    return con

def save_items(con, items):
    cur=con.cursor()
    for x in items:
        cur.execute("INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            x["id"],x["source"],x["lang"],x["region"],x["source_type"],x["title"],x["title_ja"],x["summary"],x["summary_ja"],x["link"],x["published"],x["pub_dt"],"|".join(x["categories"]),"|".join(x["brands"]),int(x["is_new"]),x["score"]
        ))
    con.commit()

CSS = """
:root{--bg:#0d1016;--panel:#151a23;--panel2:#1b2230;--line:#2a3446;--text:#ebf0f8;--muted:#9ba9bc;--accent:#4ea1ff;--accent2:#1e2f49}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:'Segoe UI',Meiryo,sans-serif}
a{color:inherit;text-decoration:none}header{position:sticky;top:0;z-index:50;background:rgba(13,16,22,.96);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.wrap{max-width:1480px;margin:0 auto;padding:14px 20px}.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}.title h1{margin:0;font-size:22px;font-weight:800}.title .sub{margin-top:4px;color:var(--muted);font-size:12px}
.right-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.page-lang-btn{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:12px;padding:9px 14px;font-size:13px;cursor:pointer}.page-lang-btn.active{background:var(--accent2);border-color:var(--accent);color:#eaf4ff}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.nav a{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(255,255,255,.10);background:rgba(255,255,255,.03);padding:10px 18px;border-radius:999px;font-size:13px;font-weight:700}.nav a.active{background:linear-gradient(135deg,#4ea1ff,#5a86ff);color:#fff}
.main{max-width:1480px;margin:18px auto;padding:0 20px 40px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:16px}.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}.stat .num{font-size:26px;font-weight:800;margin-bottom:4px}.stat .label{color:var(--muted);font-size:12px}.stat .icon{font-size:22px;margin-bottom:8px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;align-items:center}.flt-btn{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:10px;padding:7px 13px;font-size:13px;cursor:pointer}.flt-btn.active{background:var(--accent2);border-color:var(--accent);color:#eaf4ff}
.search{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:10px;padding:7px 12px;font-size:13px;min-width:220px}.filter-label{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}.grid.list{grid-template-columns:1fr}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:10px}.card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.card-meta{display:flex;gap:6px;flex-wrap:wrap;color:var(--muted);font-size:12px;align-items:center}.card-lang-toggle{display:flex;gap:5px}.card-lang-btn{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:8px;padding:4px 9px;font-size:12px;cursor:pointer}.card-lang-btn.active{background:var(--accent2);border-color:var(--accent);color:#eaf4ff}
.card h3{margin:0;font-size:15px;line-height:1.5;font-weight:700}.summary{color:#ced7e6;font-size:13px;line-height:1.6}.lang-pane.hidden{display:none!important}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:auto}.tag{display:inline-block;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:600;border:1px solid transparent}
.tag-official{background:#173126;border-color:#2f7254;color:#c6f1dc}.tag-media{background:#1e2f49;border-color:#4ea1ff;color:#cae2ff}.tag-rumor{background:#2e1a1a;border-color:#b85b6b;color:#ffc9d2}.tag-region-jp{background:#1a2e1a;border-color:#2ecc71;color:#a8ffcc}.tag-region-global{background:#2e2a1a;border-color:#f39c12;color:#ffe0a2}.tag-score{background:#1e1a3a;border-color:#a78bfa;color:#e0d9ff}.tag-brand{background:#1c2435;border-color:#566b9b;color:#d2ddff}.tag-new{background:#1a2e1a;border-color:#2ecc71;color:#2ecc71}
.lang-badge{font-size:11px;padding:2px 6px;border-radius:4px;background:#2a3446;color:var(--muted)}.lang-badge.ja{background:#173126;color:#c6f1dc}.lang-badge.en{background:#1e2f49;color:#cae2ff}
.open-btn{display:inline-flex;justify-content:center;align-items:center;background:var(--accent2);border:1px solid var(--accent);color:#eaf4ff;border-radius:10px;padding:9px 14px;font-size:13px;font-weight:700;margin-top:4px}
.theme-section{margin-bottom:28px}.theme-header{display:flex;align-items:center;gap:10px;margin-bottom:12px}.theme-badge{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:999px;font-size:13px;font-weight:700;border:1px solid transparent}
.source-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.source-card{background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:14px}.source-card .src-name{font-weight:800;font-size:15px;margin-bottom:8px}.source-card .src-count{font-size:22px;font-weight:800;color:var(--accent)}.mini-note{color:var(--muted);font-size:12px;margin-top:8px}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}.stats .stat{background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:12px}.stats .num{display:block;font-size:24px;font-weight:800}.stats .lbl{display:block;color:var(--muted);font-size:12px;margin-top:4px}
footer{max-width:1480px;margin:0 auto;padding:0 20px 28px;color:var(--muted);font-size:12px}
@media(max-width:700px){.wrap,.main,footer{padding-left:12px;padding-right:12px}.grid{grid-template-columns:1fr}.search{min-width:160px;width:100%}.card-top{flex-direction:column}}
"""

LANG_JS = """
<script>
function setPageLang(lang){
  document.getElementById('btnPageEN').classList.toggle('active', lang==='en');
  document.getElementById('btnPageJA').classList.toggle('active', lang==='ja');
  document.querySelectorAll('.item-card').forEach(function(card){
    if((card.dataset.lang||'en')==='ja') return;
    var enPane=card.querySelector('.lang-en');
    var jaPane=card.querySelector('.lang-ja');
    var enBtn=card.querySelector('.card-lang-btn[data-l="en"]');
    var jaBtn=card.querySelector('.card-lang-btn[data-l="ja"]');
    if(lang==='ja'){ enPane.classList.add('hidden'); jaPane.classList.remove('hidden'); enBtn.classList.remove('active'); jaBtn.classList.add('active'); }
    else { jaPane.classList.add('hidden'); enPane.classList.remove('hidden'); jaBtn.classList.remove('active'); enBtn.classList.add('active'); }
  });
}
document.addEventListener('click', function(e){
  var btn=e.target.closest('.card-lang-btn');
  if(!btn) return;
  var card=btn.closest('.item-card');
  card.querySelectorAll('.card-lang-btn').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  var enPane=card.querySelector('.lang-en');
  var jaPane=card.querySelector('.lang-ja');
  if(btn.dataset.l==='ja'){ enPane.classList.add('hidden'); jaPane.classList.remove('hidden'); }
  else { jaPane.classList.add('hidden'); enPane.classList.remove('hidden'); }
});
</script>
"""

def page_start(active, stamp):
    nav=[("index.html","📌 メイン"),("pickup.html","⭐ 注目記事"),("categories.html","🗂 カテゴリ別"),("brands.html","🏷 ブランド別"),("market.html","📊 市場モニター"),("analysis.html","🧠 分析")]
    nav_html="".join("<a href='%s' class='%s'>%s</a>"%(href, "active" if active==href else "", label) for href,label in nav)
    return "<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>%s</title><style>%s</style></head><body><header><div class='wrap'><div class='top'><div class='title'><h1>%s</h1><div class='sub'>完成 %s / 収集・分類・可視化ダッシュボード</div></div><div class='right-controls'><button id='btnPageEN' class='page-lang-btn active' onclick=\"setPageLang('en')\">English</button><button id='btnPageJA' class='page-lang-btn' onclick=\"setPageLang('ja')\">日本語</button></div></div><nav class='nav'>%s</nav></div></header>"%(APP_NAME,CSS,APP_NAME,stamp,nav_html)
def page_end(stamp): return LANG_JS+"<footer>Generated: %s / %s</footer></body></html>"%(stamp,APP_NAME)

def card(x, themes):
    type_cls="tag-official" if x["source_type"]=="official" else ("tag-rumor" if x["source_type"]=="rumor" else "tag-media")
    type_label={"official":"公式","media":"メディア","rumor":"噂"}.get(x["source_type"],x["source_type"])
    region_cls="tag-region-jp" if x["region"]=="JP" else "tag-region-global"
    theme_tags=""
    for t in x["categories"]:
        td=themes.get(t,{})
        color=td.get("color","#636e72"); icon=td.get("icon","")
        theme_tags += "<span class='tag' style='background:%s22;border-color:%s;color:%s'>%s %s</span>"%(color,color,color,icon,html.escape(t))
    brand_tags="".join("<span class='tag tag-brand'>%s</span>"%html.escape(b) for b in x["brands"] if b!="その他")
    return "<div class='card item-card' data-lang='%s' data-region='%s' data-type='%s' data-source='%s' data-categories='%s' data-brands='%s' data-isnew='%s'><div class='card-top'><div class='card-meta'><span class='lang-badge %s'>%s</span><span>%s</span><span>•</span><span>%s</span></div><div class='card-lang-toggle'><button class='card-lang-btn active' data-l='en'>EN</button><button class='card-lang-btn' data-l='ja'>JA</button></div></div><div class='lang-pane lang-en'><h3>%s</h3><div class='summary'>%s</div></div><div class='lang-pane lang-ja hidden'><h3>%s</h3><div class='summary'>%s</div></div><div class='tags'><span class='tag %s'>%s</span><span class='tag %s'>%s</span><span class='tag tag-score'>★%s</span>%s%s%s</div><a class='open-btn' href='%s' target='_blank' rel='noopener'>記事を開く</a></div>" % (
        x["lang"], x["region"], x["source_type"], html.escape(x["source"]), html.escape("|".join(x["categories"])), html.escape("|".join(x["brands"])), "true" if x["is_new"] else "false",
        "ja" if x["lang"]=="ja" else "en", "🇯🇵 日本語" if x["lang"]=="ja" else "🌐 English", html.escape(x["source"]), html.escape(x["published"]),
        html.escape(x["title"]), html.escape(x["summary"]), html.escape(x["title_ja"]), html.escape(x["summary_ja"]),
        type_cls, type_label, region_cls, "JP" if x["region"]=="JP" else "Global", x["score"], "<span class='tag tag-new'>NEW</span>" if x["is_new"] else "", theme_tags, brand_tags, html.escape(x["link"])
    )

def render_index(items, themes, brand_defs, stats):
    stamp=datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    theme_counts=Counter(t for x in items for t in x["categories"])
    brand_counts=Counter(b for x in items for b in x["brands"] if b!="その他")
    source_list=sorted(set(x["source"] for x in items))
    theme_btns="<button class='flt-btn flt-category active' data-category='all'>📋 すべて</button>"+"".join("<button class='flt-btn flt-category' data-category='%s'>%s %s (%s)</button>"%(html.escape(k),themes[k].get("icon",""),html.escape(k),theme_counts.get(k,0)) for k in themes if theme_counts.get(k,0))
    brand_btns="<button class='flt-btn flt-brand active' data-brand='all'>🏷 すべて</button>"+"".join("<button class='flt-btn flt-brand' data-brand='%s'>%s (%s)</button>"%(html.escape(k),html.escape(k),brand_counts.get(k,0)) for k in brand_defs if brand_counts.get(k,0))
    source_btns="<button class='flt-btn flt-source active' data-source='all'>すべて</button>"+"".join("<button class='flt-btn flt-source' data-source='%s'>%s</button>"%(html.escape(s),html.escape(s)) for s in source_list)
    cards="".join(card(x,themes) for x in items)
    js="""
<script>
(function(){
var fCategory='all',fBrand='all',fRegion='all',fType='all',fLang='all',fSource='all',fView='cards',fNew=false,search='';
function apply(){
var visible=0;
document.querySelectorAll('.item-card').forEach(function(card){
var show=true;
if(fCategory!=='all' && !(card.dataset.categories||'').split('|').includes(fCategory)) show=false;
if(fBrand!=='all' && !(card.dataset.brands||'').split('|').includes(fBrand)) show=false;
if(fRegion!=='all' && (card.dataset.region||'')!==fRegion) show=false;
if(fType!=='all' && (card.dataset.type||'')!==fType) show=false;
if(fLang!=='all' && (card.dataset.lang||'')!==fLang) show=false;
if(fSource!=='all' && (card.dataset.source||'')!==fSource) show=false;
if(fNew && (card.dataset.isnew||'')!=='true') show=false;
if(search && !(card.textContent||'').toLowerCase().includes(search.toLowerCase())) show=false;
card.style.display=show?'':'none';
if(show) visible++;
});
var total=document.getElementById('mainVisibleCount');
if(total) total.textContent=visible;
document.getElementById('itemGrid').classList.toggle('list', fView==='list');
}
function bind(sel, cb){
document.querySelectorAll(sel).forEach(function(btn){btn.addEventListener('click',function(){document.querySelectorAll(sel).forEach(function(b){b.classList.remove('active');});btn.classList.add('active');cb(btn);apply();});});
}
bind('.flt-category', function(btn){fCategory=btn.dataset.category;});
bind('.flt-brand', function(btn){fBrand=btn.dataset.brand;});
bind('.flt-region', function(btn){fRegion=btn.dataset.region;});
bind('.flt-type', function(btn){fType=btn.dataset.type;});
bind('.flt-lang', function(btn){fLang=btn.dataset.lang;});
bind('.flt-source', function(btn){fSource=btn.dataset.source;});
bind('.flt-view', function(btn){fView=btn.dataset.view;});
document.getElementById('btnNewOnly').addEventListener('click',function(){fNew=!fNew; this.classList.toggle('active',fNew); apply();});
document.getElementById('searchBox').addEventListener('input',function(){search=this.value;apply();});
})();
</script>"""
    body="<div class='main'><div class='stat-grid'><div class='stat'><div class='icon'>📰</div><div class='num'>%s</div><div class='label'>取得件数</div></div><div class='stat'><div class='icon'>📥</div><div class='num'>%s</div><div class='label'>採用件数</div></div><div class='stat'><div class='icon'>✅</div><div class='num' id='mainVisibleCount'>%s</div><div class='label'>表示記事数</div></div><div class='stat'><div class='icon'>🆕</div><div class='num'>%s</div><div class='label'>新着</div></div><div class='stat'><div class='icon'>🏛</div><div class='num'>%s</div><div class='label'>公式ソース</div></div><div class='stat'><div class='icon'>🇯🇵</div><div class='num'>%s</div><div class='label'>国内記事</div></div><div class='stat'><div class='icon'>👀</div><div class='num'>%s</div><div class='label'>噂系</div></div></div><div class='panel'><div class='mini-note'>取得件数は各ソースのfeed件数合計、採用件数は条件通過後の合計、表示記事数は重複整理後の件数です。</div><div class='filters'>%s</div><div class='filters'>%s</div><div class='filters'><span class='filter-label'>地域:</span><button class='flt-btn flt-region active' data-region='all'>全部</button><button class='flt-btn flt-region' data-region='JP'>日本</button><button class='flt-btn flt-region' data-region='Global'>海外</button><span class='filter-label'>種別:</span><button class='flt-btn flt-type active' data-type='all'>全部</button><button class='flt-btn flt-type' data-type='official'>公式</button><button class='flt-btn flt-type' data-type='media'>メディア</button><button class='flt-btn flt-type' data-type='rumor'>噂</button><span class='filter-label'>言語:</span><button class='flt-btn flt-lang active' data-lang='all'>全部</button><button class='flt-btn flt-lang' data-lang='ja'>日本語</button><button class='flt-btn flt-lang' data-lang='en'>English</button></div><div class='filters'><span class='filter-label'>ソース:</span>%s</div><div class='filters'><button class='flt-btn flt-view active' data-view='cards'>▦ カード</button><button class='flt-btn flt-view' data-view='list'>≡ リスト</button><button class='flt-btn' id='btnNewOnly'>🆕 新着のみ</button><input id='searchBox' class='search' placeholder='タイトル・本文を検索...'></div></div><div class='grid' id='itemGrid'>%s</div></div>"%(stats.get("feed_total", 0),stats.get("accepted_total", len(items)),len(items),sum(1 for x in items if x["is_new"]),sum(1 for x in items if x["official"]),sum(1 for x in items if x["region"]=="JP"),sum(1 for x in items if x["source_type"]=="rumor"),theme_btns,brand_btns,source_btns,cards)
    return page_start("index.html",stamp)+body+js+page_end(stamp)



def render_pickup(items, themes):
    stamp=datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    ranked=sorted(items, key=lambda x:(x.get("score",0), x.get("pub_dt","")), reverse=True)
    common_top=ranked[:3]
    jp_ranked=[x for x in ranked if x.get("region")=="JP"]
    global_ranked=[x for x in ranked if x.get("region")!="JP"]
    jp_top=jp_ranked[:3]
    global_top=global_ranked[:3]
    common_ids={x["id"] for x in common_top}
    jp_ids={x["id"] for x in jp_top}
    global_ids={x["id"] for x in global_top}
    rest_common=[x for x in ranked if x["id"] not in common_ids]
    rest_jp=[x for x in jp_ranked if x["id"] not in jp_ids]
    rest_global=[x for x in global_ranked if x["id"] not in global_ids]
    cards_common="".join(card(x,themes) for x in common_top)
    cards_jp="".join(card(x,themes) for x in jp_top)
    cards_global="".join(card(x,themes) for x in global_top)
    cards_rest_common="".join(card(x,themes) for x in rest_common)
    cards_rest_jp="".join(card(x,themes) for x in rest_jp)
    cards_rest_global="".join(card(x,themes) for x in rest_global)
    source_list=sorted(set(x["source"] for x in ranked))
    source_btns="<button class='flt-btn flt-source active' data-source='all'>全ソース</button>"+"".join("<button class='flt-btn flt-source' data-source='%s'>%s</button>"%(html.escape(s),html.escape(s)) for s in source_list)
    js="""
<script>
(function(){
var fRegion='all',fType='all',fLang='all',fSource='all',search='';
function matches(card){
  if(!card) return false;
  if(fRegion!=='all' && (card.dataset.region||'')!==fRegion) return false;
  if(fType!=='all' && (card.dataset.type||'')!==fType) return false;
  if(fLang!=='all' && (card.dataset.lang||'')!==fLang) return false;
  if(fSource!=='all' && (card.dataset.source||'')!==fSource) return false;
  if(search && !(card.textContent||'').toLowerCase().includes(search.toLowerCase())) return false;
  return true;
}
function applyGroup(selector){
  var visible=0;
  document.querySelectorAll(selector+' .pickup-card').forEach(function(card){
    var show=matches(card);
    card.style.display=show?'':'none';
    if(show) visible++;
  });
  return visible;
}
function setWrap(id, show){
  var el=document.getElementById(id);
  if(el) el.style.display=show?'':'none';
}
function apply(){
  var topKey = (fRegion==='JP') ? 'JP' : ((fRegion==='Global') ? 'Global' : 'Common');
  ['Common','JP','Global'].forEach(function(key){
    var isActive = (key===topKey);
    var count = applyGroup('#pickupTop'+key);
    setWrap('pickupTop'+key+'Wrap', isActive && count>0);
    var cnt=document.getElementById('pickupTop'+key+'Count'); if(cnt) cnt.textContent=count;
  });
  ['Common','JP','Global'].forEach(function(key){
    var isActive = (key===topKey);
    var count = applyGroup('#pickupList'+key);
    setWrap('pickupList'+key+'Wrap', isActive && count>0);
    var cnt=document.getElementById('pickupRest'+key+'Count'); if(cnt) cnt.textContent=count;
    if(isActive){
      var total=(parseInt(document.getElementById('pickupTop'+key+'Count')?.textContent||'0',10)||0)+count;
      var totalEl=document.getElementById('pickupCount'); if(totalEl) totalEl.textContent=total;
      var mode=document.getElementById('pickupModeLabel'); if(mode) mode.textContent=(key==='Common'?'共通':(key==='JP'?'国内':'海外'));
    }
  });
}
function bind(sel, key){
  document.querySelectorAll(sel).forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll(sel).forEach(function(b){b.classList.remove('active');});
      btn.classList.add('active');
      if(key==='region') fRegion=btn.dataset.region;
      if(key==='type') fType=btn.dataset.type;
      if(key==='lang') fLang=btn.dataset.lang;
      if(key==='source') fSource=btn.dataset.source;
      apply();
    });
  });
}
bind('.flt-region','region');
bind('.flt-type','type');
bind('.flt-lang','lang');
bind('.flt-source','source');
document.getElementById('searchBoxPickup').addEventListener('input', function(){ search=this.value; apply(); });
apply();
})();
</script>"""
    total_unique=len(ranked)
    body="<div class='main'><div class='panel'><div class='stats'><div class='stat'><span class='num'>%s</span><span class='lbl'>注目候補総数</span></div><div class='stat'><span class='num'>%s</span><span class='lbl'>共通TOP3</span></div><div class='stat'><span class='num'>%s</span><span class='lbl'>国内TOP3</span></div><div class='stat'><span class='num'>%s</span><span class='lbl'>海外TOP3</span></div><div class='stat'><span class='num'>%s</span><span class='lbl'>共通一覧</span></div><div class='stat'><span class='num'>%s</span><span class='lbl'>日本記事</span></div></div><div class='mini-note'>デフォルトは共通TOP3、地域で日本/海外を選ぶとそのTOP3に切り替わり、下の一覧も同条件でスコア順・新しい順に並びます。</div><div class='filters'><span class='filter-label'>地域:</span><button class='flt-btn flt-region active' data-region='all'>共通</button><button class='flt-btn flt-region' data-region='JP'>国内</button><button class='flt-btn flt-region' data-region='Global'>海外</button><span class='filter-label'>種別:</span><button class='flt-btn flt-type active' data-type='all'>全部</button><button class='flt-btn flt-type' data-type='official'>公式</button><button class='flt-btn flt-type' data-type='media'>メディア</button><button class='flt-btn flt-type' data-type='rumor'>噂</button><span class='filter-label'>言語:</span><button class='flt-btn flt-lang active' data-lang='all'>全部</button><button class='flt-btn flt-lang' data-lang='ja'>日本語</button><button class='flt-btn flt-lang' data-lang='en'>English</button></div><div class='filters'><span class='filter-label'>ソース:</span>%s</div><div class='filters'><input id='searchBoxPickup' class='search' placeholder='タイトル・本文を検索...'></div></div><div class='panel' id='pickupTopCommonWrap'><div class='theme-header'><span class='theme-badge' style='background:#4ea1ff22;border-color:#4ea1ff;color:#4ea1ff'>⭐ 共通TOP3</span><span style='color:var(--muted);font-size:13px'><span id='pickupTopCommonCount'>%s</span>件</span></div><div class='grid' id='pickupTopCommon'>%s</div></div><div class='panel' id='pickupTopJPWrap'><div class='theme-header'><span class='theme-badge' style='background:#16a08522;border-color:#16a085;color:#16a085'>🇯🇵 国内TOP3</span><span style='color:var(--muted);font-size:13px'><span id='pickupTopJPCount'>%s</span>件</span></div><div class='grid' id='pickupTopJP'>%s</div></div><div class='panel' id='pickupTopGlobalWrap'><div class='theme-header'><span class='theme-badge' style='background:#8e44ad22;border-color:#8e44ad;color:#8e44ad'>🌍 海外TOP3</span><span style='color:var(--muted);font-size:13px'><span id='pickupTopGlobalCount'>%s</span>件</span></div><div class='grid' id='pickupTopGlobal'>%s</div></div><div class='panel' id='pickupListCommonWrap'><div class='theme-header'><span class='theme-badge'>📰 共通注目記事一覧</span><span style='color:var(--muted);font-size:13px'><span id='pickupModeLabel'>共通</span>表示合計 <span id='pickupCount'>%s</span>件 / 一覧 <span id='pickupRestCommonCount'>%s</span>件</span></div><div class='mini-note'>一覧は現在表示中のTOP3を除いた記事です。</div><div class='grid' id='pickupListCommon'>%s</div></div><div class='panel' id='pickupListJPWrap'><div class='theme-header'><span class='theme-badge'>📰 国内注目記事一覧</span><span style='color:var(--muted);font-size:13px'>一覧 <span id='pickupRestJPCount'>%s</span>件</span></div><div class='mini-note'>一覧は国内TOP3を除いた国内記事です。</div><div class='grid' id='pickupListJP'>%s</div></div><div class='panel' id='pickupListGlobalWrap'><div class='theme-header'><span class='theme-badge'>📰 海外注目記事一覧</span><span style='color:var(--muted);font-size:13px'>一覧 <span id='pickupRestGlobalCount'>%s</span>件</span></div><div class='mini-note'>一覧は海外TOP3を除いた海外記事です。</div><div class='grid' id='pickupListGlobal'>%s</div></div></div>"%(total_unique,len(common_top),len(jp_top),len(global_top),len(rest_common),sum(1 for x in ranked if x['region']=='JP'),source_btns,len(common_top),cards_common,len(jp_top),cards_jp,len(global_top),cards_global,len(common_top)+len(rest_common),len(rest_common),cards_rest_common,len(rest_jp),cards_rest_jp,len(rest_global),cards_rest_global)
    html_text=page_start("pickup.html",stamp)+body+js+page_end(stamp)
    return html_text.replace("class='card item-card'","class='card item-card pickup-card'")

def render_section(items, themes, defs, mode):
    stamp=datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    if mode=="categories.html":
        keys=list(themes.keys()); get=lambda x:x["categories"]; icon=lambda k:themes[k].get("icon",""); color=lambda k:themes[k].get("color","#636e72"); label="カテゴリ"; all_label="📋 全カテゴリ"
    else:
        keys=list(defs.keys()); get=lambda x:[b for b in x["brands"] if b!="その他"]; icon=lambda k:"🏷"; color=lambda k:"#4ea1ff"; label="ブランド"; all_label="🏷 全ブランド"
    btns="<button class='flt-btn flt-section active' data-section='all'>%s</button>"%all_label
    sections=""
    for k in keys:
        sec=[x for x in items if k in get(x)]
        if not sec: continue
        btns += "<button class='flt-btn flt-section' data-section='%s' style='border-color:%s;color:%s'>%s %s</button>"%(html.escape(k),color(k),color(k),icon(k),html.escape(k))
        sections += "<div class='theme-section' data-section='%s'><div class='theme-header'><span class='theme-badge' style='background:%s22;border-color:%s;color:%s'>%s %s</span><span style='color:var(--muted);font-size:13px'><span class='section-count'>%s</span>件</span></div><div class='grid'>%s</div></div>"%(html.escape(k),color(k),color(k),color(k),icon(k),html.escape(k),len(sec),"".join(card(x,themes) for x in sec))
    js="""
<script>
(function(){
var fSec='all',fRegion='all',fType='all',fLang='all',search='';
function apply(){
document.querySelectorAll('.theme-section').forEach(function(sec){
var secKey=sec.getAttribute('data-section')||'';
if(fSec!=='all' && secKey!==fSec){ sec.style.display='none'; return; }
var visible=0;
sec.querySelectorAll('.item-card').forEach(function(card){
var show=true;
if(fRegion!=='all' && (card.dataset.region||'')!==fRegion) show=false;
if(fType!=='all' && (card.dataset.type||'')!==fType) show=false;
if(fLang!=='all' && (card.dataset.lang||'')!==fLang) show=false;
if(search && !(card.textContent||'').toLowerCase().includes(search.toLowerCase())) show=false;
card.style.display=show?'':'none';
if(show) visible++;
});
sec.style.display=visible>0?'':'none';
var cnt=sec.querySelector('.section-count'); if(cnt) cnt.textContent=visible;
});
}
function bind(sel, cb){ document.querySelectorAll(sel).forEach(function(btn){ btn.addEventListener('click',function(){ document.querySelectorAll(sel).forEach(function(b){b.classList.remove('active');}); btn.classList.add('active'); cb(btn); apply();});}); }
bind('.flt-section', function(btn){fSec=btn.dataset.section;});
bind('.flt-region', function(btn){fRegion=btn.dataset.region;});
bind('.flt-type', function(btn){fType=btn.dataset.type;});
bind('.flt-lang', function(btn){fLang=btn.dataset.lang;});
document.getElementById('searchBoxSection').addEventListener('input',function(){search=this.value;apply();});
})();
</script>"""
    body="<div class='main'><div class='panel'><div class='filters'><span class='filter-label'>%s:</span>%s</div><div class='filters'><span class='filter-label'>地域:</span><button class='flt-btn flt-region active' data-region='all'>全部</button><button class='flt-btn flt-region' data-region='JP'>日本</button><button class='flt-btn flt-region' data-region='Global'>海外</button><span class='filter-label'>種別:</span><button class='flt-btn flt-type active' data-type='all'>全部</button><button class='flt-btn flt-type' data-type='official'>公式</button><button class='flt-btn flt-type' data-type='media'>メディア</button><button class='flt-btn flt-type' data-type='rumor'>噂</button><span class='filter-label'>言語:</span><button class='flt-btn flt-lang active' data-lang='all'>全部</button><button class='flt-btn flt-lang' data-lang='ja'>日本語</button><button class='flt-btn flt-lang' data-lang='en'>English</button><input id='searchBoxSection' class='search' placeholder='タイトル・本文を検索...'></div></div>%s</div>"%(label,btns,sections)
    return page_start(mode,stamp)+body+js+page_end(stamp)

def render_market(items, themes, stats):
    stamp=datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    src_counts=Counter(x["source"] for x in items)
    srcmeta={}
    for x in items: srcmeta.setdefault(x["source"], (x["region"],x["source_type"]))
    source_stats = {x['name']: x for x in stats.get('source_stats', [])}
    cards="".join(card(x,themes) for x in items)
    btns="<button class='flt-btn flt-source active' data-source='all'>全ソース</button>"+"".join("<button class='flt-btn flt-source' data-source='%s'>%s</button>"%(html.escape(s),html.escape(s)) for s in sorted(src_counts))
    src_cards="".join(
        "<div class='source-card'><div class='src-name'>%s</div><div class='src-count'>表示 %s</div><div style='color:var(--muted);font-size:12px'>採用 %s / %s / %s</div><div class='mini-note'>feed=%s</div></div>" % (
            html.escape(source_name),
            count,
            source_stats.get(source_name, {}).get('accepted', count),
            srcmeta[source_name][0],
            srcmeta[source_name][1],
            source_stats.get(source_name, {}).get('raw_feed', count),
        )
        for source_name, count in src_counts.items()
    )
    js="""
<script>
(function(){
var fSource='all',fRegion='all',fType='all',fView='cards',search='';
function apply(){
document.querySelectorAll('#marketGrid .item-card').forEach(function(card){
var show=true;
if(fSource!=='all' && (card.dataset.source||'')!==fSource) show=false;
if(fRegion!=='all' && (card.dataset.region||'')!==fRegion) show=false;
if(fType!=='all' && (card.dataset.type||'')!==fType) show=false;
if(search && !(card.textContent||'').toLowerCase().includes(search.toLowerCase())) show=false;
card.style.display=show?'':'none';
});
document.getElementById('marketGrid').classList.toggle('list',fView==='list');
}
function bind(sel, cb){ document.querySelectorAll(sel).forEach(function(btn){ btn.addEventListener('click',function(){ document.querySelectorAll(sel).forEach(function(b){b.classList.remove('active');}); btn.classList.add('active'); cb(btn); apply();});}); }
bind('.flt-source', function(btn){fSource=btn.dataset.source;});
bind('.flt-region', function(btn){fRegion=btn.dataset.region;});
bind('.flt-type', function(btn){fType=btn.dataset.type;});
bind('.flt-view', function(btn){fView=btn.dataset.view;});
document.getElementById('searchBoxMarket').addEventListener('input',function(){search=this.value;apply();});
})();
</script>"""
    body="<div class='main'><div class='panel'><h2 style='margin:0 0 14px;font-size:16px'>📊 ソースモニター</h2><div class='source-grid'>%s</div></div><div class='panel'><div class='filters'><span class='filter-label'>ソース:</span>%s</div><div class='filters'><span class='filter-label'>地域:</span><button class='flt-btn flt-region active' data-region='all'>全部</button><button class='flt-btn flt-region' data-region='JP'>日本</button><button class='flt-btn flt-region' data-region='Global'>海外</button><span class='filter-label'>種別:</span><button class='flt-btn flt-type active' data-type='all'>全部</button><button class='flt-btn flt-type' data-type='official'>公式</button><button class='flt-btn flt-type' data-type='media'>メディア</button><button class='flt-btn flt-type' data-type='rumor'>噂</button><button class='flt-btn flt-view active' data-view='cards'>▦ カード</button><button class='flt-btn flt-view' data-view='list'>≡ リスト</button><input id='searchBoxMarket' class='search' placeholder='タイトル・本文を検索...'></div></div><div class='grid' id='marketGrid'>%s</div></div>"%(src_cards,btns,cards)
    return page_start("market.html",stamp)+body+js+page_end(stamp)

def render_analysis(items):
    stamp=datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    c1=Counter(t for x in items for t in x["categories"])
    c2=Counter(b for x in items for b in x["brands"] if b!="その他")
    c3=Counter(x["source"] for x in items)
    def block(counter):
        return "".join("<div class='stat'><div class='num'>%s</div><div class='label'>%s</div></div>"%(v,html.escape(k)) for k,v in counter.most_common(8))
    note="<div class='mini-note'>カテゴリとブランドは1記事が複数に属するため、各件数の合計はユニーク記事数と一致しない場合があります。</div>"
    body="<div class='main'><div class='panel'><h2 style='margin:0 0 14px;font-size:16px'>カテゴリ分布</h2>%s<div class='stat-grid'>%s</div></div><div class='panel'><h2 style='margin:0 0 14px;font-size:16px'>ブランド分布</h2>%s<div class='stat-grid'>%s</div></div><div class='panel'><h2 style='margin:0 0 14px;font-size:16px'>ソース分布</h2><div class='stat-grid'>%s</div></div></div>"%(note,block(c1),note,block(c2),block(c3))
    return page_start("analysis.html",stamp)+body+page_end(stamp)

def main():
    log('[2/3] Collecting data and building dashboards...')
    items,themes,brand_defs,stats=fetch_all()
    log(f"  Progress 2/3 | 取得件数(feed合計)={stats.get('feed_total', 0)} | 採用件数={stats.get('accepted_total', 0)} | 表示記事数={len(items)} | ソース採用合計(再計算)={stats.get('source_total_check', 0)}")
    for row in stats.get('source_stats', []):
        if row.get('status') == 'ok':
            log(f"    - {row['name']}: feed={row['raw_feed']} / 採用={row['accepted']}")
        else:
            log(f"    - {row['name']}: error")
    con=init_db(); save_items(con,items); con.close()
    log('[3/3] Writing HTML dashboards...')
    pages = [
        ('index.html', render_index(items,themes,brand_defs,stats)),
        ('pickup.html', render_pickup(items,themes)),
        ('categories.html', render_section(items,themes,brand_defs,'categories.html')),
        ('brands.html', render_section(items,themes,brand_defs,'brands.html')),
        ('market.html', render_market(items,themes,stats)),
        ('analysis.html', render_analysis(items)),
    ]
    total_pages = len(pages)
    for idx, (name, html_text) in enumerate(pages, start=1):
        (OUTPUT/name).write_text(html_text,encoding='utf-8')
        log(f"    [{idx}/{total_pages}] {name} 出力完了")
    stats_payload = {
        'accepted_before_dedup': stats.get('accepted_before_dedup', 0),
        'feed_total': stats.get('feed_total', 0),
        'accepted_total': stats.get('accepted_total', 0),
        'display_total': stats.get('display_total', 0),
        'unique_by_link': stats.get('unique_by_link', 0),
        'unique_by_content': stats.get('unique_by_content', 0),
        'source_total_check': stats.get('source_total_check', 0),
        'source_stats': stats.get('source_stats', []),
    }
    (OUTPUT/'stats_debug.json').write_text(json.dumps(stats_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    history_payload = {
        'generated_at': datetime.now(JST).isoformat(timespec='seconds'),
        'feed_total': stats.get('feed_total', 0),
        'accepted_total': stats.get('accepted_total', 0),
        'display_total': stats.get('display_total', 0),
        'accepted_before_dedup': stats.get('accepted_before_dedup', 0),
        'source_stats': stats.get('source_stats', []),
    }
    (OUTPUT/'acquisition_history.json').write_text(json.dumps(history_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f"  Progress 3/3 | pages={total_pages} | stats_debug.json / acquisition_history.json 出力完了")

if __name__=="__main__":
    main()
