"""The test workflow runs the suite on every pull request and on pushes to
main, on the Python version the image ships."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def text():
    return WORKFLOW.read_text()


def test_it_runs_on_pull_requests_and_pushes_to_main():
    trigger = re.search(r"\non:\n(.*?)\n\S", text(), re.S).group(1)

    assert "pull_request:" in trigger
    assert re.search(r"push:\s*\n\s*branches:\s*\[main\]", trigger)


def test_it_installs_the_dev_requirements_and_runs_pytest_on_python_3_12():
    assert re.search(r'python-version:\s*"3\.12"', text())
    assert "pip install -r requirements-dev.txt" in text()
    assert re.search(r"run:\s*pytest\b", text())


def test_it_can_only_read_the_repository():
    assert re.search(r"\npermissions:\n\s+contents: read\n", text())


def test_the_image_and_the_workflow_use_the_same_python():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "FROM python:3.12" in dockerfile
