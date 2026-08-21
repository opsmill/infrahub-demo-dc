"""Virtualization Host Cabling Generator.

Triggered whenever a VirtualizationPhysicalHost is created
(see the `virtualization_hosts` group in .infrahub.yml). Two things
happen per host, both scoped to the host's own metro so that loading a
second DC design in another city can never pull a host's rack or cables
across sites:

1. Placement: if a LocationRack exists in the host's metro (from
   whichever DC design has been generated there, if any), the host is
   moved out of its building-level default location into a rack and
   given a rack unit. A no-op if no racks exist yet. Which rack and
   which unit are derived from the host's own name rather than from how
   full the racks currently are - see _assign_rack for why. The position
   matters beyond tidiness: the rack_elevation transform skips any
   device without one, so a host placed in a rack but given no rack unit
   never appears in the rack drawing.
2. Cabling: dual-homes the host to two leaf switches in the same metro,
   picking whichever leafs currently have the most free "customer"-role
   interfaces available - so cabling naturally load-balances across
   leafs and adapts to whatever ports earlier hosts have already
   consumed.
3. Addressing: gives the host a management address from the fabric's
   hypervisor-management segment, once it is attached to the fabric. The
   address belongs here rather than in a generator of its own because it
   is a property of the connection: the prefix is the one the leaf pair
   renders an anycast gateway for, so the address is only reachable
   through the ports cabled above.

   Two conditions gate it, and both are about reachability rather than
   tidiness. The host must have at least one cabled NIC, and the
   deployment its leafs belong to must carry a segment over the address
   pool's prefix. Checking the pool alone is not enough: the pool is
   bootstrap data on the default branch, so it is visible from every
   branch, while the segment is loaded per DC design. Without the segment
   check a branch with no segment would hand out addresses for a subnet
   no leaf routes.

The host's eth0/eth1 NICs come from whichever sized
VIRTUALIZATION_HOST_* object template it was created from
(objects/bootstrap/10_physical_device_templates.yml), not from this
generator. A larger template carries further NICs; only eth0/eth1 are
cabled here.

Idempotent, in the specific sense Infrahub means: a host already placed
in a rack keeps its rack unit, an already-cabled NIC keeps its cable, and
a leaf port is never handed out to more than one host.

That is not the same as "skip the write". A generator run is the desired
state for its target - Infrahub deletes anything the previous run saved
that this run does not - so every object this generator owns (the host,
its cabled NICs, the leaf ports they land on, and the cables between
them) is re-saved on every run even when nothing about it changed.
Skipping those writes is what deleted host NICs, and then the hosts
themselves, from a stack this generator had cabled on an earlier run.
"""

from typing import Any

from infrahub_sdk.exceptions import GraphQLError  # type: ignore[import-not-found]
from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]
from infrahub_sdk.protocols import CoreIPAddressPool  # type: ignore[import-not-found]

from .common import extract_single_node, safe_sort_interface_list
from .schema_protocols import (
    DcimCable,
    DcimDevice,
    InterfacePhysical,
    ServiceNetworkSegment,
    VirtualizationPhysicalHost,
)

HOST_INTERFACE_NAMES = ["eth0", "eth1"]

# Declared in objects/bootstrap/21_ip_address_pools.yml over the same prefix the
# hypervisor-management segment uses, so an allocated address sits behind the
# gateway the leaf pair renders. The pool is the starting point for finding that
# segment: its prefix is what the segment must be carrying.
HOST_IP_POOL_NAME = "virtualization_host_pool"

# Segment types that render a gateway for their prefix (see the leaf templates).
# A host address in an l2_only segment would have nothing to route it.
ROUTED_SEGMENT_TYPES = ("l3_gateway", "l3_vrf")

METRO_RACKS_QUERY = """
query MetroRacks($metro_ids: [ID]) {
  LocationMetro(ids: $metro_ids) {
    edges {
      node {
        descendants {
          edges {
            node {
              id
              __typename
            }
          }
        }
      }
    }
  }
}
"""


