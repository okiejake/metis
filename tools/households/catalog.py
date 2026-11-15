"""The catalog: generic account types, budgets, bills and income.

Households pick items from it by key, and Metis offers it as suggestions at
setup. It holds no amounts: those come from a household or from the user.
"""

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict

from .errors import HouseholdError

HOUSEHOLDS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "households"
CATALOG_PATH = HOUSEHOLDS_DIR / "catalog.toml"

# TOML section -> Catalog field.
SECTIONS = {"account_type": "account_types", "budget": "budgets", "bill": "bills", "income": "incomes"}


def read_toml(path: Path) -> dict:
    """A TOML file, with every decimal number read exactly."""
    with path.open("rb") as handle:
        return tomllib.load(handle, parse_float=Decimal)


@dataclass(frozen=True)
class Catalog:
    account_types: Dict[str, dict]
    budgets: Dict[str, dict]
    bills: Dict[str, dict]
    incomes: Dict[str, dict]


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    data = read_toml(path)
    sections: Dict[str, Dict[str, dict]] = {field: {} for field in SECTIONS.values()}
    for section, items in data.items():
        if section not in SECTIONS:
            raise HouseholdError("catalog", f"unknown section {section}")
        found = sections[SECTIONS[section]]
        for item in items:
            key = item.get("key")
            if not key:
                raise HouseholdError(f"catalog {section}", "an item has no key")
            if key in found:
                raise HouseholdError(f"catalog {section} {key}", "appears twice")
            found[key] = dict(item)
    return Catalog(**sections)
