"""The throwaway target app and its planted problems. Kept as data so the seeder is idempotent:
it compares these strings with what is already in the repo.

Planted on main:   SQL injection (app/db.py), hard-coded secret (app/config.py), a pinned
                   dependency with a known CVE (requirements.txt), lint violations
                   (app/utils.py) and a failing test (tests/test_login.py).
"""

from __future__ import annotations

DESCRIPTION = "conductor-demo-throwaway: planted bugs for the Conductor demo. Safe to delete."

MAIN: dict[str, str] = {
    "README.md": "# shop\n\nA tiny web app with deliberately planted bugs. Throwaway.\n",
    "requirements.txt": "flask==3.0.0\nrequests==2.31.0\njinja2==3.1.2\n",  # jinja2: CVE-2024-22195
    "app/__init__.py": "",
    "app/config.py": (
        '"""Settings."""\n\n'
        "DEBUG = False\n"
        'DB_PATH = "shop.db"\n'
        'API_PASSWORD = "hunter2-prod-password"  # hard-coded secret\n'
    ),
    "app/db.py": (
        '"""Database access."""\n\n'
        "import sqlite3\n\n"
        "from app.config import DB_PATH\n\n\n"
        "def connect() -> sqlite3.Connection:\n"
        "    return sqlite3.connect(DB_PATH)\n\n\n"
        "def find_user(name: str) -> tuple | None:\n"
        "    cursor = connect().cursor()\n"
        "    # SQL injection: the name is formatted into the query\n"
        "    cursor.execute(\"SELECT * FROM users WHERE name = '%s'\" % name)\n"
        "    return cursor.fetchone()\n"
    ),
    "app/utils.py": (
        "import os\nimport sys\n\n\n"
        "def slugify(s):\n"
        "    return s.lower().replace(' ', '-')  # a deliberately long line, past one hundred characters, to trip E501\n"
    ),
    "tests/__init__.py": "",
    "tests/test_login.py": (
        "from app.utils import slugify\n\n\n"
        "def test_slugify():\n"
        "    assert slugify('Hello World') == 'hello-world'\n\n\n"
        "def test_login_normalises_name():\n"
        "    # planted failing test: slugify does not strip, so this fails\n"
        "    assert slugify('  Hello World ') == 'hello-world'\n"
    ),
}

PR1_BRANCH = "pr1-order-lookup"
PR1_FILES: dict[str, str] = {
    "app/orders.py": (
        '"""Order lookups."""\n\n'
        "from app.db import connect\n\n\n"
        "def get_order(order_id: str) -> tuple | None:\n"
        "    cursor = connect().cursor()\n"
        '    cursor.execute(f"SELECT * FROM orders WHERE id = {order_id}")\n'
        "    return cursor.fetchone()\n"
    ),
}

PR2_BRANCH = "fix/sql-injection"
PR2_FILES: dict[str, str] = {
    "app/db.py": MAIN["app/db.py"] + "\n\ndef search_users(term: str) -> list[tuple]:\n"
    "    cursor = connect().cursor()\n"
    '    cursor.execute("SELECT * FROM users WHERE name LIKE \'%" + term + "%\'")\n'
    "    return cursor.fetchall()\n",
}

PULLS = [
    (PR1_BRANCH, "Add order lookup", "Adds get_order(). Simple scenario that teaches memory."),
    (PR2_BRANCH, "Add user search", "Adds search_users(). The main demo scenario."),
]
