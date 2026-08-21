"""Drive the webhook pipeline end-to-end by creating a branch, adding 17 more
firewall rules, opening a proposed change, and merging it. After the merge
Infrahub re-renders the vyos-firewall-config artifact → fires the
CoreCustomWebhook → listener runs the ansible deploy → fw1 reflects the new
rule set.

Idempotent: rules collide on (policy, index), so rerun is safe. The branch is
reused if it already exists; the PC is reused if one with the same name exists.

Usage:
    export INFRAHUB_ADDRESS=http://127.0.0.1:8000
    export INFRAHUB_API_TOKEN=...
    uv run --with infrahub-sdk python scripts/webhook_demo/bootstrap_pc_rules.py
"""

from __future__ import annotations

import asyncio
import itertools
import os
import subprocess
import sys
import time

from infrahub_sdk import InfrahubClient

BRANCH_NAME = "demo-webhook-bootstrap"
PC_NAME = "Webhook demo bootstrap: add 17 firewall rules"
DEVICE_NAME = "fw1"
POLICY_NAME = "demo-allow-web"

ZONE_NAMES = ["corp", "dmz", "ops", "mgmt"]
SERVICE_GROUP_NAMES = ["https-svc", "http-svc", "ssh-svc"]

# Base seed combinations (indexes 1..3) — skip these in the bootstrap sweep.
BASE_COMBOS = {
    ("corp", "dmz", "https-svc"),
    ("corp", "dmz", "http-svc"),
    ("ops", "mgmt", "ssh-svc"),
}

# Bootstrap adds 17 rules at indexes 4..20 for 20 total.
START_INDEX = 4
TARGET_COUNT = 17

DEPLOY_SUCCESS_MARKER = "deploy play complete"
DEPLOY_FAILURE_MARKER = "FAILED!"
WAIT_TIMEOUT_S = 180

LISTENER_CONTAINER = "webhook-listener"


def _generate_specs(start_index: int, count: int) -> list[dict]:
    """Produce (index, src, dst, svc_group) tuples, skipping same-zone + base combos."""
    specs: list[dict] = []
    idx = start_index
    for src, dst, svc in itertools.product(ZONE_NAMES, ZONE_NAMES, SERVICE_GROUP_NAMES):
        if src == dst:
            continue
        if (src, dst, svc) in BASE_COMBOS:
            continue
        specs.append({"index": idx, "src": src, "dst": dst, "svc_group": svc})
        idx += 1
        if len(specs) == count:
            return specs
    raise RuntimeError(f"combination space exhausted before reaching {count} rules")


async def _ensure_branch(client: InfrahubClient, name: str) -> None:
    try:
        branches = await client.branch.all()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"failed to list branches: {exc}") from exc
    if name in branches:
        print(f"[bootstrap_pc] branch {name!r} already exists; reusing")
        return
    await client.branch.create(branch_name=name, sync_with_git=False)
    print(f"[bootstrap_pc] created branch {name!r}")


async def _resolve_main(
    client: InfrahubClient,
) -> tuple[object, dict[str, object], dict[str, object]]:
    """Resolve the main-branch policy, zones, and service groups we'll reference."""
    policies = await client.filters(kind="SecurityPolicy", name__value=POLICY_NAME)
    if not policies:
        raise RuntimeError(f"SecurityPolicy {POLICY_NAME!r} not found on main — run seed_webhook_demo.py first")
    policy = policies[0]

    zones = {(z.name.value): z for z in await client.filters(kind="SecurityZone") if (z.name.value) in ZONE_NAMES}
    groups = {
        (g.name.value): g
        for g in await client.filters(kind="SecurityServiceGroup")
        if (g.name.value) in SERVICE_GROUP_NAMES
    }
    missing_zones = [n for n in ZONE_NAMES if n not in zones]
    missing_groups = [n for n in SERVICE_GROUP_NAMES if n not in groups]
    if missing_zones or missing_groups:
        raise RuntimeError(f"seed incomplete: missing zones={missing_zones}, missing groups={missing_groups}")
    return policy, zones, groups


