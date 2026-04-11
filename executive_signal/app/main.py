from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from pathlib import Path
from typing import Any

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.classify import infer_best_module, tag_article
from app.db import connect, init_db, insert_signals, reset_runtime_tables, upsert_articles
from app.fetch import fetch_module_articles, normalize_for_match
from app.html_builder import build_html
from app.score import score_article
from app.signal import build_signals
from config.sources import MODULE_SOURCES

OUTPUT_DIR = ROOT / "output"
DB_PATH = OUTPUT_DIR / "db.sqlite"
OUTPUT_HTML = OUTPUT_DIR / "index.html"
RUNTIME_JSON = OUTPUT_DIR / "last_run.json"

_TRANSLATOR: GoogleTranslator | None = None
_TRANSLATION_CACHE: dict[tuple[str, str], str] = {}
TRANSLATE_WORKERS = 4
TRANSLATE_RETRIES = 2


def get_translator(target_lang: str = "ja") -> GoogleTranslator:
    global _TRANSLATOR
    if _TRANSLATOR is None:
        _TRANSLATOR = GoogleTranslator(source="auto", target=target_lang)
    return _TRANSLATOR


def try_translate(text: str, target_lang: str = "ja") -> str:
    text = (text or "").strip()
    if not text:
        return ""
    key = (text, target_lang)
    if key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[key]
    translated = ""
    for _ in range(max(1, TRANSLATE_RETRIES + 1)):
        try:
            translated = get_translator(target_lang).translate(text) or ""
            if translated:
                break
        except Exception:
            translated = ""
        time.sleep(0.2)
    _TRANSLATION_CACHE[key] = translated
    return translated


def title_fingerprint(article: dict[str, Any]) -> str:
    title = normalize_for_match(article.get("title", ""))
    title = re.sub(r"\b(official|news|press release|press|update)\b", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def dedupe_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    seen_titles: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    for article in articles:
        url_key = article["url"].strip().lower()
        title_key = title_fingerprint(article)
        module_title_key = (article["module"], title_key)
        if not url_key:
            continue
        if url_key in seen_urls:
            continue
        if title_key and module_title_key in seen_titles:
            continue
        seen_urls.add(url_key)
        if title_key:
            seen_titles.add(module_title_key)
        article["title_norm"] = title_key
        results.append(article)
    return results


def enrich_articles(raw_articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    en_items: list[dict[str, Any]] = []

    for article in raw_articles:
        best_module = infer_best_module(article)
        if best_module:
            article["module"] = best_module
        article = tag_article(article)
        article = score_article(article)
        article["translated_title_ja"] = article.get("title", "")
        article["translated_summary_ja"] = article.get("summary", "")
        if article.get("lang_detected") == "en":
            en_items.append(article)
        enriched.append(article)

    total_en = len(en_items)
    if total_en:
        print(f"🌐 翻訳中... ({total_en}件)")
        done = 0

        def do_translate(item: dict[str, Any]) -> dict[str, Any]:
            title = (item.get("title", "") or "").strip()
            summary = (item.get("summary", "") or "").strip()
            title_ja = try_translate(title)
            summary_ja = try_translate(summary)
            item["translated_title_ja"] = title_ja or title
            item["translated_summary_ja"] = summary_ja or summary
            return item

        with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as ex:
            futures = {ex.submit(do_translate, item): item for item in en_items}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception:
                    pass
                done += 1
                if done % 10 == 0 or done == total_en:
                    print(f"  翻訳進捗: {done}/{total_en}")

    return enriched


def summarize_health(source_health: list[dict[str, Any]]) -> dict[str, Any]:
    status_counter = Counter(x["status"] for x in source_health)
    module_counter = Counter(x["module"] for x in source_health if x.get("count", 0) > 0)
    weakest = sorted(source_health, key=lambda x: (x["status"] != "error", x.get("count", 0), x["source"]))[:8]
    return {
        "status_counter": dict(status_counter),
        "module_success_counter": dict(module_counter),
        "weakest_sources": weakest,
    }


def main() -> None:
    started = time.time()
    print("=" * 46)
    print("  Executive Signal")
    print("=" * 46)
    print("[1/3] Installing requirements...")
    print("Requirements are managed by requirements.txt and start batch.")

    print("[2/3] Collecting / Processing...")
    all_articles: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_health: list[dict[str, Any]] = []
    module_total = len(MODULE_SOURCES)

    for module_idx, (module, sources) in enumerate(MODULE_SOURCES.items(), start=1):
        print(f"  - [{module_idx}/{module_total}] {module}: source count {len(sources)}")
        module_articles, module_warnings, module_health = fetch_module_articles(module, sources, verbose=True)
        all_articles.extend(module_articles)
        warnings.extend(module_warnings)
        source_health.extend(module_health)
        ok_count = sum(1 for x in module_health if x.get("status") == "ok")
        err_count = sum(1 for x in module_health if x.get("status") == "error")
        print(f"    => {module}: fetched {len(module_articles)} items | ok sources {ok_count}/{len(module_health)} | warnings {len(module_warnings)} | errors {err_count}")

    print(f"✅ 合計 {len(all_articles)} 件収集")
    print(f"  - Deduping articles... before={len(all_articles)}")
    all_articles = dedupe_articles(all_articles)
    print(f"  - Deduped: after={len(all_articles)}")

    print(f"  - Enriching articles... target={len(all_articles)}")
    all_articles = enrich_articles(all_articles)

    print(f"  - Building signals... articles={len(all_articles)}")
    signals = build_signals(all_articles)
    print(f"  - Signals built: {len(signals)}")

    print("  - Summarizing source health...")
    health_summary = summarize_health(source_health)

    conn = connect(DB_PATH)
    init_db(conn)
    reset_runtime_tables(conn)
    print("  - Saving SQLite articles...")
    article_count = upsert_articles(conn, all_articles)
    print("  - Saving SQLite signals...")
    insert_signals(conn, signals)
    conn.close()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("  - Building HTML...")
    html = build_html(all_articles, signals, warnings=warnings, source_health=source_health, health_summary=health_summary)
    print("  - Writing index.html...")
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    runtime = {
        "article_count": article_count,
        "signal_count": len(signals),
        "warnings": warnings,
        "source_health": source_health,
        "health_summary": health_summary,
    }
    RUNTIME_JSON.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[3/3] Writing HTML / Opening dashboard...")
    print(f"Articles: {article_count} | Signals: {len(signals)}")
    if warnings:
        print(f"Warnings: {len(warnings)}")
        for warning in warnings[:12]:
            print(f"  ! {warning}")
    print(f"Output: {OUTPUT_HTML}")
    print(f"⏱ 処理時間: {time.time() - started:.1f}秒")


if __name__ == "__main__":
    main()
