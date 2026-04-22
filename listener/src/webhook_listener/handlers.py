"""Webhook request handler for the listener.

Validates the `webhook_payload.md` contract, enforces `schema_version`,
deduplicates on `event_id` for 60s, and spawns the deploy playbook via
asyncio.create_task so the HTTP response returns quickly (FR-008).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from .logging_config import stage_log
from .playbook_runner import run_playbook
from .signature import SignatureError, verify_hmac_sha256

SUPPORTED_SCHEMA_VERSION = "2"
DEDUP_TTL_SECONDS = 60

_DEDUP_CACHE: Dict[str, Tuple[float, str]] = {}
_LOG = logging.getLogger("webhook_listener")


class _Artifact(BaseModel):
    definition_name: str
    artifact_name: str
    url: str
    storage_id: str
    checksum: str
    checksum_previous: Optional[str] = None


class WebhookPayload(BaseModel):
    schema_version: str
    event_id: str
    occurred_at: str
    branch: str
    # Full HFID list (Infrahub always emits hfid as a list; compound HFIDs
    # like [pool_name, member_name] have >1 element). `device_key` is the
    # dash-joined selector for Ansible inventory lookup.
    hfid: List[str] = Field(..., min_length=1)
    device_key: str = Field(..., min_length=1)
    device_kind: str
    device_node_id: str = Field(..., min_length=1)
    artifact: _Artifact


def _json(status: int, body: Dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status, content=body)


def _prune_dedup(now: float) -> None:
    expired = [k for k, (seen_at, _) in _DEDUP_CACHE.items() if now - seen_at > DEDUP_TTL_SECONDS]
    for key in expired:
        _DEDUP_CACHE.pop(key, None)


async def handle_webhook(request: Request) -> JSONResponse:
    raw = await request.body()
    shared_key = os.environ.get("WEBHOOK_LISTENER_SHARED_KEY", "")
    disable_sig = os.environ.get("WEBHOOK_LISTENER_DISABLE_SIG", "0") == "1"

    if not disable_sig:
        sig = request.headers.get("webhook-signature")
        try:
            verify_hmac_sha256(raw, sig, shared_key, headers=request.headers)
        except SignatureError:
            return _json(401, {"error": "unauthorized"})

    try:
        payload = WebhookPayload.model_validate_json(raw)
    except ValidationError as exc:
        detail = _first_validation_detail(exc)
        return _json(400, {"error": "bad_request", "detail": detail})

    if payload.schema_version != SUPPORTED_SCHEMA_VERSION:
        return _json(
            400,
            {
                "error": "bad_request",
                "detail": (
                    f"schema_version '{payload.schema_version}' is unsupported; expected '{SUPPORTED_SCHEMA_VERSION}'"
                ),
            },
        )

    now = time.time()
    _prune_dedup(now)
    if payload.event_id in _DEDUP_CACHE:
        original_request_id = _DEDUP_CACHE[payload.event_id][1]
        return _json(409, {"error": "duplicate", "request_id": original_request_id})

    request_id = str(uuid.uuid4())
    _DEDUP_CACHE[payload.event_id] = (now, request_id)

    stage_log(
        "webhook_received",
        request_id=request_id,
        hfid=payload.device_key,  # joined form for terminal readability
        hfid_parts=payload.hfid,  # full list preserved in structured log
        artifact_url=payload.artifact.url,
        event_id=payload.event_id,
    )
    # Pretty-print the full payload to stdout so presenters can point at the
    # listener pane and see exactly what Infrahub sent — HFID, device kind,
    # artifact definition, URL, checksum, branch, event id, etc. The
    # structured JSON stream (stderr) also gets the payload under `payload`.
    _dump_payload(request_id, payload)

    asyncio.create_task(_launch_playbook(payload, request_id))
    return _json(200, {"accepted": True, "request_id": request_id})


def _dump_payload(request_id: str, payload: WebhookPayload) -> None:
    body = payload.model_dump()
    # Align key/value columns for readability in a terminal.
    width = max(len(k) for k in body) if body else 0
    lines = [
        f"================ webhook payload  request_id={request_id} ================",
    ]
    for key, value in body.items():
        if key == "artifact" and isinstance(value, dict):
            lines.append(f"  {key.ljust(width)} :")
            inner_width = max(len(k) for k in value) if value else 0
            for ik, iv in value.items():
                lines.append(f"    {ik.ljust(inner_width)} : {iv}")
        else:
            lines.append(f"  {key.ljust(width)} : {value}")
    lines.append("=" * len(lines[0]))
    _LOG.info("\n".join(lines), extra={"stage": "webhook_payload", "request_id": request_id, "payload": body})


async def _launch_playbook(payload: WebhookPayload, request_id: str) -> None:
    playbook_path = os.environ.get("WEBHOOK_LISTENER_PLAYBOOK_PATH", "listener/ansible/deploy.yml")
    inventory_path = os.environ.get("WEBHOOK_LISTENER_INVENTORY_PATH", "listener/ansible/inventory.yml")
    artifact_url = _rewrite_artifact_url(payload.artifact.url)
    await run_playbook(
        request_id=request_id,
        device_hfid=payload.hfid,
        device_key=payload.device_key,
        artifact_url=artifact_url,
        artifact_checksum=payload.artifact.checksum,
        playbook_path=playbook_path,
        inventory_path=inventory_path,
    )


def _rewrite_artifact_url(url: str) -> str:
    """Swap the transform-supplied internal host for the listener-reachable one.

    The transform emits artifact_url using the task-worker's view of Infrahub
    (often `http://infrahub-server:8000/...`), which fails to resolve from
    the listener host. If WEBHOOK_LISTENER_ARTIFACT_BASE_URL or INFRAHUB_ADDRESS
    is set, swap the scheme+host portion of the URL.
    """
    base = os.environ.get("WEBHOOK_LISTENER_ARTIFACT_BASE_URL") or os.environ.get("INFRAHUB_ADDRESS")
    if not base:
        return url
    from urllib.parse import urlsplit, urlunsplit

    have = urlsplit(url)
    want = urlsplit(base)
    return urlunsplit((want.scheme, want.netloc, have.path, have.query, have.fragment))


def _first_validation_detail(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid payload"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", []))
    msg = first.get("msg", "invalid value")
    return f"field {loc}: {msg}"
