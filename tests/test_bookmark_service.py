import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from bookmark_service import BookmarkService


class BookmarkServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.connection = sqlite3.connect(Path(self.tmp.name) / "bookmarks.sqlite")
        self.service = BookmarkService(self.connection)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def test_save_and_list_bookmarks_for_one_user(self):
        bookmark_id = self.service.add_bookmark("alice", "https://example.com/a")
        self.assertEqual(self.service.list_bookmarks("alice"), [(bookmark_id, "https://example.com/a")])

    def test_same_url_can_be_saved_by_different_users(self):
        alice_id = self.service.add_bookmark("alice", "https://example.com/a")
        bob_id = self.service.add_bookmark("bob", "https://example.com/a")
        self.assertNotEqual(alice_id, bob_id)
        self.assertEqual(self.service.list_bookmarks("alice"), [(alice_id, "https://example.com/a")])
        self.assertEqual(self.service.list_bookmarks("bob"), [(bob_id, "https://example.com/a")])

    def test_saving_the_same_bookmark_twice_is_idempotent(self):
        first_id = self.service.add_bookmark("alice", "https://example.com/a")
        second_id = self.service.add_bookmark("alice", "https://example.com/a")
        self.assertEqual(first_id, second_id)
        self.assertEqual(self.service.list_bookmarks("alice"), [(first_id, "https://example.com/a")])


class EmptyUrlTests(unittest.TestCase):
    """Issue #3: empty or whitespace-only URLs are rejected with ValueError."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.connection = sqlite3.connect(Path(self.tmp.name) / "bookmarks.sqlite")
        self.service = BookmarkService(self.connection)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def row_count(self):
        return self.connection.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]

    def test_empty_or_whitespace_url_raises_and_saves_nothing(self):
        for url in ["", " ", "   ", "\t", "\n", " \t\r\n ", "　"]:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    self.service.add_bookmark("alice", url)
                self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.service.list_bookmarks("alice"), [])
        self.assertEqual(self.row_count(), 0)

    def test_rejected_url_does_not_affect_existing_or_other_users_bookmarks(self):
        alice_id = self.service.add_bookmark("alice", "https://example.com/a")
        bob_id = self.service.add_bookmark("bob", "https://example.com/a")
        with self.assertRaises(ValueError):
            self.service.add_bookmark("alice", "  ")
        with self.assertRaises(ValueError):
            self.service.add_bookmark("bob", "")
        self.assertEqual(self.service.list_bookmarks("alice"), [(alice_id, "https://example.com/a")])
        self.assertEqual(self.service.list_bookmarks("bob"), [(bob_id, "https://example.com/a")])
        # Saving works normally after a rejection.
        new_id = self.service.add_bookmark("alice", "https://example.com/b")
        self.assertEqual(self.service.list_bookmarks("alice"),
                         [(alice_id, "https://example.com/a"), (new_id, "https://example.com/b")])

    def test_url_with_surrounding_spaces_is_stored_unchanged(self):
        bookmark_id = self.service.add_bookmark("alice", " https://example.com/a ")
        self.assertEqual(self.service.list_bookmarks("alice"), [(bookmark_id, " https://example.com/a ")])

    def test_rejection_inside_a_caller_transaction_leaves_it_untouched(self):
        self.connection.execute("BEGIN")
        self.connection.execute("INSERT INTO bookmarks (user_id, url) VALUES ('carol', 'https://example.com/c')")
        with self.assertRaises(ValueError):
            self.service.add_bookmark("alice", " ")
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual(self.row_count(), 0)


class ExistingDuplicateDataTests(unittest.TestCase):
    def test_existing_duplicates_are_kept_and_no_new_duplicate_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = sqlite3.connect(Path(tmp) / "bookmarks.sqlite")
            try:
                service = BookmarkService(connection)
                # Rows saved twice by the version before the duplicate fix.
                connection.executemany("INSERT INTO bookmarks (user_id, url) VALUES (?, ?)",
                                       [("alice", "https://example.com/a")] * 2)
                connection.commit()
                self.assertEqual(service.add_bookmark("alice", "https://example.com/a"), 1)
                self.assertEqual(service.list_bookmarks("alice"),
                                 [(1, "https://example.com/a"), (2, "https://example.com/a")])
            finally:
                connection.close()


class ConcurrentSaveTests(unittest.TestCase):
    """Separate connections to one DB file, as with several service instances."""

    WORKERS = 8

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bookmarks.sqlite"
        self.connections = []
        BookmarkService(self.connect())

    def tearDown(self):
        for connection in self.connections:
            connection.close()
        self.tmp.cleanup()

    def connect(self, timeout=10.0):
        connection = sqlite3.connect(self.path, timeout=timeout, check_same_thread=False)
        self.connections.append(connection)
        return connection

    def save_concurrently(self, url):
        services = [BookmarkService(self.connect()) for _ in range(self.WORKERS)]
        barrier = threading.Barrier(self.WORKERS)
        results, errors = [], []

        def work(service):
            try:
                barrier.wait()
                results.append(service.add_bookmark("alice", url))
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)

        threads = [threading.Thread(target=work, args=(s,)) for s in services]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        return results

    def check_one_row(self, journal_mode):
        self.connect().execute(f"PRAGMA journal_mode={journal_mode}")
        for n in range(5):
            url = f"https://example.com/{journal_mode}/{n}"
            ids = self.save_concurrently(url)
            self.assertEqual(len(set(ids)), 1, ids)
            rows = self.connect().execute(
                "SELECT id FROM bookmarks WHERE user_id = 'alice' AND url = ?", (url,)).fetchall()
            self.assertEqual(rows, [(ids[0],)])

    def test_concurrent_saves_leave_one_row_with_rollback_journal(self):
        self.check_one_row("DELETE")

    def test_concurrent_saves_leave_one_row_with_wal(self):
        self.check_one_row("WAL")

    def test_lock_in_an_already_read_caller_transaction_is_raised_for_full_retry(self):
        self.connect().execute("PRAGMA journal_mode=WAL")
        caller = self.connect(timeout=0.2)
        service = BookmarkService(caller)
        other = BookmarkService(self.connect())

        caller.execute("BEGIN")
        caller.execute("SELECT COUNT(*) FROM bookmarks").fetchone()  # read snapshot
        other_id = other.add_bookmark("alice", "https://example.com/a")
        with self.assertRaises(sqlite3.OperationalError) as raised:
            service.add_bookmark("alice", "https://example.com/a")
        self.assertIn("locked", str(raised.exception))
        self.assertTrue(caller.in_transaction)  # left for the caller to end

        caller.rollback()  # the caller retries the whole transaction
        caller.execute("BEGIN")
        caller.execute("SELECT COUNT(*) FROM bookmarks").fetchone()
        self.assertEqual(service.add_bookmark("alice", "https://example.com/a"), other_id)
        self.assertEqual(service.list_bookmarks("alice"), [(other_id, "https://example.com/a")])

    def test_busy_timeout_in_a_service_owned_transaction_raises_without_leaving_it_open(self):
        holder = self.connect()
        caller = self.connect(timeout=0.1)
        service = BookmarkService(caller)
        holder.execute("BEGIN IMMEDIATE")
        with self.assertRaises(sqlite3.OperationalError):
            service.add_bookmark("alice", "https://example.com/a")
        self.assertFalse(caller.in_transaction)
        holder.rollback()
        bookmark_id = service.add_bookmark("alice", "https://example.com/a")
        self.assertEqual(service.list_bookmarks("alice"), [(bookmark_id, "https://example.com/a")])


if __name__ == "__main__":
    unittest.main()
