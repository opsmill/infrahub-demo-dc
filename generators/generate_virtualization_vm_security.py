"""Virtualization VM Security Generator.

Triggered whenever a VirtualizationVirtualMachine is created (see the
`virtualization_vms` group in .infrahub.yml). Ensures the VM has a
primary IP address, then registers that address in the shared
"virtualization-vms" SecurityAddressGroup - which the static
allow-https-to-virtualization-vms / deny-all-other-to-virtualization-vms
rules in objects/security/16_virtualization_vm_security.yml already
reference, so newly created VMs are automatically covered by the
HTTPS-only policy without editing any rule by hand.

Idempotent: reuses an existing primary_address/SecurityIPAddress/pool
instead of recreating them, and never adds the same address to the
group twice.
"""

from typing import Any

from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]

from .common import clean_data

# CoreIPAddressPool objects are visible globally (the same pool ID shows up
# identically on every branch), so a broken/misconfigured pool created under
# a given name stays broken for every future branch until the name changes.
# Earlier versions of this generator set is_pool=True on the backing prefix,
# which marks a prefix as the source for a CoreIPPrefixPool (sub-prefix
# carving) rather than eligible for individual address allocation - every
# allocation then failed with "no more addresses available" regardless of
# size. Renamed once more (virtualization_vm_pool) to get a fresh pool built
# without that bug, on top of 100.64.0.0/10 (~4.19M addresses, RFC 6598
# shared address space, unused elsewhere in this repo) for headroom against
# branch-recreate-and-delete churn during iterative testing (deleting a
# branch does not release its resource-pool IP allocations back to the pool
# either, since IP uniqueness is tracked globally, not per-branch).
VM_SUBNET = "100.64.0.0/10"
IP_POOL_NAME = "virtualization_vm_pool"
ADDRESS_GROUP_NAME = "virtualization-vms"


class VirtualizationVMSecurityGenerator(InfrahubGenerator):
    """Assign a VM an IP and register it in the HTTPS-only address group."""

    async def generate(self, data: dict) -> None:
        """Secure the VM in `data` behind the HTTPS-only policy.

        Args:
            data: GraphQL query result containing one VirtualizationVirtualMachine
        """
        cleaned_data = clean_data(data)
        if not isinstance(cleaned_data, dict):
            raise ValueError("clean_data() did not return a dictionary")

        vms = cleaned_data.get("VirtualizationVirtualMachine", [])
        if not vms:
            self.logger.warning("No VirtualizationVirtualMachine data found in query result")
            return

        vm = vms[0]  # Generator runs per-VM
        vm_name = vm.get("name", "unknown")
        vm_id = vm.get("id")

        ip_pool = await self._get_or_create_ip_pool()

        primary_address = vm.get("primary_address")
        ip_node: Any
        if primary_address:
            ip_node = await self.client.get(
                kind="IpamIPAddress",
                branch=self.branch,
                id=primary_address["id"],
            )
            self.logger.info(f"- {vm_name} already has primary address {ip_node.address.value}")
        else:
            ip_node = await self.client.allocate_next_ip_address(
                resource_pool=ip_pool,
                identifier=f"{vm_name}-primary",
                data={"description": f"{vm_name} primary address"},
                branch=self.branch,
            )
            vm_node = await self.client.get(
                kind="VirtualizationVirtualMachine",
                branch=self.branch,
                id=vm_id,
            )
            vm_node.primary_address = ip_node.id  # type: ignore[assignment]
            await vm_node.save(allow_upsert=True)
            self.logger.info(f"- Allocated {ip_node.address.value} to {vm_name}")

        security_ip = await self.client.get(
            kind="SecurityIPAddress",
            branch=self.branch,
            name__value=f"{vm_name}-ip",
            raise_when_missing=False,
        )
        if security_ip is None:
            security_ip = await self.client.create(
                kind="SecurityIPAddress",
                branch=self.branch,
                data={
                    "name": f"{vm_name}-ip",
                    "description": f"Primary address of {vm_name}",
                    "ipam_ip_address": ip_node.id,
                },
            )
            await security_ip.save(allow_upsert=True)

        address_group = await self.client.get(
            kind="SecurityAddressGroup",
            branch=self.branch,
            name__value=ADDRESS_GROUP_NAME,
        )
        await address_group.ip_addresses.fetch()
        existing_ids = {peer.id for peer in address_group.ip_addresses.peers}
        if security_ip.id in existing_ids:
            self.logger.info(f"- {vm_name} already in {ADDRESS_GROUP_NAME}, skipping")
            return

        address_group.ip_addresses.extend([security_ip.id])  # type: ignore[list-item]
        await address_group.save(allow_upsert=True)
        self.logger.info(f"- Added {vm_name} ({ip_node.address.value}) to {ADDRESS_GROUP_NAME}")

    async def _get_or_create_ip_pool(self) -> Any:
        """Get or create the dedicated VM address pool, creating its backing prefix too."""
        pool = await self.client.get(
            kind="CoreIPAddressPool",
            branch=self.branch,
            name__value=IP_POOL_NAME,
            raise_when_missing=False,
        )
        if pool:
            return pool

        prefix = await self.client.get(
            kind="IpamPrefix",
            branch=self.branch,
            prefix__value=VM_SUBNET,
            raise_when_missing=False,
        )
        if prefix is None:
            # is_pool=True marks a prefix as the source for a CoreIPPrefixPool
            # (sub-prefix carving, e.g. Technical-IPv4/Customer-IPv4 in
            # objects/bootstrap/17_ip_prefix_pools.yml) - setting it here
            # made this prefix ineligible for individual address allocation,
            # so every CoreIPAddressPool.GetResource call failed with "no
            # more addresses available" regardless of size. Every working
            # CoreIPAddressPool-backed prefix in this instance (e.g.
            # dc-arista-Management-pool's 172.20.3.0/24) has is_pool=False.
            prefix = await self.client.create(
                kind="IpamPrefix",
                branch=self.branch,
                data={
                    "prefix": VM_SUBNET,
                    "status": "active",
                    "member_type": "address",
                },
            )
            await prefix.save(allow_upsert=True)

        pool = await self.client.create(
            kind="CoreIPAddressPool",
            branch=self.branch,
            data={
                "name": IP_POOL_NAME,
                "description": "Address pool for virtualization VM primary addresses",
                "default_address_type": "IpamIPAddress",
                "ip_namespace": "default",
                "resources": [prefix.id],
            },
        )
        await pool.save(allow_upsert=True)
        return pool
