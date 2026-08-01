"""`next` / `next_path` come straight off the wire, so whatever they hold ends up
in a Location header. Only a same-site absolute path may survive; anything that a
browser would resolve to another origin has to fall back to the dashboard.

The subtle cases are the ones a naive "starts with / but not //" check lets past.
Browsers normalize a backslash to a forward slash in the authority position, and
strip tab, newline and carriage return from anywhere in a URL — so `/\\host`,
`/\\/host` and `/<tab>/host` all resolve to the protocol-relative `//host`.

Pure function, no database.
"""

import pytest

from services import resolve_safe_redirect_target

FALLBACK = "/dashboard"


@pytest.mark.parametrize(
    "target",
    [
        "/ledger",
        "/budget?start=2026-01-01&end=2026-12-31",
        "/accounts/3/edit",
        "/",
    ],
)
def test_same_site_paths_are_preserved(target):
    assert resolve_safe_redirect_target(target) == target


@pytest.mark.parametrize(
    "target",
    [
        "//evil.example",
        "https://evil.example",
        "http://evil.example",
        "\\\\evil.example",
        "evil.example",
        "",
    ],
)
def test_obvious_off_site_targets_fall_back(target):
    assert resolve_safe_redirect_target(target) == FALLBACK


@pytest.mark.parametrize(
    "target",
    [
        "/\\evil.example",
        "/\\/evil.example",
        "/\t/evil.example",
        "/\n/evil.example",
        "/\r/evil.example",
        "/\\\t/evil.example",
    ],
)
def test_browser_normalized_off_site_targets_fall_back(target):
    """A browser rewrites each of these into `//evil.example` before resolving
    the Location header, so the redirect leaves the site."""
    assert resolve_safe_redirect_target(target) == FALLBACK


def test_leading_and_trailing_whitespace_is_ignored():
    assert resolve_safe_redirect_target("  /ledger  ") == "/ledger"
    assert resolve_safe_redirect_target("  //evil.example  ") == FALLBACK


def test_a_path_is_never_rewritten_into_another_path():
    """Sanitizing must not silently redirect somewhere unrelated: either the
    caller's own path comes back, or the fallback does."""
    for target in ("/ledger", "/\\evil.example", "//evil.example"):
        result = resolve_safe_redirect_target(target)
        assert result == FALLBACK or result == target.strip()
