"""static/date-range.js: its pure functions agree with date_range.py on the shared
cases, and the file parses.

All data here is synthetic.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def test_the_script_parses():
    result = subprocess.run([NODE, "--check", str(ROOT / "static" / "date-range.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_pure_functions_pass_their_checks():
    result = subprocess.run(
        [NODE, str(ROOT / "tests" / "js" / "date_range_check.cjs")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr or result.stdout
