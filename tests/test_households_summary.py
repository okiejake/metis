"""The summary command prints the numbers wiki pages use."""

import subprocess
import sys

from tools.households.generate import generate
from tools.households.load import find
from tools.households.summary import summary


def test_family_summary_tells_its_year():
    h = find("example-family")
    text = summary(h, generate(h))
    assert text.splitlines()[0].startswith("the Example family  2025-11-01 to 2026-10-31")
    assert sum(1 for line in text.splitlines() if line[:7].count("-") == 1 and line[:4].isdigit()) == 12
    assert "kids' fund  500.00 of 500.00  reached" in text


def test_cli_runs_and_shifts():
    out = subprocess.run([sys.executable, "-m", "tools.households", "summary", "sam-sample", "--today", "2026-11-14"],
                         capture_output=True, text=True, check=True).stdout
    assert "2026-05-15 to 2026-11-14" in out.splitlines()[0]


def test_unknown_household_says_so():
    out = subprocess.run([sys.executable, "-m", "tools.households", "summary", "nobody"],
                         capture_output=True, text=True)
    assert out.returncode == 2 and "unknown household 'nobody'" in out.stderr
