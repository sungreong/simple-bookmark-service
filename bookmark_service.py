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
        adding a row. Rows duplicated before this check existed are kept; the
        oldest one's ID is returned.

        When no transaction is open, the lookup and insert run under
        BEGIN IMMEDIATE so concurrent writers cannot both insert. If the caller
        already opened a transaction, the work joins it; a "database is locked"
        error raised there is propagated so the caller can roll back and retry
        its whole transaction.

        The transaction opened here is ended with SQL COMMIT/ROLLBACK, because
        Connection.commit()/rollback() do nothing on Python 3.12+ connections
        created with autocommit=True. A caller's transaction is still ended
        only through Connection.commit(), as before.
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
