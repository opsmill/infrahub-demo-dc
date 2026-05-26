"""Dual-handler logging: human text on stdout, JSON on stderr.

The listener emits the five FR-011 stage strings through `stage_log()` so the
demo's presenter-facing log stream has a single source of truth.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict, Optional

STAGE_MESSAGES = {
    "webhook_received": "webhook received",
    "fetch_play_start": "fetch play start",
    "fetch_play_complete": "fetch play complete",
    "deploy_play_start": "deploy play start",
    "deploy_play_complete": "deploy play complete",
}


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: Dict[str, Any] = {
            "ts": _isoformat(record.created),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("stage", "request_id", "hfid", "artifact_url", "event_id", "exit_code", "error"):
            value = getattr(record, key, None)
            if value is not None:
                out[key] = value
        return json.dumps(out, separators=(",", ":"), sort_keys=False)


class _HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = _isoformat(record.created)
        level = record.levelname.ljust(5)
        msg = record.getMessage()
        tags = []
        for key in ("request_id", "hfid", "artifact_url", "event_id", "exit_code"):
            value = getattr(record, key, None)
            if value is not None:
                tags.append(f"{key}={value}")
        suffix = f"  {' '.join(tags)}" if tags else ""
        return f"{ts}  {level}  {msg}{suffix}"


def _isoformat(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int((ts % 1) * 1000):03d}Z"


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(_HumanFormatter())
    root.addHandler(stdout)

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(_JSONFormatter())
    root.addHandler(stderr)


def stage_log(
    stage: str,
    *,
    request_id: str,
    hfid: Optional[str] = None,
    hfid_parts: Optional[list] = None,
    artifact_url: Optional[str] = None,
    exit_code: Optional[int] = None,
    event_id: Optional[str] = None,
    error: Optional[str] = None,
    level: str = "INFO",
) -> None:
    """Emit one of the FR-011 stage lines (or a failure stage).

    `hfid` is the human-readable joined form (matches Ansible inventory
    hostname). `hfid_parts` is the full HFID list preserved for
    structured consumers — compound HFIDs lose information if only `hfid`
    is recorded.
    """
    logger = logging.getLogger("webhook_listener")
    message = STAGE_MESSAGES.get(stage, stage)
    extra = {
        "stage": stage,
        "request_id": request_id,
        "hfid": hfid,
        "hfid_parts": hfid_parts,
        "artifact_url": artifact_url,
        "exit_code": exit_code,
        "event_id": event_id,
        "error": error,
    }
    logger.log(
        getattr(logging, level.upper(), logging.INFO), message, extra={k: v for k, v in extra.items() if v is not None}
    )
