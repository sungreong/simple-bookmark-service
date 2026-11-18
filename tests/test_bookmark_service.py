import sqlite3
import tempfile
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


if __name__ == "__main__":
    unittest.main()
