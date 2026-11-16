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


def test_a_phone_ledger_row_puts_description_amount_and_edit_first():
    phone = CSS[CSS.index("@media (max-width: 760px) {\n  .ledger-table,"):]
    order = {name: int(n) for name, n in re.findall(r"\.ledger-table \.(ledger-[a-z]+|row-action-cell) \{ order: (\d+)", phone)}
    first_line = ["ledger-main", "ledger-amount", "row-action-cell"]
    assert max(order[name] for name in first_line) < order["ledger-balance"]


def test_text_in_a_table_cell_never_breaks_a_word_apart_to_fit():
    cell_rules = re.findall(r"(?:^|\n)\s*td,\s*th\s*\{([^}]*)\}", CSS)
    assert cell_rules and all("overflow-wrap: anywhere" not in body for body in cell_rules)


def test_the_ledger_bar_is_layered_so_the_filter_opens_over_the_ledger():
    body = " ".join(rules(".ledger-bar"))
    assert re.search(r"position:\s*relative", body) and re.search(r"z-index:\s*[1-9]", body)


def test_the_edit_button_shows_only_on_a_hovered_or_focused_row_where_there_is_hover():
    hover = re.search(r"@media \(hover: hover\) \{(.*?)\n\}", CSS, re.S).group(1)
    assert re.search(r"\.row-edit\s*\{[^}]*opacity:\s*0", hover)
    assert re.search(r"\.ledger-row:hover \.row-edit,\s*\.ledger-row:focus-within \.row-edit\s*\{[^}]*opacity:\s*1", hover)


def test_the_date_summary_and_forecast_rows_are_centered_and_ignore_hover():
    for row in ("date-row", "month-summary-row", "forecast-divider-row"):
        assert re.search(rf"\.ledger-table tr\.{row} td[^{{]*\{{[^}}]*text-align:\s*center", CSS) or \
            re.search(rf"tr\.{row}[^{{]*\{{[^}}]*justify-content:\s*center", CSS), row
    still = re.search(r"\.ledger-table tbody tr:is\(([^)]*)\):hover\s*\{([^}]*)\}", CSS)
    assert still and all(name in still.group(1) for name in (".date-row", ".month-summary-row", ".forecast-divider-row"))
    assert "background: transparent" in still.group(2)


def test_no_lines_run_between_ledger_rows():
    body = " ".join(rules(".ledger-table td"))
    assert re.search(r"border-bottom:\s*0", body)


def test_the_ledger_summary_cards_keep_to_their_content_from_the_left():
    body = " ".join(rules(".ledger-summary"))
    assert re.search(r"display:\s*flex", body) and re.search(r"justify-content:\s*flex-start", body)
    assert re.search(r"flex:\s*none", " ".join(rules(".ledger-summary .compact-card")))


def test_the_mode_toggle_is_centered_and_its_segments_touch():
    assert re.search(r"justify-self:\s*center", " ".join(rules(".ledger-modes")))
    assert re.search(r"gap:\s*0", " ".join(rules(".ledger-modes")))


def test_the_description_box_is_a_fixed_width_so_the_pills_line_up():
    assert re.search(r"flex:\s*0 1 45ch", " ".join(rules(".ledger-line .ledger-description")))


def test_the_description_is_indented_by_the_edit_buttons_space():
    edge = re.search(r"--ledger-edge:\s*([\d.]+rem)", CSS)
    assert edge
    assert re.search(r"padding-left:\s*var\(--ledger-edge\)", " ".join(rules(".ledger-table .ledger-main")))
    assert re.search(r"width:\s*var\(--ledger-edge\)", " ".join(rules(".ledger-table .row-action-cell")))


def test_current_balance_sits_at_the_far_right_of_the_cards():
    assert re.search(r"margin-left:\s*auto", " ".join(rules(".ledger-summary .current-balance-card")))
