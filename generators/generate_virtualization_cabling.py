"""Virtualization Host Cabling Generator.

Triggered whenever a VirtualizationPhysicalHost is created or updated
(see the `virtualization_hosts` group in .infrahub.yml). Dual-homes the
host to two leaf switches, picking whichever leafs currently have the
most free "customer"-role interfaces available - so cabling naturally
load-balances across leafs and adapts to whatever ports earlier hosts
have already consumed, regardless of which DC design(s) created them.

Idempotent: already-cabled host interfaces are left alone, and a leaf
port is never handed out to more than one host.
"""

from typing import Any

from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]

from .common import clean_data, safe_sort_interface_list

HOST_INTERFACE_NAMES = ["eth0", "eth1"]


class VirtualizationHostCablingGenerator(InfrahubGenerator):
    """Cable a single VirtualizationPhysicalHost to available leaf switches."""

    async def generate(self, data: dict) -> None:
        """Cable the host in `data` to free customer ports on the least-loaded leafs.

        Args:
            data: GraphQL query result containing one VirtualizationPhysicalHost
        """
        cleaned_data = clean_data(data)
        if not isinstance(cleaned_data, dict):
            raise ValueError("clean_data() did not return a dictionary")

        hosts = cleaned_data.get("VirtualizationPhysicalHost", [])
        if not hosts:
            self.logger.warning("No VirtualizationPhysicalHost data found in query result")
            return

        host = hosts[0]  # Generator runs per-host
        host_name = host.get("name", "unknown")
        host_id = host.get("id")
        # clean_data() collapses an empty relationship's {edges: []} to None
        # rather than [] (it checks truthiness), so `or []` covers both a
        # missing key and a present-but-None one - a brand new host with no
        # interfaces yet hits this every time.
        existing_interfaces = {interface["name"]: interface for interface in host.get("interfaces") or []}

        leafs = await self.client.filters(kind="DcimDevice", role__value="leaf", branch=self.branch)
        if not leafs:
            self.logger.info(f"No leaf switches exist yet, skipping cabling for {host_name}")
            return

        # Rank leafs by how many free "customer" ports each currently has,
        # most-available first, so hosts spread out across the fabric instead
        # of piling onto whichever leaf happens to be first.
        #
        # "Free" means no cable attached (connector unset), not status=free:
        # this repo's DC generator marks every interface "active" at device
        # creation time regardless of whether anything is actually plugged
        # in, so status can't be used as an occupancy signal here.
        leaf_free_ports: dict[str, list[Any]] = {}
        for leaf in leafs:
            interfaces = await self.client.filters(
                kind="InterfacePhysical",
                branch=self.branch,
                device__name__value=leaf.name.value,
                role__value="customer",
                prefetch_relationships=True,
            )
            free_interfaces = []
            for interface in interfaces:
                try:
                    already_cabled = bool(interface.connector.peer)
                except ValueError:
                    already_cabled = False
                if not already_cabled:
                    free_interfaces.append(interface)
            by_name = {interface.name.value: interface for interface in free_interfaces}
            leaf_free_ports[leaf.name.value] = [by_name[name] for name in safe_sort_interface_list(list(by_name))]

        ranked_leafs = sorted(
            (leaf for leaf in leafs if leaf_free_ports[leaf.name.value]),
            key=lambda leaf: len(leaf_free_ports[leaf.name.value]),
            reverse=True,
        )
        if not ranked_leafs:
            self.logger.warning(f"No free customer ports on any leaf switch, skipping cabling for {host_name}")
            return

        for index, nic_name in enumerate(HOST_INTERFACE_NAMES):
            existing = existing_interfaces.get(nic_name)
            if existing and existing.get("connector"):
                self.logger.info(f"- {host_name}:{nic_name} already cabled, skipping")
                continue

            if not ranked_leafs:
                self.logger.warning(f"No leaf switches with free ports left for {host_name}:{nic_name}")
                break
            leaf = ranked_leafs[index % len(ranked_leafs)]

            host_iface = await self.client.get(
                kind="InterfacePhysical",
                branch=self.branch,
                device__name__value=host_name,
                name__value=nic_name,
                raise_when_missing=False,
            )
            if host_iface is None:
                host_iface = await self.client.create(
                    kind="InterfacePhysical",
                    branch=self.branch,
                    data={"name": nic_name, "device": host_id, "role": "access", "status": "free"},
                )
                await host_iface.save(allow_upsert=True)

            pool = leaf_free_ports[leaf.name.value]
            leaf_iface = pool.pop(0)
            if not pool:
                ranked_leafs = [item for item in ranked_leafs if item.name.value != leaf.name.value]

            host_iface.status.value = "active"
            host_iface.description.value = f"Uplink to {leaf.name.value} {leaf_iface.name.value}"
            leaf_iface.status.value = "active"
            leaf_iface.description.value = f"Downlink to {host_name} {nic_name}"

            cable = await self.client.create(
                kind="DcimCable",
                branch=self.branch,
                data={
                    "status": "connected",
                    "cable_type": "cat6",
                    "connected_endpoints": [host_iface.id, leaf_iface.id],
                },
            )
            await cable.save(allow_upsert=True)
            host_iface.connector = cable.id  # type: ignore[assignment]
            leaf_iface.connector = cable.id  # type: ignore[assignment]

            await host_iface.save(allow_upsert=True)
            await leaf_iface.save(allow_upsert=True)

            self.logger.info(f"- Cabled {host_name}:{nic_name} -> {leaf.name.value}:{leaf_iface.name.value}")
