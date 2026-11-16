"""static/split-form.js: the split lines always add up to the transaction, and
save waits until they are valid. Its pure functions are checked in node.

All data here is synthetic.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


@pytest.mark.parametrize("script", ["split-form.js", "tx-edit.js", "partial.js", "category-picker.js", "fade.js"])
def test_the_script_parses(script):
    result = subprocess.run([NODE, "--check", str(ROOT / "static" / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_pure_functions_pass_their_checks():
    result = subprocess.run([NODE, str(ROOT / "tests" / "js" / "split_form_check.cjs")], capture_output=True,
                            text=True)
    assert result.returncode == 0, result.stderr or result.stdout
