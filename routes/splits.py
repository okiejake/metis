from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from services import (
    clear_transaction_splits,
    load_categories,
    load_imported_transaction_by_id,
    load_transaction_splits,
    redirect_with_message,
    save_transaction_splits,
    split_remainder,
)
from web import CurrentUser, render

router = APIRouter()


def _split_path(tx_id: int) -> str:
    return f"/transactions/{tx_id}/split"


@router.get("/transactions/{tx_id}/split", response_class=HTMLResponse)
def split_page(request: Request, user: CurrentUser, tx_id: int, msg: str = "", err: int = 0):
    transaction = load_imported_transaction_by_id(user["id"], tx_id)
    if not transaction:
        return redirect_with_message("/ledger", "Transaction not found", is_error=True)

    return render(
        request,
        "split.html",
        msg,
        err,
        transaction=transaction,
        splits=load_transaction_splits(user["id"], tx_id),
        remainder=split_remainder(user["id"], tx_id),
        categories=load_categories(user["id"]),
    )


@router.post("/transactions/{tx_id}/split")
async def save_splits(request: Request, user: CurrentUser, tx_id: int):
    """Save the whole set at once. The editor posts parallel category_id/amount/
    note fields, so a blank amount simply drops that row."""
    form = await request.form()
    category_ids = form.getlist("category_id")
    amounts = form.getlist("amount")
    notes = form.getlist("note")

    parts = []
    for index, category_id in enumerate(category_ids):
        raw_amount = amounts[index] if index < len(amounts) else ""
        if not str(category_id).strip() or not str(raw_amount).strip():
            continue
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            return redirect_with_message(_split_path(tx_id), "Split amounts must be numbers", is_error=True)
        parts.append(
            {
                "category_id": int(category_id),
                "amount": amount,
                "note": notes[index] if index < len(notes) else "",
            }
        )

    try:
        remainder = save_transaction_splits(user["id"], tx_id, parts)
    except ValueError as exc:
        return redirect_with_message(_split_path(tx_id), str(exc), is_error=True)

    message = f"Split saved — {len(parts)} part{'s' if len(parts) != 1 else ''}"
    if remainder > 0.005:
        message += f", remainder stays on the transaction's category"
    return redirect_with_message(_split_path(tx_id), message)


@router.post("/transactions/{tx_id}/split/clear")
def clear_splits(request: Request, user: CurrentUser, tx_id: int):
    clear_transaction_splits(user["id"], tx_id)
    return redirect_with_message(_split_path(tx_id), "Split removed")
