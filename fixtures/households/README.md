# Households

Three invented households, written against the Metis 1.0 data map: accounts are
where money is, budgets are what it's for. They serve three jobs:

1. **Test data.** They fill every table, so tests work through realistic cases.
2. **Wiki examples.** Each feature page tells its story with one household, and
   its numbers come from the summary command below.
3. **Suggestions.** `catalog.toml` holds what most households track (account
   types, budgets, bills, income) with no amounts. Metis offers it at setup.

| File | Household | Shows |
| --- | --- | --- |
| `example-family.toml` | two adults, two kids | funds and goals by priority, splits, a loan with a balance, a bill every six months |
| `sam-sample.toml` | single, renting, first full-time job | starting out, an emergency fund, a forgotten subscription |
| `dana-demo.toml` | freelancer | irregular income, percent-of-income funds, quarterly taxes |

Everything here is made up. Names follow the `Example`, `Acme`, `Sample`, `Demo`
pattern, and the repo's privacy guard checks every file.

## Adding a household

Add a `.toml` file here. Pick catalog items with `use = "<key>"` and give only
what differs, such as an amount. Anything the catalog doesn't have gets its own
`key`. Every reference is by key. Loading checks the file and names anything
that doesn't add up. The design is in
`docs/superpowers/specs/2026-11-14-households-design.md`.

## The summary

```sh
python -m tools.households summary example-family
python -m tools.households summary sam-sample --today 2026-11-14
```

It prints each month's income and spending by budget, every budget's balance at
the end, and each fund's progress toward its target. `--today` moves the dates
so the household's last day is the one given, for screens and screenshots.

The generator returns plain rows per table. It doesn't write to a database: the
0.x database has a different shape, and Metis 1.0's doesn't exist yet.
