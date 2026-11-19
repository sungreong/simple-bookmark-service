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
        """Save a bookmark and return its database ID.

        Saving the same (user_id, url) again returns the existing ID instead of
        adding a row. URLs are compared as exact strings. Rows duplicated before
        this check existed are kept; the oldest one's (smallest) ID is returned.

        When no transaction is open, the lookup and insert run under
        BEGIN IMMEDIATE, so connections (threads or processes) saving to the
        same database file at once cannot both insert. That transaction is
        ended with SQL COMMIT/ROLLBACK, because Connection.commit()/rollback()
        do nothing on Python 3.12+ connections created with autocommit=True.

        If the caller already opened a transaction, the work joins it and, on
        success, Connection.commit() is called as before. An error raised there,
        such as sqlite3.OperationalError "database is locked" after the caller's
        transaction has already read the database, is propagated unchanged: the
        caller's transaction is neither committed, rolled back nor retried here.
        Retrying inside that transaction cannot succeed, so the caller must roll
        back and retry its whole transaction from the first read.
        """
        owns_transaction = not self.connection.in_transaction
        if owns_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT MIN(id) FROM bookmarks WHERE user_id = ? AND url = ?",
                (user_id, url),
            ).fetchone()
            if row[0] is not None:
                bookmark_id = row[0]
            else:
                bookmark_id = self.connection.execute(
                    "INSERT INTO bookmarks (user_id, url) VALUES (?, ?)", (user_id, url)
                ).lastrowid
            if owns_transaction:
                self.connection.execute("COMMIT")
            else:
                self.connection.commit()
        except BaseException:
            if owns_transaction and self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        return bookmark_id

    def list_bookmarks(self, user_id: str) -> list:
        """Return a user's bookmarks in insertion order as (id, url) pairs."""
        return self.connection.execute(
            "SELECT id, url FROM bookmarks WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
