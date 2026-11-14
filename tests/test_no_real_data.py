"""The privacy guard: no tracked file may hold what looks like real personal or
financial data. See the data policy in CLAUDE.md.

The samples that must be flagged are put together at run time, so this file
holds none of them as written. All data here is synthetic.
"""

from pathlib import Path

import pytest

from tools import privacy_guard as guard

ROOT = Path(__file__).resolve().parent.parent
AT = "@"
USERS = "/" + "Users" + "/"
HOME = "/" + "home" + "/"
TAILNET = ".ts" + ".net"


def rules(text):
    return [finding.rule for finding in guard.scan_text(text)]


@pytest.mark.parametrize(
    "text, rule",
    [
        ("write to jane.roe" + AT + "mailhost.test", "email"),
        ("jane" + AT + "somewhere.org signed up", "email"),
        ("ssh root" + AT + "192." + "168.1.20", "private address"),
        ("the box is at 10." + "0.0.7 on the lan", "private address"),
        ("reach it on 172." + "20.4.9", "private address"),
        ("tailnet address 100." + "101.2.3", "private address"),
        ("open https://nas.fancy-llama" + TAILNET + "/metis", "tailnet host"),
        ("saved in " + USERS + "someone/code/app.py", "home path"),
        ("log at " + HOME + "someone/metis.log", "home path"),
        ("a path like C:\\" + "Users\\someone\\file", "home path"),
    ],
)
def test_the_guard_flags(text, rule):
    assert rules(text) == [rule]


@pytest.mark.parametrize(
    "text",
    [
        "test" + AT + "example.com and other" + AT + "sub.example.org",
        "noreply" + AT + "anthropic.com",
        "12345+someone" + AT + "users.noreply.github.com",
        "a" + AT + "b.co is a shape, not a mailbox",
        "uvicorn listens on 127.0.0.1 or 0.0.0.0",
        "version 1.2.3.4 of a package at 8.8.8.8",
        "https://<server>.<tailnet>" + TAILNET + "/metis_prod",
        "a `*" + TAILNET + "` host",
        "@media (max-width: 760px) and @router.get('/x')",
        "cdn.example/npm/lucide" + AT + "0.469.0/dist/umd/lucide.min.js",
        "the user's home directory",
    ],
)
def test_the_guard_allows(text):
    assert rules(text) == []


def test_a_well_known_institution_is_flagged_whatever_its_case():
    name = guard.INSTITUTIONS[0]

    assert rules(f"paid from {name.upper()} last week") == ["institution"]
    assert rules(f"paid from {name.title()} last week") == ["institution"]


def test_an_institution_name_inside_another_word_is_not_flagged():
    name = guard.INSTITUTIONS[0]

    assert rules(f"x{name}x") == []


def test_a_finding_names_its_line_and_never_the_text():
    finding = guard.scan_text("fine\n\nwrite to jane.roe" + AT + "mailhost.test")[0]

    assert finding.line == 3
    assert "jane" not in guard.describe("notes.md", finding)


@pytest.mark.parametrize(
    "name", ["finance.duckdb", "data/old.db", "backup.sqlite3", "exports/january.csv", "card.ofx", "card.QFX", "statement.pdf"]
)
def test_a_tracked_database_or_statement_file_is_flagged(name):
    assert [finding.rule for finding in guard.scan_name(name)] == ["data file"]


@pytest.mark.parametrize("name", ["services.py", "tests/fixtures/schema_v0_4_0.sql", "static/styles.css", "README.md"])
def test_code_and_docs_are_not_data_files(name):
    assert guard.scan_name(name) == []


def test_the_guard_reads_only_tracked_files(tmp_path):
    (tmp_path / "kept.md").write_text("hello\n")
    (tmp_path / "leak.md").write_text("jane.roe" + AT + "mailhost.test\n")

    assert guard.scan_tree(tmp_path, files=["kept.md"]) == []
    assert [(path, finding.rule) for path, finding in guard.scan_tree(tmp_path, files=["kept.md", "leak.md"])] == [
        ("leak.md", "email")
    ]


def test_no_tracked_file_holds_real_data():
    findings = guard.scan_tree(ROOT)

    assert findings == [], "\n".join(guard.describe(path, finding) for path, finding in findings)
