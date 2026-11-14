"""The picker follows docs/design/neumorphism.md: the brief's tokens in both
themes, a visible focus outline, and no press motion under reduced motion."""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "static" / "styles.css").read_text()
TOKENS = ["--neu-base", "--neu-blue", "--neu-pink", "--neu-light", "--neu-dark", "--neu-accent", "--neu-heading", "--neu-text", "--neu-edge"]


def block(selector):
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_light_tokens_use_the_brief_values():
    root = block(":root")
    for token, value in [("--neu-base", "#E8E8E8"), ("--neu-blue", "#C8E0F4"), ("--neu-pink", "#F5E0E8"),
                         ("--neu-light", "#FFFFFF"), ("--neu-dark", "#D0D0D0"), ("--neu-accent", "#7B68EE")]:
        assert re.search(rf"{token}:\s*{value};", root, re.I), token


def test_dark_theme_revalues_every_token():
    dark = block(':root[data-theme="dark"]')
    for token in TOKENS:
        assert f"{token}:" in dark, token


def test_focus_outline_uses_the_accent():
    assert re.search(r"\.neu-press:focus-visible[^{]*\{[^}]*outline:\s*3px solid var\(--neu-accent\)", CSS)


def test_no_press_motion_under_reduced_motion():
    reduced = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", CSS, re.S)
    assert reduced and re.search(r"\.neu-press[^{]*\{[^}]*transition:\s*none", reduced.group(1))
