"""DC Firewall Policy Attachment Generator.

Triggered whenever a SecurityFirewall is created (see the
`dc-firewall-on-create` trigger rule in objects/events/). Attaches the
virtualization-vms-policy SecurityPolicy to the new firewall, so the
HTTPS-only rules render in that firewall's juniper_firewall_config
artifact - regardless of which DC design generated it (dc-arista,
dc-cisco, ...), rather than relying on one dedicated standalone
firewall device.

The role predicate lives in the GraphQL query (role__value:
"dc_firewall"), so any other firewall role (e.g. corp-firewall's
edge_firewall) yields an empty result and the generator no-ops.
Idempotent: never attaches the same firewall twice.
"""

from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]

from .common import clean_data
from .schema_protocols import SecurityPolicy

VIRTUALIZATION_POLICY_NAME = "virtualization-vms-policy"


class DcFirewallPolicyGenerator(InfrahubGenerator):
    """Attach the virtualization VM HTTPS-only policy to a new DC firewall."""

    async def generate(self, data: dict) -> None:
        """Attach virtualization-vms-policy to the dc_firewall in `data`.

        Args:
            data: GraphQL query result containing one dc_firewall SecurityFirewall
        """
        cleaned_data = clean_data(data)
        if not isinstance(cleaned_data, dict):
            raise ValueError("clean_data() did not return a dictionary")

        firewalls = cleaned_data.get("SecurityFirewall", [])
        if not firewalls:
            # The query filters on role__value: "dc_firewall" - an empty
            # result is the normal no-op for any other firewall role.
            self.logger.info("No dc_firewall matched the query, skipping")
            return

        firewall = firewalls[0]  # Generator runs per-firewall
        firewall_name = firewall.get("name", "unknown")
        firewall_id = firewall.get("id")

        policy = await self.client.get(
            kind=SecurityPolicy,
            branch=self.branch,
            name__value=VIRTUALIZATION_POLICY_NAME,
        )
        await policy.firewalls.fetch()
        existing_ids = {peer.id for peer in policy.firewalls.peers}
        if firewall_id in existing_ids:
            self.logger.info(f"- {firewall_name} already attached to {VIRTUALIZATION_POLICY_NAME}, skipping")
            return

        policy.firewalls.extend([firewall_id])  # type: ignore[list-item]
        await policy.save(allow_upsert=True)
        self.logger.info(f"- Attached {firewall_name} to {VIRTUALIZATION_POLICY_NAME}")
