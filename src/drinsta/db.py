"""SQLite 커넥션 + 스키마.

AInstagram과 같은 방식: GitHub Actions는 실행마다 새 환경이라, 이 DB 파일을 저장소에 커밋해서
상태를 영속시킨다. DR의 DB(Neon)에는 읽기만 하고 절대 쓰지 않는다.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = ROOT_DIR / "data" / "drinsta.db"

SCHEMA = """
-- 실제로 인스타그램에 게시된 것만 기록한다.
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_key TEXT NOT NULL UNIQUE,
    news_key TEXT NOT NULL,
    origin TEXT NOT NULL,
    news_title TEXT NOT NULL,
    title TEXT NOT NULL,
    caption TEXT NOT NULL,
    slides_json TEXT NOT NULL,
    image_urls_json TEXT NOT NULL,
    embedding_json TEXT,
    source_url TEXT,
    instagram_media_id TEXT,
    published_at TEXT NOT NULL
);

-- 뉴스별 처리 결과. 여기 올라간 뉴스(게시/건너뜀/실패 한도 초과)는 다시 후보가 되지 않는다.
CREATE TABLE IF NOT EXISTS news_log (
    news_key TEXT PRIMARY KEY,
    origin TEXT NOT NULL,
    batch_date TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
