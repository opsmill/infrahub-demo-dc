"""Report what a firewall's policy permits transitively, not just rule by rule.

Every rule in a policy is reviewed on its own, which is why the dangerous ones get approved: the
rule that opens `dmz -> internal` is unremarkable until you notice `internal -> database` was
approved last quarter, and that together they put a web server two hops from the database tier.
Nobody wrote that path and nobody reviews it, because it does not appear in the rulebase.

This check builds the directed graph the permit rules describe -- zones as nodes, permits as edges --
and reports pairs reachable through an intermediate zone but never permitted directly. It is a
policy-level claim, not a packet-level one: it says the rulebase allows a chain of connections, so a
host compromised in the first zone could pivot to the last. Whether an intermediary would actually
forward traffic is a host question this cannot see, and the messages are worded accordingly.

`validate_security_policy` covers each rule in isolation. This is the question that only has an
answer once they are considered together.
"""

from typing import Any

from infrahub_sdk.checks import InfrahubCheck

UNTRUSTED_MAX = 50
"""At or below this trust level, a zone is treated as hostile ground to pivot from."""

SENSITIVE_MIN = 90
"""At or above this trust level, a zone holds something worth reaching."""

MAX_DEPTH = 6
"""Longest chain explored. Beyond this a path is too indirect to be actionable."""


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


def build_graph(policies: list[dict[str, Any]]) -> tuple[dict[str, list[tuple[str, str]]], dict[str, int]]:
    """Turn every permit rule into an edge between two zones.

    Deny rules are ignored rather than subtracted. A deny that genuinely cancels a permit makes that
    permit unreachable, which `validate_security_policy` reports as dead configuration -- so acting
    on it here as well would report the same fault twice, in a check whose subject is something
    else. A rule missing either zone is skipped: it constrains nothing about direction.

    Args:
        policies: ``SecurityPolicy`` nodes attached to the firewall.

    Returns:
        Edges keyed by source zone as ``(destination zone, rule name)``, and each zone's trust level.
    """
    edges: dict[str, list[tuple[str, str]]] = {}
    trust: dict[str, int] = {}

    for policy in policies:
        for rule in _nodes(policy, "rules"):
            if (_value(rule, "action") or "deny") != "permit":
                continue

            source, destination = _peer(rule, "source_zone"), _peer(rule, "destination_zone")
            if not source or not destination:
                continue

            source_name = _value(source, "name")
            destination_name = _value(destination, "name")
            if not source_name or not destination_name or source_name == destination_name:
                continue

            for zone, node in ((source_name, source), (destination_name, destination)):
                level = _value(node, "trust_level")
                if level is not None:
                    trust[str(zone)] = int(level)

            rule_name = str(_value(rule, "name") or "unnamed-rule")
            edges.setdefault(str(source_name), []).append((str(destination_name), rule_name))

    return edges, trust


def shortest_indirect_paths(
    edges: dict[str, list[tuple[str, str]]],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Find the shortest multi-hop route between zones that no single rule connects.

    Args:
        edges: Permit edges keyed by source zone.

    Returns:
        For each ``(source, destination)`` pair reachable only through an intermediate zone, the
        shortest route as a list of ``(zone, rule that leads out of it)`` steps.
    """
    direct = {(source, destination) for source, hops in edges.items() for destination, _ in hops}
    found: dict[tuple[str, str], list[tuple[str, str]]] = {}

    for start in edges:
        # Breadth-first, so the first route to a zone is the shortest one.
        queue: list[tuple[str, list[tuple[str, str]]]] = [(start, [])]
        seen = {start}

        while queue:
            current, route = queue.pop(0)
            if len(route) >= MAX_DEPTH:
                continue

            for destination, rule_name in edges.get(current, []):
                if destination in seen:
                    continue
                seen.add(destination)
                extended = route + [(current, rule_name)]
                queue.append((destination, extended))

                pair = (start, destination)
                if len(extended) > 1 and pair not in direct and pair not in found:
                    found[pair] = extended

    return found


def describe(route: list[tuple[str, str]], destination: str, trust: dict[str, int]) -> str:
    """Render a route as a readable chain of zones and the rules that link them.

    Args:
        route: ``(zone, rule)`` steps.
        destination: The final zone.
        trust: Trust level per zone.

    Returns:
        A string such as ``external(0) -[allow-x]-> dmz(30) -[allow-y]-> internal(80)``.
    """
    parts = []
    for zone, rule_name in route:
        parts.append(f"{zone}({trust.get(zone, '?')}) -[{rule_name}]->")
    parts.append(f"{destination}({trust.get(destination, '?')})")
    return " ".join(parts)


def analyse(edges: dict[str, list[tuple[str, str]]], trust: dict[str, int]) -> tuple[list[str], list[str]]:
    """Judge every indirect route by the trust it starts and ends in.

    Args:
        edges: Permit edges keyed by source zone.
        trust: Trust level per zone.

    Returns:
        Messages that must fail the check, and messages worth reporting but not failing it.
    """
    errors: list[str] = []
    notes: list[str] = []

    for (source, destination), route in sorted(shortest_indirect_paths(edges).items()):
        source_trust = trust.get(source)
        destination_trust = trust.get(destination)
        if source_trust is None or destination_trust is None:
            continue

        chain = describe(route, destination, trust)
        hops = len(route)

        if source_trust <= UNTRUSTED_MAX and destination_trust >= SENSITIVE_MIN:
            errors.append(
                f"{source!r} (trust {source_trust}) reaches {destination!r} (trust "
                f"{destination_trust}) in {hops} hops, though no rule permits it directly. A host "
                f"compromised in {source!r} could pivot to {destination!r} along: {chain}. Break "
                f"the chain, or constrain one of those rules so the pivot is not available."
            )
        elif destination_trust > source_trust:
            notes.append(
                f"{source!r} (trust {source_trust}) reaches {destination!r} (trust "
                f"{destination_trust}) in {hops} hops with no direct rule: {chain}"
            )

    return errors, notes


class CheckReachability(InfrahubCheck):
    """Report zone pairs a policy connects transitively but never permits directly."""

    query = "firewall_policy"

    def validate(self, data: Any) -> None:
        """Report every indirect route the firewall's policies allow.

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

        edges, trust = build_graph(policies)
        if not edges:
            self.log_info(f"Firewall {device_name!r} has no zone-to-zone permits to traverse.")
            return

        errors, notes = analyse(edges, trust)

        self.log_info(
            f"{device_name}: traversed {sum(len(v) for v in edges.values())} permit edge(s) across "
            f"{len(edges)} zone(s)."
        )
        for note in notes:
            self.log_info(f"{device_name}: {note}")
        for error in errors:
            self.log_error(message=f"{device_name}: {error}")
