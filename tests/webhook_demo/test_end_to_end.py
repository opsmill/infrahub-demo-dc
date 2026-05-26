"""End-to-end test for the webhook firewall demo.

Assumes the demo is already up (via `invoke demo-webhook-up`); mutates a
SecurityPolicyRule via the SDK, merges the resulting branch through a
proposed-change, then polls the listener container's log for a
`deploy play complete` marker within 90 s.

Requires RUN_E2E=1.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time

from .conftest import skip_unless_e2e

LISTENER_CONTAINER = "webhook-listener"
DEPLOY_MARKER = "deploy play complete"
TIMEOUT_S = 90


@skip_unless_e2e
def test_rule_change_fires_webhook_and_deploys():
    from infrahub_sdk import InfrahubClient

    address = os.environ.get("INFRAHUB_ADDRESS", "http://127.0.0.1:8000")
    branch_name = f"e2e-test-{int(time.time())}"

    async def _mutate_and_merge():
        client = InfrahubClient(address=address)
        await client.branch.create(branch_name=branch_name, sync_with_git=False)
        # Flip rule index=1 action from permit to deny on the branch.
        policies = await client.filters(kind="SecurityPolicy", branch=branch_name, name__value="demo-allow-web")
        assert policies, "demo-allow-web policy missing on branch"
        rules = await client.filters(
            kind="SecurityPolicyRule",
            branch=branch_name,
            policy__ids=[policies[0].id],
            index__value=1,
        )
        assert rules, "rule index=1 missing on branch"
        rule = rules[0]
        current = rule.action.value
        rule.action.value = "deny" if current == "permit" else "permit"
        await rule.save()

        pc = await client.create(
            kind="CoreProposedChange",
            data={
                "name": f"E2E test PC {int(time.time())}",
                "source_branch": branch_name,
                "destination_branch": "main",
                "description": "automated E2E webhook-demo test",
            },
        )
        await pc.save()
        await client.branch.merge(branch_name=branch_name)

    asyncio.run(_mutate_and_merge())

    # Poll the listener container's log for a new deploy completion.
    start = time.time()
    deadline = time.monotonic() + TIMEOUT_S
    seen = False
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "logs", "--since", f"{int(start)}", LISTENER_CONTAINER],
            check=False,
            capture_output=True,
            text=True,
        )
        if DEPLOY_MARKER in (result.stdout + result.stderr):
            seen = True
            break
        time.sleep(3)

    assert seen, (
        f"did not see {DEPLOY_MARKER!r} in listener logs within {TIMEOUT_S}s after merging branch {branch_name}"
    )
