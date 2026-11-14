"""static/theme.js: the header's three-way theme toggle (light, dark, system)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def test_the_script_parses():
    result = subprocess.run([NODE, "--check", str(ROOT / "static" / "theme.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_toggle_cycles_and_resolves():
    result = subprocess.run([NODE, str(ROOT / "tests" / "js" / "theme_check.cjs")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