# Mirrors queries/visualization/rack_elevation.gql: position and device_type
# sit on the relationship peer, only name/status need the generic fragment.
RACK_OCCUPANCY_QUERY = """
query RackOccupancy($rack_ids: [ID]) {
  LocationRack(ids: $rack_ids) {
    edges {
      node {
        id
        name {
          value
        }
        height {
          value
        }
        devices {
          edges {
            node {
              position {
                value
              }
              device_type {
                node {
                  height {
                    value
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

DEFAULT_RACK_HEIGHT = 42

# Hosts are placed on a fixed grid counted up from the bottom of the rack: the
# first host of a rack takes U1, the next U3, and so on. The stride has to be at
# least as tall as the tallest host, or a 2U host at U1 would overlap the next
# host at U2; _assign_rack warns if it meets a taller one. Counting up from the
# bottom keeps hosts clear of the fabric, which the DC generator stacks
# downwards from U42 in TopologyCreator.assign_devices_to_racks.
RACK_UNIT_STRIDE = 2


class VirtualizationHostCablingGenerator(InfrahubGenerator):
    """Cable a single VirtualizationPhysicalHost to available leaf switches."""

    async def generate(self, data: dict) -> None:
        """Cable the host in `data` to free customer ports on the least-loaded leafs.

        Args:
            data: GraphQL query result containing one VirtualizationPhysicalHost
        """
        host = extract_single_node(data, "VirtualizationPhysicalHost")
        if host is None:
            self.logger.warning("No VirtualizationPhysicalHost data found in query result")
            return

        host_name = host.get("name", "unknown")
        # Fetched once and re-saved in the `finally` below whatever else
        # happens: the host is the generator's own target, so dropping it out of
        # the tracking group means Infrahub deletes it - together with its
        # interfaces and its VMs, which are components of it - on the next run.
        # A single save on the way out is what makes that unconditional; the
        # steps in between only set fields on host_node. One fetch also saves
        # the placement and addressing steps a round trip each.
        host_node = await self.client.get(kind=VirtualizationPhysicalHost, branch=self.branch, id=host["id"])
        try:
            await self._place_and_cable(host, host_name, host_node)
        finally:
            await host_node.save(allow_upsert=True)

    async def _place_and_cable(self, host: dict[str, Any], host_name: str, host_node: Any) -> None:
        """Rack, cable and address the host, setting fields on `host_node`.

        Never saves `host_node` - generate() does that on every exit path,
        including an exception, so no early return here can drop the host out of
        the tracking group.

        Args:
            host: The cleaned host node from the query.
            host_name: Host name, for logging.
            host_node: The host, fetched by generate().
        """
        # clean_data() collapses an empty relationship's {edges: []} to None
        # rather than [] (it checks truthiness), so `or []` covers both a
        # missing key and a present-but-None one - a brand new host with no
        # interfaces yet hits this every time.
        existing_interfaces = {interface["name"]: interface for interface in host.get("interfaces") or []}

        current_location = host.get("location") or {}
        metro_id = self._resolve_metro_id(current_location)
        if metro_id is None:
            self.logger.warning(f"{host_name} has no metro in its location hierarchy, skipping placement and cabling")
            return

        # Every rack and leaf candidate below is restricted to this set, so a
        # host declared in Frankfurt can never end up racked or cabled in
        # another city's DC.
        rack_ids = await self._get_metro_rack_ids(metro_id)
        if not rack_ids:
            self.logger.info(
                f"No racks exist in {host_name}'s metro yet, leaving it at its "
                "building-level location and skipping cabling"
            )
            return

        host_height = (host.get("device_type") or {}).get("height") or 1
        await self._assign_rack(host_name, host_node, current_location.get("typename"), rack_ids, host_height)

        leafs = await self.client.filters(
            kind=DcimDevice,
            role__value="leaf",
            location__ids=rack_ids,
            branch=self.branch,
        )
        if not leafs:
            self.logger.info(f"No leaf switches exist in {host_name}'s metro yet, skipping cabling")
            return

        # Rank leafs by how many free "customer" ports each currently has,
        # most-available first, so hosts spread out across the fabric instead
        # of piling onto whichever leaf happens to be first.
        #
        # "Free" means no cable attached (connector unset), not status=free:
        # this repo's DC generator marks every interface "active" at device
        # creation time regardless of whether anything is actually plugged
        # in, so status can't be used as an occupancy signal here.
        #
        # One filters() call for all leafs at once - a per-leaf loop here is
        # an N+1 round-trip per host creation.
        # No prefetch_relationships: only the connector's and device's ids are
        # needed, and a cardinality-one relationship always carries its peer id
        # in the response. Prefetching would splice the whole peer device and
        # cable into the selection set once per interface, for two ids.
        interfaces = await self.client.filters(
            kind=InterfacePhysical,
            branch=self.branch,
            device__ids=[leaf.id for leaf in leafs],
            role__value="customer",
        )
        by_name: dict[str, dict[str, Any]] = {leaf.id: {} for leaf in leafs}
        for interface in interfaces:
            # connector.id is None when nothing is cabled to the port.
            if interface.connector.id:
                continue
            device_id = interface.device.id
            if device_id in by_name:
                by_name[device_id][interface.name.value] = interface

        # Keyed by leaf id, not name: two leafs in different DCs of the same
        # metro can share a name, and merging their ports would hand one leaf's
        # port out as the other's.
        free_ports: dict[str, list[Any]] = {
            leaf_id: [ports[name] for name in safe_sort_interface_list(list(ports))]
            for leaf_id, ports in by_name.items()
        }

        ranked_leafs = sorted(
            (leaf for leaf in leafs if free_ports[leaf.id]),
            key=lambda leaf: len(free_ports[leaf.id]),
            reverse=True,
        )
        if not ranked_leafs:
            # Not a return: a host cabled by an earlier run is still attached to
            # the fabric and still needs its address, and the loop below reports
            # each NIC it could not cable.
            self.logger.warning(f"No free customer ports on any leaf switch, cannot cable {host_name}")
        elif len(ranked_leafs) == 1 and len(HOST_INTERFACE_NAMES) > 1:
            # `index % len(ranked_leafs)` still cables every NIC, but with one
            # leaf to choose from they all land on it. The host comes up, so
            # nothing below reports it - say so here, because a single-homed
            # host loses the leaf redundancy this generator exists to provide.
            self.logger.warning(
                f"Only one leaf switch has free customer ports, so {host_name} will be "
                f"single-homed to {ranked_leafs[0].name.value} instead of dual-homed"
            )

        attached_nics = 0
        for index, nic_name in enumerate(HOST_INTERFACE_NAMES):
            existing = existing_interfaces.get(nic_name)
            if existing is None:
                # NICs are cloned from the host's sized object template at
                # creation - a missing one means the host was created without
                # a VIRTUALIZATION_HOST_* template.
                self.logger.warning(
                    f"- {host_name} has no {nic_name} interface (expected from its "
                    f"VIRTUALIZATION_HOST_* object template), skipping"
                )
                continue
            if existing.get("connector"):
                self.logger.info(f"- {host_name}:{nic_name} already cabled, keeping it")
                await self._retain_cabling(existing["id"], (existing["connector"] or {}).get("id"))
                attached_nics += 1
                continue

            host_iface = await self.client.get(
                kind=InterfacePhysical,
                branch=self.branch,
                id=existing["id"],
            )

            # A concurrent generator run for another host can grab the same
            # port between our scan above and this write - Infrahub rejects
            # the second cable with a "maximum of 1 peer" error. Retry with
            # the next candidate port instead of failing the whole host.
            cabled = False
            while not cabled and ranked_leafs:
                leaf = ranked_leafs[index % len(ranked_leafs)]
                pool = free_ports[leaf.id]
                leaf_iface = pool.pop(0)
                if not pool:
                    # Dropping an exhausted leaf here is what keeps every leaf
                    # left in ranked_leafs backed by at least one free port.
                    ranked_leafs = [item for item in ranked_leafs if item.id != leaf.id]

                host_iface.status.value = "active"
                host_iface.description.value = f"Uplink to {leaf.name.value} {leaf_iface.name.value}"
                leaf_iface.status.value = "active"
                leaf_iface.description.value = f"Downlink to {host_name} {nic_name}"

                try:
                    cable = await self.client.create(
                        kind=DcimCable,
                        branch=self.branch,
                        data={
                            "status": "connected",
                            "cable_type": "cat6",
                            "connected_endpoints": [host_iface.id, leaf_iface.id],
                        },
                    )
                    await cable.save(allow_upsert=True)
                except GraphQLError as exc:
                    self.logger.warning(
                        f"- {leaf.name.value}:{leaf_iface.name.value} was claimed by a concurrent "
                        f"run ({exc}), trying another port for {host_name}:{nic_name}"
                    )
                    continue

                host_iface.connector = cable.id  # type: ignore[assignment]
                leaf_iface.connector = cable.id  # type: ignore[assignment]

                await host_iface.save(allow_upsert=True)
                await leaf_iface.save(allow_upsert=True)

                self.logger.info(f"- Cabled {host_name}:{nic_name} -> {leaf.name.value}:{leaf_iface.name.value}")
                cabled = True
                attached_nics += 1

            if not cabled:
                self.logger.warning(f"No leaf switches with free ports left for {host_name}:{nic_name}")

        if not attached_nics:
            self.logger.info(
                f"{host_name} is not attached to the fabric, so it gets no management address "
                "(an address it cannot reach is worse than none)"
            )
            return

        await self._assign_management_address(
            host_name=host_name,
            host_node=host_node,
            primary_address=host.get("primary_address"),
            deployment_ids=self._deployment_ids(leafs),
        )

    @staticmethod
    async def _keep_tracked(node: Any) -> None:
        """Re-save an object this generator already owns, so Infrahub keeps it.

        A generator run is the desired state for its target: Infrahub deletes
        whatever the previous run saved and this run does not. "Nothing changed,
        skip the write" is therefore not a no-op, it is a delete one run later.

        Args:
            node: Node to re-save. A save with no changed attribute is a cheap
                way to say "still mine".
        """
        await node.save(allow_upsert=True)

    async def _retain_cabling(self, host_interface_id: str, cable_id: str | None) -> None:
        """Keep an existing cable, and both ports it joins, in the tracking group.

        The cable and the leaf port were created and saved by the run that did
        the cabling, so they are this generator's to keep alive. Without this,
        the run that finds a host already cabled hands Infrahub a desired state
        with no cable in it, and the fabric loses the connection.

        Args:
            host_interface_id: The host NIC that is already cabled.
            cable_id: The cable attached to it, from the query payload.
        """
        if cable_id is None:
            host_interface = await self.client.get(kind=InterfacePhysical, branch=self.branch, id=host_interface_id)
            await self._keep_tracked(host_interface)
            return

        # The cable's endpoints are both ports it joins - the host NIC and the
        # leaf port - so fetching it prefetched covers the NIC too, with no
        # separate read for it.
        cable = await self.client.get(
            kind=DcimCable,
            branch=self.branch,
            id=cable_id,
            prefetch_relationships=True,
            include=["connected_endpoints"],
        )
        await self._keep_tracked(cable)
        for endpoint in cable.connected_endpoints.peers:
            await self._keep_tracked(endpoint.peer)

    @staticmethod
    def _deployment_ids(leafs: list[Any]) -> list[str]:
        """Collect the deployments the host's leaf switches belong to.

        The DC generator stamps `topology` on every switch it creates, which is
        what ties a leaf to its deployment - and a segment names exactly one
        deployment. Going host -> leafs -> deployment is what lets the segment
        be found without the generator knowing any DC design by name.

        Args:
            leafs: Leaf switches in the host's metro.

        Returns:
            Unique deployment IDs, empty when no leaf carries one.
        """
        ids = []
        for leaf in leafs:
            try:
                deployment_id = leaf.topology.id
            except (AttributeError, ValueError):
                continue
            if deployment_id and deployment_id not in ids:
                ids.append(deployment_id)
        return ids

    async def _find_management_segment(self, deployment_ids: list[str], prefix_ids: list[str]) -> Any | None:
        """Find the routed segment that carries one of the pool's prefixes.

        Args:
            deployment_ids: Deployments the host's leafs belong to.
            prefix_ids: Prefix IDs backing the host address pool.

        Returns:
            The matching segment, or None when this fabric has none.
        """
        if not deployment_ids or not prefix_ids:
            return None

        segments = await self.client.filters(
            kind=ServiceNetworkSegment,
            branch=self.branch,
            deployment__ids=deployment_ids,
            prefix__ids=prefix_ids,
        )
        return next(
            (segment for segment in segments if segment.segment_type.value in ROUTED_SEGMENT_TYPES),
            None,
        )

    async def _assign_management_address(
        self,
        host_name: str,
        host_node: Any,
        primary_address: dict[str, Any] | None,
        deployment_ids: list[str],
    ) -> None:
        """Give the host an address from its fabric's hypervisor-management segment.

        Idempotent: a host that already has a primary address keeps it, so
        re-running the generator never reallocates.

        Neither a missing pool nor a missing segment is an error. Both mean the
        same thing - this fabric has nowhere to put a hypervisor address - and
        leaving the host unaddressed beats inventing a subnet no leaf routes.
        The segment is what makes the address reachable, so it is the condition
        that matters: the pool is bootstrap data visible from every branch,
        while the segment is loaded per DC design.

        Args:
            host_name: Host name, for logging
            host_node: The host node. Only mutated here; generate() saves it.
            primary_address: The host's existing primary address from the query,
                or None when it has none.
            deployment_ids: Deployments the host's leaf switches belong to, used
                to find the segment that serves this fabric.
        """
        if primary_address:
            self.logger.info(f"- {host_name} already has management address {primary_address.get('address')}")
            return

        pool = await self.client.get(
            kind=CoreIPAddressPool,
            branch=self.branch,
            name__value=HOST_IP_POOL_NAME,
            raise_when_missing=False,
            prefetch_relationships=True,
            include=["resources"],
        )
        if pool is None:
            self.logger.info(
                f"- No {HOST_IP_POOL_NAME} in this branch, leaving {host_name} without a management address"
            )
            return

        prefix_ids = [resource.id for resource in pool.resources.peers if resource.id]
        segment = await self._find_management_segment(deployment_ids, prefix_ids)
        if segment is None:
            self.logger.info(
                f"- No routed segment over the {HOST_IP_POOL_NAME} prefix in {host_name}'s deployment, "
                "leaving it without a management address - load a hypervisor-management segment "
                "(objects/segments/) for this DC design to have one allocated"
            )
            return
        self.logger.info(f"- {host_name} is served by segment {segment.name.value} (VLAN {segment.vlan_id.value})")

        address: Any = await self.client.allocate_next_ip_address(
            resource_pool=pool,
            identifier=f"{host_name}-mgmt",
            data={"description": f"{host_name} hypervisor management address"},
            branch=self.branch,
        )
        if address is None:
            self.logger.warning(f"- {HOST_IP_POOL_NAME} had no free address for {host_name}")
            return

        host_node.primary_address = address.id  # type: ignore[assignment]
        self.logger.info(f"- Allocated {address.address.value} to {host_name} from {HOST_IP_POOL_NAME}")

    @staticmethod
    def _resolve_metro_id(location: dict) -> str | None:
        """Find the LocationMetro in the host's location hierarchy.

        Args:
            location: The cleaned location dict from the query (id, typename,
                and ancestors when the location is a building or rack).

        Returns:
            The metro ID, or None if the hierarchy has no metro.
        """
        if location.get("typename") == "LocationMetro":
            return location.get("id")
        for ancestor in location.get("ancestors") or []:
            if ancestor.get("typename") == "LocationMetro":
                return ancestor.get("id")
        return None

    async def _get_metro_rack_ids(self, metro_id: str) -> list[str]:
        """Return the IDs of every LocationRack under the given metro.

        Args:
            metro_id: ID of the LocationMetro to scope to.

        Returns:
            Rack IDs under the metro (empty when no DC design exists there yet).
        """
        result = await self.client.execute_graphql(
            query=METRO_RACKS_QUERY,
            variables={"metro_ids": [metro_id]},
            branch_name=self.branch,
        )
        rack_ids = []
        for metro_edge in result.get("LocationMetro", {}).get("edges", []):
            for edge in metro_edge["node"].get("descendants", {}).get("edges", []):
                if edge["node"]["__typename"] == "LocationRack":
                    rack_ids.append(edge["node"]["id"])
        return rack_ids

    async def _assign_rack(
        self,
        host_name: str,
        host_node: Any,
        current_location_typename: str | None,
        rack_ids: list[str],
        host_height: int,
    ) -> None:
        """Place the host in a rack of its own metro, if any exist.

        A host already in a rack keeps its place. Nothing is saved here -
        generate() saves the host on every exit path.

        Rack and rack unit are a function of the host's own name, not of how
        full the racks are when this runs. One generator instance runs per host,
        and loading a file of hosts fires all of them at once across the task
        workers, so an occupancy-derived choice makes every host read the racks
        before any sibling has saved: all of them then pick the same "emptiest"
        rack and the same free unit, and the elevation draws six hosts stacked
        on one another. Sorting the metro's hosts by name and taking this host's
        own index gives each concurrent run a different answer from the same
        input, which is how the DC generator places the fabric too - there
        `leaf-01` owns Rack-1 by its name rather than by what it finds.

        Args:
            host_name: Host name, for logging
            host_node: The host node to place
            current_location_typename: __typename of the host's current location
            rack_ids: IDs of the racks in the host's metro (never empty)
            host_height: Height of the host in rack units, from its device type
        """
        if current_location_typename == "LocationRack":
            self.logger.info(f"- {host_name} already placed in a rack, keeping it there")
            return

        racks = await self._rack_occupancy(rack_ids)
        if not racks:
            self.logger.warning(f"- No rack occupancy returned for {host_name}, leaving it where it is")
            return

        slot = await self._placement_slot(host_name)
        if slot is None:
            self.logger.warning(f"- {host_name} not found among the hosts to place, leaving it where it is")
            return

        # Ordered by name so every concurrent run indexes the same list. Racks
        # spread across slots and units stack within them, so consecutive hosts
        # fill rack 1 U1, rack 2 U1, ... and only come back to rack 1 at U3 once
        # every rack holds one.
        racks.sort(key=lambda rack: rack["name"])
        target = racks[slot % len(racks)]
        position = 1 + (slot // len(racks)) * RACK_UNIT_STRIDE

        if host_height > RACK_UNIT_STRIDE:
            self.logger.warning(
                f"- {host_name} is {host_height}U, taller than the {RACK_UNIT_STRIDE}U placement grid"
                " - it may overlap the host above it"
            )

        # The slot keeps hosts off each other, but not off the fabric the DC
        # generator already racked, nor off a host placed by an earlier run
        # whose name sorts later than this one's. Step up the grid until the
        # host's whole footprint is clear.
        while position + host_height - 1 <= target["rack_height"] and not target["occupied_units"].isdisjoint(
            range(position, position + host_height)
        ):
            position += RACK_UNIT_STRIDE

        host_node.location = target["id"]  # type: ignore[assignment]
        if position + host_height - 1 > target["rack_height"]:
            self.logger.warning(
                f"- Placed {host_name} in rack {target['name']} without a position "
                f"({host_height}U does not fit) - it will not show in the rack elevation"
            )
        else:
            host_node.position = position  # type: ignore[assignment]
            self.logger.info(f"- Placed {host_name} in rack {target['name']} at U{position}")

    async def _placement_slot(self, host_name: str) -> int | None:
        """Return this host's index among all hosts, ordered by name.

        The index is what makes placement deterministic: it depends on the set
        of hosts, which every concurrent run of this generator sees identically,
        rather than on how far those runs have progressed.

        Hosts of other metros are counted too. They cost nothing but a wider
        spread, since the slot is only ever resolved against the racks of the
        host's own metro, and leaving them in keeps the index independent of
        which location each host has reached.

        Args:
            host_name: Name of the host being placed

        Returns:
            The host's 0-based index, or None if it is not found.
        """
        hosts = await self.client.filters(
            kind=VirtualizationPhysicalHost,
            branch=self.branch,
            include=["name"],
        )
        names = sorted(host.name.value for host in hosts)
        return names.index(host_name) if host_name in names else None

    async def _rack_occupancy(self, rack_ids: list[str]) -> list[dict[str, Any]]:
        """Summarise how full each candidate rack is, in one round trip.

        A rack's devices are read through GraphQL rather than
        `LocationRack.devices.peers`: `filters(prefetch_relationships=True)`
        leaves a many-relationship empty, so a peer count is always zero and
        every host would pile into whichever rack happened to be first.

        Args:
            rack_ids: IDs of the racks in the host's metro

        Returns:
            One dict per rack with its id, name, rack_height, and the set of
            rack units its devices already occupy - a device's whole footprint,
            not just the unit it is anchored at, so a 2U device at U41 reports
            both U41 and U42.
        """
        result = await self.client.execute_graphql(
            query=RACK_OCCUPANCY_QUERY,
            variables={"rack_ids": rack_ids},
            branch_name=self.branch,
        )

        racks = []
        for edge in result.get("LocationRack", {}).get("edges", []):
            node = edge["node"]
            rack_height = (node.get("height") or {}).get("value") or DEFAULT_RACK_HEIGHT
            occupied_units: set[int] = set()
            for device_edge in node.get("devices", {}).get("edges", []):
                device = device_edge["node"]
                device_height = (((device.get("device_type") or {}).get("node") or {}).get("height") or {}).get(
                    "value"
                ) or 1
                position = (device.get("position") or {}).get("value")
                # A device with no position is not drawn in the elevation, so it
                # holds no unit anything could collide with.
                if position is not None:
                    occupied_units.update(range(position, position + device_height))
            racks.append(
                {
                    "id": node["id"],
                    "name": node["name"]["value"],
                    "rack_height": rack_height,
                    "occupied_units": occupied_units,
                }
            )
        return racks
