"""Data Center Topology Generator.

This module generates complete data center network topologies in Infrahub, including:
- Physical infrastructure (sites, racks, devices)
- Network connectivity (fabric peering, cables)
- Routing protocols (OSPF or eBGP for underlay, iBGP EVPN for overlay)
- IP addressing (loopbacks, management, VTEP)

Supports two deployment scenarios:
1. OSPF + iBGP: OSPF for underlay routing, iBGP EVPN for overlay
2. eBGP + iBGP: eBGP for underlay routing, iBGP EVPN for overlay

The generator creates a fully operational spine-leaf fabric with route reflection,
dual loopbacks (underlay + VTEP), and proper BGP peer group configuration.
"""

from typing import Any

from infrahub_sdk.generator import InfrahubGenerator
from infrahub_sdk.protocols import CoreNumberPool

from .common import TopologyCreator, clean_data, safe_sort_interface_list
from .schema_protocols import DcimCable, InterfacePhysical, InterfaceVirtual


class DCTopologyCreator(TopologyCreator):
    """Create data center topology with spine-leaf architecture."""

    # ============================================================================
    # Physical Fabric Connectivity
    # ============================================================================

    async def create_fabric_peering(self) -> None:
        """Create physical fabric peering connections between spine and leaf devices.

        This method creates the physical layer connectivity for a spine-leaf fabric:
        - Connects each spine to all leaf devices (full mesh)
        - Connects each spine to all border leaf devices (full mesh)
        - Uses unnumbered point-to-point interfaces
        - Creates explicit DcimCable objects with bidirectional connector relationships

        The connections are based on device templates which define available interfaces
        and their roles (uplink, leaf, etc.).
        """
        batch = await self.client.create_batch()

        # ========================================
        # Step 1: Build interface mappings from device templates
        # ========================================
        # Extract available interfaces from each device's template
        interfaces: dict = {}
        for device in self.devices:
            # Only process fabric devices (skip edge, servers, etc.)
            if device.role.value not in ["leaf", "spine", "border_leaf"]:
                continue

            # Get the device template to know which interfaces are available
            template_name = self._get_device_template_name(device)
            if not template_name or template_name not in self.data["templates"]:
                continue

            # Extract interfaces that participate in fabric peering
            # - "leaf" role: spine-facing ports (on spine devices)
            # - "uplink" role: spine-facing ports (on leaf/border_leaf devices)
            interfaces[device.name.value] = [
                {
                    "name": interface["name"],
                    "role": interface["role"],
                }
                for interface in self.data["templates"][template_name]
                if interface["role"] in ["leaf", "uplink"]
            ]

        # ========================================
        # Step 2: Group interfaces by device type and role
        # ========================================
        # Spine interfaces facing leaf devices (role="leaf" on spine)
        spines_leaves = {
            name: safe_sort_interface_list([iface.get("name") for iface in ifaces if iface.get("role") == "leaf"])
            for name, ifaces in interfaces.items()
            if "spine" in name
        }
        # Spine interfaces facing border leaf devices (role="uplink" on spine for borders)
        spine_borders = {
            name: safe_sort_interface_list([iface.get("name") for iface in ifaces if iface.get("role") == "uplink"])
            for name, ifaces in interfaces.items()
            if "spine" in name
        }

        # Leaf uplink interfaces (facing spine)
        leafs = {
            name: safe_sort_interface_list([iface.get("name") for iface in ifaces if iface.get("role") == "uplink"])
            for name, ifaces in interfaces.items()
            if "leaf" in name and "border" not in name
        }

        # Border leaf uplink interfaces (facing spine)
        border_leafs = {
            name: safe_sort_interface_list([iface.get("name") for iface in ifaces if iface.get("role") == "uplink"])
            for name, ifaces in interfaces.items()
            if "border_leaf" in name
        }

        # ========================================
        # Step 3: Build connection matrix
        # ========================================
        # Create spine-to-leaf connections (full mesh)
        # Each spine connects to each leaf using next available interface from each list
        connections: list = [
            {
                "source": spine,
                "target": leaf,
                "source_interface": spine_interfaces.pop(0),
                "destination_interface": leaf_interfaces.pop(0),
            }
            for spine, spine_interfaces in spines_leaves.items()
            for leaf, leaf_interfaces in leafs.items()
            if spine_interfaces and leaf_interfaces  # Guard against empty lists
        ]

        # Add spine-to-border-leaf connections (full mesh)
        connections.extend(
            {
                "source": spine,
                "target": leaf,
                "source_interface": spine_interfaces.pop(0),
                "destination_interface": leaf_interfaces.pop(0),
            }
            for spine, spine_interfaces in spine_borders.items()
            for leaf, leaf_interfaces in border_leafs.items()
            if spine_interfaces and leaf_interfaces  # Guard against empty lists
        )

        # ========================================
        # Step 4: Create physical connections with cables
        # ========================================
        # All connections use unnumbered P2P interfaces (supports both OSPF and eBGP)
        interface_role = "unnumbered"

        # Process each connection: configure interfaces and create cables
        for connection in connections:
            # Fetch both endpoints from Infrahub
            source_endpoint = await self.client.get(
                kind=InterfacePhysical,
                name__value=connection["source_interface"],
                device__name__value=connection["source"],
                prefetch_relationships=True,
                populate_store=True,
            )
            target_endpoint = await self.client.get(
                kind=InterfacePhysical,
                name__value=connection["destination_interface"],
                device__name__value=connection["target"],
                prefetch_relationships=True,
                populate_store=True,
            )

            # Configure source interface
            source_endpoint.status.value = "active"
            source_endpoint.description.value = f"Peering connection to {' -> '.join(target_endpoint.hfid or [])}"
            source_endpoint.role.value = interface_role

            # Configure target interface
            target_endpoint.status.value = "active"
            target_endpoint.description.value = f"Peering connection to {' -> '.join(source_endpoint.hfid or [])}"
            target_endpoint.role.value = interface_role

            # Check if either endpoint already has a cable attached
            # Note: Accessing .peer can raise ValueError if the related node exists
            # but doesn't have an ID/HFID (e.g., hasn't been saved yet). We catch
            # this and treat it as "no existing cable".
            existing_cable_id = None
            try:
                if source_endpoint.connector.peer:
                    existing_cable_id = source_endpoint.connector.peer.id
            except ValueError:
                pass  # No existing cable or peer doesn't have an ID
            if not existing_cable_id:
                try:
                    if target_endpoint.connector.peer:
                        existing_cable_id = target_endpoint.connector.peer.id
                except ValueError:
                    pass  # No existing cable or peer doesn't have an ID

            # Create or update cable object connecting both endpoints
            # Uses DAC (Direct Attach Copper) passive cables for fabric links
            cable_data: dict[str, Any] = {
                "status": "connected",
                "cable_type": "dac-passive",
                "connected_endpoints": [source_endpoint.id, target_endpoint.id],
            }
            if existing_cable_id:
                cable_data["id"] = existing_cable_id

            cable = await self.client.create(
                kind=DcimCable,
                data=cable_data,
            )

            # Save the cable first so it exists in the database
            await cable.save(allow_upsert=True)

            # Set bidirectional connector relationship
            # This allows queries to traverse: interface → connector → cable → connected_endpoints
            # After save(), cable.id is guaranteed to be set
            if cable.id is not None:
                source_endpoint.connector = cable.id  # type: ignore[assignment]
                target_endpoint.connector = cable.id  # type: ignore[assignment]

            # Queue interface saves for batch
            batch.add(task=source_endpoint.save, allow_upsert=True, node=source_endpoint)
            batch.add(task=target_endpoint.save, allow_upsert=True, node=target_endpoint)

        # Execute batch and log results
        async for node, _ in batch.execute():
            hfid_str = " -> ".join(node.hfid) if isinstance(node.hfid, list) else str(node.hfid)
            if hasattr(node, "description"):
                self.log.info(f"- Created/Updated [{node.get_kind()}] {node.description.value} from {hfid_str}")
            else:
                self.log.info(f"- Created/Updated [{node.get_kind()}] from {hfid_str}")

    # ============================================================================
    # Routing Protocol Configuration - OSPF Underlay
    # ============================================================================

    async def create_ospf_underlay(self) -> None:
        """Create OSPF underlay routing for the fabric.

        Creates OSPFv3 instances on all spine, leaf, and border_leaf devices:
        - Single area (area 0)
        - Associates all unnumbered and loopback interfaces
        - Uses loopback0 as router-id
        - Enables IPv4/IPv6 reachability across the fabric
        """
        topology_name = self.data.get("name")
        self.log.info(f"Creating OSPF underlay for {topology_name}")

        # Create OSPF Area 0 for the entire fabric
        await self._create(
            kind="RoutingOSPFArea",
            data={
                "payload": {
                    "name": f"{topology_name}-UNDERLAY",
                    "description": f"{topology_name} OSPF UNDERLAY service",
                    "area": 0,
                    "status": "active",
                    "owner": self.data.get("provider"),
                },
                "store_key": f"UNDERLAY-{topology_name}",
            },
        )

        # Create OSPF instance on each fabric device
        self.log.info(f"Creating OSPF instances for {topology_name}")
        await self._create_in_batch(
            kind="ServiceOSPF",
            data_list=[
                {
                    "payload": {
                        "name": f"{device.name.value.upper()}-UNDERLAY",
                        "owner": self.data.get("provider"),
                        # "description": f"{device.name.value} OSPF UNDERLAY",
                        "area": self.client.store.get(
                            kind="RoutingOSPFArea",
                            key=f"UNDERLAY-{topology_name}",
                            branch=self.branch,
                        ),
                        "version": "ospfv3",
                        "device": device.id,
                        "status": "active",
                        "router_id": self.client.store.get(
                            key=f"{device.name.value}-loopback0",
                            kind=InterfaceVirtual,
                            branch=self.branch,
                        )
                        .ip_addresses[0]
                        .id,
                        "interfaces": await self.client.filters(
                            kind="DcimInterface",
                            role__values=["unnumbered", "loopback"],
                            device__name__value=device.name.value,
                        ),
                    },
                    "store_key": f"UNDERLAY-{device.name.value}",
                }
                for device in self.devices
                if device.role.value in ["spine", "leaf", "border_leaf"]
            ],
        )

    # ============================================================================
    # Routing Protocol Configuration - BGP
    # ============================================================================

    async def create_bgp_peer_groups(self, scenario: str) -> None:
        """Create BGP peer groups based on deployment scenario.

        Two scenarios are supported:
        1. eBGP scenario: Creates underlay + overlay peer groups
           - SPINE-TO-LEAF-UNDERLAY (for spine perspective)
           - LEAF-TO-SPINE-UNDERLAY (for leaf perspective)
           - RR-SERVERS-OVERLAY (spine route reflectors)
           - RR-CLIENTS-OVERLAY (leaf route reflector clients)

        2. OSPF scenario: Creates only overlay peer groups
           - RR-SERVERS-OVERLAY (spine route reflectors)
           - RR-CLIENTS-OVERLAY (leaf route reflector clients)

        Args:
            scenario: Either "ebgp" or "ospf"
        """
        topology_name = self.data.get("name")
        if not topology_name:
            raise ValueError("Topology name is required")

        self.log.info(f"Creating BGP peer groups for {topology_name} ({scenario} scenario)")

        # ========================================
        # Underlay peer groups (eBGP scenario only)
        # ========================================
        if scenario == "ebgp":
            # Create SPINE-TO-LEAF UNDERLAY peer group
            await self._create(
                kind="RoutingBGPPeerGroup",
                data={
                    "payload": {
                        "name": f"{topology_name}-SPINE-TO-LEAF-UNDERLAY",
                        "description": f"{topology_name} UNDERLAY from spine perspective",
                        "peer_group_type": "SPINE_TO_LEAF",
                        "bfd_enabled": True,
                        "ebgp_multihop": 0,
                        "send_community": True,
                        "send_community_extended": False,
                        "password": "UNDERLAY-secret",
                    },
                    "store_key": f"SPINE-TO-LEAF-UNDERLAY-PG-{topology_name}",
                },
            )

            # Create LEAF-TO-SPINE UNDERLAY peer group
            await self._create(
                kind="RoutingBGPPeerGroup",
                data={
                    "payload": {
                        "name": f"{topology_name}-LEAF-TO-SPINE-UNDERLAY",
                        "description": f"{topology_name} UNDERLAY from leaf perspective",
                        "peer_group_type": "LEAF_TO_SPINE",
                        "bfd_enabled": True,
                        "ebgp_multihop": 0,
                        "send_community": True,
                        "send_community_extended": False,
                        "password": "UNDERLAY-secret",
                    },
                    "store_key": f"LEAF-TO-SPINE-UNDERLAY-PG-{topology_name}",
                },
            )

        # ========================================
        # Overlay peer groups (both scenarios)
        # ========================================
        # These are always created for EVPN overlay
        await self._create(
            kind="RoutingBGPPeerGroup",
            data={
                "payload": {
                    "name": f"{topology_name}-RR-CLIENTS-OVERLAY",
                    "description": f"{topology_name} OVERLAY route reflector clients",
                    "peer_group_type": "EVPN_RR_CLIENT",
                    "bfd_enabled": True,
                    "ebgp_multihop": 3,
                    "send_community": True,
                    "send_community_extended": True,
                    "route_reflector_client": False,
                    "password": "OVERLAY-secret",
                },
                "store_key": f"RR-CLIENTS-OVERLAY-PG-{topology_name}",
            },
        )

        await self._create(
            kind="RoutingBGPPeerGroup",
            data={
                "payload": {
                    "name": f"{topology_name}-RR-SERVERS-OVERLAY",
                    "description": f"{topology_name} OVERLAY route reflector servers",
                    "peer_group_type": "EVPN_RR_SERVER",
                    "bfd_enabled": True,
                    "ebgp_multihop": 3,
                    "send_community": True,
                    "send_community_extended": True,
                    "route_reflector_client": True,
                    "password": "OVERLAY-secret",
                },
                "store_key": f"RR-SERVERS-OVERLAY-PG-{topology_name}",
            },
        )

    async def create_autonomous_systems(self, scenario: str) -> None:
        """Create AS numbers for BGP routing.

        Two scenarios:
        1. eBGP scenario: Creates separate ASNs for each routing domain
           - SPINE-ASN: Shared by all spines
           - LEAF-ASN-<device>: Unique ASN per leaf/border_leaf
           - OVERLAY-ASN: Shared by all devices for iBGP EVPN

        2. OSPF scenario: Creates only overlay ASN
           - OVERLAY-ASN: Shared by all devices for iBGP EVPN

        All ASNs are allocated from the PRIVATE-ASN4 pool.

        Args:
            scenario: Either "ebgp" or "ospf"
        """
        topology_name = self.data.get("name")
        self.log.info(f"Creating autonomous systems for {topology_name} (scenario: {scenario})")

        # Get the PRIVATE-ASN4 pool (4-byte private ASNs: 4200000000-4294967294)
        asn_pool = await self.client.get(
            kind=CoreNumberPool,
            name__value="PRIVATE-ASN4",
            raise_when_missing=True,
            branch=self.branch,
        )

        if scenario == "ebgp":
            # Create spine ASN using pool
            await self._create(
                kind="RoutingAutonomousSystem",
                data={
                    "payload": {
                        "asn": asn_pool,
                        "status": "active",
                        "description": f"{topology_name} SPINES ASN for eBGP UNDERLAY",
                        "location": self.client.store.get(
                            kind="LocationBuilding",
                            key=self.data["name"],
                            branch=self.branch,
                        ),
                    },
                    "store_key": f"SPINE-ASN-{topology_name}",
                },
            )

            # Create leaf ASNs (one per leaf and border_leaf for maximum flexibility)
            leaf_devices = [device for device in self.devices if device.role.value in ["leaf", "border_leaf"]]
            for device in leaf_devices:
                await self._create(
                    kind="RoutingAutonomousSystem",
                    data={
                        "payload": {
                            "asn": asn_pool,
                            "status": "active",
                            "description": f"{topology_name} {device.name.value} ASN for eBGP UNDERLAY",
                            "location": self.client.store.get(
                                kind="LocationBuilding",
                                key=self.data["name"],
                                branch=self.branch,
                            ),
                        },
                        "store_key": f"LEAF-ASN-{device.name.value}",
                    },
                )

            # Create overlay ASN for iBGP EVPN using pool
            await self._create(
                kind="RoutingAutonomousSystem",
                data={
                    "payload": {
                        "asn": asn_pool,
                        "status": "active",
                        "description": f"{topology_name} OVERLAY ASN for iBGP EVPN over eBGP UNDERLAY",
                        "location": self.client.store.get(
                            kind="LocationBuilding",
                            key=self.data["name"],
                            branch=self.branch,
                        ),
                    },
                    "store_key": f"OVERLAY-ASN-{topology_name}",
                },
            )
        else:
            # OSPF scenario: only create overlay ASN for iBGP EVPN
            await self._create(
                kind="RoutingAutonomousSystem",
                data={
                    "payload": {
                        "asn": asn_pool,
                        "status": "active",
                        "description": f"{topology_name} OVERLAY ASN for iBGP EVPN over OSPF UNDERLAY",
                        "location": self.client.store.get(
                            kind="LocationBuilding",
                            key=self.data["name"],
                            branch=self.branch,
                        ),
                    },
                    "store_key": f"OVERLAY-ASN-{topology_name}",
                },
            )

    async def create_ebgp_underlay(self, loopback_name: str) -> None:
        """Create eBGP underlay peering sessions between spine and leaf devices.

        Creates full mesh of eBGP sessions:
        - Each spine creates sessions to all leafs (SPINE-TO-LEAF peer group)
        - Each leaf creates sessions to all spines (LEAF-TO-SPINE peer group)
        - Uses unnumbered interfaces for peering
        - Uses loopback0 IPs as router-id and session endpoints
        - Different ASNs: SPINE-ASN for spines, unique LEAF-ASN per leaf

        This provides IP reachability for the overlay iBGP sessions.

        Args:
            loopback_name: Loopback interface name (typically "loopback0")
        """
        topology_name = self.data.get("name")
        self.log.info(f"Creating eBGP UNDERLAY for {topology_name} (interface-based peering)")

        # Get peer groups created in create_bgp_peer_groups()
        server_pg = self.client.store.get(
            kind="RoutingBGPPeerGroup",
            key=f"SPINE-TO-LEAF-UNDERLAY-PG-{topology_name}",
            branch=self.branch,
        )

        # Get all ASNs for spines and leaves
        spine_asn_obj = self.client.store.get(
            kind="RoutingAutonomousSystem",
            key=f"SPINE-ASN-{topology_name}",
            branch=self.branch,
        )
        spine_asn = spine_asn_obj.id if spine_asn_obj else None

        # Build device lists
        leaf_devices = [device for device in self.devices if device.role.value in ["leaf", "border_leaf"]]
        spine_devices = [device for device in self.devices if device.role.value == "spine"]

        # Create BGP sessions batch - ONLY spine-to-leaf sessions (unidirectional)
        batch = await self.client.create_batch()

        # Create spine-to-leaf sessions only (BGP will handle bidirectional communication)
        for spine_device in spine_devices:
            for leaf_device in leaf_devices:
                leaf_asn_obj = self.client.store.get(
                    kind="RoutingAutonomousSystem",
                    key=f"LEAF-ASN-{leaf_device.name.value}",
                    branch=self.branch,
                )
                leaf_asn = leaf_asn_obj.id if leaf_asn_obj else None
                session_name = f"{spine_device.name.value}-{leaf_device.name.value}".upper()

                spine_bgp_data = {
                    "name": session_name,
                    "owner": self.data.get("provider"),
                    "device": spine_device.id,
                    "local_as": spine_asn,
                    "remote_as": leaf_asn,
                    "router_id": self.client.store.get(
                        key=f"{spine_device.name.value}-loopback0",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "local_ip": self.client.store.get(
                        key=f"{spine_device.name.value}-loopback0",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "remote_ip": self.client.store.get(
                        key=f"{leaf_device.name.value}-loopback0",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    # Associate with unnumbered interfaces like OSPF does
                    "interfaces": await self.client.filters(
                        kind="DcimInterface",
                        role__values=["unnumbered"],
                        device__name__value=spine_device.name.value,
                    ),
                    "session_type": "EXTERNAL",
                    "status": "active",
                }
                if server_pg:
                    spine_bgp_data["peer_group"] = server_pg.id
                else:
                    spine_bgp_data["role"] = "peering"

                spine_bgp = await self.client.create(kind="ServiceBGP", data=spine_bgp_data)
                batch.add(task=spine_bgp.save, allow_upsert=True, node=spine_bgp)

        # Create leaf BGP sessions (one session per leaf-spine pair on leaf)
        for leaf_device in leaf_devices:
            leaf_asn_obj = self.client.store.get(
                kind="RoutingAutonomousSystem",
                key=f"LEAF-ASN-{leaf_device.name.value}",
                branch=self.branch,
            )
            leaf_asn = leaf_asn_obj.id if leaf_asn_obj else None
            for spine_device in spine_devices:
                session_name = f"{leaf_device.name.value}-{spine_device.name.value}".upper()

                leaf_bgp_data = {
                    "name": session_name,
                    "owner": self.data.get("provider"),
                    "device": leaf_device.id,
                    "local_as": leaf_asn,
                    "remote_as": spine_asn,
                    "router_id": self.client.store.get(
                        key=f"{leaf_device.name.value}-loopback0",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "local_ip": self.client.store.get(
                        key=f"{leaf_device.name.value}-loopback0",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "remote_ip": self.client.store.get(
                        key=f"{spine_device.name.value}-loopback0",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    # Associate with unnumbered interfaces like OSPF does
                    "interfaces": await self.client.filters(
                        kind="DcimInterface",
                        role__values=["unnumbered"],
                        device__name__value=leaf_device.name.value,
                    ),
                    "session_type": "EXTERNAL",
                    "status": "active",
                }

                # Get client peer group for leaves
                client_pg = self.client.store.get(
                    kind="RoutingBGPPeerGroup",
                    key=f"LEAF-TO-SPINE-UNDERLAY-PG-{topology_name}",
                    branch=self.branch,
                )
                if client_pg:
                    leaf_bgp_data["peer_group"] = client_pg.id
                else:
                    leaf_bgp_data["role"] = "peering"

                leaf_bgp = await self.client.create(kind="ServiceBGP", data=leaf_bgp_data)
                batch.add(task=leaf_bgp.save, allow_upsert=True, node=leaf_bgp)

        # Execute the batch
        async for node, _ in batch.execute():
            self.log.info(f"- Created [{node.get_kind()}] {node.name.value} (eBGP underlay)")

    async def create_ibgp_overlay(self, loopback_name: str, session_type: str = "overlay") -> None:
        """Create iBGP EVPN overlay sessions for VXLAN control plane.

        Creates full mesh of iBGP sessions using route reflection:
        - Spines act as route reflectors (RR-SERVERS-OVERLAY peer group)
        - Leafs act as route reflector clients (RR-CLIENTS-OVERLAY peer group)
        - Uses loopback1 (VTEP) IPs for session endpoints
        - All devices share same OVERLAY-ASN (iBGP requirement)
        - Enables EVPN address family for VXLAN fabric

        Args:
            loopback_name: Loopback interface for BGP session (typically "loopback1" for VTEP)
            session_type: Type of session - "overlay" for traditional iBGP or "evpn" for EVPN
        """
        topology_name = self.data.get("name")

        # Get the shared overlay ASN (all devices use same ASN for iBGP)
        overlay_asn = self.client.store.get(
            kind="RoutingAutonomousSystem",
            key=f"OVERLAY-ASN-{topology_name}",
            branch=self.branch,
        )
        asn_id = overlay_asn.id if overlay_asn else None

        # Get peer groups
        client_pg = self.client.store.get(
            kind="RoutingBGPPeerGroup",
            key=f"RR-CLIENTS-OVERLAY-PG-{topology_name}",
            branch=self.branch,
        )
        server_pg = self.client.store.get(
            kind="RoutingBGPPeerGroup",
            key=f"RR-SERVERS-OVERLAY-PG-{topology_name}",
            branch=self.branch,
        )

        # Filter devices by role
        leaf_devices = [device for device in self.devices if device.role.value in ["leaf", "border_leaf"]]
        spine_devices = [device for device in self.devices if device.role.value == "spine"]

        # Create BGP sessions batch
        batch = await self.client.create_batch()

        # Create spine-to-leaf sessions (RR server to clients)
        for spine_device in spine_devices:
            for leaf_device in leaf_devices:
                session_name = f"{spine_device.name.value}-{leaf_device.name.value}-EVPN".upper()

                spine_bgp_data = {
                    "name": session_name,
                    "owner": self.data.get("provider"),
                    "device": spine_device.id,
                    "local_as": asn_id,
                    "remote_as": asn_id,
                    "router_id": self.client.store.get(
                        key=f"{spine_device.name.value}-loopback0",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "local_ip": self.client.store.get(
                        key=f"{spine_device.name.value}-{loopback_name}",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "remote_ip": self.client.store.get(
                        key=f"{leaf_device.name.value}-{loopback_name}",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "session_type": "INTERNAL",
                    "status": "active",
                }

                if server_pg:
                    spine_bgp_data["peer_group"] = server_pg.id
                else:
                    spine_bgp_data["role"] = "peering"

                spine_bgp = await self.client.create(kind="ServiceBGP", data=spine_bgp_data)
                batch.add(task=spine_bgp.save, allow_upsert=True, node=spine_bgp)

        # Create leaf-to-spine sessions (RR clients to servers)
        for leaf_device in leaf_devices:
            for spine_device in spine_devices:
                session_name = f"{leaf_device.name.value}-{spine_device.name.value}-EVPN".upper()

                leaf_bgp_data = {
                    "name": session_name,
                    "owner": self.data.get("provider"),
                    "device": leaf_device.id,
                    "local_as": asn_id,
                    "remote_as": asn_id,
                    "router_id": self.client.store.get(
                        key=f"{leaf_device.name.value}-loopback0",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "local_ip": self.client.store.get(
                        key=f"{leaf_device.name.value}-{loopback_name}",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "remote_ip": self.client.store.get(
                        key=f"{spine_device.name.value}-{loopback_name}",
                        kind=InterfaceVirtual,
                        branch=self.branch,
                    )
                    .ip_addresses[0]
                    .id,
                    "session_type": "INTERNAL",
                    "status": "active",
                }

                if client_pg:
                    leaf_bgp_data["peer_group"] = client_pg.id
                else:
                    leaf_bgp_data["role"] = "peering"

                leaf_bgp = await self.client.create(kind="ServiceBGP", data=leaf_bgp_data)
                batch.add(task=leaf_bgp.save, allow_upsert=True, node=leaf_bgp)

        # Execute the batch
        async for node, _ in batch.execute():
            self.log.info(f"- Created [{node.get_kind()}] {node.name.value} (iBGP EVPN overlay)")

    # ============================================================================
    # IP Addressing - Loopback Interfaces
    # ============================================================================

    async def create_dual_loopbacks(self) -> None:
        """Create dual loopback interfaces on all fabric devices.

        Creates two loopbacks with different purposes:
        1. loopback0 (underlay):
           - Used for underlay routing (OSPF/eBGP router-id)
           - IP from loopback_ip_pool
           - Role: "loopback"

        2. loopback1 (VTEP):
           - Used for VXLAN tunnel endpoints
           - Used for iBGP EVPN overlay sessions
           - IP from loopback-vtep_ip_pool
           - Role: "loopback-vtep"

        This separation allows for independent scaling and troubleshooting of
        underlay vs overlay routing.
        """
        self.log.info("Creating dual loopback interfaces: loopback0 (underlay) and loopback1 (VTEP)")

        # Create loopback0 for underlay routing using standard loopback pool and role
        await self.create_loopback("loopback0", "loopback_ip_pool", "loopback", "Underlay")

        # Create loopback1 for VTEP/overlay using VTEP pool and role
        await self.create_loopback("loopback1", "loopback-vtep_ip_pool", "loopback-vtep", "VTEP")


# ==============================================================================
# Main Generator Class
# ==============================================================================


class DCTopologyGenerator(InfrahubGenerator):
    """Main generator entry point for data center topology creation.

    This generator orchestrates the complete creation of a data center fabric
    by calling methods on DCTopologyCreator in the proper sequence.
    """

    async def generate(self, data: dict) -> None:
        """Generate complete data center topology from design specification.

        Creates a full spine-leaf data center fabric including:
        - Physical infrastructure (sites, racks, devices)
        - Network connectivity (cables, fabric peering)
        - IP addressing (management, loopbacks, VTEP)
        - Routing protocols (OSPF or eBGP underlay, iBGP EVPN overlay)

        Args:
            data: Topology design specification from TopologyDataCenter object
        """
        # ========================================
        # Data preparation and scenario detection
        # ========================================
        cleaned_data = clean_data(data)
        if isinstance(cleaned_data, dict):
            data = cleaned_data["TopologyDataCenter"][0]
        else:
            raise ValueError("clean_data() did not return a dictionary")

        # Determine deployment scenario (OSPF or eBGP for underlay)
        scenario = data.get("scenario", data.get("strategy", "ospf")).lower()
        if scenario in ["ebgp-ibgp", "ebgp"]:
            scenario = "ebgp"
        elif scenario in ["ospf-ibgp", "ospf"]:
            scenario = "ospf"
        else:
            scenario = "ospf"  # Default fallback

        self.logger.info(f"Using {scenario} scenario for topology generation (unnumbered P2P only)")

        # ========================================
        # Phase 1: Physical Infrastructure
        # ========================================
        network_creator = DCTopologyCreator(client=self.client, log=self.logger, branch=self.branch, data=data)
        # Load existing devices, platforms, templates, etc.
        await network_creator.load_data()

        # Create building/site object
        await network_creator.create_site()

        # Create location hierarchy within the site (Pod-1, Row-1, etc.)
        await network_creator.create_location_hierarchy()

        # Create rack objects (number based on leaf count)
        await network_creator.create_racks()

        # ========================================
        # Phase 2: IP Address Planning
        # ========================================
        # Load technical_subnet (used for loopbacks) if it exists
        if data.get("technical_subnet"):
            technical_subnet_obj = await self.client.get(
                kind="IpamPrefix", id=data["technical_subnet"]["id"], branch=self.branch
            )
        else:
            technical_subnet_obj = None

        # Build management subnet list
        subnets = []
        if data.get("management_subnet"):
            subnets.append(
                {
                    "type": "Management",
                    "prefix_id": data["management_subnet"]["id"],
                }
            )

        # Create IP address pools
        if technical_subnet_obj:
            # Create management pool
            await network_creator.create_address_pools(subnets)
            # Split technical subnet into separate pools for loopback0 and loopback1
            await network_creator.create_split_loopback_pools(technical_subnet_obj)
        else:
            # Fallback to regular address pool creation if no technical subnet
            await network_creator.create_address_pools(subnets)

        # Create VLAN pool for Layer 2 services
        await network_creator.create_L2_pool()

        # ========================================
        # Phase 3: Device Creation and Placement
        # ========================================
        # Create device objects (spines, leafs, border_leafs)
        await network_creator.create_devices()

        # Assign devices to racks with proper positioning (U-position, face)
        await network_creator.assign_devices_to_racks()

        # ========================================
        # Phase 4: Physical Connectivity
        # ========================================
        # Create out-of-band management connections (Cat6 cables)
        await network_creator.create_oob_connections("management")
        # Create console connections (Cat6 cables)
        await network_creator.create_oob_connections("console")

        # Create spine-leaf fabric peering (DAC cables with unnumbered interfaces)
        await network_creator.create_fabric_peering()

        # ========================================
        # Phase 5: IP Addressing
        # ========================================
        # Create dual loopbacks on all fabric devices
        # - loopback0: underlay routing
        # - loopback1: VTEP and overlay routing
        await network_creator.create_dual_loopbacks()

        # ========================================
        # Phase 6: Routing Protocol Configuration
        # ========================================
        if scenario == "ospf":
            # ========================================
            # OSPF + iBGP Scenario
            # ========================================
            # Underlay: OSPFv3 provides IP reachability between loopbacks
            await network_creator.create_ospf_underlay()

            # Create overlay ASN (shared by all devices for iBGP)
            await network_creator.create_autonomous_systems("ospf")

            # Create BGP peer groups (route reflector model)
            await network_creator.create_bgp_peer_groups("ospf")

            # Overlay: iBGP EVPN sessions for VXLAN control plane
            await network_creator.create_ibgp_overlay("loopback1", "overlay")
        else:
            # ========================================
            # eBGP + iBGP Scenario
            # ========================================
            # Create ASNs: SPINE-ASN, LEAF-ASN-<device>, OVERLAY-ASN
            await network_creator.create_autonomous_systems("ebgp")

            # Create BGP peer groups (underlay + overlay)
            await network_creator.create_bgp_peer_groups("ebgp")

            # Underlay: eBGP provides IP reachability between loopbacks
            await network_creator.create_ebgp_underlay("loopback0")

            # Overlay: iBGP EVPN sessions for VXLAN control plane
            await network_creator.create_ibgp_overlay("loopback1", "evpn")
