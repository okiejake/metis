"""Settle one transaction (#103, #105, #106, #107): category, match, unmatch,
one-time, unexpected and its clearing. Each returns to `next` on this site with
a message that carries an Undo. A category or a bill link also asks the page to
open the rule prompt for that transaction.

POST /rules/preview counts what a rule would take. It is a POST so the pattern
and amounts never sit in an address.
"""

from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

import settle
from services import parse_optional_date, parse_optional_float, redirect_with_message
from web import CurrentUser

router = APIRouter()


def _with(next_path: str, **params: str) -> str:
    """next with the prompt's ids added, before any #fragment."""
    path, hash_mark, fragment = (next_path or "/budget").partition("#")
    extra = "&".join(f"{key}={value}" for key, value in params.items())
    joined = f"{path}{'&' if '?' in path else '?'}{extra}" if extra else path
    return joined + (f"#{fragment}" if hash_mark else "")


def _done(next_path: str, message: str, action: Optional[int], **prompt: str):
    target = _with(next_path or "/budget", **prompt) if action is not None else (next_path or "/budget")
    if action is None:
        return redirect_with_message(target, "nothing left to change")
    return redirect_with_message(target, message, undo=action)


def _failed(next_path: str, exc: Exception):
    return redirect_with_message(next_path or "/budget", str(exc), is_error=True)


@router.post("/transactions/{tx_id}/category")
def category(request: Request, user: CurrentUser, tx_id: int, choice: str = Form(""),
             new_category_name: str = Form(""), next: str = Form("/budget")):
    try:
        action = settle.assign_category(user["id"], tx_id, choice, new_category_name)
    except ValueError as exc:
        return _failed(next, exc)
    return _done(next, "categorized", action, rule_tx=str(tx_id), rule_kind="category")


@router.post("/transactions/{tx_id}/match")
def match(request: Request, user: CurrentUser, tx_id: int, bill_id: str = Form(""), next: str = Form("/budget")):
    try:
        if not bill_id.strip().isdigit():
            raise ValueError("choose a bill")
        action = settle.match(user["id"], tx_id, int(bill_id))
    except ValueError as exc:
        return _failed(next, exc)
    return _done(next, "matched", action, rule_tx=str(tx_id), rule_kind="bill")


@router.post("/transactions/{tx_id}/unmatch")
def unmatch(request: Request, user: CurrentUser, tx_id: int, next: str = Form("/ledger")):
    try:
        action = settle.unmatch(user["id"], tx_id)
    except ValueError as exc:
        return _failed(next, exc)
    return _done(next, "unmatched", action)


@router.post("/transactions/{tx_id}/one-time")
def one_time(request: Request, user: CurrentUser, tx_id: int, next: str = Form("/budget")):
    try:
        action = settle.make_one_time(user["id"], tx_id)
    except ValueError as exc:
        return _failed(next, exc)
    return _done(next, "added one-time", action)


@router.post("/transactions/{tx_id}/unexpected")
def unexpected(request: Request, user: CurrentUser, tx_id: int, next: str = Form("/budget")):
    try:
        action = settle.mark_unexpected(user["id"], tx_id)
    except ValueError as exc:
        return _failed(next, exc)
    return _done(next, "marked unexpected", action)


@router.post("/transactions/{tx_id}/unexpected/clear")
def unexpected_clear(request: Request, user: CurrentUser, tx_id: int, next: str = Form("/inbox")):
    try:
        action = settle.clear_unexpected(user["id"], tx_id)
    except ValueError as exc:
        return _failed(next, exc)
    return _done(next, "unmarked", action)


@router.post("/rules/preview")
def rules_preview(request: Request, user: CurrentUser, kind: str = Form("category"), tx_id: str = Form(""),
                  pattern: str = Form(""), amount_min: str = Form(""), amount_max: str = Form(""),
                  date_from: str = Form(""), date_to: str = Form("")):
    try:
        count = settle.preview_count(
            user["id"], kind, int(tx_id) if tx_id.strip().isdigit() else None, pattern,
            parse_optional_float(amount_min), parse_optional_float(amount_max),
            parse_optional_date(date_from), parse_optional_date(date_to),
        )
    except ValueError:
        count = 0
    return JSONResponse({"count": count})
