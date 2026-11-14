"""The privacy guard: find what looks like real personal or financial data in
tracked files. tests/test_no_real_data.py runs it on every test run; run it by
hand with `python -m tools.privacy_guard`.

It reports where a finding is and which rule it broke, never the text itself.
"""

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Well-known banks, card products and brokerages. A general list, not anyone's
# own: none of these belongs in the repo, whoever banks there.
INSTITUTIONS = (
    "wells fargo", "bank of america", "jpmorgan", "chase bank", "citibank", "capital one",
    "american express", "amex", "us bancorp", "pnc bank", "td bank", "truist", "navy federal",
    "usaa", "synchrony", "barclays", "barclaycard", "goldman sachs", "hsbc", "santander",
    "ally bank", "discover card", "discover bank", "apple card", "fidelity", "vanguard",
    "schwab", "robinhood", "etrade", "e*trade", "merrill lynch", "morgan stanley",
    "wealthfront", "betterment", "td ameritrade", "sofi",
)
# This file lists the names, so it is the one place they may appear.
INSTITUTIONS_SOURCE = "tools/privacy_guard.py"

DATA_FILE_SUFFIXES = {
    ".duckdb", ".db", ".sqlite", ".sqlite3", ".wal",
    ".csv", ".tsv", ".ofx", ".qfx", ".qif", ".xls", ".xlsx", ".pdf",
}
TAILNET_PLACEHOLDERS = {"<server>.<tailnet>", "*"}
NO_REPLY_LOCAL_PARTS = {"noreply", "no-reply"}
NO_REPLY_DOMAINS = {"users.noreply.github.com"}

_EMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
_ADDRESS = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
_TAILNET = re.compile(r"([A-Za-z0-9<>*_-]+(?:\.[A-Za-z0-9<>*_-]+)*)\.ts\.net\b")
_HOME_PATH = re.compile(r"(?<![\w.])/(?:Users|home)/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[^\\\s]+")
_INSTITUTION = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(name) for name in INSTITUTIONS) + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    rule: str
    line: int


def _placeholder_email(local: str, domain: str) -> bool:
    labels = domain.lower().split(".")
    if "example" in labels:
        return True
    if local.lower() in NO_REPLY_LOCAL_PARTS or domain.lower() in NO_REPLY_DOMAINS:
        return True
    # a@b.co: a shape used to test a validator, not anyone's mailbox.
    return all(len(label) == 1 for label in labels[:-1])


def _private_address(octets: Tuple[int, int, int, int]) -> bool:
    first, second = octets[0], octets[1]
    if any(octet > 255 for octet in octets):
        return False
    return (
        first == 10
        or (first == 172 and 16 <= second <= 31)
        or (first == 192 and second == 168)
        or (first == 100 and 64 <= second <= 127)  # carrier-grade NAT, where a tailnet lives
        or (first == 169 and second == 254)
    )


def scan_text(text: str, institutions: bool = True) -> List[Finding]:
    """Every finding in a piece of text, in the order they appear."""
    found: List[Tuple[int, str]] = []
    for match in _EMAIL.finditer(text):
        if not _placeholder_email(match.group(1), match.group(2)):
            found.append((match.start(), "email"))
    for match in _ADDRESS.finditer(text):
        if _private_address(tuple(int(part) for part in match.groups())):
            found.append((match.start(), "private address"))
    for match in _TAILNET.finditer(text):
        if match.group(1) not in TAILNET_PLACEHOLDERS:
            found.append((match.start(), "tailnet host"))
    for match in _HOME_PATH.finditer(text):
        found.append((match.start(), "home path"))
    if institutions:
        for match in _INSTITUTION.finditer(text):
            found.append((match.start(), "institution"))
    return [Finding(rule, text.count("\n", 0, position) + 1) for position, rule in sorted(found)]


def scan_name(name: str) -> List[Finding]:
    """A database or statement file must not be tracked at all."""
    return [Finding("data file", 0)] if Path(name).suffix.lower() in DATA_FILE_SUFFIXES else []


def tracked_files(root: Path) -> Optional[List[str]]:
    """The files git tracks under root, or None when root is not a checkout."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]


def scan_tree(root: Path, files: Optional[Iterable[str]] = None) -> List[Tuple[str, Finding]]:
    """Every finding in the tracked files under root, as (path, finding)."""
    names = list(files) if files is not None else (tracked_files(root) or [])
    found: List[Tuple[str, Finding]] = []
    for name in sorted(names):
        found += [(name, finding) for finding in scan_name(name)]
        path = Path(root) / name
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue  # not text
        text = raw.decode("utf-8", "ignore")
        found += [(name, finding) for finding in scan_text(text, institutions=name != INSTITUTIONS_SOURCE)]
    return found


def describe(path: str, finding: Finding) -> str:
    where = f"{path}:{finding.line}" if finding.line else path
    return f"{where}: {finding.rule}"


def main() -> int:
    root = Path.cwd()
    if tracked_files(root) is None:
        print("not a git checkout: nothing is tracked here")
        return 2
    findings = scan_tree(root)
    for path, finding in findings:
        print(describe(path, finding))
    print(f"{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
