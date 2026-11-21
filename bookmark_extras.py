"""Exercise-only bookmark operations. This project is not a production service."""

import sqlite3


class BookmarkExtras:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def delete(self, user_id: str, bookmark_id: int) -> bool:
        """Delete the user's bookmark, returning whether a row was removed."""
        cursor = self.connection.execute(
            "DELETE FROM bookmarks WHERE id = ? AND user_id = ?",
            (bookmark_id, user_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def search(self, user_id: str, text: str) -> list:
        """Find the user's bookmarks containing the literal text, in ID order."""
        return self.connection.execute(
            "SELECT id, url FROM bookmarks WHERE user_id = ? AND url LIKE ? ORDER BY id",
            (user_id, f"%{text}%"),
        ).fetchall()

    def page(self, user_id: str, page_number: int, page_size: int) -> list:
        """Return a 1-based page of the user's bookmarks, ordered by ID."""
        if page_number < 1 or page_size < 1:
            raise ValueError("page_number and page_size must be positive")
        return self.connection.execute(
            "SELECT id, url FROM bookmarks WHERE user_id = ? ORDER BY id LIMIT ? OFFSET ?",
            (user_id, page_size, page_number * page_size),
        ).fetchall()
