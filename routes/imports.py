"""Importing into an account, and making the templates imports read files with."""

from fastapi import APIRouter, File, Form, Request, UploadFile

import import_templates as it
from services import CsvImportError, load_account_by_id, map_import_label_to_account, redirect_with_message
from web import CurrentUser

router = APIRouter()


def _safe_next(next_path: str) -> str:
    return next_path if next_path.startswith("/") and not next_path.startswith("//") else "/accounts"


@router.post("/accounts/{account_id}/import")
async def import_into_account(
    request: Request, user: CurrentUser, account_id: int,
    file: UploadFile = File(...),
    template_id: str = Form(""),
    next: str = Form("/accounts"),
):
    back = _safe_next(next)
    account = load_account_by_id(user["id"], account_id)
    if not account:
        return redirect_with_message(back, "Account not found", is_error=True)
    template = it.load_template(user["id"], template_id)
    if not template:
        return redirect_with_message(back, "Choose a template", is_error=True)
    try:
        result = it.import_into_account(user["id"], account, template, await file.read(),
                                        (file.filename or "upload.csv").strip())
    except CsvImportError as exc:
        return redirect_with_message(back, f"{account['name']}: {exc}", is_error=True)
    return redirect_with_message(back, it.import_message(account["name"], result))


@router.post("/accounts/unplaced")
def place_unplaced_rows(request: Request, user: CurrentUser, label: str = Form(...), account_id: int = Form(...)):
    try:
        moved = map_import_label_to_account(user["id"], label, account_id)
    except ValueError as exc:
        return redirect_with_message("/accounts", str(exc), is_error=True)
    return redirect_with_message("/accounts", f"Moved {moved} rows")
