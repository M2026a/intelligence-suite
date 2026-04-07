import html
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import json
import feedparser
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
OUTPUT_FILE = OUTPUT_DIR / "index.html"
DB_FILE     = OUTPUT_DIR / "strategic_it_suite.db"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 StrategicITSuite"
JST = ZoneInfo("Asia/Tokyo")

CONFIG = json.loads((ROOT / "shared" / "config.json").read_text(encoding="utf-8"))

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
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


def safe_text(value):
    return html.escape(str(value)) if value is not None else ""


def get_ai_risk_entries():
    url = "https://news.google.com/rss/search?q=AI+脆弱性+規制+漏洩+セキュリティ&hl=ja&gl=JP&ceid=JP:ja"
    return feedparser.parse(url, agent=USER_AGENT).entries[:100]


def get_dev_articles():
    url = "https://zenn.dev/api/articles?topicname=ai&order=latest"
    res = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT})
    res.raise_for_status()
    return res.json().get("articles", [])[:100]


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

    con.commit()
    return con


def save_run_history(con, run_at, risk_entries, dev_articles, config_result):
    cur = con.cursor()
    cur.execute(
        "INSERT INTO run_history (run_at, risk_count, dev_count, config_name, config_key) VALUES (?, ?, ?, ?, ?)",
        (run_at, len(risk_entries), len(dev_articles), config_result["name"], config_result["key"]),
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

    con.commit()


def get_recent_history(con, limit=8):
    cur = con.cursor()
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
        return '<tr><td colspan="4">履歴なし</td></tr>'

    rows = []
    for run_at, risk_count, dev_count, config_name in history:
        rows.append(
            "<tr>"
            f"<td>{safe_text(run_at)}</td>"
            f"<td>{safe_text(risk_count)}</td>"
            f"<td>{safe_text(dev_count)}</td>"
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
        link = safe_text("https://zenn.dev" + a.get("path", ""))
        published = format_date(a.get("published_at", ""))
        liked = safe_text(a.get("liked_count", 0))
        meta = " / ".join([x for x in [published, f"いいね {liked}"] if x])

        parts.append(
            f"""
        <div class="item-card">
            <div class="item-index">{idx:02d}</div>
            <div class="item-body">
                <a class="item-title" href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>
                <div class="item-meta">{meta}</div>
            </div>
        </div>
        """
        )
    return "\n".join(parts)


def build_list_html(items):
    return "".join([f"<li>{safe_text(x)}</li>" for x in items])


def build_html(risk_entries, dev_articles, generated_at, history, config_result):
    risk_count = len(risk_entries)
    dev_count = len(dev_articles)
    risk_html = build_list_items_news(risk_entries)
    dev_html = build_list_items_articles(dev_articles)
    history_rows = build_history_rows(history)
    reasons_html = build_list_html(config_result["reasons"])
    fit_for_html = build_list_html(config_result["fit_for"])
    features_html = build_list_html(config_result["features"])
    cautions_html = build_list_html(config_result["cautions"])
    next_actions_html = build_list_html(config_result["next_actions"])

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
    grid-template-columns:repeat(4,minmax(0,1fr));
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
@media (max-width:980px) {{
    .nav, .metrics, .ai-top, .ai-grid {{ grid-template-columns:1fr; }}
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
</style>
<script>
function showPage(pageId, btn) {{
    document.querySelectorAll('.page').forEach(function(el) {{ el.classList.remove('active'); }});
    document.querySelectorAll('.nav-btn').forEach(function(el) {{ el.classList.remove('active'); }});
    document.getElementById(pageId).classList.add('active');
    btn.classList.add('active');
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
      <div class="title">🛡️ {safe_text(CONFIG.get("app_name", ""))}</div>
      <div class="subtitle">｜ {html.escape(CONFIG.get("subtitle", ""))}</div>
      <div class="timestamp">最終更新: {safe_text(generated_at)}</div>
    </div>
    <div class="nav">
      <button class="nav-btn" onclick="showPage('page-main', this)">🏠 MAIN</button>
      <button class="nav-btn" onclick="showPage('page-gov', this)">🛡 AIガバナンス</button>
      <button class="nav-btn" onclick="showPage('page-dev', this)">🚀 開発効率</button>
      <button class="nav-btn" onclick="showPage('page-ai', this)">🤖 推奨AI構成</button>
    </div>
  </div>

  <div class="content">
    <div id="page-main" class="page active">
      <div class="section-title">📊 エグゼクティブ・サマリー</div>

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

      <div class="panel">
        <div class="section-title" style="font-size:18px; margin-bottom:10px;">過去ログ（SQLite）</div>
        <table class="history-table">
          <thead>
            <tr>
              <th>取得日時</th>
              <th>AIリスク</th>
              <th>開発記事</th>
              <th>構成</th>
            </tr>
          </thead>
          <tbody>
            {history_rows}
          </tbody>
        </table>
      </div>
    </div>

    <div id="page-gov" class="page">
      <div class="section-title">🛡 AIガバナンス</div>
      <div class="panel">{risk_html}</div>
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
  </div>
</div>
</body>
</html>"""
    return html_doc


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    start = time.time()
    print("  🚀 並列取得: AI Risk / Dev Articles 同時実行")
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_risk = ex.submit(get_ai_risk_entries)
        f_dev  = ex.submit(get_dev_articles)
        try:
            risk_entries = f_risk.result()
            risk_entries.sort(key=lambda e: e.get("published_parsed") or (0,)*9, reverse=True)
        except Exception as e:
            print(f"[WARN] AI Risk取得失敗: {e}")
            risk_entries = []
        try:
            dev_articles = f_dev.result()
            dev_articles.sort(key=lambda a: a.get("published_at") or "", reverse=True)
        except Exception as e:
            print(f"[WARN] Dev Articles取得失敗: {e}")
            dev_articles = []

    generated_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    config_result = analyze_config(risk_entries, dev_articles)
    print(f"リスク記事: {len(risk_entries)}件 / 開発記事: {len(dev_articles)}件")

    con = init_db()
    save_run_history(con, generated_at, risk_entries, dev_articles, config_result)
    history = get_recent_history(con, limit=8)
    con.close()

    html_text = build_html(risk_entries, dev_articles, generated_at, history, config_result)

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
