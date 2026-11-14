"""The leak check: before a PR is opened, the working tree and the PR text are
compared against what is in the local database. It reads the database
read-only, keeps nothing, and prints only masked hits.

All data here is synthetic.
"""

from datetime import date

import pytest

from conftest import add_bill, add_imported
from tools import leak_check


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    return root


@pytest.fixture
def database(db, user_id, accounts):
    """A database holding one of each kind of thing that must not leak."""
    db.execute("UPDATE users SET display_name = 'Quentin Zebrowski' WHERE id = ?", [user_id])
    db.execute("UPDATE accounts SET name = 'Zephyr Rewards Plus' WHERE id = ?", [accounts["Example Card"]])
    add_imported(db, user_id, accounts["Example Card"], "Zephyr Rewards Plus", date(2026, 3, 4),
                 "WOMBAT WAREHOUSE #0042 SPRINGFIELD", -137.46)
    add_imported(db, user_id, accounts["Example Checking"], "Example Checking", date(2026, 3, 5),
                 "TARGET", -20.0)
    add_bill(db, user_id, "Larkspur Daycare", 310.0, date(2026, 1, 5))
    path = db.DB_PATH
    db.close_database()
    return path


WORDS = {"target", "wombat", "warehouse"}  # stands in for the system dictionary


def scan(tree, database, files, texts=()):
    tokens = leak_check.load_tokens(database, words=WORDS)
    return leak_check.scan(tree, tokens, files=files, texts=texts)


def kinds(hits):
    return sorted({hit.kind for hit in hits})


def test_a_merchant_from_the_database_is_found_in_the_tree(tree, database):
    (tree / "notes.md").write_text("line one\nsaw a charge from wombat warehouse today\n")

    hits = scan(tree, database, ["notes.md"])

    assert [(hit.kind, hit.path, hit.line) for hit in hits] == [("merchant", "notes.md", 2)]


def test_names_accounts_and_bill_names_are_found(tree, database):
    (tree / "a.py").write_text('OWNER = "Quentin Zebrowski"\nCARD = "zephyr rewards plus"\nBILL = "Larkspur Daycare"\n')

    assert kinds(scan(tree, database, ["a.py"])) == ["account", "name"]


def test_an_amount_from_the_database_is_found_but_a_round_one_is_not(tree, database):
    (tree / "test_x.py").write_text("assert total == 137.46\nassert other == 20.00\nassert third == 137.47\n")

    hits = scan(tree, database, ["test_x.py"])

    assert [(hit.kind, hit.line) for hit in hits] == [("amount", 1)]


def test_an_everyday_word_is_not_a_hit(tree, database):
    """A merchant that is one common word would match code everywhere."""
    (tree / "code.py").write_text("target = compute(target)\n")

    assert scan(tree, database, ["code.py"]) == []


def test_a_hit_is_masked(tree, database, capsys):
    (tree / "notes.md").write_text("Quentin Zebrowski paid wombat warehouse 137.46\n")

    code = leak_check.main(["--db", database, "--root", str(tree), "--files", "notes.md"])
    shown = capsys.readouterr().out

    assert code == 1
    assert "notes.md:1" in shown
    for secret in ("Quentin", "Zebrowski", "wombat", "warehouse", "137.46"):
        assert secret.lower() not in shown.lower()


def test_a_text_file_such_as_a_pr_body_is_checked_too(tree, database, tmp_path):
    body = tmp_path / "pr-body.md"
    body.write_text("Proven on real data: Larkspur Daycare now forecasts correctly.\n")

    hits = scan(tree, database, [], texts=[str(body)])

    assert [(hit.kind, hit.line) for hit in hits] == [("name", 1)]


def test_a_clean_tree_passes(tree, database, capsys):
    (tree / "notes.md").write_text("acme utilities paid 40.00 from example checking, as test user\n")

    assert leak_check.main(["--db", database, "--root", str(tree), "--files", "notes.md"]) == 0
    assert "0 hit" in capsys.readouterr().out


def test_the_database_is_opened_read_only(database):
    before = open(database, "rb").read()

    leak_check.load_tokens(database)

    assert open(database, "rb").read() == before


def test_a_missing_database_is_an_error_not_a_pass(tree, tmp_path, capsys):
    code = leak_check.main(["--db", str(tmp_path / "none.duckdb"), "--root", str(tree), "--files"])

    assert code == 2
