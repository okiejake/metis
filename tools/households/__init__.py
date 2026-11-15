"""Invented households for Metis 1.0: test data, wiki examples and suggestions.

See docs/superpowers/specs/2026-11-14-households-design.md. Nothing here
imports the 0.x app.
"""

from .catalog import CATALOG_PATH, HOUSEHOLDS_DIR, Catalog, load_catalog, read_toml
from .errors import HouseholdError
from .generate import generate
from .load import find, load_all, load_household
from .model import TO_ASSIGN, Household, Rows
from .schedules import shift
from .summary import summary

__all__ = ["CATALOG_PATH", "HOUSEHOLDS_DIR", "TO_ASSIGN", "Catalog", "Household", "HouseholdError", "Rows",
           "find", "generate", "load_all", "load_catalog", "load_household", "read_toml", "shift", "summary"]
