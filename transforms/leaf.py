from typing import Any

from infrahub_sdk.transforms import InfrahubTransform
from jinja2 import Environment, FileSystemLoader

from .common import (
    get_bgp_profile,
    get_data,
    get_interface_roles,
    get_interfaces,
    get_loopbacks,
    get_ospf,
    get_vlans,
)


class Leaf(InfrahubTransform):
    query = "leaf_config"

    async def transform(self, data: Any) -> Any:
        data = get_data(data)

        # Get platform information
        platform = data["device_type"]["platform"]["netmiko_device_type"]

        # Set up Jinja2 environment to load templates from the role subfolder
        template_path = f"{self.root_directory}/templates/configs/leafs"
        env = Environment(
            loader=FileSystemLoader(template_path),
            autoescape=False,  # Disable autoescape for device configs (not HTML)
        )
        # Select the template for leaf devices based on platform
        template_name = f"{platform}.j2"

        # Render the template with enhanced data
        template = env.get_template(template_name)

        bgp_profiles = get_bgp_profile(data.get("device_services"))
        ospf_configs = get_ospf(data.get("device_services"))

        # Create both flattened BGP dict (for Arista/Cisco templates)
        # and pass original bgp_profiles list (for Juniper template)
        bgp = {}
        if bgp_profiles:
            # Get common BGP settings from first profile
            first_profile = bgp_profiles[0]
            # `or {}` rather than a get() default throughout: clean_data leaves an
            # unset relationship as an explicit None, so the key exists and the
            # default never applies. `.get("local_as", {}).get(...)` then raises
            # AttributeError on None and fails the whole artifact, which is how a
            # session missing its local ASN took out every leaf config.
            router_id = (first_profile.get("router_id") or {}).get("address", "")
            if router_id and "/" in router_id:
                router_id = router_id.split("/")[0]
            bgp = {
                "local_as": (first_profile.get("local_as") or {}).get("asn", ""),
                "router_id": router_id,
                "neighbors": [],
            }
            # Collect all neighbors from all profiles
            for profile in bgp_profiles:
                for session in profile.get("sessions", []):
                    neighbor = {
                        "name": session.get("name", ""),
                        "remote_ip": (session.get("remote_ip") or {}).get("address", ""),
                        "remote_as": (session.get("remote_as") or {}).get("asn", ""),
                    }
                    bgp["neighbors"].append(neighbor)

        # Extract first OSPF config for templates that expect a single dict (Arista)
        ospf_single = ospf_configs[0] if ospf_configs else {}

        config = {
            "hostname": data.get("name"),
            "name": data.get("name"),  # Alias for Juniper template compatibility
            "bgp": bgp,  # Flattened dict for Arista/Cisco/SONiC templates
            "bgp_profiles": bgp_profiles,  # Original list for Cisco/Juniper templates
            "ospf": ospf_single,  # Single dict for Arista templates
            "ospf_configs": ospf_configs,  # List for Cisco/Juniper templates (iteration)
            "interfaces": get_interface_roles(data.get("interfaces")),  # Dict by role for Arista
            "interface_list": get_interfaces(data.get("interfaces")),  # Flat list for Cisco/Juniper
            "vlans": get_vlans(data.get("interfaces")),
            "loopbacks": get_loopbacks(data.get("interfaces")),
        }

        return template.render(**config)
