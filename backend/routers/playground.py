"""
Playground — interactive REST + WebSocket tester.

A traders'/developers' surface for hitting OpenBull's ``/api/v1/*`` endpoints
and the WebSocket proxy in either Live or Sandbox mode.

The page itself is React (``frontend/src/pages/Playground.tsx``). This router
serves three helpers it needs:

- ``GET  /web/playground/api-key``   — the caller's API key (already decrypted)
- ``GET  /web/playground/endpoints`` — Bruno collection parsed into JSON
- ``GET  /web/playground/host``      — base URL the page should display

All three are cookie-authed (session JWT) via ``get_current_user``. The
endpoints listing is mode-agnostic — every endpoint dispatches via the same
``dispatch_by_mode`` machinery as the rest of the API, so a single playground
covers both Live and Sandbox.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.dependencies import get_current_user, get_db
from backend.models.auth import ApiKey
from backend.models.user import User
from backend.security import decrypt_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/playground", tags=["playground"])

# Resolve the collections directory at module load time so we don't recompute
# it on every request. ``backend/routers/playground.py`` ->
# ``backend/`` -> project root, then ``collections/openbull/IN_stock``.
_COLLECTIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "collections" / "openbull"
)


# ---------------------------------------------------------------------------
# Bruno (.bru) parser
# ---------------------------------------------------------------------------
#
# Ported from openalgo's ``blueprints/playground.py`` so existing tooling that
# treats Bruno collections as the source of truth keeps working. We extract:
#
# - HTTP endpoints  → method, path, request body (JSON), query params
# - WebSocket endpoints → URL, description, sample message
#
# API key values inside ``body:json`` are scrubbed before being returned to
# the browser; the caller injects their own key on the client side.


def _parse_bru_file(filepath: str) -> dict | None:
    """Parse a Bruno .bru file and return its endpoint metadata."""
    try:
        with open(filepath, encoding="utf-8") as fh:
            content = fh.read()

        endpoint: dict = {}

        # meta block
        meta_match = re.search(r"meta\s*\{([^}]+)\}", content)
        if meta_match:
            meta = meta_match.group(1)
            if (m := re.search(r"name:\s*(.+)", meta)):
                endpoint["name"] = m.group(1).strip()
            if (m := re.search(r"seq:\s*(\d+)", meta)):
                endpoint["seq"] = int(m.group(1).strip())
            if (m := re.search(r"type:\s*(.+)", meta)):
                endpoint["type"] = m.group(1).strip()

        # WebSocket endpoint
        if endpoint.get("type") == "websocket":
            ws_match = re.search(r"websocket\s*\{([^}]+)\}", content)
            if ws_match:
                ws = ws_match.group(1)
                if (m := re.search(r"url:\s*(.+)", ws)):
                    endpoint["path"] = m.group(1).strip()
                if (m := re.search(r"description:\s*(.+)", ws)):
                    endpoint["description"] = m.group(1).strip()
                endpoint["method"] = "WS"

            message_start = content.find("message:json")
            if message_start != -1:
                brace_start = content.find("{", message_start)
                if brace_start != -1:
                    depth = 0
                    body_end = brace_start
                    for i, ch in enumerate(content[brace_start:], start=brace_start):
                        if ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                body_end = i
                                break
                    body_text = content[brace_start + 1 : body_end].strip()
                    try:
                        body_json = json.loads(body_text, object_pairs_hook=OrderedDict)
                        if isinstance(body_json, (dict, OrderedDict)) and "apikey" in body_json:
                            body_json["apikey"] = ""
                        if isinstance(body_json, (dict, OrderedDict)) and "api_key" in body_json:
                            body_json["api_key"] = ""
                        endpoint["body"] = body_json
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse WS message JSON in %s", filepath)

            return endpoint if "name" in endpoint else None

        # HTTP endpoint — http verb block
        method_match = re.search(
            r"(get|post|put|delete|patch)\s*\{([^}]+)\}", content, re.IGNORECASE
        )
        if method_match:
            endpoint["method"] = method_match.group(1).upper()
            method_body = method_match.group(2)
            if (m := re.search(r"url:\s*(.+)", method_body)):
                full_url = m.group(1).strip()
                if (p := re.search(r"(/api/v1/[^?]+)", full_url)):
                    endpoint["path"] = p.group(1)
                # GET requests can encode params in the URL after ``?``
                if endpoint.get("method") == "GET":
                    if (q := re.search(r"\?(.+)$", full_url)):
                        params = {}
                        for pair in q.group(1).split("&"):
                            if "=" in pair:
                                k, v = pair.split("=", 1)
                                params[k] = "" if k == "apikey" else v
                        if params:
                            endpoint["params"] = params

        # body:json block — balanced brace matching to allow nested objects
        body_start = content.find("body:json")
        if body_start != -1:
            brace_start = content.find("{", body_start)
            if brace_start != -1:
                depth = 0
                body_end = brace_start
                for i, ch in enumerate(content[brace_start:], start=brace_start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            body_end = i
                            break
                body_text = content[brace_start + 1 : body_end].strip()
                try:
                    body_json = json.loads(body_text, object_pairs_hook=OrderedDict)
                    if isinstance(body_json, (dict, OrderedDict)) and "apikey" in body_json:
                        body_json["apikey"] = ""
                    endpoint["body"] = body_json
                except json.JSONDecodeError:
                    logger.warning("Failed to parse body JSON in %s", filepath)

        # params:query block — explicit query-param map
        if (pm := re.search(r"params:query\s*\{([^}]+)\}", content)):
            params: dict = {}
            for line in pm.group(1).split("\n"):
                if (kv := re.search(r"(\w+):\s*(.+)", line)):
                    params[kv.group(1).strip()] = kv.group(2).strip()
            if params:
                endpoint["params"] = params

        return endpoint if "name" in endpoint and "path" in endpoint else None

    except Exception:
        logger.exception("Error parsing Bruno file %s", filepath)
        return None


def _categorise(path: str) -> str:
    """Bucket an HTTP endpoint into one of the sidebar categories."""
    p = path.lower()

    if any(
        x in p
        for x in (
            "/funds",
            "/orderbook",
            "/tradebook",
            "/positionbook",
            "/positions",
            "/holdings",
            "/analyzer",
            "/margin",
        )
    ):
        return "account"

    if any(
        x in p
        for x in (
            "/placeorder",
            "/placesmartorder",
            "/optionsorder",
            "/optionsmultiorder",
            "/basketorder",
            "/splitorder",
            "/modifyorder",
            "/cancelorder",
            "/cancelallorder",
            "/closeposition",
            "/orderstatus",
            "/openposition",
        )
    ):
        return "orders"

    if any(
        x in p
        for x in (
            "/quotes",
            "/multiquotes",
            "/depth",
            "/history",
            "/intervals",
            "/symbol",
            "/search",
            "/expiry",
            "/optionsymbol",
            "/optiongreeks",
            "/optionchain",
            "/syntheticfuture",
        )
    ):
        return "data"

    if any(
        x in p
        for x in (
            "/oitracker",
            "/maxpain",
            "/ivchart",
            "/ivsmile",
            "/volsurface",
            "/straddle",
            "/gex",
        )
    ):
        return "analytics"

    return "utilities"


def _load_endpoints(broker_type: str = "IN_stock") -> dict:
    """Walk ``collections/openbull/<broker_type>/`` and return a categorised dict."""
    out: dict[str, list[dict]] = {
        "account": [],
        "orders": [],
        "data": [],
        "analytics": [],
        "utilities": [],
        "websocket": [],
    }

    base = _COLLECTIONS_DIR / broker_type
    files = glob.glob(str(base / "**" / "*.bru"), recursive=True)

    parsed = []
    for f in files:
        if os.path.basename(f) == "collection.bru":
            continue
        ep = _parse_bru_file(f)
        if ep:
            parsed.append(ep)

    parsed.sort(key=lambda e: e.get("seq", 999))

    for ep in parsed:
        category = "websocket" if ep.get("type") == "websocket" else _categorise(ep.get("path", ""))
        clean: dict = {
            "name": ep.get("name", ""),
            "method": ep.get("method", "POST"),
            "path": ep.get("path", ""),
        }
        for k in ("body", "params", "description"):
            if k in ep:
                clean[k] = ep[k]
        out[category].append(clean)

    for cat in out:
        out[cat].sort(key=lambda e: e.get("name", "").lower())

    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api-key")
async def playground_api_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the caller's decrypted API key (empty string if none generated)."""
    result = await db.execute(select(ApiKey).where(ApiKey.user_id == user.id))
    record = result.scalar_one_or_none()
    if not record:
        return {"api_key": ""}
    try:
        key = decrypt_value(record.api_key_encrypted)
    except Exception:
        logger.error("Failed to decrypt API key for user %d", user.id)
        raise HTTPException(status_code=500, detail="Failed to decrypt API key")
    return {"api_key": key}


