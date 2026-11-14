"""UI copy is lower case (#24): template text and the display attributes
(placeholder, title, aria-label, alt). Jinja expressions, scripts and user data
are not checked here; they keep their own case."""

import re
from pathlib import Path

import pytest

TEMPLATES = sorted((Path(__file__).resolve().parent.parent / "templates").glob("*.html"))
TOKEN = re.compile(r"(\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}|<script\b.*?</script>|<style\b.*?</style>|<[^>]+>)", re.S | re.I)
DISPLAY_ATTR = re.compile(r'\b(?:placeholder|title|aria-label|alt)="([^"]*)"', re.I)
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def copy_with_capitals(source):
    found = []
    for part in TOKEN.split(source):
        if not part or part.startswith("{") or re.match(r"<(script|style)\b", part, re.I):
            continue
        texts = DISPLAY_ATTR.findall(part) if part.startswith("<") else [part]
        found += [text.strip() for text in texts if re.search(r"[A-Z]", JINJA.sub("", text))]
    return found


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
def test_template_copy_is_lower_case(template):
    assert copy_with_capitals(template.read_text()) == []


CSS = (Path(__file__).resolve().parent.parent / "static" / "styles.css").read_text()


def test_everything_shown_is_lower_case_except_passwords():
    """One CSS rule lowercases every displayed word (data included) without
    changing what is stored; passwords keep their case."""
    assert re.search(r"\nbody \{[^}]*text-transform:\s*lowercase", CSS)
    assert re.search(r"input,\s*select,\s*textarea,\s*button \{[^}]*text-transform:\s*inherit", CSS)
    assert re.search(r'input\[type="password"\] \{[^}]*text-transform:\s*none', CSS)


def test_no_rule_turns_lower_case_back_off():
    rules = re.findall(r"([^{}]+)\{[^}]*text-transform:\s*(none|uppercase|capitalize)", CSS)
    assert [selector.strip() for selector, _ in rules] == ['input[type="password"]']


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
def test_dropdown_options_showing_data_are_lowered_at_the_source(template):
    """Native dropdown menus ignore CSS text-transform."""
    for text in re.findall(r"<option[^>]*>(.*?)</option>", template.read_text(), re.S):
        if "{{" in text:
            assert text.startswith("{% filter lower %}") and text.endswith("{% endfilter %}"), text
