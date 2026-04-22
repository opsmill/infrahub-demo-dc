"""Register the CoreCustomWebhook that fires on artifact updates for the
webhook firewall demo. Upserts by name; idempotent.

Usage:
    export INFRAHUB_ADDRESS=http://127.0.0.1:8000
    export INFRAHUB_API_TOKEN=...
    export WEBHOOK_LISTENER_URL=http://webhook-listener:8001/webhook
    export WEBHOOK_LISTENER_SHARED_KEY=demo-shared-key
    uv run --with infrahub-sdk python scripts/webhook_demo/register_webhook.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from infrahub_sdk import InfrahubClient

NAME = "vyos-firewall-deploy"
EVENT_TYPE = "infrahub.artifact.updated"
NODE_KIND = "CoreArtifact"
TRANSFORMATION_NAME = "webhook_firewall_deploy"


async def main() -> int:
    address = os.environ.get("INFRAHUB_ADDRESS")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    listener_url = os.environ.get("WEBHOOK_LISTENER_URL", "http://webhook-listener:8001/webhook")
    shared_key = os.environ.get("WEBHOOK_LISTENER_SHARED_KEY", "demo-shared-key")
    if not address or not token:
        print("INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN must be set", file=sys.stderr)
        return 2

    client = InfrahubClient(address=address)

    transforms = await client.filters(kind="CoreTransformPython", name__value=TRANSFORMATION_NAME)
    if not transforms:
        print(
            f"CoreTransformPython {TRANSFORMATION_NAME!r} not found; make sure the "
            "webhook-demo CoreRepository has been registered and its .infrahub.yml "
            "has been processed by the task-worker.",
            file=sys.stderr,
        )
        return 3
    transform = transforms[0]

    existing = await client.filters(kind="CoreCustomWebhook", name__value=NAME)
    if existing:
        hook = existing[0]
        hook.url.value = listener_url
        hook.shared_key.value = shared_key
        hook.active.value = True
        hook.event_type.value = EVENT_TYPE
        hook.node_kind.value = NODE_KIND
        await hook.save()
        await hook.add_relationships(relation_to_update="transformation", related_nodes=[transform.id])
        print(f"updated CoreCustomWebhook {NAME!r} -> {hook.id}")
        return 0

    hook = await client.create(
        kind="CoreCustomWebhook",
        data={
            "name": NAME,
            "description": "VyOS firewall deploy listener (webhook demo)",
            "url": listener_url,
            "shared_key": shared_key,
            "event_type": EVENT_TYPE,
            "node_kind": NODE_KIND,
            "active": True,
            "validate_certificates": False,
            "transformation": transform.id,
        },
    )
    await hook.save()
    print(f"created CoreCustomWebhook {NAME!r} -> {hook.id}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
