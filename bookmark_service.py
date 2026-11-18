"""Small SQLite bookmark service used for an agent workflow exercise."""

import sqlite3


class BookmarkService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                url TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def add_bookmark(self, user_id: str, url: str) -> int:
        """Save a bookmark and return its database ID."""
        cursor = self.connection.execute(
            "INSERT INTO bookmarks (user_id, url) VALUES (?, ?)", (user_id, url)
        )
        self.connection.commit()
        return cursor.lastrowid

    def list_bookmarks(self, user_id: str) -> list:
        """Return a user's bookmarks in insertion order as (id, url) pairs."""
        return self.connection.execute(
            "SELECT id, url FROM bookmarks WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
