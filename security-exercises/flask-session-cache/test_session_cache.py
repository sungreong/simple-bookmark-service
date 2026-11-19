"""Security regression: this test is expected to fail in the exercise baseline."""
import unittest

from app import create_app


class SessionCacheTests(unittest.TestCase):
    def test_session_dependent_page_varies_by_cookie(self):
        app = create_app()
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = "synthetic-user"
            response = client.get("/bookmarks")
            self.assertEqual(response.get_data(as_text=True), "Signed-in bookmark page")
            vary = {part.strip().lower() for part in response.headers.get("Vary", "").split(",")}
            self.assertIn("cookie", vary, "Session-dependent responses must vary by Cookie")


if __name__ == "__main__":
    unittest.main()