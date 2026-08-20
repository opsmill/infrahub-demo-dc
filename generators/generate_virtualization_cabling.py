"""Virtualization Host Cabling Generator.

Triggered whenever a VirtualizationPhysicalHost is created
(see the `virtualization_hosts` group in .infrahub.yml). Two things
happen per host, both scoped to the host's own metro so that loading a
second DC design in another city can never pull a host's rack or cables
across sites:

1. Placement: if a LocationRack exists in the host's metro (from
   whichever DC design has been generated there, if any), the host is
   moved into the least-occupied one from its building-level default
   location and given a free rack unit. A no-op if no racks exist yet.
   The position matters beyond tidiness: the rack_elevation transform
   skips any device without one, so a host placed in a rack but left
   positionless never appears in the rack drawing.
2. Cabling: dual-homes the host to two leaf switches in the same metro,
   picking whichever leafs currently have the most free "customer"-role
   interfaces available - so cabling naturally load-balances across
   leafs and adapts to whatever ports earlier hosts have already
   consumed.

The host's eth0/eth1 NICs come from whichever sized
VIRTUALIZATION_HOST_* object template it was created from
(objects/bootstrap/10_physical_device_templates.yml), not from this
generator. A larger template carries further NICs; only eth0/eth1 are
cabled here.

Idempotent: a host already placed in a rack is left alone, already-cabled
host interfaces are left alone, and a leaf port is never handed out to
more than one host.
"""

from typing import Any

from infrahub_sdk.exceptions import GraphQLError  # type: ignore[import-not-found]
from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]

from .common import extract_single_node, safe_sort_interface_list
from .schema_protocols import (
    DcimCable,
    DcimDevice,
    InterfacePhysical,
    VirtualizationPhysicalHost,
)

HOST_INTERFACE_NAMES = ["eth0", "eth1"]

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
        host_id = host["id"]
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
        await self._assign_rack(host_name, host_id, current_location.get("typename"), rack_ids, host_height)

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
        interfaces = await self.client.filters(
            kind=InterfacePhysical,
            branch=self.branch,
            device__ids=[leaf.id for leaf in leafs],
            role__value="customer",
            prefetch_relationships=True,
        )
        free_by_leaf: dict[str, dict[str, Any]] = {leaf.id: {} for leaf in leafs}
        for interface in interfaces:
            try:
                already_cabled = bool(interface.connector.peer)
            except ValueError:
                already_cabled = False
            if not already_cabled:
                device_id = interface.device.peer.id
                if device_id in free_by_leaf:
                    free_by_leaf[device_id][interface.name.value] = interface

        leaf_free_ports: dict[str, list[Any]] = {}
        for leaf in leafs:
            by_name = free_by_leaf[leaf.id]
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
                self.logger.info(f"- {host_name}:{nic_name} already cabled, skipping")
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
                pool = leaf_free_ports[leaf.name.value]
                leaf_iface = pool.pop(0)
                if not pool:
                    # Dropping an exhausted leaf here is what keeps every leaf
                    # left in ranked_leafs backed by at least one free port.
                    ranked_leafs = [item for item in ranked_leafs if item.name.value != leaf.name.value]

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

            if not cabled:
                self.logger.warning(f"No leaf switches with free ports left for {host_name}:{nic_name}")

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
        host_id: str,
        current_location_typename: str | None,
        rack_ids: list[str],
        host_height: int,
    ) -> None:
        """Move the host into the emptiest rack of its own metro, if any exist.

        Args:
            host_name: Host name, for logging
            host_id: Host ID
            current_location_typename: __typename of the host's current location
            rack_ids: IDs of the racks in the host's metro (never empty)
            host_height: Height of the host in rack units, from its device type
        """
        if current_location_typename == "LocationRack":
            self.logger.info(f"- {host_name} already placed in a rack, skipping")
            return

        racks = await self._rack_occupancy(rack_ids)
        if not racks:
            self.logger.warning(f"- No rack occupancy returned for {host_name}, leaving it where it is")
            return

        # Occupancy is measured in rack units rather than device count, so a
        # rack holding one 24U chassis does not read as emptier than a rack
        # holding three 1U switches. Ties break on name to keep placement
        # deterministic across runs.
        #
        # There is no conflict-retry here: two concurrent host-creation runs can
        # both read the same emptiest rack and pick the same free unit in it.
        # The worst case is two devices drawn at one position rather than an
        # error, so a guard is not worth the complexity.
        target = min(racks, key=lambda rack: (rack["used_units"], rack["name"]))
        if target["positions"]:
            position = min(target["positions"]) - host_height
        else:
            position = target["rack_height"] - (host_height - 1)
        if position < 1:
            position = None

        host_node = await self.client.get(kind=VirtualizationPhysicalHost, branch=self.branch, id=host_id)
        host_node.location = target["id"]  # type: ignore[assignment]
        if position is None:
            self.logger.warning(
                f"- Placed {host_name} in rack {target['name']} without a position "
                f"({host_height}U does not fit) - it will not show in the rack elevation"
            )
        else:
            host_node.position = position  # type: ignore[assignment]
            self.logger.info(f"- Placed {host_name} in rack {target['name']} at U{position}")
        await host_node.save(allow_upsert=True)

    async def _rack_occupancy(self, rack_ids: list[str]) -> list[dict[str, Any]]:
        """Summarise how full each candidate rack is, in one round trip.

        A rack's devices are read through GraphQL rather than
        `LocationRack.devices.peers`: `filters(prefetch_relationships=True)`
        leaves a many-relationship empty, so a peer count is always zero and
        every host would pile into whichever rack happened to be first.

        Args:
            rack_ids: IDs of the racks in the host's metro

        Returns:
            One dict per rack with its id, name, used_units, the positions its
            devices already occupy, and its rack_height. The caller stacks
            downward from the top of the rack, the convention the DC generator
            uses in TopologyCreator.assign_devices_to_racks.
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
            positions, used_units = [], 0
            for device_edge in node.get("devices", {}).get("edges", []):
                device = device_edge["node"]
                device_height = (((device.get("device_type") or {}).get("node") or {}).get("height") or {}).get(
                    "value"
                ) or 1
                used_units += device_height
                position = (device.get("position") or {}).get("value")
                if position is not None:
                    positions.append(position)
            racks.append(
                {
                    "id": node["id"],
                    "name": node["name"]["value"],
                    "used_units": used_units,
                    "positions": positions,
                    "rack_height": rack_height,
                }
            )
        return racks
