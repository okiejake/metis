"""The split address opens the edit form on its split lines (#123). The inbox
links here; the form saves through POST /transactions/{tx_id}/edit."""

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from services import resolve_safe_redirect_target
from web import CurrentUser

router = APIRouter()


@router.get("/transactions/{tx_id}/split")
def split_page(request: Request, user: CurrentUser, tx_id: int, next: str = "/ledger"):
    query = urlencode({"split": "1", "next": resolve_safe_redirect_target(next or "/ledger")})
    return RedirectResponse(url=f"/transactions/{tx_id}/edit?{query}", status_code=303)
