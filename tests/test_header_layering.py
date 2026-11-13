"""The profile menu drops down from the header over the page below it. The
header gives itself a z-index to stay on top, but a z-index only takes effect
on a positioned element. With the header left static, page sections that form
their own layer (anything with a backdrop filter, a transform or a position)
were painted over the open menu.

Reads the stylesheet; no browser.
"""

import re
from pathlib import Path

STYLES = Path(__file__).resolve().parent.parent / "static" / "styles.css"


def declarations(selector):
    """Every declaration in top-level rules for exactly this selector, last one wins."""
    found = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", STYLES.read_text()):
        if selector in [s.strip() for s in selectors.split(",")]:
            for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", body):
                found[prop.strip()] = value.strip()
    return found


def test_the_header_is_positioned_so_its_layer_applies():
    header = declarations(".site-header")

    assert "z-index" in header
    assert header.get("position", "static") in {"relative", "sticky", "absolute", "fixed"}
