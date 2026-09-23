"""posts / news_log CRUD."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .news import NewsItem

STATUS_POSTED = "posted"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PostRecord:
    slot_key: str
    news_title: str
    title: str
    embedding: Optional[list[float]]
    published_at: str


# ---- posts ----

def post_exists(conn: sqlite3.Connection, slot_key: str) -> bool:
    row = conn.execute("SELECT 1 FROM posts WHERE slot_key = ?", (slot_key,)).fetchone()
    return row is not None


def insert_post(
    conn: sqlite3.Connection,
    *,
    slot_key: str,
    item: NewsItem,
    title: str,
    caption: str,
    slides: list[str],
    image_urls: list[str],
    embedding: Optional[list[float]],
    instagram_media_id: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO posts
            (slot_key, news_key, origin, news_title, title, caption, slides_json, image_urls_json,
             embedding_json, source_url, instagram_media_id, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            slot_key,
            item.key,
            item.origin,
            item.title,
            title,
            caption,
            json.dumps(slides, ensure_ascii=False),
            json.dumps(image_urls),
            json.dumps(embedding) if embedding is not None else None,
            item.source_url,
            instagram_media_id,
            _now(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def recent_posts(conn: sqlite3.Connection, since: str) -> list[PostRecord]:
    rows = conn.execute(
        "SELECT * FROM posts WHERE published_at >= ? ORDER BY published_at DESC", (since,)
    ).fetchall()
    return [
        PostRecord(
            slot_key=r["slot_key"],
            news_title=r["news_title"],
            title=r["title"],
            embedding=json.loads(r["embedding_json"]) if r["embedding_json"] else None,
            published_at=r["published_at"],
        )
        for r in rows
    ]


# ---- news_log ----

def excluded_news_keys(conn: sqlite3.Connection, max_attempts: int) -> set[str]:
    """다시 후보로 삼지 않을 뉴스: 게시됨, 건너뜀, 실패 한도 초과."""
    rows = conn.execute(
        "SELECT news_key FROM news_log WHERE status IN (?, ?) OR (status = ? AND attempts >= ?)",
        (STATUS_POSTED, STATUS_SKIPPED, STATUS_FAILED, max_attempts),
    ).fetchall()
    return {r["news_key"] for r in rows}


def log_news(
    conn: sqlite3.Connection, item: NewsItem, status: str, reason: Optional[str] = None
) -> None:
    conn.execute(
        """
        INSERT INTO news_log (news_key, origin, batch_date, title, status, reason, attempts, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(news_key) DO UPDATE SET
            status = excluded.status, reason = excluded.reason, updated_at = excluded.updated_at
        """,
        (item.key, item.origin, item.batch_date, item.title, status, reason, _now()),
    )
    conn.commit()


def record_news_failure(
    conn: sqlite3.Connection, item: NewsItem, reason: str, give_up_at: Optional[int] = None
) -> int:
    """실패 횟수를 올리고 누적 횟수를 반환한다. give_up_at을 주면 그 횟수로 바로 올려서
    (중복 게시 위험 등) 다시 시도하지 않게 한다."""
    conn.execute(
        """
        INSERT INTO news_log (news_key, origin, batch_date, title, status, reason, attempts, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(news_key) DO UPDATE SET
            status = excluded.status, reason = excluded.reason,
            attempts = news_log.attempts + 1, updated_at = excluded.updated_at
        """,
        (item.key, item.origin, item.batch_date, item.title, STATUS_FAILED, reason[:1000], _now()),
    )
    if give_up_at is not None:
        conn.execute(
            "UPDATE news_log SET attempts = MAX(attempts, ?) WHERE news_key = ?", (give_up_at, item.key)
        )
    conn.commit()
    row = conn.execute("SELECT attempts FROM news_log WHERE news_key = ?", (item.key,)).fetchone()
    return row["attempts"]
