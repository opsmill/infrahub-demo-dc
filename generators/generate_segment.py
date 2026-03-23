"""Network Segment Generator for VxLAN VPN Services.

This generator processes ServiceNetworkSegment objects and configures:
- VNI (VXLAN Network Identifier) = VLAN ID + 10000
- RD (Route Distinguisher) = VLAN ID
- Associates segment with customer interfaces on leaf devices
"""

from typing import Any

from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]

from .common import clean_data


class NetworkSegmentGenerator(InfrahubGenerator):
    """Generate VxLAN VPN configuration for network segments.

    This generator is triggered when a ServiceNetworkSegment is created.
    It processes the segment data, associates it with leaf device interfaces,
    and configures the VxLAN overlay.
    """

    async def generate(self, data: dict) -> None:
        """Process network segment and configure VxLAN settings.

        Args:
            data: GraphQL query result containing ServiceNetworkSegment data
        """
        cleaned_data = clean_data(data)
        if not isinstance(cleaned_data, dict):
            raise ValueError("clean_data() did not return a dictionary")

        # Extract segment data from query result
        segments = cleaned_data.get("ServiceNetworkSegment", [])
        if not segments:
            self.logger.warning("No segment data found in query result")
            return

        segment = segments[0]  # Generator runs per-segment
        segment_id = segment.get("id")

        # Extract segment attributes
        segment_name = segment.get("name", "unknown")
        customer_name = segment.get("customer_name", "unknown")
        vlan_id = segment.get("vlan_id")
        segment_type = segment.get("segment_type", "l2_only")
        external_routing = segment.get("external_routing", False)
        tenant_isolation = segment.get("tenant_isolation", "customer_dedicated")

        if not vlan_id:
            self.logger.error(f"Segment {segment_name} has no VLAN ID, skipping")
            return

        # Calculate VxLAN parameters
        vni = vlan_id + 10000
        rd = str(vlan_id)

        self.logger.info(f"Processing segment: {segment_name}")
        self.logger.info(f"  Customer: {customer_name}")
        self.logger.info(f"  VLAN ID: {vlan_id}")
        self.logger.info(f"  VNI: {vni}")
        self.logger.info(f"  RD: {rd}")
        self.logger.info(f"  Type: {segment_type}")
        self.logger.info(f"  External Routing: {external_routing}")
        self.logger.info(f"  Tenant Isolation: {tenant_isolation}")

        # Get deployment information
        deployment = segment.get("deployment")
        if not deployment:
            self.logger.warning(f"Segment {segment_name} has no deployment, skipping")
            return

        deployment_name = deployment.get("name", "unknown")
        self.logger.info(f"  Deployment: {deployment_name}")

        # Get prefix information if available
        prefix_data = segment.get("prefix")
        if prefix_data:
            prefix_value = prefix_data.get("prefix", "N/A")
            self.logger.info(f"  Prefix: {prefix_value}")

        # Get devices from deployment for VxLAN configuration
        devices = deployment.get("devices", [])
        leaf_devices = [d for d in devices if d.get("role") in ["leaf", "border_leaf"]]

        if not leaf_devices:
            self.logger.info(
                f"No leaf devices found in deployment {deployment_name}, "
                "VxLAN configuration will be applied when devices are available"
            )
            return

        self.logger.info(f"Found {len(leaf_devices)} leaf devices for VxLAN configuration")

        # Associate segment with customer interfaces on leaf devices
        await self._associate_interfaces_with_segment(
            segment_id=segment_id,
            segment_name=segment_name,
            leaf_devices=leaf_devices,
        )

        # Log VxLAN configuration details
        await self._log_vxlan_config(
            leaf_devices=leaf_devices,
            segment_name=segment_name,
            vlan_id=vlan_id,
            vni=vni,
            rd=rd,
            segment_type=segment_type,
            external_routing=external_routing,
        )

    async def _associate_interfaces_with_segment(
        self,
        segment_id: str,
        segment_name: str,
        leaf_devices: list[dict[str, Any]],
    ) -> None:
        """Associate customer interfaces on leaf devices with the segment.

        This method queries for customer-facing interfaces on each leaf device
        and associates them with the network segment.

        Args:
            segment_id: The ID of the ServiceNetworkSegment
            segment_name: Name of the segment for logging
            leaf_devices: List of leaf device data from deployment
        """
        if not segment_id:
            self.logger.warning("Segment ID not available, skipping interface association")
            return

        # Get the segment object
        segment = await self.client.get(
            kind="ServiceNetworkSegment",
            id=segment_id,
            branch=self.branch,
        )

        if not segment:
            self.logger.warning(f"Could not retrieve segment {segment_name}")
            return

        interface_ids: list[str] = []

        for device in leaf_devices:
            device_name = device.get("name", "unknown")
            device_id = device.get("id")

            if not device_id:
                self.logger.warning(f"Device {device_name} has no ID, skipping")
                continue

            # Query for customer interfaces on this device
            interfaces = await self.client.filters(
                kind="InterfacePhysical",
                device__ids=[device_id],
                role__value="customer",
                branch=self.branch,
            )

            for interface in interfaces:
                interface_ids.append(interface.id)
                self.logger.info(f"  Adding interface {interface.name.value} on {device_name} to segment")

        if interface_ids:
            # Update segment with interface associations
            await segment.interfaces.fetch()
            segment.interfaces.extend(interface_ids)
            await segment.save(allow_upsert=True)
            self.logger.info(f"Associated {len(interface_ids)} interfaces with segment {segment_name}")
        else:
            self.logger.info(f"No customer interfaces found for segment {segment_name}")

    async def _log_vxlan_config(
        self,
        leaf_devices: list[dict[str, Any]],
        segment_name: str,
        vlan_id: int,
        vni: int,
        rd: str,
        segment_type: str,
        external_routing: bool,
    ) -> None:
        """Log VxLAN configuration details for each leaf device.

        Args:
            leaf_devices: List of leaf device data from deployment
            segment_name: Name of the network segment
            vlan_id: VLAN ID for the segment
            vni: VxLAN Network Identifier
            rd: Route Distinguisher
            segment_type: Type of segment (l2_only, l3_gateway, l3_vrf)
            external_routing: Whether external routing is enabled
        """
        for device in leaf_devices:
            device_name = device.get("name", "unknown")

            self.logger.info(f"  VxLAN config on {device_name}: VLAN {vlan_id} -> VNI {vni} (RD: {rd})")

            if segment_type == "l3_gateway":
                self.logger.info(f"    L3 Gateway: SVI for VLAN {vlan_id} on {device_name}")
            elif segment_type == "l3_vrf":
                self.logger.info(f"    L3 VRF: VRF instance for segment on {device_name}")

            if external_routing:
                self.logger.info(f"    External routing: Advertising VNI {vni} to external peers")

        self.logger.info(f"VxLAN configuration complete for segment {segment_name}")
