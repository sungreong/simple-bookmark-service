import multiprocessing
import random
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from bookmark_service import BookmarkService


def _save_in_subprocess(path, user_id, url, barrier, results):
    """Run in a separate process: open an own connection and save once."""
    try:
        connection = sqlite3.connect(path, timeout=30.0)
        try:
            service = BookmarkService(connection)
            barrier.wait(60)
            results.put(("id", service.add_bookmark(user_id, url)))
        finally:
            connection.close()
    except BaseException as error:  # reported to the parent test
        results.put(("error", repr(error)))


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

    def assert_each_url_is_its_own_bookmark(self, urls):
        ids = [self.service.add_bookmark("alice", url) for url in urls]
        self.assertEqual(len(set(ids)), len(urls), ids)
        self.assertEqual([self.service.add_bookmark("alice", url) for url in urls], ids)
        self.assertEqual(self.service.list_bookmarks("alice"), list(zip(ids, urls)))

    def test_urls_are_compared_as_exact_strings(self):
        # No normalization: case and a trailing slash make a different bookmark.
        self.assert_each_url_is_its_own_bookmark(
            ["https://example.com/a", "https://example.com/A", "https://example.com/a/", "HTTPS://EXAMPLE.COM/a"]
        )

    def test_urls_with_surrounding_whitespace_are_stored_unchanged(self):
        self.assert_each_url_is_its_own_bookmark(
            ["https://example.com/a", " https://example.com/a", "https://example.com/a ", "\thttps://example.com/a\n"]
        )

    def test_empty_and_whitespace_only_urls_are_stored_unchanged(self):
        # URL validation is out of scope here (issue #3): as at the base commit
        # these are accepted, and now they are deduplicated like any other string.
        # A fix for issue #3 is expected to change this test.
        self.assert_each_url_is_its_own_bookmark(["", " ", "   "])

    def test_direct_insert_outside_the_service_is_not_prevented(self):
        # No UNIQUE constraint was added (it cannot be created on a database that
        # already holds duplicates), so writes that bypass the service can still
        # create a duplicate. The service then keeps returning the smallest ID.
        unique_indexes = [row for row in self.connection.execute("PRAGMA index_list(bookmarks)") if row[2]]
        self.assertEqual(unique_indexes, [])

        url = "https://example.com/a"
        first_id = self.service.add_bookmark("alice", url)
        self.connection.execute("INSERT INTO bookmarks (user_id, url) VALUES (?, ?)", ("alice", url))
        self.connection.commit()
        self.assertEqual(len(self.service.list_bookmarks("alice")), 2)

        self.assertEqual(self.service.add_bookmark("alice", url), first_id)
        self.assertEqual(len(self.service.list_bookmarks("alice")), 2)

    def test_default_connection_is_not_shared_with_another_thread(self):
        # Sharing one Connection across threads is outside the guarantee. With the
        # default check_same_thread=True sqlite3 rejects it before anything is
        # written; only an explicit check_same_thread=False allows it.
        errors = []

        def other_thread():
            try:
                self.service.add_bookmark("alice", "https://example.com/a")
            except sqlite3.ProgrammingError as error:
                errors.append(error)

        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1, errors)
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.service.list_bookmarks("alice"), [])


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

    def all_rows(self):
        return self.connection.execute("SELECT id, user_id, url FROM bookmarks ORDER BY id").fetchall()

    def test_existing_duplicates_are_kept_and_no_new_duplicate_is_added(self):
        service = BookmarkService(self.connection)
        existing = [(1, "https://example.com/a"), (2, "https://example.com/a")]
        self.assertEqual(service.list_bookmarks("alice"), existing)

        self.assertEqual(service.add_bookmark("alice", "https://example.com/a"), 1)
        self.assertEqual(service.list_bookmarks("alice"), existing)

        new_id = service.add_bookmark("alice", "https://example.com/b")
        self.assertEqual(service.add_bookmark("alice", "https://example.com/b"), new_id)
        self.assertEqual(service.list_bookmarks("alice"), existing + [(new_id, "https://example.com/b")])

    def test_service_initializes_on_existing_duplicates_without_changing_them(self):
        before = self.all_rows()
        service = BookmarkService(self.connection)  # must not fail or touch the rows
        self.assertEqual(self.all_rows(), before)
        self.assertFalse(self.connection.in_transaction)

        # Another user can still save the same URL, and lists stay per user.
        bob_id = service.add_bookmark("bob", "https://example.com/a")
        self.assertNotIn(bob_id, (1, 2))
        self.assertEqual(service.add_bookmark("bob", "https://example.com/a"), bob_id)
        self.assertEqual(service.list_bookmarks("bob"), [(bob_id, "https://example.com/a")])
        self.assertEqual(
            service.list_bookmarks("alice"), [(1, "https://example.com/a"), (2, "https://example.com/a")]
        )
        self.assertEqual(self.all_rows(), before + [(bob_id, "bob", "https://example.com/a")])


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
            # A TEMP table write is part of the caller's transaction but takes no
            # lock on the main database, so it shows whether anything committed it.
            caller.execute("CREATE TEMP TABLE caller_work (note TEXT)")
            caller.execute("BEGIN")
            caller.execute("SELECT COUNT(*) FROM bookmarks").fetchone()
            caller.execute("INSERT INTO caller_work (note) VALUES ('before save')")

            other_id = BookmarkService(other).add_bookmark("alice", url)

            with self.assertRaises(sqlite3.OperationalError):
                caller_service.add_bookmark("alice", url)
            # The service neither committed nor rolled back the caller's transaction.
            self.assertTrue(caller.in_transaction)
            self.assertEqual(caller.execute("SELECT note FROM caller_work").fetchall(), [("before save",)])
            caller.rollback()
            self.assertEqual(caller.execute("SELECT note FROM caller_work").fetchall(), [])
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


