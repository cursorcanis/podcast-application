"""Delivery routes — M5 (POD-12).

POST /episodes/{id}/delivery/resend   explicitly re-send (idempotent, logged)
                                      — disabled in the UI while delivery is
                                      paused, and refused here (same boundary)
                                      so the two can never diverge.

There is no "run delivery" route: delivery fires automatically the moment QA
passes (app/qa.py -> app/delivery.run_delivery_on_qa_pass). The only manual
action is Resend, and it is deliberately explicit and never automatic.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from . import delivery

router = APIRouter()


def _err_redirect(episode_id: int, message: str) -> RedirectResponse:
    return RedirectResponse(url=f"/episodes/{episode_id}?error={quote(message)}", status_code=303)


@router.post("/episodes/{episode_id}/delivery/resend")
def resend_delivery(episode_id: int):
    try:
        delivery.resend(episode_id)
    except delivery.DeliveryError as exc:
        return _err_redirect(episode_id, str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}", status_code=303)