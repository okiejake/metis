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


def test_a_phone_ledger_row_puts_date_description_amount_and_menu_first():
    phone = CSS[CSS.index(".ledger-table thead {"):]
    order = {name: int(n) for name, n in re.findall(r"\.ledger-table \.(ledger-[a-z]+|row-action-cell) \{ order: (\d+)", phone)}
    first_line = ["ledger-date", "ledger-description", "ledger-payments", "ledger-charges", "row-action-cell"]
    second_line = ["ledger-bill", "ledger-category", "ledger-type", "ledger-balance"]
    assert max(order[name] for name in first_line) < min(order[name] for name in second_line)


def test_text_in_a_table_cell_never_breaks_a_word_apart_to_fit():
    cell_rules = re.findall(r"(?:^|\n)\s*td,\s*th\s*\{([^}]*)\}", CSS)
    assert cell_rules and all("overflow-wrap: anywhere" not in body for body in cell_rules)


def test_the_ledger_bar_is_layered_so_the_filter_opens_over_the_ledger():
    body = " ".join(rules(".ledger-bar"))
    assert re.search(r"position:\s*relative", body) and re.search(r"z-index:\s*[1-9]", body)
