import random
import sqlite3
import tempfile
import threading
import time
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

    def test_saving_twice_inside_a_caller_transaction_is_idempotent(self):
        self.connection.execute("BEGIN")
        first_id = self.service.add_bookmark("alice", "https://example.com/a")
        self.connection.execute("BEGIN")
        second_id = self.service.add_bookmark("alice", "https://example.com/a")
        self.assertEqual(first_id, second_id)
        self.assertEqual(self.service.list_bookmarks("alice"), [(first_id, "https://example.com/a")])

    def test_saving_inside_a_caller_transaction_commits_it(self):
        # Compatibility: add_bookmark always commits on success, which also ends
        # a transaction the caller opened and commits the caller's earlier work.
        self.connection.execute("CREATE TABLE audit_log (note TEXT)")
        self.connection.commit()
        self.connection.execute("BEGIN")
        self.connection.execute("INSERT INTO audit_log (note) VALUES ('before save')")
        bookmark_id = self.service.add_bookmark("alice", "https://example.com/a")
        self.assertFalse(self.connection.in_transaction)

        reader = sqlite3.connect(Path(self.tmp.name) / "bookmarks.sqlite")
        try:
            self.assertEqual(reader.execute("SELECT note FROM audit_log").fetchall(), [("before save",)])
            self.assertEqual(
                reader.execute("SELECT id, url FROM bookmarks WHERE user_id = 'alice'").fetchall(),
                [(bookmark_id, "https://example.com/a")],
            )
        finally:
            reader.close()


class ExistingDuplicateDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bookmarks.sqlite"
        legacy = sqlite3.connect(self.path)
        legacy.execute(
            "CREATE TABLE bookmarks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, url TEXT NOT NULL)"
        )
        legacy.executemany(
            "INSERT INTO bookmarks (user_id, url) VALUES (?, ?)",
            [("alice", "https://example.com/a"), ("alice", "https://example.com/a")],
        )
        legacy.commit()
        legacy.close()
        self.connection = sqlite3.connect(self.path)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def test_existing_duplicates_are_kept_and_no_new_duplicate_is_added(self):
        service = BookmarkService(self.connection)
        existing = [(1, "https://example.com/a"), (2, "https://example.com/a")]
        self.assertEqual(service.list_bookmarks("alice"), existing)

        self.assertEqual(service.add_bookmark("alice", "https://example.com/a"), 1)
        self.assertEqual(service.list_bookmarks("alice"), existing)

        new_id = service.add_bookmark("alice", "https://example.com/b")
        self.assertEqual(service.add_bookmark("alice", "https://example.com/b"), new_id)
        self.assertEqual(service.list_bookmarks("alice"), existing + [(new_id, "https://example.com/b")])


