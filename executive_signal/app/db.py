from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            source TEXT NOT NULL,
            source_url TEXT,
            source_type TEXT,
            title TEXT NOT NULL,
            title_norm TEXT,
            url TEXT NOT NULL UNIQUE,
            summary TEXT,
            published TEXT,
            lang TEXT,
            lang_detected TEXT,
            impact_label TEXT,
            score REAL DEFAULT 0,
            translated_title_ja TEXT,
            translated_summary_ja TEXT,
            positive_hits TEXT,
            negative_hits TEXT,
            entity_hits TEXT,
            target_hits TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            type TEXT NOT NULL,
            target TEXT NOT NULL,
            direction TEXT NOT NULL,
            strength INTEGER NOT NULL,
            reason TEXT,
            score REAL,
            article_count INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    _ensure_column(conn, "articles", "source_type", "TEXT")
    _ensure_column(conn, "articles", "title_norm", "TEXT")
    _ensure_column(conn, "articles", "translated_title_ja", "TEXT")
    _ensure_column(conn, "articles", "translated_summary_ja", "TEXT")
    conn.commit()


def reset_runtime_tables(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM signals")
    conn.commit()


def upsert_articles(conn: sqlite3.Connection, articles: list[dict[str, Any]]) -> int:
    existing_links = {row[0] for row in conn.execute("SELECT url FROM articles")}
    rows = []
    for a in articles:
        url = a.get("url", "")
        if not url:
            continue
        rows.append((
            a["module"], a["source"], a.get("source_url", ""), a.get("source_type", ""), a["title"], a.get("title_norm", ""),
            url, a.get("summary", ""), a.get("published", ""), a.get("lang", ""), a.get("lang_detected", ""),
            a.get("impact_label", ""), a.get("score", 0), a.get("translated_title_ja", ""), a.get("translated_summary_ja", ""),
            ", ".join(a.get("positive_hits", [])), ", ".join(a.get("negative_hits", [])),
            ", ".join(a.get("entity_hits", [])), ", ".join(a.get("target_hits", [])),
        ))
    conn.executemany(
        """
        INSERT INTO articles (
            module, source, source_url, source_type, title, title_norm, url, summary, published, lang, lang_detected,
            impact_label, score, translated_title_ja, translated_summary_ja,
            positive_hits, negative_hits, entity_hits, target_hits
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            module=excluded.module,
            source=excluded.source,
            source_url=excluded.source_url,
            source_type=excluded.source_type,
            title=excluded.title,
            title_norm=excluded.title_norm,
            summary=excluded.summary,
            published=excluded.published,
            lang=excluded.lang,
            lang_detected=excluded.lang_detected,
            impact_label=excluded.impact_label,
            score=excluded.score,
            translated_title_ja=excluded.translated_title_ja,
            translated_summary_ja=excluded.translated_summary_ja,
            positive_hits=excluded.positive_hits,
            negative_hits=excluded.negative_hits,
            entity_hits=excluded.entity_hits,
            target_hits=excluded.target_hits,
            fetched_at=CURRENT_TIMESTAMP
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def insert_signals(conn: sqlite3.Connection, signals: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT INTO signals (module, type, target, direction, strength, reason, score, article_count)
        VALUES (:module, :type, :target, :direction, :strength, :reason, :score, :article_count)
        """,
        signals,
    )
    conn.commit()


def cleanup_db(conn: sqlite3.Connection, history_days: int) -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    JST = ZoneInfo("Asia/Tokyo")
    cutoff = (datetime.now(JST) - timedelta(days=history_days)).isoformat()
    conn.execute("DELETE FROM articles WHERE published < ? AND published != ''", (cutoff,))
    conn.commit()
