"""Force a firewall deploy against fw1 using the current main `vyos-firewall-
config` artifact. Useful after `demo-webhook-lab-down && demo-webhook-lab-up`
or after a first seed so fw1's config matches Infrahub without waiting for
the next webhook event.

Runs `ansible-playbook` inside the webhook-listener container (where
paramiko + scp + the vyos.vyos collection are already installed) via
`docker compose ... exec`.

Exit codes:
  0 — deploy succeeded
  2 — env misconfig
  3 — artifact not yet rendered (run bootstrap first); not a failure
  non-zero other — ansible exit code

Usage:
    export INFRAHUB_ADDRESS=http://127.0.0.1:8000
    export INFRAHUB_API_TOKEN=...
    uv run --with infrahub-sdk python scripts/webhook_demo/sync_firewall.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

from infrahub_sdk import InfrahubClient

ARTIFACT_NAME = "vyos-firewall-config"
DEVICE_NAME = "fw1"
LISTENER_CONTAINER = "webhook-listener"


async def _resolve_artifact(client: InfrahubClient) -> tuple[str, str]:
    artifacts = await client.filters(kind="CoreArtifact", name__value=ARTIFACT_NAME)
    for art in artifacts:
        obj_rel = getattr(art, "object", None)
        peer = None
        if obj_rel is not None and hasattr(obj_rel, "fetch"):
            try:
                peer = await obj_rel.fetch()
            except Exception:  # noqa: BLE001
                peer = None
        peer = peer or getattr(obj_rel, "peer", None)
        peer_name = getattr(getattr(peer, "name", None), "value", None) if peer else None
        if peer_name == DEVICE_NAME:
            checksum = getattr(getattr(art, "checksum", None), "value", None) or ""
            return art.id, checksum
    if len(artifacts) == 1:
        art = artifacts[0]
        checksum = getattr(getattr(art, "checksum", None), "value", None) or ""
        return art.id, checksum
    raise RuntimeError(
        f"Could not locate CoreArtifact {ARTIFACT_NAME!r} for device "
        f"{DEVICE_NAME!r} (found {len(artifacts)} candidates)"
    )


def main() -> int:
    address = os.environ.get("INFRAHUB_ADDRESS")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not address or not token:
        print("INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN must be set", file=sys.stderr)
        return 2

    client = InfrahubClient(address=address)
    try:
        artifact_id, checksum = asyncio.run(_resolve_artifact(client))
    except RuntimeError as exc:
        print(f"[sync_firewall] {exc}. Skipping sync.", file=sys.stderr)
        return 3
    if not checksum:
        print(
            f"[sync_firewall] WARN: artifact {artifact_id} has no checksum yet; "
            "bootstrap may not have rendered it. Skipping sync.",
            file=sys.stderr,
        )
        return 3

    # The listener container reaches Infrahub at http://infrahub-server:8000
    # (compose default network), so we pass that host in the artifact URL.
    internal_base = os.environ.get("INFRAHUB_INTERNAL_ADDRESS", "http://infrahub-server:8000")
    artifact_url = f"{internal_base.rstrip('/')}/api/artifact/{artifact_id}"
    request_id = f"sync-{uuid.uuid4()}"
    print(f"[sync_firewall] artifact_id={artifact_id} checksum={checksum}")
    print(
        f"[sync_firewall] invoking deploy.yml for {DEVICE_NAME} (request_id={request_id}) inside {LISTENER_CONTAINER}"
    )

    cmd = [
        "docker",
        "exec",
        LISTENER_CONTAINER,
        "uv",
        "run",
        "ansible-playbook",
        "-i",
        "/opt/webhook-listener/ansible/inventory.yml",
        "/opt/webhook-listener/ansible/deploy.yml",
        "-e",
        f"device_key={DEVICE_NAME}",
        "-e",
        f'device_hfid=["{DEVICE_NAME}"]',
        "-e",
        f"artifact_url={artifact_url}",
        "-e",
        f"artifact_checksum={checksum}",
        "-e",
        f"request_id={request_id}",
    ]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(
            f"[sync_firewall] ansible-playbook exited {result.returncode}",
            file=sys.stderr,
        )
        return result.returncode
    print("[sync_firewall] sync complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
