"""Render a ``SecurityFirewall``'s modelled policy as PAN-OS ``set`` configuration.

The output is Panorama-shaped rather than firewall-shaped: address, service, profile and schedule
objects go to ``shared``, and the rules of each ``SecurityPolicy`` become the pre-rulebase of a
device group named after that policy. That mirrors how the same policy reaches many firewalls in
Panorama, and it is why one ``SecurityPolicy`` in Infrahub can drive both this and the JunOS
transform without either owning the rules.

``set`` format is deliberate. It is what a Palo Alto engineer reads, diffs and pastes; the XML
Panorama actually stores is machine-facing and would make the artifact useless as a review surface
in a proposed change.
"""

from typing import Any

from infrahub_sdk.transforms import InfrahubTransform
from jinja2 import Environment, FileSystemLoader

# PAN-OS spells the permit action "allow"; everything else in the model is already its own name.
ACTION_MAP = {"permit": "allow", "deny": "deny"}

# Prefixes that mean "everywhere". A rule constrained only by one of these is not constrained.
ANY_PREFIXES = {"0.0.0.0/0", "::/0"}


def _value(node: Any, *path: str) -> Any:
    """Walk an Infrahub GraphQL node down ``path``, returning ``None`` at the first gap.

    Args:
        node: A node dictionary from the GraphQL response.
        *path: Keys to descend through, ending at the one holding ``value``.

    Returns:
        The attribute's value, or ``None`` if any step is absent.
    """
    current: Any = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    if isinstance(current, dict):
        return current.get("value")
    return current


def _nodes(node: Any, key: str) -> list[dict[str, Any]]:
    """Return the peer nodes of a cardinality-many relationship.

    Args:
        node: A node dictionary from the GraphQL response.
        key: Name of the relationship.

    Returns:
        One dictionary per related node, empty when the relationship is unset.
    """
    edges = (node.get(key) or {}).get("edges") or []
    return [edge["node"] for edge in edges if edge.get("node")]


def _peer(node: Any, key: str) -> dict[str, Any] | None:
    """Return the peer node of a cardinality-one relationship.

    Args:
        node: A node dictionary from the GraphQL response.
        key: Name of the relationship.

    Returns:
        The related node, or ``None`` when the relationship is unset.
    """
    peer = (node.get(key) or {}).get("node")
    return peer if isinstance(peer, dict) else None


