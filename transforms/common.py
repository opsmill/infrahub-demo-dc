"""
Common utility functions for Infrahub topology generators.

This module provides data cleaning utilities to normalize and extract values
from nested data structures returned by Infrahub APIs.
"""

import html
import ipaddress
import re
from collections import defaultdict
from typing import Any

from netutils.interface import sort_interface_list  # type: ignore[import-not-found]

# Range expansion pattern from infrahub_sdk
RANGE_PATTERN = re.compile(r"(\[[\w,-]*[-,][\w,-]*\])")


def clean_data(data: Any) -> Any:
    """
    Recursively normalize Infrahub API data by extracting values from nested dictionaries and lists.
    """
    # Handle dictionaries
    if isinstance(data, dict):
        dict_result = {}
        for key, value in data.items():
            if isinstance(value, dict):
                # Handle special cases with single keys
                keys = set(value.keys())
                if keys == {"value"}:
                    dict_result[key] = value["value"]  # This handles None values too
                elif keys == {"edges"} and not value["edges"]:
                    dict_result[key] = []
                # Handle nested structures
                elif "node" in value:
                    dict_result[key] = clean_data(value["node"])
                elif "edges" in value:
                    dict_result[key] = clean_data(value["edges"])
                # Process any other dictionaries
                else:
                    dict_result[key] = clean_data(value)
            elif "__" in key:
                dict_result[key.replace("__", "")] = value
            else:
                dict_result[key] = clean_data(value)
        return dict_result

    # Handle lists
    if isinstance(data, list):
        return [clean_data(item.get("node", item)) for item in data]

    # Return primitives unchanged
    return data


def get_data(data: Any) -> Any:
    """
    Extracts the relevant data from the input.
    Returns the first value from the cleaned data dictionary.
    """
    cleaned_data = clean_data(data)
    if isinstance(cleaned_data, dict) and cleaned_data:
        first_key = next(iter(cleaned_data))
        first_value = cleaned_data[first_key]
        if isinstance(first_value, list):
            if not first_value:
                raise ValueError(
                    f"The query returned no {first_key}, so there is nothing to render. This "
                    f"usually means the transform targets a device of a kind the query does not match."
                )
            return first_value[0]
        return first_value
    else:
        raise ValueError("clean_data() did not return a non-empty dictionary")


