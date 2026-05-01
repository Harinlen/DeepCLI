"""Webhook route for push-based gateway adapters (WhatsApp, LINE, etc.).

Discord uses an outbound Gateway WebSocket and does not need this route.
Webhook-based adapters receive platform events via
``POST /gateways/{adapter_id}/webhook``, which is forwarded to
``GatewayManager.handle_webhook``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from kernel.module_table import KernelModuleTable

router = APIRouter()


@router.post("/gateways/{adapter_id}/webhook")
async def gateway_webhook(adapter_id: str, request: Request) -> Response:
    """Receive an inbound webhook payload from a platform and forward it.

    Args:
        adapter_id: Instance ID of the target gateway adapter (from URL).
        request: Incoming HTTP request; body is parsed as JSON.

    Returns:
        Empty 200 response on success.

    Raises:
        HTTPException 404: If no adapter with ``adapter_id`` is running.
        HTTPException 503: If GatewayManager is not loaded (disabled flag).
        HTTPException 500: If the adapter raises an unexpected error.
    """
    from kernel.gateways import GatewayManager

    module_table: KernelModuleTable = request.app.state.module_table
    if not module_table.has(GatewayManager):
        raise HTTPException(status_code=503, detail="GatewayManager not running")

    gm = module_table.get(GatewayManager)
    payload: Any = await request.json()
    try:
        await gm.handle_webhook(adapter_id, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No adapter: {adapter_id!r}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(status_code=200)
