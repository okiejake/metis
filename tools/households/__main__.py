"""python -m tools.households summary <household-key> [--today YYYY-MM-DD]"""

import argparse
import sys
from datetime import date

from .errors import HouseholdError
from .generate import generate
from .load import find
from .schedules import shift
from .summary import summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.households")
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("summary", help="print a household's months, balances and funds")
    show.add_argument("household")
    show.add_argument("--today", type=date.fromisoformat,
                      help="move the household's dates so its last day is this one")
    args = parser.parse_args(argv)
    try:
        household = find(args.household)
        rows = generate(household)
    except HouseholdError as error:
        print(error, file=sys.stderr)
        return 2
    window = None
    if args.today:
        rows = shift(rows, household.end, args.today)
        window = (household.start + (args.today - household.end), args.today)
    sys.stdout.write(summary(household, rows, window))
    return 0


if __name__ == "__main__":
    sys.exit(main())
