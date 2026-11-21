import sqlite3
import unittest

from bookmark_extras import BookmarkExtras
from bookmark_service import BookmarkService


class ScenarioBase(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.service = BookmarkService(self.connection)
        self.extras = BookmarkExtras(self.connection)


class DeleteOwnershipTests(ScenarioBase):
    def test_cannot_delete_another_users_bookmark(self):
        bookmark = self.service.add_bookmark("bob", "https://example.com/b")
        self.assertFalse(self.extras.delete("alice", bookmark))
        self.assertEqual(self.service.list_bookmarks("bob"), [(bookmark, "https://example.com/b")])

    def test_owner_can_delete_own_bookmark(self):
        mine = self.service.add_bookmark("alice", "https://example.com/a")
        other = self.service.add_bookmark("bob", "https://example.com/b")
        self.assertTrue(self.extras.delete("alice", mine))
        self.assertEqual(self.service.list_bookmarks("alice"), [])
        self.assertEqual(self.service.list_bookmarks("bob"), [(other, "https://example.com/b")])

    def test_deleting_missing_or_already_deleted_id_returns_false(self):
        mine = self.service.add_bookmark("alice", "https://example.com/a")
        self.assertFalse(self.extras.delete("alice", mine + 100))
        self.assertTrue(self.extras.delete("alice", mine))
        self.assertFalse(self.extras.delete("alice", mine))


class LiteralSearchTests(ScenarioBase):
    def test_percent_and_underscore_are_literal_text(self):
        url = "https://example.com/report_100%"
        wanted = self.service.add_bookmark("alice", url)
        self.service.add_bookmark("alice", "https://example.com/reportX100other")
        self.assertEqual(self.extras.search("alice", "_100%"), [(wanted, url)])


class PaginationTests(ScenarioBase):
    def test_first_page_starts_at_first_bookmark(self):
        urls = [f"https://example.com/{n}" for n in range(3)]
        rows = [(self.service.add_bookmark("alice", url), url) for url in urls]
        self.assertEqual(self.extras.page("alice", 1, 2), rows[:2])
