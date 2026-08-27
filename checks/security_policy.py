"""Flag overly permissive and dead rules in a firewall's security policy.

Run on every proposed change that touches a firewall, this is the gate that stops an any-to-any
permit reaching a device. It works on the modelled policy rather than on rendered configuration, so
one implementation covers every vendor the repository renders -- a rule that is too permissive is
too permissive whether it ends up as JunOS or as PAN-OS.

What counts as "any" is the subtle part. A rule looks constrained in the UI whenever it references
an address or service group, but a group with no members renders as ``any`` on both platforms, and
so does a group whose only member is a default route. Both are treated here as the unconstrained
rules they actually are.
"""

from typing import Any

from infrahub_sdk.checks import InfrahubCheck

# Prefixes that match everything. A rule constrained only by one of these is not constrained.
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


def resolve_addresses(groups: list[dict[str, Any]]) -> tuple[frozenset[str], list[str]]:
    """Resolve address groups to the concrete addresses they match.

    Args:
        groups: ``SecurityAddressGroup`` nodes attached to one side of a rule.

    Returns:
        The union of every member's value, and the names of any groups that hold no members at all.
        An empty set means the side matches any address -- which a group holding only a default
        route also produces, deliberately: that is what ``any`` means, not a modelling mistake.
    """
    members: set[str] = set()
    empty: list[str] = []

    for group in groups:
        held_members = False

        for address in _nodes(group, "ip_addresses"):
            held_members = True
            value = _value(_peer(address, "ipam_ip_address") or {}, "address")
            if value:
                members.add(str(value))

        for prefix in _nodes(group, "prefixes"):
            held_members = True
            value = _value(_peer(prefix, "ipam_prefix") or {}, "prefix")
            if value and value not in ANY_PREFIXES:
                members.add(str(value))

        for ip_range in _nodes(group, "ip_ranges"):
            held_members = True
            start, end = _value(ip_range, "start"), _value(ip_range, "end")
            if start and end:
                members.add(f"{start}-{end}")

        for fqdn in _nodes(group, "fqdns"):
            held_members = True
            value = _value(fqdn, "fqdn")
            if value:
                members.add(str(value))

        if not held_members:
            empty.append(str(_value(group, "name") or "unnamed-group"))

    return frozenset(members), empty


def resolve_services(groups: list[dict[str, Any]]) -> tuple[frozenset[str], list[str]]:
    """Resolve service groups to the concrete protocol/port pairs they match.

    Args:
        groups: ``SecurityServiceGroup`` nodes attached to the rule.

    Returns:
        The union of every member's protocol and port, and the names of any groups that hold no
        members at all. An empty set means the rule matches any service.
    """
    members: set[str] = set()
    empty: list[str] = []

    for group in groups:
        held_members = False

        for service in _nodes(group, "services"):
            held_members = True
            protocol, port = _value(service, "protocol"), _value(service, "port")
            if protocol and port is not None:
                members.add(f"{protocol}/{port}")

        for service_range in _nodes(group, "service_ranges"):
            held_members = True
            protocol = _value(service_range, "protocol")
            start, end = _value(service_range, "start"), _value(service_range, "end")
            if protocol and start is not None and end is not None:
                members.add(f"{protocol}/{start}-{end}")

        if not held_members:
            empty.append(str(_value(group, "name") or "unnamed-group"))

    return frozenset(members), empty