class MultiProcessSaveTests(unittest.TestCase):
    """Separate processes, each with its own connection to the same DB file."""

    PROCESSES = 4
    ROUNDS = 3

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bookmarks.sqlite"
        connection = sqlite3.connect(self.path)
        BookmarkService(connection)
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def count_rows(self, user_id, url):
        connection = sqlite3.connect(self.path)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE user_id = ? AND url = ?", (user_id, url)
            ).fetchone()[0]
        finally:
            connection.close()

    def test_concurrent_saves_from_separate_processes_leave_one_row(self):
        # spawn works the same on Linux and Windows and avoids forking a
        # process that has threads or open SQLite connections.
        context = multiprocessing.get_context("spawn")
        for round_number in range(self.ROUNDS):
            url = "https://example.com/process-%d" % round_number
            barrier = context.Barrier(self.PROCESSES)
            results = context.Queue()
            processes = [
                context.Process(
                    target=_save_in_subprocess, args=(str(self.path), "alice", url, barrier, results)
                )
                for _ in range(self.PROCESSES)
            ]
            for process in processes:
                process.start()
            try:
                outcomes = [results.get(timeout=120) for _ in processes]
            finally:
                for process in processes:
                    process.join(30)
                    if process.is_alive():
                        process.terminate()
                        process.join()
            errors = [value for kind, value in outcomes if kind == "error"]
            ids = [value for kind, value in outcomes if kind == "id"]
            self.assertEqual(errors, [])
            self.assertEqual([process.exitcode for process in processes], [0] * self.PROCESSES)
            self.assertEqual(len(ids), self.PROCESSES)
            self.assertEqual(len(set(ids)), 1, ids)
            self.assertEqual(self.count_rows("alice", url), 1)


