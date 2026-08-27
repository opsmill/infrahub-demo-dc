from typing import Any

from infrahub_sdk.transforms import InfrahubTransform
from jinja2 import Environment, FileSystemLoader


class JuniperFirewall(InfrahubTransform):
    query = "juniper_firewall_config"

    async def transform(self, data: Any) -> Any:
        """Transform SecurityFirewall data into Juniper SRX configuration."""
        # Extract the firewall device
        firewall_edges = data["SecurityFirewall"]["edges"]
        if not firewall_edges:
            return "# No firewall device found"

        firewall = firewall_edges[0]["node"]

        # Build template data structure with explicit typing for zone_pairs
        zone_pairs: dict[tuple[str, str], list[dict[str, Any]]] = {}
        template_data: dict[str, Any] = {
            "device_name": firewall["name"]["value"],
            "interfaces": [],
            "zone_pairs": zone_pairs,
            "addresses": {},  # Unique addresses for global address book
            "applications": {},  # Unique applications
        }

        # Find management interface
        management_interface = None
        for intf_edge in firewall.get("interfaces", {}).get("edges", []):
            intf = intf_edge["node"]
            intf_name = intf["name"]["value"]
            intf_role = intf.get("role", {}).get("value")

            # Get IP address if available
            ip_addr = None
            ip_edges = intf.get("ip_addresses", {}).get("edges", [])
            if ip_edges:
                ip_addr = ip_edges[0]["node"]["address"]["value"]

            if intf_role == "management":
                management_interface = intf_name

            template_data["interfaces"].append(
                {
                    "name": intf_name,
                    "role": intf_role,
                    "ip_address": ip_addr,
                }
            )

        template_data["management_interface"] = management_interface or "fxp0"

        # Process policies and their rules
        for policy_edge in firewall.get("policies", {}).get("edges", []):
            policy = policy_edge["node"]

            for rule_edge in policy.get("rules", {}).get("edges", []):
                rule = rule_edge["node"]

                # Build rule data with typed lists for appending
                source_addresses: list[str] = []
                destination_addresses: list[str] = []
                applications: list[str] = []
                rule_data: dict[str, Any] = {
                    "index": rule.get("index", {}).get("value", 0),
                    "name": rule.get("name", {}).get("value", "unnamed-rule"),
                    "action": rule.get("action", {}).get("value", "deny"),
                    "log": rule.get("log", {}).get("value", False),
                    "source_zone": None,
                    "destination_zone": None,
                    "source_addresses": source_addresses,
                    "destination_addresses": destination_addresses,
                    "applications": applications,
                }

                # Extract zones
                if rule.get("source_zone", {}).get("node"):
                    rule_data["source_zone"] = rule["source_zone"]["node"]["name"]["value"]

                if rule.get("destination_zone", {}).get("node"):
                    rule_data["destination_zone"] = rule["destination_zone"]["node"]["name"]["value"]

                # Extract source addresses from address groups
                for addr_group_edge in rule.get("source_addresses", {}).get("edges", []):
                    addr_group = addr_group_edge["node"]

                    # Process IP addresses in the group
                    for ip_edge in addr_group.get("ip_addresses", {}).get("edges", []):
                        ip = ip_edge["node"]
                        addr_name = ip["name"]["value"]
                        addr_value = ip["ipam_ip_address"]["node"]["address"]["value"]

                        source_addresses.append(addr_name)

                        if addr_name not in template_data["addresses"]:
                            template_data["addresses"][addr_name] = {
                                "type": "SecurityIPAddress",
                                "value": addr_value,
                            }

                    # Process prefixes in the group
                    for prefix_edge in addr_group.get("prefixes", {}).get("edges", []):
                        prefix = prefix_edge["node"]
                        addr_name = prefix["name"]["value"]
                        addr_value = prefix["ipam_prefix"]["node"]["prefix"]["value"]

                        source_addresses.append(addr_name)

                        if addr_name not in template_data["addresses"]:
                            template_data["addresses"][addr_name] = {
                                "type": "SecurityPrefix",
                                "value": addr_value,
                            }

                    # Process FQDNs in the group
                    for fqdn_edge in addr_group.get("fqdns", {}).get("edges", []):
                        fqdn = fqdn_edge["node"]
                        addr_name = fqdn["name"]["value"]
                        addr_value = fqdn["fqdn"]["value"]

                        source_addresses.append(addr_name)

                        if addr_name not in template_data["addresses"]:
                            template_data["addresses"][addr_name] = {
                                "type": "SecurityFQDN",
                                "value": addr_value,
                            }

                # Extract destination addresses from address groups
                for addr_group_edge in rule.get("destination_addresses", {}).get("edges", []):
                    addr_group = addr_group_edge["node"]

                    # Process IP addresses in the group
                    for ip_edge in addr_group.get("ip_addresses", {}).get("edges", []):
                        ip = ip_edge["node"]
                        addr_name = ip["name"]["value"]
                        addr_value = ip["ipam_ip_address"]["node"]["address"]["value"]

                        destination_addresses.append(addr_name)

                        if addr_name not in template_data["addresses"]:
                            template_data["addresses"][addr_name] = {
                                "type": "SecurityIPAddress",
                                "value": addr_value,
                            }

                    # Process prefixes in the group
                    for prefix_edge in addr_group.get("prefixes", {}).get("edges", []):
                        prefix = prefix_edge["node"]
                        addr_name = prefix["name"]["value"]
                        addr_value = prefix["ipam_prefix"]["node"]["prefix"]["value"]

                        destination_addresses.append(addr_name)

                        if addr_name not in template_data["addresses"]:
                            template_data["addresses"][addr_name] = {
                                "type": "SecurityPrefix",
                                "value": addr_value,
                            }

                    # Process FQDNs in the group
                    for fqdn_edge in addr_group.get("fqdns", {}).get("edges", []):
                        fqdn = fqdn_edge["node"]
                        addr_name = fqdn["name"]["value"]
                        addr_value = fqdn["fqdn"]["value"]

                        destination_addresses.append(addr_name)

                        if addr_name not in template_data["addresses"]:
                            template_data["addresses"][addr_name] = {
                                "type": "SecurityFQDN",
                                "value": addr_value,
                            }

                # Extract services from service groups
                for svc_group_edge in rule.get("services", {}).get("edges", []):
                    svc_group = svc_group_edge["node"]

                    # Process services in the group
                    for svc_edge in svc_group.get("services", {}).get("edges", []):
                        svc = svc_edge["node"]
                        svc_name = svc["name"]["value"]

                        applications.append(svc_name)

                        # Add to global applications
                        if svc_name not in template_data["applications"]:
                            template_data["applications"][svc_name] = {
                                "name": svc_name,
                                "type": "SecurityService",
                                "protocol": svc["protocol"]["value"],
                                "port": svc["port"]["value"],
                            }

                # Organize rules by zone pairs
                src_zone = rule_data["source_zone"]
                dst_zone = rule_data["destination_zone"]
                if src_zone and dst_zone:
                    zone_pair_key: tuple[str, str] = (str(src_zone), str(dst_zone))
                    if zone_pair_key not in zone_pairs:
                        zone_pairs[zone_pair_key] = []
                    zone_pairs[zone_pair_key].append(rule_data)

        # Sort rules within each zone pair by index
        for zp_key in zone_pairs:
            zone_pairs[zp_key].sort(key=lambda x: x["index"])

        # Set up Jinja2 environment
        template_path = f"{self.root_directory}/templates"
        env = Environment(
            loader=FileSystemLoader(template_path),
            # Device configuration is not HTML. `select_autoescape(["j2"])` turned it on for every
            # template here, so a description containing `>` rendered as `&gt;` into JunOS.
            autoescape=False,
        )

        # Render the template
        template = env.get_template("configs/juniper_firewall.j2")
        return template.render(data=template_data)