def normalise_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Flatten a ``SecurityPolicyRule`` into the sets the analysis compares.

    Every match dimension becomes a frozenset in which *empty means universal*, because that is how
    both target platforms read an unset match criterion.

    Args:
        rule: The ``SecurityPolicyRule`` node.

    Returns:
        The rule's index, name, action, logging flag, zones, match sets and any empty groups it
        references.
    """
    source_zone = _peer(rule, "source_zone")
    destination_zone = _peer(rule, "destination_zone")

    sources, empty_sources = resolve_addresses(_nodes(rule, "source_addresses"))
    destinations, empty_destinations = resolve_addresses(_nodes(rule, "destination_addresses"))
    services, empty_services = resolve_services(_nodes(rule, "services"))

    return {
        "index": _value(rule, "index") or 0,
        "name": _value(rule, "name") or "unnamed-rule",
        "action": _value(rule, "action") or "deny",
        "log": bool(_value(rule, "log")),
        "from_zone": _value(source_zone, "name") if source_zone else None,
        "to_zone": _value(destination_zone, "name") if destination_zone else None,
        "from_trust": _value(source_zone, "trust_level") if source_zone else None,
        "to_trust": _value(destination_zone, "trust_level") if destination_zone else None,
        "sources": sources,
        "destinations": destinations,
        "services": services,
        "applications": frozenset(str(_value(app, "name")) for app in _nodes(rule, "applications")),
        "url_categories": frozenset(str(_value(cat, "name")) for cat in _nodes(rule, "url_categories")),
        "has_profile": _peer(rule, "security_profile") is not None,
        "empty_groups": empty_sources + empty_destinations + empty_services,
    }


def _covers(broad: frozenset[str], narrow: frozenset[str]) -> bool:
    """Report whether one match set subsumes another.

    Args:
        broad: The candidate superset. Empty means universal.
        narrow: The candidate subset. Empty means universal.

    Returns:
        ``True`` when every packet matching ``narrow`` also matches ``broad``.
    """
    if not broad:
        return True
    if not narrow:
        return False
    return narrow <= broad


def _zone_covers(broad: str | None, narrow: str | None) -> bool:
    """Report whether one rule's zone subsumes another's.

    Args:
        broad: The candidate superset zone. ``None`` means any zone.
        narrow: The candidate subset zone.

    Returns:
        ``True`` when ``broad`` matches everything ``narrow`` matches.
    """
    return broad is None or broad == narrow


def shadows(earlier: dict[str, Any], later: dict[str, Any]) -> bool:
    """Report whether an earlier rule makes a later one unreachable.

    Args:
        earlier: The rule evaluated first.
        later: The rule evaluated after it.

    Returns:
        ``True`` when every packet ``later`` would match is already matched by ``earlier``.
    """
    return (
        _zone_covers(earlier["from_zone"], later["from_zone"])
        and _zone_covers(earlier["to_zone"], later["to_zone"])
        and _covers(earlier["sources"], later["sources"])
        and _covers(earlier["destinations"], later["destinations"])
        and _covers(earlier["services"], later["services"])
        and _covers(earlier["applications"], later["applications"])
        and _covers(earlier["url_categories"], later["url_categories"])
    )


def is_catch_all(rule: dict[str, Any]) -> bool:
    """Report whether a rule matches every packet.

    Args:
        rule: A normalised rule.

    Returns:
        ``True`` when no match dimension constrains anything.
    """
    return not any(
        (
            rule["from_zone"],
            rule["to_zone"],
            rule["sources"],
            rule["destinations"],
            rule["services"],
            rule["applications"],
            rule["url_categories"],
        )
    )


def analyse_policy(policy_name: str, rules: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Inspect one policy's rules for permissiveness and dead configuration.

    Args:
        policy_name: Name of the policy, used in every message.
        rules: Normalised rules, which this sorts into evaluation order.

    Returns:
        Messages that must fail the check, and messages worth reporting but not failing it.
    """
    errors: list[str] = []
    notes: list[str] = []

    ordered = sorted(rules, key=lambda rule: rule["index"])
    if not ordered:
        notes.append(f"Policy {policy_name!r} has no rules.")
        return errors, notes

    for rule in ordered:
        where = f"Policy {policy_name!r} rule {rule['index']} ({rule['name']!r})"
        permit = rule["action"] == "permit"

        for group in rule["empty_groups"]:
            message = (
                f"{where} references group {group!r}, which has no members. An empty group renders "
                f"as 'any', so this rule is unconstrained despite looking otherwise."
            )
            (errors if permit else notes).append(message)

        if not permit:
            continue

        destination_any = not rule["destinations"]
        service_any = not rule["services"] and not rule["applications"]

        if destination_any and service_any:
            scope = (
                "any source to any destination on any service"
                if not rule["sources"]
                else ("to any destination on any service")
            )
            errors.append(
                f"{where} permits {scope}. Constrain the destination, the service, or the "
                f"application before this reaches a device."
            )
        elif destination_any:
            notes.append(f"{where} permits to any destination. The service constraint is the only thing limiting it.")

        from_trust, to_trust = rule["from_trust"], rule["to_trust"]
        if from_trust is not None and to_trust is not None and from_trust < to_trust:
            if destination_any or service_any:
                errors.append(
                    f"{where} permits from {rule['from_zone']!r} (trust {from_trust}) into "
                    f"{rule['to_zone']!r} (trust {to_trust}) without constraining both destination "
                    f"and service. Traffic into a more trusted zone has to be specific."
                )

        if not rule["log"] and rule["from_zone"] != rule["to_zone"]:
            notes.append(f"{where} permits across a zone boundary without logging, so it leaves no audit trail.")

        if not rule["has_profile"]:
            notes.append(f"{where} permits without a security profile attached.")

    for position, earlier in enumerate(ordered):
        for later in ordered[position + 1 :]:
            if shadows(earlier, later):
                errors.append(
                    f"Policy {policy_name!r} rule {later['index']} ({later['name']!r}) is unreachable: "
                    f"rule {earlier['index']} ({earlier['name']!r}) already matches everything it would."
                )

    return errors, notes


