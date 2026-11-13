"""Which charges are one merchant.

`merchant_key` is the single grouping used by the recurring detector and by
Review. Pure functions, no database.
"""

import re
from typing import Any

# Any standalone two-letter capitalized word, read as a trailing state.
_STATE_RE = re.compile(r"\b[A-Z]{2}\b")
_PROCESSOR_SPLIT_RE = re.compile(r"[*_]")


def _finish(value: str, text: Any) -> str:
    value = _STATE_RE.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or str(text or "").upper().strip()


def _strip_numbers(text: Any) -> str:
    value = str(text or "").upper()
    value = re.sub(r"#\s*\d+", "", value)  # store numbers
    return re.sub(r"\b\d{3,}\b", "", value)  # long digit runs / phone tails


def merchant_key(text: Any) -> str:
    """Collapse a merchant or description to a stable grouping key: drop store
    numbers, long digit runs and a trailing state. A payment processor's prefix
    ("PAYPAL *", "TST*") keeps the merchant written after it, up to the first
    word holding a digit, which is usually an order or terminal id."""
    value = _strip_numbers(text)
    parts = _PROCESSOR_SPLIT_RE.split(value, maxsplit=1)
    if len(parts) == 2:
        head, tail = parts
        kept = []
        for word in tail.split():
            if any(ch.isdigit() for ch in word):
                break
            kept.append(word)
        value = " ".join([head.strip(), *kept])
    return _finish(value, text)


def legacy_merchant_key(text: Any) -> str:
    """The grouping used before `merchant_key`, which dropped everything after a
    processor prefix. Kept only so suggestions dismissed under it stay dismissed."""
    value = re.sub(r"[*_].*$", "", _strip_numbers(text))
    return _finish(value, text)
