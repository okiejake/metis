"""Text from a form must not be able to make the server work for seconds, and a
redirect must stay on this site. These pin the behavior of the email check, the
rule-seed suggestion and the redirect helper, and that each takes time in
proportion to its input rather than its square.

Pure functions, no database.
"""

import time

import pytest

import services
from routes.expected import _reconcile_path

# Long enough that the old patterns took seconds; linear code takes milliseconds.
CRAFTED_LENGTH = 40_000
FAST = 0.5


def elapsed(fn, *args):
    start = time.perf_counter()
    try:
        fn(*args)
    except ValueError:
        pass
    return time.perf_counter() - start


@pytest.mark.parametrize(
    "email",
    ["a@b.co", "First.Last@Example.com", "x@sub.example.org", "a@b..com", "  a@b.co  "],
)
def test_a_valid_email_is_accepted_lowercased(email):
    assert services.normalize_user_email(email) == email.strip().lower()


@pytest.mark.parametrize(
    "email",
    ["no-at-sign", "a@b", "a@@b.com", "a@b.com@", "a b@c.com", "@b.com", "a@.com", "a@b.", "a@b .com"],
)
def test_an_invalid_email_is_refused(email):
    with pytest.raises(ValueError):
        services.normalize_user_email(email)


def test_an_empty_email_means_none():
    assert services.normalize_user_email("   ") == ""


def test_a_crafted_email_is_checked_quickly():
    crafted = "a@" + "b." * (CRAFTED_LENGTH // 2) + "@"

    assert elapsed(services.normalize_user_email, crafted) < FAST


@pytest.mark.parametrize(
    "description, seed",
    [
        ("ACH ACME UTILITIES TYPE: PAYMENT ID: 0000", "ACME UTILITIES"),
        ("ach   acme   utilities type: payment", "ACME UTILITIES"),
        ("ACH TYPE: X TYPE: Y", "TYPE: X"),
        ("ACME COFFEE #1234 SPRINGFIELD", "ACME COFFEE"),
        ("ACH ACME WITHOUT A TYPE", "ACH ACME WITHOUT"),
        ("", ""),
    ],
)
def test_the_rule_seed_is_unchanged(description, seed):
    assert services.suggest_rule_pattern(description) == seed


def test_a_crafted_description_seeds_quickly():
    crafted = "ACH " * (CRAFTED_LENGTH // 4)

    assert elapsed(services.suggest_rule_pattern, crafted) < FAST


def test_a_redirect_stays_on_this_site():
    location = services.redirect_with_message("//evil.example/x", "Saved").headers["location"]

    assert location.startswith("/dashboard?")


def test_a_redirect_keeps_a_same_site_path_and_its_query():
    location = services.redirect_with_message("/ledger?start=2026-01-01", "Saved").headers["location"]

    assert location == "/ledger?start=2026-01-01&msg=Saved&err=0"


def test_a_reconcile_path_is_built_only_for_known_item_types():
    assert _reconcile_path("recurring", 3) == "/expected/recurring/3/reconcile"
    assert _reconcile_path("/evil.example", 3) == "/budget"