class ConcurrentSaveTests(unittest.TestCase):
    WORKERS = 8
    ROUNDS = 5

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bookmarks.sqlite"
        connection = sqlite3.connect(self.path)
        BookmarkService(connection)
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self, timeout=10.0):
        return sqlite3.connect(self.path, timeout=timeout)

    def count_rows(self, user_id, url):
        connection = self.connect()
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE user_id = ? AND url = ?", (user_id, url)
            ).fetchone()[0]
        finally:
            connection.close()

    def save_concurrently(self, url):
        barrier = threading.Barrier(self.WORKERS)
        ids, errors = [], []

        def worker():
            connection = self.connect()
            try:
                service = BookmarkService(connection)
                barrier.wait()
                ids.append(service.add_bookmark("alice", url))
            except Exception as error:  # surfaced through the assertion below
                errors.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(self.WORKERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        return ids

    def assert_concurrent_saves_leave_one_row(self):
        for round_number in range(self.ROUNDS):
            url = "https://example.com/concurrent-%d" % round_number
            ids = self.save_concurrently(url)
            self.assertEqual(len(ids), self.WORKERS)
            self.assertEqual(len(set(ids)), 1, ids)
            self.assertEqual(self.count_rows("alice", url), 1)

    def test_concurrent_saves_leave_one_row_with_rollback_journal(self):
        self.assert_concurrent_saves_leave_one_row()

    def test_concurrent_saves_leave_one_row_with_wal(self):
        connection = self.connect()
        connection.execute("PRAGMA journal_mode=WAL")
        connection.close()
        self.assert_concurrent_saves_leave_one_row()

    def test_lock_in_an_already_read_caller_transaction_is_raised_for_full_retry(self):
        setup = self.connect()
        setup.execute("PRAGMA journal_mode=WAL")
        setup.close()
        url = "https://example.com/a"
        caller = self.connect(timeout=0.1)
        other = self.connect()
        try:
            caller_service = BookmarkService(caller)
            caller.execute("BEGIN")
            caller.execute("SELECT COUNT(*) FROM bookmarks").fetchone()

            other_id = BookmarkService(other).add_bookmark("alice", url)

            with self.assertRaises(sqlite3.OperationalError):
                caller_service.add_bookmark("alice", url)
            self.assertTrue(caller.in_transaction)
            caller.rollback()
            self.assertEqual(self.count_rows("alice", url), 1)

            caller.execute("BEGIN")
            caller.execute("SELECT COUNT(*) FROM bookmarks").fetchone()
            self.assertEqual(caller_service.add_bookmark("alice", url), other_id)
            self.assertEqual(self.count_rows("alice", url), 1)
        finally:
            caller.close()
            other.close()

    def test_lock_in_an_already_read_caller_transaction_is_raised_for_full_retry_with_rollback_journal(self):
        url = "https://example.com/a"
        # A long busy timeout: SQLite must still fail at once, because waiting
        # while holding a read lock against a committing writer would deadlock.
        caller = self.connect(timeout=5.0)
        caller_service = BookmarkService(caller)
        caller.execute("BEGIN")
        caller.execute("SELECT COUNT(*) FROM bookmarks").fetchone()

        committing = threading.Event()
        other_result = {}

        def other_writer():
            connection = self.connect()
            connection.set_trace_callback(
                lambda statement: committing.set() if statement.strip().upper() == "COMMIT" else None
            )
            try:
                other_result["id"] = BookmarkService(connection).add_bookmark("alice", url)
            except Exception as error:  # surfaced through the assertion below
                other_result["error"] = error
            finally:
                connection.close()

        thread = threading.Thread(target=other_writer)
        thread.start()
        try:
            # The other writer has inserted and now waits for the caller's read lock.
            self.assertTrue(committing.wait(5))
            with self.assertRaises(sqlite3.OperationalError):
                caller_service.add_bookmark("alice", url)
            self.assertTrue(caller.in_transaction)
            caller.rollback()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertNotIn("error", other_result)
            self.assertEqual(self.count_rows("alice", url), 1)

            caller.execute("BEGIN")
            caller.execute("SELECT COUNT(*) FROM bookmarks").fetchone()
            self.assertEqual(caller_service.add_bookmark("alice", url), other_result["id"])
            self.assertEqual(self.count_rows("alice", url), 1)
        finally:
            if caller.in_transaction:
                caller.rollback()
            thread.join(10)
            caller.close()

    def test_busy_timeout_in_a_service_owned_transaction_raises_without_leaving_it_open(self):
        url = "https://example.com/a"
        blocker = self.connect()
        caller = self.connect(timeout=0.1)
        try:
            caller_service = BookmarkService(caller)
            blocker.execute("BEGIN IMMEDIATE")

            with self.assertRaises(sqlite3.OperationalError):
                caller_service.add_bookmark("alice", url)
            self.assertFalse(caller.in_transaction)

            blocker.rollback()
            self.assertEqual(self.count_rows("alice", url), 0)
            bookmark_id = caller_service.add_bookmark("alice", url)
            self.assertEqual(caller_service.add_bookmark("alice", url), bookmark_id)
            self.assertEqual(self.count_rows("alice", url), 1)
        finally:
            blocker.close()
            caller.close()


@unittest.skipUnless(
    hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"), "sqlite3.connect(autocommit=...) needs Python 3.12+"
)
class AutocommitModeTests(unittest.TestCase):
    """Python 3.12+ connections where Connection.commit()/rollback() may be no-ops."""

    WORKERS = 8
    ROUNDS = 5

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bookmarks.sqlite"
        connection = sqlite3.connect(self.path)
        BookmarkService(connection)
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self, autocommit=True, timeout=10.0):
        return sqlite3.connect(self.path, autocommit=autocommit, timeout=timeout)

    def count_rows(self, user_id, url):
        connection = sqlite3.connect(self.path)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE user_id = ? AND url = ?", (user_id, url)
            ).fetchone()[0]
        finally:
            connection.close()

    def run_workers(self, worker):
        threads = [threading.Thread(target=worker) for _ in range(self.WORKERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def test_saving_does_not_leave_a_transaction_open_in_memory(self):
        connection = sqlite3.connect(":memory:", autocommit=True)
        try:
            service = BookmarkService(connection)
            first_id = service.add_bookmark("u", "v")
            self.assertFalse(connection.in_transaction)
            self.assertEqual(service.add_bookmark("u", "v"), first_id)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(service.list_bookmarks("u"), [(first_id, "v")])
        finally:
            connection.close()

    def test_saved_bookmark_is_committed_and_the_write_lock_released(self):
        url = "https://example.com/a"
        writer = self.connect()
        other = sqlite3.connect(self.path, timeout=0.1)
        try:
            bookmark_id = BookmarkService(writer).add_bookmark("alice", url)
            self.assertEqual(
                other.execute("SELECT id FROM bookmarks WHERE user_id = 'alice'").fetchall(), [(bookmark_id,)]
            )
            BookmarkService(other).add_bookmark("bob", url)
            self.assertEqual(self.count_rows("bob", url), 1)
        finally:
            writer.close()
            other.close()

    def test_concurrent_saves_leave_one_committed_row(self):
        for round_number in range(self.ROUNDS):
            url = "https://example.com/autocommit-%d" % round_number
            barrier = threading.Barrier(self.WORKERS)
            ids, errors = [], []

            def worker():
                connection = self.connect()
                try:
                    service = BookmarkService(connection)
                    barrier.wait()
                    ids.append(service.add_bookmark("alice", url))
                except Exception as error:  # surfaced through the assertion below
                    errors.append(error)
                finally:
                    connection.close()

            self.run_workers(worker)
            self.assertEqual(errors, [])
            self.assertEqual(len(set(ids)), 1, ids)
            # Before the fix every connection closed with its transaction open,
            # so all callers got an ID but no row was ever committed.
            self.assertEqual(self.count_rows("alice", url), 1)

    def test_busy_timeout_in_a_service_owned_transaction_raises_without_leaving_it_open(self):
        url = "https://example.com/a"
        blocker = sqlite3.connect(self.path)
        caller = self.connect(timeout=0.1)
        try:
            caller_service = BookmarkService(caller)
            blocker.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                caller_service.add_bookmark("alice", url)
            self.assertFalse(caller.in_transaction)

            blocker.rollback()
            bookmark_id = caller_service.add_bookmark("alice", url)
            self.assertFalse(caller.in_transaction)
            self.assertEqual(caller_service.add_bookmark("alice", url), bookmark_id)
            self.assertEqual(self.count_rows("alice", url), 1)
        finally:
            blocker.close()
            caller.close()

    def test_error_after_begin_rolls_back_the_service_owned_transaction(self):
        connection = self.connect()
        try:
            service = BookmarkService(connection)
            with self.assertRaises(sqlite3.IntegrityError):
                service.add_bookmark("alice", None)  # NOT NULL fails after BEGIN IMMEDIATE
            self.assertFalse(connection.in_transaction)
            bookmark_id = service.add_bookmark("alice", "https://example.com/a")
            self.assertEqual(service.list_bookmarks("alice"), [(bookmark_id, "https://example.com/a")])
            self.assertEqual(self.count_rows("alice", "https://example.com/a"), 1)
        finally:
            connection.close()

    def test_lock_in_an_already_read_caller_transaction_needs_sql_rollback_and_full_retry(self):
        setup = sqlite3.connect(self.path)
        setup.execute("PRAGMA journal_mode=WAL")
        setup.close()
        url = "https://example.com/a"
        caller = self.connect(timeout=0.1)
        other = self.connect()
        try:
            caller_service = BookmarkService(caller)
            caller.execute("BEGIN")
            caller.execute("SELECT COUNT(*) FROM bookmarks").fetchone()

            other_id = BookmarkService(other).add_bookmark("alice", url)

            with self.assertRaises(sqlite3.OperationalError):
                caller_service.add_bookmark("alice", url)
            # The service leaves the caller's transaction for the caller to end.
            self.assertTrue(caller.in_transaction)
            caller.rollback()  # a no-op with autocommit=True
            self.assertTrue(caller.in_transaction)
            caller.execute("ROLLBACK")
            self.assertFalse(caller.in_transaction)
            self.assertEqual(self.count_rows("alice", url), 1)

            caller.execute("BEGIN")
            caller.execute("SELECT COUNT(*) FROM bookmarks").fetchone()
            self.assertEqual(caller_service.add_bookmark("alice", url), other_id)
            caller.execute("COMMIT")
            self.assertEqual(self.count_rows("alice", url), 1)
        finally:
            caller.close()
            other.close()

    def test_caller_transaction_is_left_for_the_caller_to_end(self):
        # Compatibility: add_bookmark ends a caller transaction only through
        # Connection.commit(), which does nothing with autocommit=True.
        caller = self.connect()
        try:
            service = BookmarkService(caller)
            caller.execute("BEGIN")
            kept_id = service.add_bookmark("alice", "https://example.com/kept")
            self.assertTrue(caller.in_transaction)
            self.assertEqual(self.count_rows("alice", "https://example.com/kept"), 0)
            caller.execute("COMMIT")
            self.assertEqual(self.count_rows("alice", "https://example.com/kept"), 1)

            caller.execute("BEGIN")
            service.add_bookmark("alice", "https://example.com/discarded")
            caller.execute("ROLLBACK")
            self.assertEqual(self.count_rows("alice", "https://example.com/discarded"), 0)
            self.assertEqual(service.list_bookmarks("alice"), [(kept_id, "https://example.com/kept")])
        finally:
            caller.close()

    def test_autocommit_false_commits_and_concurrent_callers_retry_without_duplicates(self):
        connection = self.connect(autocommit=False)
        try:
            service = BookmarkService(connection)
            first_id = service.add_bookmark("alice", "https://example.com/a")
            self.assertEqual(service.add_bookmark("alice", "https://example.com/a"), first_id)
            self.assertEqual(self.count_rows("alice", "https://example.com/a"), 1)
        finally:
            connection.close()

        # With autocommit=False sqlite3 always keeps a transaction open, so every
        # call is in a caller transaction: lock errors propagate and the caller
        # rolls back and retries the whole transaction.
        url = "https://example.com/pep249"
        barrier = threading.Barrier(self.WORKERS)
        ids, errors = [], []

        def worker():
            connection = self.connect(autocommit=False)
            try:
                service = BookmarkService(connection)
                barrier.wait()
                for _ in range(200):
                    try:
                        ids.append(service.add_bookmark("alice", url))
                        return
                    except sqlite3.OperationalError as error:
                        if "locked" not in str(error):
                            raise
                        connection.rollback()
                        # Back off: immediate retries can starve the writer that is committing.
                        time.sleep(random.uniform(0.001, 0.02))
                errors.append("retries exhausted")
            except Exception as error:  # surfaced through the assertion below
                errors.append(error)
            finally:
                connection.close()

        self.run_workers(worker)
        self.assertEqual(errors, [])
        self.assertEqual(len(ids), self.WORKERS)
        self.assertEqual(len(set(ids)), 1, ids)
        self.assertEqual(self.count_rows("alice", url), 1)


if __name__ == "__main__":
    unittest.main()
