"""On a phone, Review shows each list one row at a time, with Skip and a
position such as "3 of 45". Same page, same rows, same actions.

All data here is synthetic.
"""

import re
import shutil
import subprocess
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from conftest import add_imported
from services import SESSION_COOKIE

TODAY = date.today()
STYLES = Path(__file__).resolve().parent.parent / "static" / "styles.css"


@pytest.fixture
def page(db, user_id, accounts):
    for name in ("EXAMPLE FUEL", "ACME GROCER", "ACME DINER"):
        add_imported(db, user_id, accounts["Example Card"], "Example Card", TODAY, name, -20.0)
    db.set_user_password(user_id, "synthetic-password-for-tests")
    db.save_forecast_window(user_id, TODAY.replace(day=1), TODAY)
    with TestClient(app_module.app) as client:
        client.cookies.set(SESSION_COOKIE, db.create_session(user_id))
        return client.get("/review").text


def test_every_row_has_skip_and_every_list_has_a_position_line(page):
    # Three merchants to categorize, and the checking account with no imports yet.
    assert page.count('class="review-row"') == len(re.findall(r"<button[^>]*data-review-skip", page)) == 4
    assert page.count('<ol class="review-list"') == len(re.findall(r"<p[^>]*data-review-position", page)) >= 1


class _InlineScripts(HTMLParser):
    """Collects the body of every <script> that has no src."""

    def __init__(self):
        super().__init__()
        self.scripts = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and "src" not in dict(attrs):
            self._current = []

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None


def inline_scripts(html):
    parser = _InlineScripts()
    parser.feed(html)
    parser.close()
    return parser.scripts


def test_the_page_scripts_parse(page, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    scripts = inline_scripts(page)
    assert scripts, "no inline scripts found"
    for index, script in enumerate(scripts):
        path = tmp_path / f"script{index}.js"
        path.write_text(script)
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_the_phone_rule_shows_one_row_with_skip():
    css = STYLES.read_text()
    block = re.search(r"@media \(max-width: 640px\) \{(.*?)\n\}", css, re.S)

    assert block, "no 640px block"
    assert re.search(r"\.review-row:not\(\.is-current\)\s*\{[^}]*display:\s*none", block.group(1))
    assert re.search(r"\.review-skip[^{]*\{[^}]*display:\s*inline-flex", block.group(1))