class IsolationLevelNoneTests(unittest.TestCase):
    """isolation_level=None: sqlite3's autocommit mode on every supported Python (3.9+)."""

    WORKERS = 8
    ROUNDS = 3

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bookmarks.sqlite"
        connection = sqlite3.connect(self.path)
        BookmarkService(connection)
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self, timeout=10.0):
        return sqlite3.connect(self.path, isolation_level=None, timeout=timeout)

    def count_rows(self, user_id, url):
        connection = sqlite3.connect(self.path)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE user_id = ? AND url = ?", (user_id, url)
            ).fetchone()[0]
        finally:
            connection.close()

    def test_saving_is_committed_and_leaves_no_transaction_open(self):
        url = "https://example.com/a"
        connection = self.connect()
        try:
            service = BookmarkService(connection)
            first_id = service.add_bookmark("alice", url)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(service.add_bookmark("alice", url), first_id)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(self.count_rows("alice", url), 1)
        finally:
            connection.close()

    def test_caller_transaction_is_joined_and_committed_on_success(self):
        url = "https://example.com/a"
        connection = self.connect()
        try:
            service = BookmarkService(connection)
            connection.execute("BEGIN")
            first_id = service.add_bookmark("alice", url)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(self.count_rows("alice", url), 1)
            connection.execute("BEGIN")
            self.assertEqual(service.add_bookmark("alice", url), first_id)
            self.assertEqual(self.count_rows("alice", url), 1)
        finally:
            connection.close()

    def test_concurrent_saves_leave_one_row(self):
        for round_number in range(self.ROUNDS):
            url = "https://example.com/isolation-none-%d" % round_number
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
            self.assertEqual(len(ids), self.WORKERS)
            self.assertEqual(len(set(ids)), 1, ids)
            self.assertEqual(self.count_rows("alice", url), 1)


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
            # Ending the service-owned transaction with Connection.commit() would
            # be a no-op here: every caller would get an ID but no row would be
            # committed.
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


class _BaseCommitBookmarkService(BookmarkService):
    """add_bookmark exactly as it was at base commit f07bdd2 (always INSERT).

    Only used by BaseCommitReproductionTests; __init__ and list_bookmarks are
    unchanged since that commit.
    """

    def add_bookmark(self, user_id, url):
        cursor = self.connection.execute(
            "INSERT INTO bookmarks (user_id, url) VALUES (?, ?)", (user_id, url)
        )
        self.connection.commit()
        return cursor.lastrowid


class BaseCommitReproductionTests(unittest.TestCase):
    """Run regression tests against the base commit's add_bookmark: they must catch the defect."""

    EXPECTED_TO_FAIL = (
        (BookmarkServiceTests, "test_saving_the_same_bookmark_twice_is_idempotent"),
        (BookmarkServiceTests, "test_saving_twice_inside_a_caller_transaction_is_idempotent"),
        (ExistingDuplicateDataTests, "test_existing_duplicates_are_kept_and_no_new_duplicate_is_added"),
        (ExistingDuplicateDataTests, "test_service_initializes_on_existing_duplicates_without_changing_them"),
        (ConcurrentSaveTests, "test_concurrent_saves_leave_one_row_with_rollback_journal"),
        (ConcurrentSaveTests, "test_concurrent_saves_leave_one_row_with_wal"),
    )
    EXPECTED_TO_PASS = (
        (BookmarkServiceTests, "test_save_and_list_bookmarks_for_one_user"),
        (BookmarkServiceTests, "test_same_url_can_be_saved_by_different_users"),
    )

    @staticmethod
    def run_against_base_code(case_class, name):
        result = unittest.TestResult()
        # The test classes look BookmarkService up as a module global at run time.
        with mock.patch.dict(globals(), {"BookmarkService": _BaseCommitBookmarkService}):
            case_class(name).run(result)
        return result

    @staticmethod
    def report(name, result):
        # Written to the test log as reproduction evidence. Worded so it cannot be
        # mistaken for a unittest result line of this suite.
        problems = result.failures + result.errors
        if problems:
            outcome = "defect detected -> %s" % problems[0][1].strip().splitlines()[-1]
        else:
            outcome = "passes (not affected by the defect)"
        sys.stderr.write("\n[base f07bdd2 add_bookmark] %s: %s" % (name, outcome))
        sys.stderr.flush()

    def test_regression_tests_fail_against_the_base_commit_code(self):
        for case_class, name in self.EXPECTED_TO_FAIL:
            result = self.run_against_base_code(case_class, name)
            self.report(name, result)
            with self.subTest(test=name):
                self.assertEqual(result.errors, [])
                self.assertEqual(len(result.failures), 1, result.failures)
                if name == "test_saving_the_same_bookmark_twice_is_idempotent":
                    self.assertIn("AssertionError: 1 != 2", result.failures[0][1])

        for case_class, name in self.EXPECTED_TO_PASS:
            result = self.run_against_base_code(case_class, name)
            self.report(name, result)
            with self.subTest(test=name):
                self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        sys.stderr.write("\n")


if __name__ == "__main__":
    unittest.main()