def get_bgp_profile(device_services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Groups BGP sessions by peer group and returns a list of peer group dicts in the desired structure.
    """
    unique_keys = {"name", "remote_ip", "remote_as"}
    peer_groups = defaultdict(list)
    for service in device_services:
        if service.get("typename") == "ServiceBGP":
            peer_group_name = (service.get("peer_group") or {}).get("name", "unknown")
            peer_groups[peer_group_name].append(service)

    grouped = []
    for sessions in peer_groups.values():
        if not sessions:
            continue
        base_settings = {k: v for k, v in sessions[0].items() if k not in unique_keys and k != "peer_group"}
        for session in sessions[1:]:
            keys_to_remove = []
            for k in base_settings:
                if session.get(k) != base_settings[k]:
                    keys_to_remove.append(k)
            for k in keys_to_remove:
                base_settings.pop(k)
        session_entries = []
        for session in sessions:
            entry = {k: v for k, v in session.items() if k in unique_keys}
            session_entries.append(entry)
        if sessions[0].get("peer_group"):
            base_settings["profile"] = sessions[0]["peer_group"].get("name")
        base_settings["sessions"] = session_entries
        grouped.append(base_settings)  # Store as list element

    return grouped


def get_ospf(device_services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Extract OSPF configuration information.
    """
    ospf_configs: list[dict[str, Any]] = []

    for service in device_services:
        if service.get("typename") == "ServiceOSPF":
            # Extract router_id address and strip CIDR notation if present
            router_id = (service.get("router_id") or {}).get("address", "")
            if router_id and "/" in router_id:
                router_id = router_id.split("/")[0]

            ospf_config = {
                "process_id": service.get("process_id", 1),
                "router_id": router_id,
                "area": (service.get("area") or {}).get("area"),
                "reference_bandwidth": service.get("reference_bandwidth", 10000),
            }
            ospf_configs.append(ospf_config)

    return ospf_configs


def get_anycast_gateway(prefix: str | None) -> str | None:
    """
    Return the first usable address of a prefix, as the segment's gateway.

    An L3 segment's gateway is configured identically on every leaf of the pair
    (an anycast gateway), and the convention is the first usable address of the
    segment's prefix. Deriving it here rather than in the template keeps the
    address arithmetic out of Jinja, and keeps the rendered config tied to the
    prefix actually recorded in Infrahub.

    Args:
        prefix: The segment's prefix in CIDR form, or None when it has none.

    Returns:
        The gateway as "address/prefixlen", or None when the prefix is missing
        or unparseable. A /31 or /32 yields its own first address, since
        `hosts()` treats those as usable rather than as network addresses.
    """
    if not prefix:
        return None
    try:
        network = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return None
    # `hosts()` returns a generator for a normal prefix but a plain list for a
    # /31 or /32, so it has to be wrapped in iter() before taking the first.
    first = next(iter(network.hosts()), None)
    return f"{first}/{network.prefixlen}" if first is not None else None


def get_vlans(data: list) -> list[dict[str, Any]]:
    """
    Extracts VLAN information from the input data.

    Returns a list of dicts with vlan_id, name, vni, rd, segment_type,
    external_routing, prefix, and gateway. VNI is computed as VLAN ID + 10000,
    RD is the VLAN ID as a string, and the gateway is the first usable address
    of the segment's prefix. Unique per vlan_id.
    """
    vlans: dict[int, dict[str, Any]] = {}
    for interface in data:
        for segment in interface.get("interface_services", []):
            if segment.get("typename") == "ServiceNetworkSegment":
                vlan_id = segment.get("vlan_id")
                if vlan_id is not None and vlan_id not in vlans:
                    prefix = (segment.get("prefix") or {}).get("prefix")
                    vlans[vlan_id] = {
                        "vlan_id": vlan_id,
                        "name": segment.get("name") or segment.get("customer_name") or f"VLAN_{vlan_id}",
                        "vni": vlan_id + 10000,
                        "rd": str(vlan_id),
                        "segment_type": segment.get("segment_type", "l2_only"),
                        "external_routing": segment.get("external_routing", False),
                        "prefix": prefix,
                        "gateway": get_anycast_gateway(prefix),
                    }
    return list(vlans.values())


def get_loopbacks(data: list) -> dict[str, str]:
    """
    Extracts loopback interfaces and their primary IP addresses.
    Returns a dictionary mapping loopback interface names to IP addresses (without mask).
    Example: {"loopback0": "10.0.0.1", "loopback1": "10.0.0.2"}
    """
    loopbacks = {}
    for iface in data:
        name = iface.get("name", "")
        if not name:
            continue

        name_lower = name.lower()
        role = iface.get("role", "").lower()

        # Check if this is a loopback interface by role or name
        is_loopback = (
            role == "loopback" or "loopback" in name_lower or (len(name_lower) >= 2 and name_lower[:2] == "lo")
        )

        if is_loopback:
            ip_addresses = iface.get("ip_addresses", [])
            if ip_addresses:
                # Get the first IP address and strip the mask if present
                address = ip_addresses[0].get("address", "")
                # Remove CIDR notation for router-id compatibility
                if "/" in address:
                    address = address.split("/")[0]
                loopbacks[name_lower] = address

    return loopbacks


def expand_interface_range(interface_name: str) -> list[str]:
    """
    Expand interface name with bracket notation into individual interfaces.

    Examples:
        "Ethernet[1-3]" -> ["Ethernet1", "Ethernet2", "Ethernet3"]
        "Ethernet5" -> ["Ethernet5"]
    """
    # Check if interface name has bracket notation
    if not RANGE_PATTERN.search(interface_name):
        return [interface_name]

    # Simple range expansion for interfaces like Ethernet[1-48]
    # Extract the pattern
    match = RANGE_PATTERN.search(interface_name)
    if not match:
        return [interface_name]

    bracket_content = match.group(1)[1:-1]  # Remove [ and ]
    prefix = interface_name[: match.start()]
    suffix = interface_name[match.end() :]

    # Handle numeric ranges like [1-48] or [1,3,5]
    expanded = []
    for part in bracket_content.split(","):
        if "-" in part:
            start, end = part.split("-")
            if start.isdigit() and end.isdigit():
                for i in range(int(start), int(end) + 1):
                    expanded.append(f"{prefix}{i}{suffix}")
            else:
                # Can't parse, return as-is
                return [interface_name]
        elif part.isdigit():
            expanded.append(f"{prefix}{part}{suffix}")
        else:
            # Can't parse, return as-is
            return [interface_name]

    return expanded if expanded else [interface_name]


def get_interfaces(data: list) -> list[dict[str, Any]]:
    """
    Returns a list of interface dictionaries sorted by interface name.
    Only includes 'ospf' key if OSPF area is present.
    Includes IP addresses, description, status, role, and other interface data.

    Expands interface ranges like Ethernet[1-48] into individual interfaces.
    """
    # First, expand any interface names with range notation
    expanded_interfaces = []
    for iface in data:
        name = iface.get("name")
        if not name:
            continue

        # Check if this interface needs expansion
        if RANGE_PATTERN.search(name):
            # Expand the range and create a copy for each expanded name
            for expanded_name in expand_interface_range(name):
                iface_copy = iface.copy()
                iface_copy["name"] = expanded_name
                expanded_interfaces.append(iface_copy)
        else:
            expanded_interfaces.append(iface)

    interface_names = [iface.get("name") for iface in expanded_interfaces if iface.get("name")]

    # Try to use netutils intelligent sorting, fall back to alphabetical if it fails
    try:
        sorted_names = sort_interface_list(interface_names)
    except (ValueError, TypeError):
        # If netutils can't parse interface names (e.g., special characters),
        # fall back to simple alphabetical sorting
        sorted_names = sorted(interface_names)
    name_to_interface = {}
    for iface in expanded_interfaces:
        name = iface.get("name")
        if not name:
            continue

        vlans = [
            s.get("vlan_id")
            for s in iface.get("interface_services", [])
            if s.get("typename") == "ServiceNetworkSegment"
        ]
        ospf_areas = [
            s.get("area", {}).get("area")
            for s in iface.get("interface_services", [])
            if s.get("typename") == "ServiceOSPF"
        ]

        # Decode HTML entities in description (e.g., &gt; -> >)
        description = iface.get("description")
        if description:
            description = html.unescape(description)

        iface_dict = {
            "name": name,
            "vlans": vlans,
            "description": description,
            "status": iface.get("status"),
            "role": iface.get("role"),
            "mtu": iface.get("mtu"),
            "ip_addresses": iface.get("ip_addresses", []),
        }

        if ospf_areas:
            iface_dict["ospf"] = {"area": ospf_areas[0]}

        name_to_interface[name] = iface_dict

    return [name_to_interface[name] for name in sorted_names if name in name_to_interface]


def get_interface_roles(data: list) -> dict[str, list[dict[str, Any]]]:
    """
    Organizes interfaces by their role for template consumption.
    Returns a dictionary with keys like 'loopback', 'uplink', 'downlink', 'all_downlink', 'all_physical'.
    Each value is a list of interface dictionaries with ip_address (first IP with mask).
    """
    # First get all interfaces using existing function
    all_interfaces = get_interfaces(data)

    # Organize by role
    roles: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for iface in all_interfaces:
        role = (iface.get("role") or "").lower()
        name_lower = iface.get("name", "").lower()

        # Create interface dict with ip_address (first IP with mask)
        iface_copy = iface.copy()
        if iface.get("ip_addresses"):
            iface_copy["ip_address"] = iface["ip_addresses"][0].get("address", "")
        else:
            iface_copy["ip_address"] = ""

        # Categorize by role
        if role == "loopback" or "loopback" in name_lower or name_lower.startswith("lo"):
            roles["loopback"].append(iface_copy)
        elif role in ("uplink", "spine"):
            roles["uplink"].append(iface_copy)
        elif role in ("downlink", "leaf"):
            roles["downlink"].append(iface_copy)
        elif role in ("customer", "access"):
            roles["customer"].append(iface_copy)
        else:
            # Physical interfaces (non-loopback)
            if not (role == "loopback" or "loopback" in name_lower):
                roles["other"].append(iface_copy)

    # Create aggregate lists
    roles["all_downlink"] = roles["downlink"] + roles["customer"]
    roles["all_physical"] = roles["uplink"] + roles["downlink"] + roles["customer"] + roles["other"]

    # Re-sort aggregate lists to maintain interface name order
    if roles["all_downlink"]:
        interface_names = [iface["name"] for iface in roles["all_downlink"]]
        try:
            sorted_names = sort_interface_list(interface_names)
        except (ValueError, TypeError):
            # If netutils can't parse, fall back to alphabetical sorting
            sorted_names = sorted(interface_names)
        name_to_interface = {iface["name"]: iface for iface in roles["all_downlink"]}
        roles["all_downlink"] = [name_to_interface[name] for name in sorted_names if name in name_to_interface]

    if roles["all_physical"]:
        interface_names = [iface["name"] for iface in roles["all_physical"]]
        try:
            sorted_names = sort_interface_list(interface_names)
        except (ValueError, TypeError):
            # If netutils can't parse, fall back to alphabetical sorting
            sorted_names = sorted(interface_names)
        name_to_interface = {iface["name"]: iface for iface in roles["all_physical"]}
        roles["all_physical"] = [name_to_interface[name] for name in sorted_names if name in name_to_interface]

    return dict(roles)
