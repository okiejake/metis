"""Settle one transaction (#103, #105, #106, #107): category, match, unmatch,
one-time, unexpected and its clearing. Each returns to `next` on this site with
a message that carries an Undo. A category or a bill link also asks the page to
open the rule prompt for that transaction.

The edit form (#123) settles everything about one transaction at once: its
category and bill, note, transfer mark and split lines. The ledger opens it over
the table (part=1); without script, and from the split address, it is a page.
Saving a category or a bill match goes on to step 2, the rule.

POST /rules/preview counts what a rule would take. It is a POST so the pattern
and amounts never sit in an address.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

import settle
from services import (
    load_bill_by_id,
    load_categories,
    load_imported_transaction_by_id,
    load_transaction_splits,
    parse_optional_date,
    parse_optional_float,
    redirect_with_message,
    resolve_forecast_window,
    resolve_safe_redirect_target,
)
from web import CurrentUser, render, render_part

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


# --- The edit form (#123) -----------------------------------------------------------


def _edit_path(tx_id: int, next_path: str, part: bool, **params: str) -> str:
    query = {**params, "next": next_path, **({"part": "1"} if part else {})}
    return f"/transactions/{tx_id}/edit?{urlencode(query)}"


def _bill_options(user_id: int, tx: Dict[str, Any], chosen: List[Any]) -> List[Dict[str, Any]]:
    """The bills a line can take: active bills of the transaction's kind, nearest
    in amount first, plus any bill already chosen that is no longer active."""
    bills = settle.bills_to_match(user_id, {"signed": tx["amount"], "amount": tx["magnitude"]})
    listed = {int(bill["id"]) for bill in bills}
    for bill_id in chosen:
        if str(bill_id or "").isdigit() and int(bill_id) not in listed:
            found = load_bill_by_id(user_id, int(bill_id))
            if found:
                bills.append(found)
                listed.add(int(bill_id))
    return bills


@router.get("/transactions/{tx_id}/edit", response_class=HTMLResponse)
def edit_form(request: Request, user: CurrentUser, tx_id: int, next: str = "/ledger", part: str = "",
              split: str = "", step: str = "", rule_kind: str = "", msg: str = "", err: int = 0):
    user_id = int(user["id"])
    tx = load_imported_transaction_by_id(user_id, tx_id)
    if not tx:
        return redirect_with_message("/ledger", "Transaction not found", is_error=True)
    next_path = resolve_safe_redirect_target(next or "/ledger")
    rule_prompt = None
    if step == "rule":
        rule_prompt = settle.rule_prompt(user_id, tx_id, rule_kind, resolve_forecast_window(user_id, "", "")[0])
        if rule_prompt is None:
            return redirect_with_message(next_path, msg or "saved")
    lines = load_transaction_splits(user_id, tx_id)
    if split == "1" and not lines:
        lines = [{"category_id": None, "bill_id": None, "amount": settle.half(tx["magnitude"]), "note": ""}]
    choice = settle.bill_choice(tx)
    context = {
        "tx": tx,
        "categories": load_categories(user_id),
        "bills": _bill_options(user_id, tx, [choice] + [line["bill_id"] for line in lines]),
        "choice": choice,
        "lines": lines,
        "half": settle.half(tx["magnitude"]),
        "original_amount": round(tx["magnitude"] - sum(line["amount"] for line in lines), 2),
        "is_card": tx.get("account_type") == "credit_card",
        "next_path": next_path,
        "part": bool(part),
        "split_url": _edit_path(tx_id, next_path, False, split="1"),
        "new_bill_url": f"/bills/new?{urlencode({'from_tx': tx_id, 'next': next_path})}",
        "rule_prompt": rule_prompt,
    }
    if part:
        return render_part(request, "_tx_edit.html", msg, err, **context)
    return render(request, "tx_edit.html", msg, err, **context)


@router.post("/transactions/{tx_id}/edit")
async def save_edit(request: Request, user: CurrentUser, tx_id: int):
    user_id = int(user["id"])
    form = await request.form()
    next_path = resolve_safe_redirect_target(str(form.get("next") or "/ledger"))
    part = bool(form.get("part"))
    tx = load_imported_transaction_by_id(user_id, tx_id)
    if not tx:
        return _failed(next_path, ValueError("Transaction not found"))
    try:
        lines = [] if form.get("unsplit") else settle.split_lines(
            user_id, tx, form.getlist("part_category"), form.getlist("part_bill"), form.getlist("part_amount"),
            form.getlist("part_note"))
        transfer = (form.get("transfer") == "1") if form.get("transfer_field") else None
        rule = settle.edit(user_id, tx_id, str(form.get("category") or ""), str(form.get("new_category_name") or ""),
                           str(form.get("bill") or ""), str(form.get("note") or ""), transfer, lines)
    except ValueError as exc:
        return redirect_with_message(_edit_path(tx_id, next_path, part), str(exc), is_error=True)
    if rule:
        return redirect_with_message(_edit_path(tx_id, next_path, part, step="rule", rule_kind=rule), "saved")
    return redirect_with_message(next_path, "saved")