def ends_in_catch_all_deny(rules: list[dict[str, Any]]) -> bool:
    """Report whether a policy's last rule denies everything.

    Args:
        rules: Normalised rules, in any order.

    Returns:
        ``True`` when the highest-index rule matches every packet and denies it.
    """
    if not rules:
        return False
    last = max(rules, key=lambda rule: rule["index"])
    return is_catch_all(last) and last["action"] == "deny"


class CheckSecurityPolicy(InfrahubCheck):
    """Check a firewall's security policy for overly permissive and unreachable rules."""

    query = "firewall_policy"

    def validate(self, data: Any) -> None:
        """Report every finding across every policy attached to the firewall.

        Args:
            data: Result of ``firewall_policy``.
        """
        firewall_edges = (data.get("SecurityFirewall") or {}).get("edges") or []
        if not firewall_edges:
            self.log_info("No firewall returned by the query; nothing to validate.")
            return

        firewall = firewall_edges[0]["node"]
        device_name = _value(firewall, "name") or "unknown"

        policies = _nodes(firewall, "policies")
        if not policies:
            self.log_info(f"Firewall {device_name!r} has no security policy attached.")
            return

        errors: list[str] = []
        notes: list[str] = []
        catch_all_policies: list[str] = []

        for policy in policies:
            policy_name = str(_value(policy, "name") or "unnamed-policy")
            rules = [normalise_rule(rule) for rule in _nodes(policy, "rules")]
            policy_errors, policy_notes = analyse_policy(policy_name, rules)
            errors.extend(policy_errors)
            notes.extend(policy_notes)
            if ends_in_catch_all_deny(rules):
                catch_all_policies.append(policy_name)

        # The catch-all is a property of the firewall's whole rulebase, not of each policy: a
        # firewall carrying three policies needs one terminal deny, not three. Requiring it per
        # policy would fail every multi-policy firewall for doing nothing wrong.
        if not catch_all_policies:
            errors.append(
                "No policy attached to this firewall ends in an explicit deny-all. Without one the "
                "result depends on each platform's implicit default, which differs by vendor."
            )

        for note in notes:
            self.log_info(f"{device_name}: {note}")
        for error in errors:
            self.log_error(message=f"{device_name}: {error}")
