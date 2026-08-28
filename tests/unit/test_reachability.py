"""Transitive reachability over a firewall's permit rules.

The interesting cases are the ones no single rule expresses: a pair of zones connected only by
going through a third. Each test below builds the minimum policy that produces one, so what makes
the check fire is visible in the test rather than buried in fixture data.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from checks.reachability import (  # noqa: E402
    CheckReachability,
    analyse,
    build_graph,
    shortest_indirect_paths,
)

# Trust levels as `objects/security/02_security_zones.yml` sets them.
TRUST = {
    "external": 0,
    "guest": 10,
    "dmz": 30,
    "development": 60,
    "staging": 70,
    "internal": 80,
    "server-farm": 85,
    "management": 90,
    "database": 95,
}


def attr(value: Any) -> dict[str, Any]:
    """Wrap a scalar the way Infrahub returns an attribute.

    Args:
        value: The attribute's value.

    Returns:
        The wrapped attribute.
    """
    return {"value": value}


def zone(name: str) -> dict[str, Any]:
    """Build a zone node at its documented trust level.

    Args:
        name: Zone name; must appear in :data:`TRUST`.

    Returns:
        The wrapped cardinality-one relationship.
    """
    return {"node": {"name": attr(name), "trust_level": attr(TRUST[name])}}


def rule(name: str, source: str, destination: str, action: str = "permit") -> dict[str, Any]:
    """Build a policy rule connecting two zones.

    Args:
        name: Rule name.
        source: Source zone name.
        destination: Destination zone name.
        action: ``permit`` or ``deny``.

    Returns:
        The rule node.
    """
    return {
        "name": attr(name),
        "action": attr(action),
        "source_zone": zone(source),
        "destination_zone": zone(destination),
    }


def policies(*rules: dict[str, Any]) -> list[dict[str, Any]]:
    """Wrap rules as a single policy.

    Args:
        *rules: The rules.

    Returns:
        A one-policy list as ``build_graph`` expects.
    """
    return [{"name": attr("corporate-firewall-policy"), "rules": {"edges": [{"node": r} for r in rules]}}]


def firewall(*rules: dict[str, Any]) -> dict[str, Any]:
    """Build a full query result around a set of rules.

    Args:
        *rules: The policy's rules.

    Returns:
        The query result.
    """
    return {
        "SecurityFirewall": {
            "edges": [
                {
                    "node": {
                        "name": attr("corp-firewall"),
                        "policies": {"edges": [{"node": p} for p in policies(*rules)]},
                    }
                }
            ]
        }
    }


def findings(*rules: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Run the analysis over a set of rules.

    Args:
        *rules: The policy's rules.

    Returns:
        Errors and notes.
    """
    edges, trust = build_graph(policies(*rules))
    return analyse(edges, trust)


# The permits the shipped corporate policy actually contains.
SHIPPED = [
    rule("allow-internet-to-dmz-web", "external", "dmz"),
    rule("allow-internal-to-dmz-web", "internal", "dmz"),
    rule("allow-internal-to-database", "internal", "database"),
    rule("allow-business-email", "internal", "external"),
    rule("allow-management-access", "management", "internal"),
]


class TestGraph:
    """Edge construction in ``build_graph``."""

    def test_only_permits_become_edges(self) -> None:
        """A deny expresses no reachability, so it contributes no edge."""
        edges, _ = build_graph(
            policies(
                rule("allow", "external", "dmz"),
                rule("block", "dmz", "internal", action="deny"),
            )
        )
        assert edges == {"external": [("dmz", "allow")]}

    def test_a_rule_without_zones_is_skipped(self) -> None:
        """The terminal catch-all constrains no direction and must not become an edge."""
        catch_all = {
            "name": attr("default-deny-all"),
            "action": attr("deny"),
            "source_zone": {"node": None},
            "destination_zone": {"node": None},
        }
        edges, _ = build_graph(policies(catch_all))
        assert edges == {}

    def test_a_self_loop_is_skipped(self) -> None:
        """Intra-zone traffic cannot extend a chain to somewhere new."""
        edges, _ = build_graph(policies(rule("allow-infra", "internal", "internal")))
        assert edges == {}


