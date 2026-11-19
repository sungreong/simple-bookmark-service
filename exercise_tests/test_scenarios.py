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

    def test_pages_continue_without_gaps_or_overlap(self):
        rows = [
            (self.service.add_bookmark("alice", f"https://example.com/{n}"), f"https://example.com/{n}")
            for n in range(5)
        ]
        pages = [self.extras.page("alice", number, 2) for number in range(1, 4)]
        self.assertEqual(pages, [rows[:2], rows[2:4], rows[4:]])
        self.assertEqual(self.extras.page("alice", 4, 2), [])

    def test_pages_only_include_requested_user(self):
        alice_first = self.service.add_bookmark("alice", "https://example.com/a1")
        bob = self.service.add_bookmark("bob", "https://example.com/b")
        alice_second = self.service.add_bookmark("alice", "https://example.com/a2")
        self.assertEqual(
            self.extras.page("alice", 1, 2),
            [(alice_first, "https://example.com/a1"), (alice_second, "https://example.com/a2")],
        )
        self.assertEqual(self.extras.page("bob", 1, 2), [(bob, "https://example.com/b")])

    def test_page_number_and_size_must_be_positive(self):
        for page_number, page_size in [(0, 2), (-1, 2), (1, 0), (1, -1)]:
            with self.subTest(page_number=page_number, page_size=page_size):
                with self.assertRaises(ValueError):
                    self.extras.page("alice", page_number, page_size)

    def test_empty_user_and_single_item_pages(self):
        self.service.add_bookmark("bob", "https://example.com/b")
        self.assertEqual(self.extras.page("alice", 1, 1), [])
        rows = [
            (self.service.add_bookmark("alice", f"https://example.com/{n}"), f"https://example.com/{n}")
            for n in range(2)
        ]
        self.assertEqual(self.extras.page("alice", 1, 1), rows[:1])
        self.assertEqual(self.extras.page("alice", 2, 1), rows[1:])
        self.assertEqual(self.extras.page("alice", 3, 1), [])
        self.assertEqual(self.extras.page("alice", 2, 2), [])

    def test_large_positive_page_values(self):
        row = (self.service.add_bookmark("alice", "https://example.com/a"), "https://example.com/a")
        huge = 1 << 63
        self.assertEqual(self.extras.page("alice", 1, huge), [row])
        self.assertEqual(self.extras.page("alice", huge, 1), [])
        self.assertEqual(self.extras.page("alice", 2, huge), [])