class PanosFirewall(InfrahubTransform):
    """Transform a ``SecurityFirewall`` into PAN-OS ``set`` configuration."""

    query = "firewall_policy"

    async def transform(self, data: Any) -> Any:
        """Build the PAN-OS configuration for the firewall the query returned.

        Args:
            data: Result of ``firewall_policy``.

        Returns:
            The rendered configuration.
        """
        firewall_edges = data["SecurityFirewall"]["edges"]
        if not firewall_edges:
            return "# No firewall device found"

        firewall = firewall_edges[0]["node"]
        template_data = self._build(firewall)

        env = Environment(
            loader=FileSystemLoader(f"{self.root_directory}/templates/configs"),
            autoescape=False,  # Device configuration is not HTML; escaping corrupts it.
            trim_blocks=True,
            lstrip_blocks=True,
        )
        return env.get_template("panos.j2").render(data=template_data)

    def _build(self, firewall: dict[str, Any]) -> dict[str, Any]:
        """Flatten a firewall node into everything the template needs.

        Shared objects are accumulated across every rule of every policy so each address, service,
        profile group and schedule is emitted exactly once, no matter how many rules reference it.

        Args:
            firewall: The ``SecurityFirewall`` node.

        Returns:
            The template context.
        """
        device_name = _value(firewall, "name")

        shared: dict[str, dict[str, Any]] = {
            "addresses": {},
            "address_groups": {},
            "services": {},
            "service_groups": {},
            "profile_groups": {},
            "schedules": {},
        }
        zones: dict[str, dict[str, Any]] = {}
        unsupported: list[str] = []

        device_groups = []
        for policy in _nodes(firewall, "policies"):
            rules = [
                self._build_rule(rule, shared, zones, unsupported)
                for rule in sorted(
                    _nodes(policy, "rules"),
                    key=lambda node: _value(node, "index") or 0,
                )
            ]
            device_groups.append(
                {
                    "name": f"DG-{_value(policy, 'name')}",
                    "policy_name": _value(policy, "name"),
                    "description": _value(policy, "description"),
                    "rules": rules,
                }
            )

        return {
            "device_name": device_name,
            "template_stack": f"TS-{device_name}",
            "platform": _value(_peer(firewall, "platform") or {}, "name"),
            "location": _value(_peer(firewall, "location") or {}, "name"),
            "role": _value(firewall, "role"),
            "interfaces": self._build_interfaces(firewall),
            "zones": [zones[name] for name in sorted(zones)],
            "device_groups": device_groups,
            "unsupported": unsupported,
            **shared,
        }

    @staticmethod
    def _build_interfaces(firewall: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect the firewall's interfaces, separating out the management interface.

        Args:
            firewall: The ``SecurityFirewall`` node.

        Returns:
            One dictionary per interface, in query order.
        """
        interfaces = []
        for interface in _nodes(firewall, "interfaces"):
            addresses = _nodes(interface, "ip_addresses")
            interfaces.append(
                {
                    "name": _value(interface, "name"),
                    "role": _value(interface, "role"),
                    "description": _value(interface, "description"),
                    "ip_address": _value(addresses[0], "address") if addresses else None,
                }
            )
        return interfaces

    def _build_rule(
        self,
        rule: dict[str, Any],
        shared: dict[str, dict[str, Any]],
        zones: dict[str, dict[str, Any]],
        unsupported: list[str],
    ) -> dict[str, Any]:
        """Turn one ``SecurityPolicyRule`` into a rule dict, registering the objects it references.

        Args:
            rule: The ``SecurityPolicyRule`` node.
            shared: Accumulator for objects emitted under ``shared``; mutated in place.
            zones: Accumulator for zones seen on any rule; mutated in place.
            unsupported: Accumulator for objects PAN-OS cannot express; mutated in place.

        Returns:
            The rule as the template consumes it.
        """
        rule_name = _value(rule, "name") or "unnamed-rule"

        source_zone = self._register_zone(_peer(rule, "source_zone"), zones)
        destination_zone = self._register_zone(_peer(rule, "destination_zone"), zones)

        profile = _peer(rule, "security_profile")
        profile_name = _value(profile, "name") if profile else None
        if profile and profile_name:
            shared["profile_groups"][profile_name] = {
                "name": profile_name,
                "antivirus": _value(profile, "antivirus_enabled"),
                "ips": _value(profile, "ips_enabled"),
                "url_filtering": _value(profile, "url_filtering_enabled"),
            }

        schedule = _peer(rule, "schedule")
        schedule_name = _value(schedule, "name") if schedule else None
        if schedule and schedule_name:
            shared["schedules"][schedule_name] = {
                "name": schedule_name,
                "days": [day.strip() for day in (_value(schedule, "days_of_week") or "").split(",") if day.strip()],
                "start_time": _value(schedule, "start_time"),
                "end_time": _value(schedule, "end_time"),
            }

        return {
            "index": _value(rule, "index") or 0,
            "name": rule_name,
            "action": ACTION_MAP.get(_value(rule, "action") or "deny", "deny"),
            "log": bool(_value(rule, "log")),
            "from_zone": source_zone or "any",
            "to_zone": destination_zone or "any",
            "source": self._register_address_groups(_nodes(rule, "source_addresses"), shared),
            "destination": self._register_address_groups(_nodes(rule, "destination_addresses"), shared),
            "service": self._register_service_groups(_nodes(rule, "services"), shared, unsupported, rule_name),
            "application": [_value(app, "name") for app in _nodes(rule, "applications")] or ["any"],
            "url_categories": [_value(cat, "name") for cat in _nodes(rule, "url_categories")],
            "profile_group": profile_name,
            "schedule": schedule_name,
        }

    @staticmethod
    def _register_zone(zone: dict[str, Any] | None, zones: dict[str, dict[str, Any]]) -> str | None:
        """Record a zone so the template emits it, and return its name.

        Args:
            zone: A ``SecurityZone`` node, or ``None`` when the rule leaves the zone unset.
            zones: Accumulator keyed by zone name; mutated in place.

        Returns:
            The zone's name, or ``None``.
        """
        if not zone:
            return None
        name = _value(zone, "name")
        if not name:
            return None
        zones.setdefault(name, {"name": name, "trust_level": _value(zone, "trust_level")})
        return str(name)

    @staticmethod
    def _register_address_groups(groups: list[dict[str, Any]], shared: dict[str, dict[str, Any]]) -> list[str]:
        """Register address groups and their members, returning the names a rule should reference.

        A group whose only member is a default route is dropped rather than emitted: PAN-OS spells
        that ``any``, and an address object for ``0.0.0.0/0`` reads as a constraint when it is not.

        Args:
            groups: ``SecurityAddressGroup`` nodes attached to one side of a rule.
            shared: Accumulator for shared objects; mutated in place.

        Returns:
            Group names, or ``["any"]`` when nothing constrains this side.
        """
        referenced: list[str] = []

        for group in groups:
            group_name = _value(group, "name")
            if not group_name:
                continue

            members: list[str] = []
            for address in _nodes(group, "ip_addresses"):
                name = _value(address, "name")
                value = _value(_peer(address, "ipam_ip_address") or {}, "address")
                if name and value:
                    shared["addresses"][name] = {"name": name, "kind": "ip-netmask", "value": value}
                    members.append(name)

            for prefix in _nodes(group, "prefixes"):
                name = _value(prefix, "name")
                value = _value(_peer(prefix, "ipam_prefix") or {}, "prefix")
                if name and value and value not in ANY_PREFIXES:
                    shared["addresses"][name] = {"name": name, "kind": "ip-netmask", "value": value}
                    members.append(name)

            for ip_range in _nodes(group, "ip_ranges"):
                name = _value(ip_range, "name")
                start, end = _value(ip_range, "start"), _value(ip_range, "end")
                if name and start and end:
                    shared["addresses"][name] = {"name": name, "kind": "ip-range", "value": f"{start}-{end}"}
                    members.append(name)

            for fqdn in _nodes(group, "fqdns"):
                name = _value(fqdn, "name")
                value = _value(fqdn, "fqdn")
                if name and value:
                    shared["addresses"][name] = {"name": name, "kind": "fqdn", "value": value}
                    members.append(name)

            if not members:
                continue

            shared["address_groups"][group_name] = {"name": group_name, "members": members}
            referenced.append(str(group_name))

        return referenced or ["any"]

    @staticmethod
    def _register_service_groups(
        groups: list[dict[str, Any]],
        shared: dict[str, dict[str, Any]],
        unsupported: list[str],
        rule_name: str,
    ) -> list[str]:
        """Register service groups and their members, returning the names a rule should reference.

        ICMP is modelled as a ``SecurityService`` but PAN-OS has no port-based service for it -- it
        is matched as an application. Rather than emit a service object the firewall would reject,
        those are collected for the template to surface as a comment.

        Args:
            groups: ``SecurityServiceGroup`` nodes attached to the rule.
            shared: Accumulator for shared objects; mutated in place.
            unsupported: Accumulator for objects PAN-OS cannot express; mutated in place.
            rule_name: Name of the rule, for the note attached to a dropped service.

        Returns:
            Group names, or ``["any"]`` when nothing constrains the service.
        """
        referenced: list[str] = []

        for group in groups:
            group_name = _value(group, "name")
            if not group_name:
                continue

            members: list[str] = []
            for service in _nodes(group, "services"):
                name = _value(service, "name")
                protocol = (_value(service, "protocol") or "").lower()
                port = _value(service, "port")
                if not name or port is None:
                    continue
                if protocol not in ("tcp", "udp"):
                    unsupported.append(
                        f"service {name!r} on rule {rule_name!r} is {protocol.upper() or 'an unknown protocol'}; "
                        f"PAN-OS matches this as an application, not a service object"
                    )
                    continue
                shared["services"][name] = {"name": name, "protocol": protocol, "port": str(port)}
                members.append(name)

            for service_range in _nodes(group, "service_ranges"):
                name = _value(service_range, "name")
                protocol = (_value(service_range, "protocol") or "").lower()
                start, end = _value(service_range, "start"), _value(service_range, "end")
                if not name or start is None or end is None or protocol not in ("tcp", "udp"):
                    continue
                shared["services"][name] = {"name": name, "protocol": protocol, "port": f"{start}-{end}"}
                members.append(name)

            if not members:
                continue

            shared["service_groups"][group_name] = {"name": group_name, "members": members}
            referenced.append(str(group_name))

        return referenced or ["any"]
