"""Subprocess wrapper for ansible-playbook.

Launches the deploy playbook with the extra-vars contract described in
`contracts/playbook_interface.md`, streams the subprocess stdout/stderr
into the listener's own log stream, and records the exit code.

The run is detached from the request's lifecycle (the webhook handler
returns before the playbook finishes) but the subprocess's output is
forwarded in real time so presenters can watch the five stage lines.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .logging_config import stage_log


@dataclass
class PlaybookRun:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str = ""
    device_hfid: List[str] = field(default_factory=list)
    device_key: str = ""
    artifact_url: str = ""
    artifact_checksum: str = ""
    playbook: str = ""
    inventory: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None


_STAGE_MARKERS = {
    "fetch play start": "fetch_play_start",
    "fetch play complete": "fetch_play_complete",
    "deploy play start": "deploy_play_start",
    "deploy play complete": "deploy_play_complete",
}


def _find_stage(line: str) -> Optional[str]:
    for marker, stage in _STAGE_MARKERS.items():
        if marker in line:
            return stage
    return None


async def _pump(stream: asyncio.StreamReader, *, request_id: str, hfid: str, sink: list, emit_stages: bool) -> None:
    while True:
        raw = await stream.readline()
        if not raw:
            return
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        sink.append(line)
        if emit_stages:
            stage = _find_stage(line)
            if stage is not None:
                stage_log(stage, request_id=request_id, hfid=hfid)


async def run_playbook(
    *,
    request_id: str,
    device_hfid: List[str],
    device_key: str,
    artifact_url: str,
    artifact_checksum: str,
    playbook_path: str,
    inventory_path: str,
    extra_env: Optional[Dict[str, str]] = None,
) -> PlaybookRun:
    """Run ansible-playbook and return the completed PlaybookRun.

    The listener calls this via asyncio.create_task() so the HTTP handler
    returns before the playbook completes.
    """
    run = PlaybookRun(
        request_id=request_id,
        device_hfid=list(device_hfid),
        device_key=device_key,
        artifact_url=artifact_url,
        artifact_checksum=artifact_checksum,
        playbook=playbook_path,
        inventory=inventory_path,
    )

    exe = shutil.which("ansible-playbook")
    if not exe:
        run.exit_code = 127
        stage_log(
            "failure fetch_play_start",
            request_id=request_id,
            hfid=device_key,
            hfid_parts=device_hfid,
            error="ansible-playbook not found on PATH",
            level="ERROR",
        )
        return run

    env = dict(os.environ)
    env.update(extra_env or {})
    env.setdefault("ANSIBLE_HOST_KEY_CHECKING", "False")
    env.setdefault("ANSIBLE_STDOUT_CALLBACK", "default")
    env.setdefault("ANSIBLE_FORCE_COLOR", "0")

    # device_hfid is passed as JSON so Ansible gets it back as a real list,
    # not a stringified Python repr. device_key is the inventory selector
    # used by `hosts:` in the playbook.
    args = [
        exe,
        "-i",
        inventory_path,
        "-e",
        f"device_key={device_key}",
        "-e",
        f"device_hfid={json.dumps(device_hfid)}",
        "-e",
        f"artifact_url={artifact_url}",
        "-e",
        f"artifact_checksum={artifact_checksum}",
        "-e",
        f"request_id={request_id}",
        playbook_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout_lines: list = []
    stderr_lines: list = []
    await asyncio.gather(
        _pump(proc.stdout, request_id=request_id, hfid=device_key, sink=stdout_lines, emit_stages=True),
        _pump(proc.stderr, request_id=request_id, hfid=device_key, sink=stderr_lines, emit_stages=False),
    )
    run.exit_code = await proc.wait()
    run.stdout = "\n".join(stdout_lines)
    run.stderr = "\n".join(stderr_lines)

    if run.exit_code != 0:
        stage_log(
            "failure deploy_play_complete",
            request_id=request_id,
            hfid=device_key,
            hfid_parts=device_hfid,
            exit_code=run.exit_code,
            level="ERROR",
        )
    else:
        stage_log(
            "deploy_play_complete",
            request_id=request_id,
            hfid=device_key,
            hfid_parts=device_hfid,
            exit_code=run.exit_code,
        )

    _persist(run)
    return run


def _persist(run: PlaybookRun) -> None:
    directory = Path(os.environ.get("WEBHOOK_LISTENER_ARTIFACT_DIR", "/tmp/webhook-listener"))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{run.run_id}.stdout").write_text(run.stdout)
    (directory / f"{run.run_id}.stderr").write_text(run.stderr)
