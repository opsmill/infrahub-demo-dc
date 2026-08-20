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

Idempotent and concurrency-safe: the firewall is attached through the
RelationshipAdd mutation, which adds only the named peer and tolerates a
peer that is already attached.
"""

from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]

from .common import extract_single_node
from .schema_protocols import SecurityPolicy

VIRTUALIZATION_POLICY_NAME = "virtualization-vms-policy"


class DcFirewallPolicyGenerator(InfrahubGenerator):
    """Attach the virtualization VM HTTPS-only policy to a new DC firewall."""

    async def generate(self, data: dict) -> None:
        """Attach virtualization-vms-policy to the dc_firewall in `data`.

        Args:
            data: GraphQL query result containing one dc_firewall SecurityFirewall
        """
        firewall = extract_single_node(data, "SecurityFirewall")
        if firewall is None:
            # The query filters on role__value: "dc_firewall" - an empty
            # result is the normal no-op for any other firewall role.
            self.logger.info("No dc_firewall matched the query, skipping")
            return

        firewall_name = firewall.get("name", "unknown")
        firewall_id = firewall["id"]

        policy = await self.client.get(
            kind=SecurityPolicy,
            branch=self.branch,
            name__value=VIRTUALIZATION_POLICY_NAME,
        )
        # add_relationships issues a RelationshipAdd mutation, which touches
        # only the peers it names. `firewalls.extend()` + `save()` writes back
        # the whole firewall list as it was read moments earlier, and one of
        # these generators runs per firewall concurrently - so each run would
        # clobber the firewalls its siblings attached in between, and a dropped
        # firewall would render its config without the HTTPS-only rules.
        await policy.add_relationships(relation_to_update="firewalls", related_nodes=[firewall_id])
        self.logger.info(f"- Attached {firewall_name} to {VIRTUALIZATION_POLICY_NAME}")
