import aiosqlite
import json
from typing import Optional

DB_PATH = "rss_bot.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                url TEXT NOT NULL,
                last_checked REAL DEFAULT 0,
                filter_enabled INTEGER DEFAULT 0,
                criteria TEXT DEFAULT NULL,
                UNIQUE(room_id, url)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS seen_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER NOT NULL,
                article_url TEXT NOT NULL,
                UNIQUE(feed_id, article_url),
                FOREIGN KEY(feed_id) REFERENCES feeds(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.commit()


async def add_feed(
    room_id: str,
    url: str,
    filter_enabled: bool = False,
    criteria: Optional[str] = None,
) -> bool:
    """Returns True if inserted, False if already exists."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """
                INSERT INTO feeds (room_id, url, filter_enabled, criteria)
                VALUES (?, ?, ?, ?)
                """,
                (room_id, url, int(filter_enabled), criteria),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_feed(room_id: str, url: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM feeds WHERE room_id = ? AND url = ?",
            (room_id, url),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_feeds_for_room(room_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM feeds WHERE room_id = ?", (room_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_all_feeds() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM feeds") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def update_feed_last_checked(feed_id: int, timestamp: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE feeds SET last_checked = ? WHERE id = ?",
            (timestamp, feed_id),
        )
        await db.commit()


async def update_feed_filter(
    room_id: str,
    url: str,
    filter_enabled: bool,
    criteria: Optional[str] = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE feeds SET filter_enabled = ?, criteria = ?
            WHERE room_id = ? AND url = ?
            """,
            (int(filter_enabled), criteria, room_id, url),
        )
        await db.commit()


async def is_article_seen(feed_id: int, article_url: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM seen_articles WHERE feed_id = ? AND article_url = ?",
            (feed_id, article_url),
        ) as cursor:
            return await cursor.fetchone() is not None


async def mark_article_seen(feed_id: int, article_url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO seen_articles (feed_id, article_url) VALUES (?, ?)",
                (feed_id, article_url),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            pass


async def get_config(key: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_config(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value),
        )
        await db.commit()


async def get_feed_by_id(feed_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM feeds WHERE id = ?", (feed_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
