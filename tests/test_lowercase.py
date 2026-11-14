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
