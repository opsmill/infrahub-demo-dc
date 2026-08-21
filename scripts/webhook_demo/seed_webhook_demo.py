"""Seed the local Infrahub with data for the custom-webhook firewall demo.

Creates (all idempotent):
- 4 SecurityZone: corp (internal), dmz (dmz), ops (internal), mgmt (internal)
- 3 SecurityService: https (443/tcp), http (80/tcp), ssh (22/tcp)
- 3 SecurityServiceGroup: https-svc, http-svc, ssh-svc (each wrapping its
  matching SecurityService)
- 1 SecurityFirewall: fw1 (active, edge_firewall)
- 1 CoreStandardGroup: vyos-demo-firewalls containing fw1
- 1 SecurityPolicy: demo-allow-web attached to fw1
- 3 SecurityPolicyRule: indexes 1..3 (corp→dmz https, corp→dmz http,
  ops→mgmt ssh) — the "before" demo state

Usage:
    export INFRAHUB_ADDRESS=http://127.0.0.1:8000
    export INFRAHUB_API_TOKEN=...
    uv run --with infrahub-sdk python scripts/webhook_demo/seed_webhook_demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from infrahub_sdk import InfrahubClient

GROUP_NAME = "vyos-demo-firewalls"
DEVICE_NAME = "fw1"
POLICY_NAME = "demo-allow-web"

# (name, trust_level) — trust_level is a Number (0-100): higher = more trusted.
# Values chosen to distinguish internal zones (corp/ops) from management
# (highest trust) and dmz (lowest).
ZONES = [
    ("corp", 80),
    ("dmz", 30),
    ("ops", 80),
    ("mgmt", 95),
]

# (name, port, ip_protocol) — bundle-dc's SecurityService takes port+protocol
# inline (no separate service-range needed for the demo's three services).
SERVICES = [
    ("https", 443, "tcp"),
    ("http", 80, "tcp"),
    ("ssh", 22, "tcp"),
]

# (group_name, member_service_name) — one group per service for rule attachment.
SERVICE_GROUPS = [
    ("https-svc", "https"),
    ("http-svc", "http"),
    ("ssh-svc", "ssh"),
]

# Base rules seeded on main. index ∈ {1..3}. Bootstrap PC flow later adds 4..20.
BASE_RULES = [
    {"index": 1, "src": "corp", "dst": "dmz", "svc_group": "https-svc"},
    {"index": 2, "src": "corp", "dst": "dmz", "svc_group": "http-svc"},
    {"index": 3, "src": "ops", "dst": "mgmt", "svc_group": "ssh-svc"},
]


async def _get_or_create(client: InfrahubClient, kind: str, data: dict, match: dict):
    existing = await client.filters(kind=kind, **match)
    if existing:
        return existing[0]
    node = await client.create(kind=kind, data=data)
    await node.save()
    return node


async def main() -> int:
    address = os.environ.get("INFRAHUB_ADDRESS")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not address or not token:
        print("INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN must be set", file=sys.stderr)
        return 2

    client = InfrahubClient(address=address)

    zones: dict[str, object] = {}
    for name, trust in ZONES:
        zones[name] = await _get_or_create(
            client=client,
            kind="SecurityZone",
            data={"name": name, "trust_level": trust},
            match={"name__value": name},
        )

    services: dict[str, object] = {}
    for name, port, proto in SERVICES:
        services[name] = await _get_or_create(
            client=client,
            kind="SecurityService",
            data={"name": name, "port": port, "protocol": proto},
            match={"name__value": name},
        )

    service_groups: dict[str, object] = {}
    for group_name, member in SERVICE_GROUPS:
        group = await _get_or_create(
            client=client,
            kind="SecurityServiceGroup",
            data={"name": group_name, "description": f"Demo group: {member}"},
            match={"name__value": group_name},
        )
        await group.add_relationships(relation_to_update="services", related_nodes=[services[member].id])
        service_groups[group_name] = group

    # SecurityFirewall inherits DcimGenericDevice, which requires `location`.
    # Use any pre-seeded LocationBuilding from bundle-dc's bootstrap.
    locations = await client.filters(kind="LocationBuilding")
    if not locations:
        raise RuntimeError(
            "No LocationBuilding found; bundle-dc bootstrap should have seeded "
            "several (BCN-1/CHI-1/...). Run `invoke bootstrap` first."
        )
    location_id = locations[0].id

    existing = await client.filters(kind="SecurityFirewall", name__value=DEVICE_NAME)
    if existing:
        device = existing[0]
    else:
        device = await client.create(
            kind="SecurityFirewall",
            data={
                "name": DEVICE_NAME,
                "status": "active",
                "role": "edge_firewall",
                "location": location_id,
            },
        )
        await device.save()

    group = await _get_or_create(
        client=client,
        kind="CoreStandardGroup",
        data={"name": GROUP_NAME, "description": "Webhook demo — VyOS firewalls"},
        match={"name__value": GROUP_NAME},
    )
    await group.add_relationships(relation_to_update="members", related_nodes=[device.id])

    policy = await _get_or_create(
        client=client,
        kind="SecurityPolicy",
        data={
            "name": POLICY_NAME,
            "description": "Demo security policy exercised by the webhook pipeline",
        },
        match={"name__value": POLICY_NAME},
    )
    await policy.add_relationships(relation_to_update="firewalls", related_nodes=[device.id])

    # Rules: idempotency keyed on (policy, index).
    for spec in BASE_RULES:
        existing = await client.filters(
            kind="SecurityPolicyRule",
            policy__ids=[policy.id],
            index__value=spec["index"],
        )
        if existing:
            print(f"rule index={spec['index']} already present -> {existing[0].id}")
            continue
        rule = await client.create(
            kind="SecurityPolicyRule",
            data={
                "index": spec["index"],
                "name": f"rule-{spec['index']:02d}-{spec['src']}-to-{spec['dst']}",
                "action": "permit",
                "log": False,
                "policy": policy.id,
                "source_zone": zones[spec["src"]].id,
                "destination_zone": zones[spec["dst"]].id,
                "services": [service_groups[spec["svc_group"]].id],
            },
        )
        await rule.save()
        print(f"rule {spec['index']:02d} {spec['src']}->{spec['dst']}/{spec['svc_group']} -> {rule.id}")

    # Register the CoreArtifactDefinition now that the target group exists.
    # (Registering this via .infrahub.yml would fail at repo-import time
    # because the target group doesn't yet exist.)
    transform = await client.filters(
        kind="CoreTransformPython",
        name__value="vyos_firewall_config",
    )
    if not transform:
        print(
            "WARN: CoreTransformPython 'vyos_firewall_config' not registered; "
            "skipping artifact definition creation. Re-run this script once "
            "the repo sync completes.",
            file=sys.stderr,
        )
    else:
        existing = await client.filters(kind="CoreArtifactDefinition", name__value="vyos_firewall_config")
        if existing:
            print(f"artifact def 'vyos_firewall_config' already exists -> {existing[0].id}")
        else:
            art_def = await client.create(
                kind="CoreArtifactDefinition",
                data={
                    "name": "vyos_firewall_config",
                    "artifact_name": "vyos-firewall-config",
                    "content_type": "text/plain",
                    "parameters": {"device": "name__value"},
                    "transformation": transform[0].id,
                    "targets": group.id,
                },
            )
            await art_def.save()
            print(f"artifact def 'vyos_firewall_config' -> {art_def.id}")

    print(f"device {DEVICE_NAME} -> {device.id}; group {GROUP_NAME!r} wired; policy {POLICY_NAME!r} wired")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
