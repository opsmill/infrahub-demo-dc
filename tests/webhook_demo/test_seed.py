"""Integration test: seed_webhook_demo.py against a real Infrahub.

Requires RUN_INTEGRATION=1 and a live Infrahub reachable at INFRAHUB_ADDRESS
with the webhook-demo `.infrahub.yml` registered via INFRAHUB_GIT_LOCAL=true.
"""

from __future__ import annotations

import os
import subprocess

from .conftest import skip_unless_integration


@skip_unless_integration
def test_seed_creates_3_base_rules(tmp_path):
    import asyncio

    from infrahub_sdk import InfrahubClient

    env = os.environ.copy()
    result = subprocess.run(
        ["uv", "run", "python", "scripts/webhook_demo/seed_webhook_demo.py"],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    async def _check():
        client = InfrahubClient(address=env["INFRAHUB_ADDRESS"])
        fw = await client.filters(kind="SecurityFirewall", name__value="fw1")
        assert len(fw) == 1
        policy = await client.filters(kind="SecurityPolicy", name__value="demo-allow-web")
        assert len(policy) == 1
        rules = await client.filters(kind="SecurityPolicyRule", policy__ids=[policy[0].id])
        assert len(rules) >= 3, f"expected at least 3 base rules, got {len(rules)}"
        group = await client.filters(kind="CoreStandardGroup", name__value="vyos-demo-firewalls")
        assert len(group) == 1

    asyncio.run(_check())
