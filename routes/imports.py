from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from services import (
    CsvImportError,
    apply_match_rules,
    delete_import_template,
    get_current_user,
    load_accounts,
    load_categories,
    load_import_template,
    load_import_templates,
    load_imported_accounts,
    load_imported_transactions,
    load_unmapped_import_labels,
    map_import_label_to_account,
    parse_import_csv,
    redirect_with_message,
    save_import_template,
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
            unmapped_labels=load_unmapped_import_labels(user["id"]),
            user_accounts=load_accounts(user["id"]),
            import_templates=load_import_templates(user["id"]),
        ),
    )


@router.post("/import/map-account")
def map_account(
    request: Request,
    label: str = Form(...),
    account_id: int = Form(...),
):
    user = get_current_user(request)
    try:
        updated = map_import_label_to_account(user["id"], label.strip(), account_id)
    except ValueError as exc:
        return redirect_with_message("/import", str(exc), is_error=True)
    return redirect_with_message(
        "/import", f"Linked {updated} “{label}” transaction(s) to the selected account."
    )


@router.post("/import")
async def upload_import(
    request: Request,
    file: UploadFile = File(...),
    template_id: str = Form(""),
):
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

    forced_template = None
    if template_id.strip():
        forced_template = load_import_template(user["id"], int(template_id))
        if not forced_template:
            return redirect_with_message("/import", "Selected template not found.", is_error=True)

    try:
        account_label, _account_id, rows = parse_import_csv(
            filename, text, templates=load_import_templates(user["id"]), forced_template=forced_template
        )
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


@router.post("/import/templates")
def save_template(
    request: Request,
    name: str = Form(...),
    account_id: int = Form(...),
    date_field: str = Form(...),
    amount_field: str = Form(...),
    description_field: str = Form(...),
    signature: str = Form(...),
    amount_sign: str = Form("standard"),
    date_format: str = Form(""),
    type_field: str = Form(""),
    credit_value: str = Form(""),
    merchant_field: str = Form(""),
    template_id: str = Form(""),
):
    user = get_current_user(request)
    signature_cols = [part.strip() for part in signature.split(",") if part.strip()]
    try:
        save_import_template(
            user["id"],
            name=name,
            account_id=account_id,
            date_field=date_field,
            amount_field=amount_field,
            description_field=description_field,
            signature=signature_cols,
            amount_sign=amount_sign,
            date_format=date_format,
            type_field=type_field,
            credit_value=credit_value,
            merchant_field=merchant_field,
            template_id=int(template_id) if template_id.strip() else None,
        )
    except ValueError as exc:
        return redirect_with_message("/import", str(exc), is_error=True)
    return redirect_with_message("/import", f"Import template “{name.strip()}” saved.")


@router.post("/import/templates/{template_id}/delete")
def remove_template(request: Request, template_id: int):
    user = get_current_user(request)
    delete_import_template(user["id"], template_id)
    return redirect_with_message("/import", "Import template deleted.")