async def _seed_rules_on_branch(
    client: InfrahubClient,
    branch: str,
    policy_id: str,
    zones: dict[str, object],
    groups: dict[str, object],
) -> int:
    created = 0
    for spec in _generate_specs(START_INDEX, TARGET_COUNT):
        existing = await client.filters(
            kind="SecurityPolicyRule",
            branch=branch,
            policy__ids=[policy_id],
            index__value=spec["index"],
        )
        if existing:
            continue
        rule = await client.create(
            kind="SecurityPolicyRule",
            branch=branch,
            data={
                "index": spec["index"],
                "name": f"rule-{spec['index']:02d}-{spec['src']}-to-{spec['dst']}",
                "action": "permit",
                "log": False,
                "policy": policy_id,
                "source_zone": zones[spec["src"]].id,
                "destination_zone": zones[spec["dst"]].id,
                "services": [groups[spec["svc_group"]].id],
            },
        )
        await rule.save()
        created += 1
        print(f"[bootstrap_pc] rule {spec['index']:02d} {spec['src']}->{spec['dst']}/{spec['svc_group']}")
    return created


async def _ensure_pc(client: InfrahubClient, branch: str) -> str:
    existing = await client.filters(kind="CoreProposedChange", name__value=PC_NAME)
    if existing:
        pc = existing[0]
        print(f"[bootstrap_pc] reusing existing PC {PC_NAME!r} (id={pc.id})")
        return pc.id
    pc = await client.create(
        kind="CoreProposedChange",
        data={
            "name": PC_NAME,
            "source_branch": branch,
            "destination_branch": "main",
            "description": (
                "Webhook demo bootstrap: adds 17 SecurityPolicyRule entries so "
                "the full pipeline (artifact re-render → CoreCustomWebhook → "
                "listener → ansible deploy) fires end-to-end during first-time "
                "setup."
            ),
        },
    )
    await pc.save()
    print(f"[bootstrap_pc] created PC {PC_NAME!r} (id={pc.id})")
    return pc.id


async def _merge_branch(client: InfrahubClient, branch: str) -> None:
    try:
        await client.branch.merge(branch_name=branch)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "not found" in msg or "already merged" in msg or "does not exist" in msg:
            print(f"[bootstrap_pc] branch {branch!r} already merged or gone")
            return
        raise
    print(f"[bootstrap_pc] merged branch {branch!r} into main")


def _wait_for_deploy(start_time: float) -> bool:
    """Poll the listener container's logs for a deploy-play-complete marker
    emitted after `start_time`.
    """
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    seen_success = False
    print(f"[bootstrap_pc] waiting up to {WAIT_TIMEOUT_S}s for deploy marker in {LISTENER_CONTAINER} logs…")
    while time.monotonic() < deadline:
        try:
            out = subprocess.check_output(
                [
                    "docker",
                    "logs",
                    "--since",
                    f"{int(time.time() - (time.time() - start_time) - 1)}s",
                    LISTENER_CONTAINER,
                ],
                stderr=subprocess.STDOUT,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            print(f"[bootstrap_pc] docker logs failed: {exc.output[:200]}", file=sys.stderr)
            time.sleep(3)
            continue
        for line in out.splitlines():
            if DEPLOY_SUCCESS_MARKER in line:
                print(f"[bootstrap_pc] deploy succeeded: {line.strip()}")
                seen_success = True
                return True
            if DEPLOY_FAILURE_MARKER in line and "deploy" in line.lower():
                print(f"[bootstrap_pc] deploy FAILED: {line.strip()}", file=sys.stderr)
                return False
        time.sleep(3)
    print(f"[bootstrap_pc] TIMEOUT waiting {WAIT_TIMEOUT_S}s for deploy marker", file=sys.stderr)
    return seen_success


async def main() -> int:
    address = os.environ.get("INFRAHUB_ADDRESS")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not address or not token:
        print("INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN must be set", file=sys.stderr)
        return 2

    client = InfrahubClient(address=address)

    start = time.time()
    await _ensure_branch(client, BRANCH_NAME)
    policy, zones, groups = await _resolve_main(client)
    created = await _seed_rules_on_branch(client, BRANCH_NAME, policy.id, zones, groups)
    print(f"[bootstrap_pc] {created} rule(s) created on branch (0 = already present)")
    await _ensure_pc(client, BRANCH_NAME)
    await _merge_branch(client, BRANCH_NAME)

    ok = _wait_for_deploy(start_time=start)
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
