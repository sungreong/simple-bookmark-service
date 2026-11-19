"""An isolated session-dependent page for a security exercise."""
from flask import Flask, session


def create_app():
    app = Flask(__name__)
    # Test clients only; this value is not a real credential.
    app.config.update(SECRET_KEY="exercise-only-not-for-deployment", TESTING=True)

    @app.get("/bookmarks")
    def bookmarks():
        # Key-only session access: affected Flask releases may omit Vary: Cookie.
        if "user_id" in session:
            return "Signed-in bookmark page"
        return "Guest bookmark page"

    return app