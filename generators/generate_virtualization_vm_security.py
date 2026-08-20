"""Virtualization VM Security Generator.

Triggered whenever a VirtualizationVirtualMachine is created (see the
`virtualization_vms` group in .infrahub.yml). Ensures the VM has a
primary IP address, then registers that address in the shared
"virtualization-vms" SecurityAddressGroup - which the static
allow-https-to-virtualization-vms / deny-all-other-to-virtualization-vms
rules in objects/security/16_virtualization_vm_security.yml already
reference, so newly created VMs are automatically covered by the
HTTPS-only policy without editing any rule by hand.

The backing prefix and CoreIPAddressPool are bootstrap data
(objects/bootstrap/21_ip_address_pools.yml), not created here.

Idempotent and concurrency-safe: reuses an existing
primary_address/SecurityIPAddress instead of recreating them, and adds
the address to the group through the RelationshipAdd mutation, which
adds only the named peer and tolerates a peer that is already a member.
"""

from typing import Any

from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]
from infrahub_sdk.protocols import CoreIPAddressPool  # type: ignore[import-not-found]

from .common import extract_single_node
from .schema_protocols import (
    SecurityAddressGroup,
    SecurityIPAddress,
    VirtualizationVirtualMachine,
)

IP_POOL_NAME = "virtualization_vm_pool"
ADDRESS_GROUP_NAME = "virtualization-vms"


class VirtualizationVMSecurityGenerator(InfrahubGenerator):
    """Assign a VM an IP and register it in the HTTPS-only address group."""

    async def generate(self, data: dict) -> None:
        """Secure the VM in `data` behind the HTTPS-only policy.

        Args:
            data: GraphQL query result containing one VirtualizationVirtualMachine
        """
        vm = extract_single_node(data, "VirtualizationVirtualMachine")
        if vm is None:
            self.logger.warning("No VirtualizationVirtualMachine data found in query result")
            return

        vm_name = vm.get("name", "unknown")
        vm_id = vm["id"]

        # The query already returns the primary address' id and value, the only
        # two things needed below, so an existing address needs no extra fetch.
        primary_address = vm.get("primary_address")
        if primary_address:
            ip_id = primary_address["id"]
            ip_address = primary_address["address"]
            self.logger.info(f"- {vm_name} already has primary address {ip_address}")
        else:
            # Declared in objects/bootstrap/21_ip_address_pools.yml - a missing
            # pool means bootstrap has not run, which is a hard error.
            ip_pool = await self.client.get(
                kind=CoreIPAddressPool,
                branch=self.branch,
                name__value=IP_POOL_NAME,
            )
            ip_node: Any = await self.client.allocate_next_ip_address(
                resource_pool=ip_pool,
                identifier=f"{vm_name}-primary",
                data={"description": f"{vm_name} primary address"},
                branch=self.branch,
            )
            vm_node = await self.client.get(
                kind=VirtualizationVirtualMachine,
                branch=self.branch,
                id=vm_id,
            )
            vm_node.primary_address = ip_node.id  # type: ignore[assignment]
            await vm_node.save(allow_upsert=True)
            ip_id = ip_node.id
            ip_address = ip_node.address.value
            self.logger.info(f"- Allocated {ip_address} to {vm_name}")

        # Keyed on the IPAM address, not the VM name: renaming a VM must reuse
        # the SecurityIPAddress already registered for its IP instead of
        # leaving an orphaned entry in the address group.
        security_ips = await self.client.filters(
            kind=SecurityIPAddress,
            branch=self.branch,
            ipam_ip_address__ids=[ip_id],
        )
        if security_ips:
            security_ip = security_ips[0]
        else:
            security_ip = await self.client.create(
                kind=SecurityIPAddress,
                branch=self.branch,
                data={
                    "name": f"{vm_name}-ip",
                    "description": f"Primary address of {vm_name}",
                    "ipam_ip_address": ip_id,
                },
            )
            await security_ip.save(allow_upsert=True)

        address_group = await self.client.get(
            kind=SecurityAddressGroup,
            branch=self.branch,
            name__value=ADDRESS_GROUP_NAME,
        )
        # add_relationships issues a RelationshipAdd mutation, which touches
        # only the peers it names. `ip_addresses.extend()` + `save()` writes back
        # the whole address list as it was read moments earlier, and one of these
        # generators runs per VM concurrently - so each run would clobber the
        # addresses its siblings added in between, and a VM dropped from the
        # group would silently escape the HTTPS-only policy.
        await address_group.add_relationships(relation_to_update="ip_addresses", related_nodes=[security_ip.id])
        self.logger.info(f"- Added {vm_name} ({ip_address}) to {ADDRESS_GROUP_NAME}")
