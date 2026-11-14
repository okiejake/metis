"""One name per thing, in the UI, routes, code and data (#95, #101, #108). The
names a thing used to have are gone from the code. They live on only where an
old database is upgraded, and in the one redirect from the inbox's old address.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Where old names belong: bringing an old database forward, and its tests.
UPGRADE_ONLY = {
    "legacy_schema.py", "migrations.py", "tests/test_migrations.py", "tests/test_bill_schedule.py",
    "tests/fixtures/schema_v0_4_0.sql", "tests/test_one_name.py", "tests/test_expected_form.py",
    "tools/leak_check.py",
}
SOURCES = sorted(
    path for pattern in ("*.py", "routes/*.py", "templates/*.html", "static/*", "tests/*.py", "tools/*.py")
    for path in ROOT.glob(pattern)
    if path.is_file() and str(path.relative_to(ROOT)) not in UPGRADE_ONLY
)

OLD_NAMES = {
    "recurring": re.compile(r"recurring", re.I),
    "manual transaction": re.compile(r"manual_transaction|manual_edit|/manual\b|source-manual|manual transaction|['\"]manual['\"](?!,? \"template)", re.I),
    "one-off": re.compile(r"one[-_ ]off", re.I),
    "review": re.compile(r"(?<![A-Za-z])review", re.I),
}
# The inbox's old address still redirects, so its path is written once.
ALLOWED = {
    ("routes/inbox.py", "review"): 1,
    ("tests/test_header_drawer.py", "review"): 2,
    ("tests/test_date_range_js.py", "one-off"): 0,
}


@pytest.mark.parametrize("name", sorted(OLD_NAMES))
def test_an_old_name_is_gone_from_the_code(name):
    found = {}
    for path in SOURCES:
        relative = str(path.relative_to(ROOT))
        hits = [number for number, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1)
                if OLD_NAMES[name].search(line)]
        if len(hits) > ALLOWED.get((relative, name), 0):
            found[relative] = hits
    assert found == {}


def test_the_ledger_calls_scheduled_rows_bill_income_or_one_time():
    ledger = (ROOT / "templates" / "ledger.html").read_text()

    assert "source-recurring" not in ledger and "source-manual" not in ledger