class TestIndirectPaths:
    """Route finding in ``shortest_indirect_paths``."""

    def test_a_directly_permitted_pair_is_not_indirect(self) -> None:
        """A pair with its own rule is already reviewed; it is not this check's subject."""
        edges, _ = build_graph(
            policies(
                rule("a", "external", "dmz"),
                rule("b", "dmz", "internal"),
                rule("c", "external", "internal"),
            )
        )
        assert ("external", "internal") not in shortest_indirect_paths(edges)

    def test_the_shortest_route_is_the_one_reported(self) -> None:
        """A pair reachable two ways is reported once, by its shortest route."""
        edges, _ = build_graph(
            policies(
                rule("short-1", "external", "dmz"),
                rule("short-2", "dmz", "database"),
                rule("long-1", "external", "guest"),
                rule("long-2", "guest", "staging"),
                rule("long-3", "staging", "database"),
            )
        )
        route = shortest_indirect_paths(edges)[("external", "database")]
        assert [zone_name for zone_name, _ in route] == ["external", "dmz"]

    def test_a_cycle_terminates(self) -> None:
        """Rules that form a loop must not send the traversal round it forever."""
        edges, _ = build_graph(
            policies(
                rule("a", "external", "dmz"),
                rule("b", "dmz", "guest"),
                rule("c", "guest", "external"),
            )
        )
        assert shortest_indirect_paths(edges)


class TestAnalysis:
    """Severity in ``analyse``."""

    def test_the_shipped_policy_raises_nothing(self) -> None:
        """The permits demo-dc ships put nothing untrusted within reach of anything sensitive.

        This is the headroom the check needs. If it fails, the demo data has grown a pivot path and
        every proposed change will open red.
        """
        errors, _ = findings(*SHIPPED)
        assert errors == []

    def test_one_new_rule_can_open_a_pivot_to_the_database(self) -> None:
        """The demo: permitting dmz to internal puts the database within reach of the internet.

        No rule connects external to database, or dmz to database. The chain does both, and every
        untrusted origin is reported rather than only the longest -- an operator narrowing the
        exposure needs to know dmz reaches it in two hops as well as external in three.
        """
        errors, _ = findings(*SHIPPED, rule("allow-dmz-to-internal", "dmz", "internal"))

        assert len(errors) == 2
        joined = "\n".join(errors)
        assert "'dmz' (trust 30) reaches 'database' (trust 95) in 2 hops" in joined
        assert "'external' (trust 0) reaches 'database' (trust 95) in 3 hops" in joined

        # The chain names the rule behind each hop, so the reviewer knows what to change.
        three_hop = next(e for e in errors if "in 3 hops" in e)
        for rule_name in ("allow-internet-to-dmz-web", "allow-dmz-to-internal", "allow-internal-to-database"):
            assert rule_name in three_hop

    def test_an_indirect_path_into_higher_trust_from_a_trusted_zone_is_a_note(self) -> None:
        """management reaching database via internal is worth saying, not worth blocking."""
        errors, notes = findings(*SHIPPED)
        assert errors == []
        assert any("'management' (trust 90) reaches 'database' (trust 95)" in note for note in notes)

    def test_a_path_descending_in_trust_is_not_reported(self) -> None:
        """Reaching somewhere less trusted indirectly is not an exposure."""
        _, notes = findings(rule("a", "management", "internal"), rule("b", "internal", "external"))
        assert not any("'external'" in note and "reaches" in note for note in notes)

    def test_an_untrusted_zone_reaching_a_middling_one_is_not_an_error(self) -> None:
        """The error is reserved for a sensitive destination, not any climb in trust."""
        errors, notes = findings(rule("a", "external", "dmz"), rule("b", "dmz", "development"))
        assert errors == []
        assert any("'development'" in note for note in notes)


class TestCheck:
    """``CheckReachability`` end to end."""

    async def test_the_shipped_policy_passes(self) -> None:
        """The check is green on the data the repository ships."""
        check = CheckReachability(branch="main")
        assert await check.run(data=firewall(*SHIPPED)) is True

    async def test_the_pivot_fails_the_check(self) -> None:
        """One extra permit turns the same policy red."""
        check = CheckReachability(branch="main")
        assert await check.run(data=firewall(*SHIPPED, rule("allow-dmz-to-internal", "dmz", "internal"))) is False

    @pytest.mark.parametrize("policies_present", [True, False])
    async def test_a_firewall_with_nothing_to_traverse_passes(self, policies_present: bool) -> None:
        """No policy, or a policy with no zone-to-zone permits, is not a failure."""
        data = firewall(rule("allow-infra", "internal", "internal")) if policies_present else firewall()
        if not policies_present:
            data["SecurityFirewall"]["edges"][0]["node"]["policies"] = {"edges": []}
        check = CheckReachability(branch="main")
        assert await check.run(data=data) is True
