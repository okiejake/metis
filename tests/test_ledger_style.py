"""No table scrolls, at any width (#98), and on a phone a ledger row takes two
lines. Reads static/styles.css; the browser check covers how it looks."""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "static" / "styles.css").read_text()


def rules(selector):
    return re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)


def test_no_table_wrap_is_a_scroll_container():
    for body in rules(".table-wrap"):
        assert not re.search(r"overflow(-x|-y)?:\s*(auto|scroll|hidden)", body)
    assert not re.search(r"\btable\s*\{[^}]*min-width:\s*[1-9]", CSS)


def test_a_phone_ledger_row_has_two_lines():
    phone = CSS[CSS.index(".ledger-table thead {"):]
    assert re.search(r'grid-template-areas:\s*"date desc desc pay chg"\s*"bill cat type bal menu"', phone)
