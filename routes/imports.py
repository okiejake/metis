from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse

from services import (
    CsvImportError,
    apply_match_rules,
    get_current_user,
    load_categories,
    load_imported_accounts,
    load_imported_transactions,
    parse_import_csv,
    redirect_with_message,
    summarize_imported,
    template_context,
    upsert_imported_transactions,
)
from web import templates

router = APIRouter()


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, account: str = "", msg: str = "", err: int = 0):
    user = get_current_user(request)
    account_filter = account.strip()
    return templates.TemplateResponse(
        "imports.html",
        template_context(
            request,
            msg,
            err,
            summary=summarize_imported(user["id"]),
            transactions=load_imported_transactions(user["id"], account_filter),
            accounts=load_imported_accounts(user["id"]),
            account_filter=account_filter,
            categories=load_categories(user["id"]),
        ),
    )


@router.post("/import")
async def upload_import(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    filename = (file.filename or "upload.csv").strip()

    raw = await file.read()
    if not raw:
        return redirect_with_message("/import", "The uploaded file is empty.", is_error=True)

    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return redirect_with_message(
            "/import", "Could not read the file (unknown text encoding).", is_error=True
        )

    try:
        account_label, rows = parse_import_csv(filename, text)
        result = upsert_imported_transactions(user["id"], rows, filename)
    except CsvImportError as exc:
        return redirect_with_message("/import", str(exc), is_error=True)

    # Auto-reconcile: apply the user's match rules to any newly imported actuals.
    auto_matched = apply_match_rules(user["id"]) if result["inserted"] else 0

    message = (
        f"{account_label}: imported {result['inserted']} new, "
        f"skipped {result['skipped']} existing"
    )
    if result["transfers"]:
        message += f" ({result['transfers']} transfers flagged)"
    if auto_matched:
        message += f" · auto-matched {auto_matched} to expected items"
    return redirect_with_message("/import", message)