@router.get("/endpoints")
async def playground_endpoints(user: User = Depends(get_current_user)):
    """Return the parsed Bruno collection grouped by category.

    Field order from the .bru file is preserved via ``OrderedDict``; we drop
    out of FastAPI's default JSON renderer to keep it stable on the wire.
    """
    try:
        endpoints = _load_endpoints(broker_type="IN_stock")
        if not any(endpoints.values()):
            logger.warning("No endpoints loaded from Bruno collections")
        return JSONResponse(
            content=json.loads(json.dumps(endpoints, sort_keys=False)),
            status_code=200,
        )
    except Exception:
        logger.exception("Failed to load playground endpoints")
        raise HTTPException(status_code=500, detail="Failed to load endpoints")


@router.get("/host")
async def playground_host(request: Request, user: User = Depends(get_current_user)):
    """Return the host URL the playground should display in the URL bar.

    Prefers the configured ``FRONTEND_URL`` so the displayed base matches what
    external SDK callers would hit, but falls back to the request's own origin
    when the frontend URL is misconfigured.
    """
    settings = get_settings()
    host = getattr(settings, "frontend_url", None) or f"{request.url.scheme}://{request.url.netloc}"
    # The page joins this with the endpoint path itself, so strip a trailing /
    return {"host_server": host.rstrip("/")}
