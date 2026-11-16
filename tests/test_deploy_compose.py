"""The deploy compose file gives each environment time to stop cleanly.

Closing the database writes everything to the file, which takes several seconds
on a small home server. Docker's default ten seconds can run out first: the
container is killed (exit 137) and the server reports it as stopped unexpectedly.
"""

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "deploy" / "compose.yml"
MIN_STOP_SECONDS = 30


def _shared_settings() -> str:
    match = re.search(r"^x-metis: &metis\n((?:[ \t]+.*\n)+)", COMPOSE.read_text(), re.M)
    assert match, "compose.yml lost its shared x-metis settings"
    return match.group(1)


def _seconds(duration: str) -> int:
    total = 0
    for amount, unit in re.findall(r"(\d+)([hms])", duration):
        total += int(amount) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def test_containers_get_time_to_close_the_database():
    match = re.search(r"^\s+stop_grace_period:\s*(\S+)", _shared_settings(), re.M)
    assert match, "x-metis needs a stop_grace_period"
    assert _seconds(match.group(1)) >= MIN_STOP_SECONDS


def test_both_environments_use_the_shared_settings():
    assert COMPOSE.read_text().count("<<: *metis") == 2
