"""WebBridge local install and status routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.agents.mustang.tools.web.web_bridge.install_assets import (
    install_page_html,
    zip_path,
)

router = APIRouter(prefix="/web-bridge", tags=["web-bridge"])


def _tool_manager(request: Request) -> Any:
    from kernel.agents.mustang.tools import ToolManager

    module_table: KernelModuleTable = request.app.state.module_table
    if not module_table.has(ToolManager):
        raise HTTPException(status_code=503, detail="ToolManager not running")
    return module_table.get(ToolManager)


@router.get("/status.json")
async def status_json(request: Request) -> dict[str, object]:
    """Return WebBridge status for the local install wizard."""

    return _tool_manager(request).web_bridge_status(include_pairing_token=True)


@router.post("/pair")
async def pair(request: Request) -> dict[str, object]:
    """Generate a new short-lived WebBridge pairing token."""

    return _tool_manager(request).web_bridge_pair_start()


@router.post("/reset")
async def reset(request: Request) -> dict[str, object]:
    """Reset the live WebBridge pairing connection."""

    return await _tool_manager(request).web_bridge_pair_reset()


@router.get("/install")
async def install(request: Request) -> Response:
    """Render the guided local Chrome extension installer."""

    status = _tool_manager(request).web_bridge_pair_start()
    return HTMLResponse(install_page_html(json.dumps(status, indent=2)))


@router.get("/deepcli-web-bridge.zip")
async def extension_zip() -> FileResponse:
    """Download the packaged WebBridge Chrome extension."""

    archive = zip_path()
    if not archive.exists():
        raise HTTPException(status_code=404, detail="WebBridge extension zip not built")
    return FileResponse(archive, media_type="application/zip", filename=archive.name)


__all__ = ["router"]
