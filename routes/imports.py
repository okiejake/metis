"""Importing into an account, and making the templates imports read files with."""

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

import import_templates as it
from services import CsvImportError, load_account_by_id, map_import_label_to_account, redirect_with_message
from web import CurrentUser, render

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


# --- Making a template ------------------------------------------------------------

def _account(user_id: int, account_id: Any) -> Optional[Dict[str, Any]]:
    text = str(account_id or "").strip()
    return load_account_by_id(user_id, int(text)) if text.isdigit() else None


def _with_account(path: str, account: Optional[Dict[str, Any]]) -> str:
    return f"{path}?account_id={account['id']}" if account else path


PREVIEW_ACCOUNT = {"id": 0, "name": "", "account_type": "checking"}
PREVIEW_ROWS = 8


def _preview(header, rows, values) -> Dict[str, Any]:
    """What the template in `values` makes of the file: rows or what is wrong."""
    try:
        parsed = it.parse_rows(header, rows, it.build_template(values), PREVIEW_ACCOUNT)
    except (ValueError, CsvImportError) as exc:
        return {"error": str(exc), "rows": [], "count": 0}
    return {"error": "", "rows": parsed[:PREVIEW_ROWS], "count": len(parsed)}


def _render_map(request, user_id, upload_id, account, values, msg="", err=0):
    upload = it.load_upload(user_id, upload_id)
    if not upload:
        return redirect_with_message("/accounts", "That file is no longer here. Upload it again.", is_error=True)
    header, rows = it.read_csv(upload[1])
    return render(request, "template_map.html", msg, err, upload_id=upload_id, account=account, header=header,
                  sample=rows[:it.SAMPLE_ROWS], values=values, date_formats=it.date_format_choices(),
                  preview=_preview(header, rows, values))


@router.get("/templates/new", response_class=HTMLResponse)
def new_template_page(request: Request, user: CurrentUser, account_id: str = "", msg: str = "", err: int = 0):
    return render(request, "template_new.html", msg, err, account=_account(user["id"], account_id))


@router.post("/templates/upload")
async def upload_template_file(request: Request, user: CurrentUser, file: UploadFile = File(...),
                               account_id: str = Form("")):
    account = _account(user["id"], account_id)
    raw = await file.read()
    try:
        it.read_csv(raw)
    except CsvImportError as exc:
        return redirect_with_message(_with_account("/templates/new", account), str(exc), is_error=True)
    upload_id = it.save_upload(user["id"], (file.filename or "upload.csv").strip(), raw)
    return RedirectResponse(_with_account(f"/templates/map/{upload_id}", account), status_code=303)


@router.get("/templates/map/{upload_id}", response_class=HTMLResponse)
def map_template_page(request: Request, user: CurrentUser, upload_id: int, account_id: str = ""):
    upload = it.load_upload(user["id"], upload_id)
    if not upload:
        return redirect_with_message("/accounts", "That file is no longer here. Upload it again.", is_error=True)
    account = _account(user["id"], account_id)
    header, rows = it.read_csv(upload[1])
    values: Dict[str, Any] = {"amount_layout": "signed", "amount_sign": "standard", **it.suggest_mapping(header)}
    values["name"] = f"{account['name']} export" if account else os.path.splitext(upload[0])[0]
    if values.get("date_field"):
        values["date_format"] = it.detect_date_format([row.get(values["date_field"], "") for row in rows[:50]])
    return _render_map(request, user["id"], upload_id, account, values)


@router.post("/templates/preview", response_class=HTMLResponse)
async def preview_template(request: Request, user: CurrentUser):
    values = dict(await request.form())
    upload = it.load_upload(user["id"], values.get("upload_id"))
    if not upload:
        preview = {"error": "That file is no longer here. Upload it again.", "rows": [], "count": 0}
    else:
        header, rows = it.read_csv(upload[1])
        preview = _preview(header, rows, values)
    return render(request, "_template_preview.html", preview=preview)


@router.post("/templates")
async def save_template(request: Request, user: CurrentUser):
    values = dict(await request.form())
    upload_id = values.get("upload_id")
    upload = it.load_upload(user["id"], upload_id)
    if not upload:
        return redirect_with_message("/accounts", "That file is no longer here. Upload it again.", is_error=True)
    account = _account(user["id"], values.get("account_id"))
    filename, raw = upload
    try:
        template = it.build_template(values)
        header, rows = it.read_csv(raw)
        it.parse_rows(header, rows, template, account or PREVIEW_ACCOUNT)
        template_id = it.save_template(user["id"], template)
    except (ValueError, CsvImportError) as exc:
        return _render_map(request, user["id"], int(upload_id), account, values, str(exc), 1)
    it.delete_upload(user["id"], int(upload_id))
    if not account:
        return redirect_with_message("/accounts#templates", f"{template['name']} saved")
    it.set_default_template(user["id"], int(account["id"]), template_id)
    try:
        result = it.import_into_account(user["id"], account, it.load_template(user["id"], template_id), raw, filename)
    except CsvImportError as exc:
        return redirect_with_message("/accounts", f"{account['name']}: {exc}", is_error=True)
    return redirect_with_message("/accounts", it.import_message(account["name"], result))


@router.post("/templates/{template_id}/delete")
def delete_template(request: Request, user: CurrentUser, template_id: int):
    it.delete_template(user["id"], template_id)
    return redirect_with_message("/accounts#templates", "Template deleted")
