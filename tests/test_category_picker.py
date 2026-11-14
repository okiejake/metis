"""The one category picker (#27): a select with an optgroup per parent, so it
works without script, turned into a type-ahead box by one shared script.

Renders macros and reads static files; no database.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import app  # noqa: F401  registers the template globals and filters
from web import templates

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "static" / "category-picker.js"
CATEGORIES = [
    {"id": 1, "name": "Auto", "label": "Auto", "parent_id": None},
    {"id": 2, "name": "Food", "label": "Food", "parent_id": None},
    {"id": 3, "name": "Dining", "label": "Food › Dining", "parent_id": 2},
    {"id": 4, "name": "Groceries", "label": "Food › Groceries", "parent_id": 2},
]


def picker(*args, **kwargs):
    return str(templates.env.get_template("_parts.html").module.category_picker(*args, **kwargs))


def test_a_parent_and_its_children_share_an_optgroup():
    html = picker("category_id", CATEGORIES, 4)

    group = re.search(r'<optgroup label="food">(.*?)</optgroup>', html, re.S)
    assert group, html
    assert 'value="2"' in group.group(1) and 'value="3"' in group.group(1) and 'value="4"' in group.group(1)
    assert re.search(r'<option value="4"\s+selected', group.group(1))
    assert 'value="1"' not in group.group(1) and 'value="1"' in html


def test_children_read_with_their_parent():
    assert "food › groceries" in picker("category_id", CATEGORIES)


def test_every_picker_is_marked_for_the_shared_script():
    assert "data-category-picker" in picker("category_id", CATEGORIES)


def test_a_list_row_picker_submits_its_row_and_a_form_picker_does_not():
    assert "data-picker-submit" in picker("choice", CATEGORIES, submit=True)
    assert "data-picker-submit" not in picker("category_id", CATEGORIES)


def test_the_page_loads_the_shared_script_once():
    base = (ROOT / "templates" / "base.html").read_text()

    assert base.count("/static/category-picker.js") == 1
    others = [p.name for p in (ROOT / "templates").glob("*.html") if p.name != "base.html"
              and "category-picker.js?" in p.read_text()]
    assert others == []


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_script_parses():
    result = subprocess.run([shutil.which("node"), "--check", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_no_page_wires_new_category_by_hand():
    """The script owns revealing the new-category name; pages don't repeat it."""
    offenders = [p.name for p in (ROOT / "templates").glob("*.html") if "[data-new-category]" in p.read_text()]
    assert offenders == []
