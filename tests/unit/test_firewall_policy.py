"""The PAN-OS transform and the security-policy check, over hand-built query results.

Both consume the same ``firewall_policy`` query, so both are exercised from one set of builders
that produce the GraphQL shape Infrahub returns. That shape -- attributes wrapped in ``value``,
relationships wrapped in ``node`` or ``edges`` -- is the part most likely to be got wrong, and
building it explicitly here means a change to either consumer is checked against it without a
running deployment.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# `checks` and `transforms` sit at the repository root, which pytest does not put on the path for a
# test module outside a package.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from checks.security_policy import (  # noqa: E402
    CheckSecurityPolicy,
    analyse_policy,
    normalise_rule,
)
from transforms.panos import PanosFirewall  # noqa: E402

# --- builders for the GraphQL shape --------------------------------------------------------------


def attr(value: Any) -> dict[str, Any]:
    """Wrap a scalar the way Infrahub returns an attribute.

    Args:
        value: The attribute's value.

    Returns:
        The wrapped attribute.
    """
    return {"value": value}


def many(*nodes: dict[str, Any]) -> dict[str, Any]:
    """Wrap nodes the way Infrahub returns a cardinality-many relationship.

    Args:
        *nodes: The related nodes.

    Returns:
        The wrapped relationship.
    """
    return {"edges": [{"node": node} for node in nodes]}


def one(node: dict[str, Any] | None) -> dict[str, Any]:
    """Wrap a node the way Infrahub returns a cardinality-one relationship.

    Args:
        node: The related node, or ``None`` when unset.

    Returns:
        The wrapped relationship.
    """
    return {"node": node}


def ip_address(name: str, address: str) -> dict[str, Any]:
    """Build a ``SecurityIPAddress`` node.

    Args:
        name: Object name.
        address: The IPAM address, with mask.

    Returns:
        The node.
    """
    return {"name": attr(name), "ipam_ip_address": one({"address": attr(address)})}


def prefix(name: str, value: str) -> dict[str, Any]:
    """Build a ``SecurityPrefix`` node.

    Args:
        name: Object name.
        value: The IPAM prefix.

    Returns:
        The node.
    """
    return {"name": attr(name), "ipam_prefix": one({"prefix": attr(value)})}


def address_group(name: str, **members: Any) -> dict[str, Any]:
    """Build a ``SecurityAddressGroup`` node.

    Args:
        name: Group name.
        **members: Any of ``ip_addresses``, ``prefixes``, ``ip_ranges`` or ``fqdns``, each a list
            of member nodes. Omitted kinds default to empty.

    Returns:
        The node.
    """
    return {
        "name": attr(name),
        "ip_addresses": many(*members.get("ip_addresses", [])),
        "prefixes": many(*members.get("prefixes", [])),
        "ip_ranges": many(*members.get("ip_ranges", [])),
        "fqdns": many(*members.get("fqdns", [])),
    }


def service(name: str, protocol: str, port: int) -> dict[str, Any]:
    """Build a ``SecurityService`` node.

    Args:
        name: Service name.
        protocol: ``tcp``, ``udp`` or ``icmp``.
        port: Port number.

    Returns:
        The node.
    """
    return {"name": attr(name), "protocol": attr(protocol), "port": attr(port)}


def service_group(name: str, *services: dict[str, Any], ranges: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a ``SecurityServiceGroup`` node.

    Args:
        name: Group name.
        *services: Member ``SecurityService`` nodes.
        ranges: Member ``SecurityServiceRange`` nodes.

    Returns:
        The node.
    """
    return {
        "name": attr(name),
        "services": many(*services),
        "service_ranges": many(*(ranges or [])),
    }


def zone(name: str, trust_level: int) -> dict[str, Any]:
    """Build a ``SecurityZone`` node.

    Args:
        name: Zone name.
        trust_level: Trust level, 0-100.

    Returns:
        The node.
    """
    return {"name": attr(name), "trust_level": attr(trust_level)}


def rule(
    index: int,
    name: str,
    action: str = "permit",
    log: bool = True,
    from_zone: dict[str, Any] | None = None,
    to_zone: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
    destinations: list[dict[str, Any]] | None = None,
    services: list[dict[str, Any]] | None = None,
    applications: list[str] | None = None,
    url_categories: list[str] | None = None,
    profile: str | None = None,
    schedule: str | None = None,
) -> dict[str, Any]:
    """Build a ``SecurityPolicyRule`` node.

    Args:
        index: Evaluation order.
        name: Rule name.
        action: ``permit`` or ``deny``.
        log: Whether the rule logs.
        from_zone: Source zone node.
        to_zone: Destination zone node.
        sources: Source address group nodes.
        destinations: Destination address group nodes.
        services: Service group nodes.
        applications: Application names.
        url_categories: URL category names.
        profile: Security profile name, or ``None`` for no profile.
        schedule: Schedule name, or ``None`` for no schedule.

    Returns:
        The node.
    """
    return {
        "id": f"rule-{index}",
        "index": attr(index),
        "name": attr(name),
        "action": attr(action),
        "log": attr(log),
        "source_zone": one(from_zone),
        "destination_zone": one(to_zone),
        "source_addresses": many(*(sources or [])),
        "destination_addresses": many(*(destinations or [])),
        "services": many(*(services or [])),
        "applications": many(
            *({"name": attr(app), "category": attr(app), "risk_level": attr(2)} for app in (applications or []))
        ),
        "url_categories": many(*({"name": attr(cat), "risk_level": attr("high")} for cat in (url_categories or []))),
        "security_profile": one(
            {
                "name": attr(profile),
                "antivirus_enabled": attr(True),
                "ips_enabled": attr(True),
                "url_filtering_enabled": attr(True),
            }
            if profile
            else None
        ),
        "schedule": one(
            {
                "name": attr(schedule),
                "days_of_week": attr("monday,tuesday"),
                "start_time": attr("08:00"),
                "end_time": attr("18:00"),
                "timezone": attr("UTC"),
            }
            if schedule
            else None
        ),
    }


def firewall(
    *rules: dict[str, Any], name: str = "dc1-pan-fw-01", policy: str = "corporate-firewall-policy"
) -> dict[str, Any]:
    """Build a full ``firewall_policy`` query result around a set of rules.

    Args:
        *rules: The policy's rules.
        name: Device name.
        policy: Policy name.

    Returns:
        The query result.
    """
    return {
        "SecurityFirewall": many(
            {
                "id": "fw-1",
                "name": attr(name),
                "role": attr("dc_firewall"),
                "platform": one({"name": attr("PAN-OS"), "netmiko_device_type": attr("paloalto_panos")}),
                "location": one({"name": attr("par-1")}),
                "interfaces": many(
                    {
                        "name": attr("management"),
                        "role": attr("management"),
                        "description": attr(None),
                        "ip_addresses": many({"address": attr("10.0.0.9/24")}),
                    },
                    {
                        "name": attr("ethernet1/1"),
                        "role": attr("uplink"),
                        "description": attr("Uplink to core"),
                        "ip_addresses": many({"address": attr("10.1.0.1/30")}),
                    },
                ),
                "policies": many(
                    {
                        "id": "policy-1",
                        "name": attr(policy),
                        "description": attr("Main corporate firewall security policy"),
                        "rules": many(*rules),
                    }
                ),
            }
        )
    }


# --- shared rule fixtures ------------------------------------------------------------------------

INTERNAL = zone("internal", 80)
DMZ = zone("dmz", 30)
EXTERNAL = zone("external", 0)
DATABASE = zone("database", 95)

WEB_SERVERS = address_group("web-servers", ip_addresses=[ip_address("web-server-01", "10.1.1.10/32")])
INTERNAL_NETWORKS = address_group("internal-networks", prefixes=[prefix("internal-network", "192.168.1.0/24")])
INTERNET = address_group("internet", prefixes=[prefix("internet", "0.0.0.0/0")])
WEB_SERVICES = service_group("web-services", service("https", "tcp", 443), service("http", "tcp", 80))

DEFAULT_DENY = rule(9999, "default-deny-all", action="deny")


def clean_rules() -> list[dict[str, Any]]:
    """Build a policy that every control accepts.

    Returns:
        Rules in evaluation order.
    """
    return [
        rule(
            100,
            "allow-internal-to-dmz-web",
            from_zone=INTERNAL,
            to_zone=DMZ,
            sources=[INTERNAL_NETWORKS],
            destinations=[WEB_SERVERS],
            services=[WEB_SERVICES],
            applications=["web-browsing"],
            profile="standard-security",
        ),
        DEFAULT_DENY,
    ]


def build_transform() -> PanosFirewall:
    """Build the transform without a deployment behind it.

    ``InfrahubTransform`` requires a client so it can fetch its own query, and clones it during
    construction. ``transform()`` is handed its data directly here and never reaches the client, so
    a stand-in is enough -- and keeps these tests off a deployment.

    Returns:
        A transform rooted at the repository, so it loads the real template.
    """
    return PanosFirewall(
        client=MagicMock(),
        infrahub_node=MagicMock(),
        branch="main",
        root_directory=str(ROOT),
    )


def findings(*rules: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Run the analysis over a set of rules.

    Args:
        *rules: The policy's rules.

    Returns:
        Errors and notes.
    """
    return analyse_policy("corporate-firewall-policy", [normalise_rule(node) for node in rules])


# --- the check -----------------------------------------------------------------------------------


class TestSecurityPolicyCheck:
    """Controls in ``checks/security_policy.py``."""

    def test_a_constrained_policy_passes(self) -> None:
        """A policy whose permits name a destination and a service raises nothing."""
        errors, _ = findings(*clean_rules())
        assert errors == []

    def test_any_to_any_permit_is_an_error(self) -> None:
        """The rule the check exists for: permit with nothing constraining it."""
        errors, _ = findings(rule(100, "allow-everything", from_zone=INTERNAL, to_zone=DMZ), DEFAULT_DENY)
        assert any("any source to any destination on any service" in error for error in errors)

    def test_any_destination_with_a_source_is_still_an_error(self) -> None:
        """Naming a source does not make a permit to anywhere on any port acceptable."""
        errors, _ = findings(
            rule(100, "allow-outbound", from_zone=INTERNAL, to_zone=DMZ, sources=[INTERNAL_NETWORKS]),
            DEFAULT_DENY,
        )
        assert any("to any destination on any service" in error for error in errors)

    def test_a_constrained_service_downgrades_an_any_destination_to_a_note(self) -> None:
        """Permitting to any destination on a named service is reported but does not fail."""
        errors, notes = findings(
            rule(
                100,
                "allow-outbound-web",
                from_zone=INTERNAL,
                to_zone=DMZ,
                sources=[INTERNAL_NETWORKS],
                services=[WEB_SERVICES],
                profile="standard-security",
            ),
            DEFAULT_DENY,
        )
        assert errors == []
        assert any("permits to any destination" in note for note in notes)

    def test_an_empty_group_is_reported_as_unconstrained(self) -> None:
        """A group with no members renders as ``any``, so it is not a constraint.

        This is the failure mode that reads as safe in the UI: the rule shows a destination group,
        the group is empty, and the device permits everything.
        """
        errors, _ = findings(
            rule(
                100,
                "allow-to-empty-group",
                from_zone=INTERNAL,
                to_zone=DMZ,
                sources=[INTERNAL_NETWORKS],
                destinations=[address_group("database-servers")],
                services=[WEB_SERVICES],
                profile="standard-security",
            ),
            DEFAULT_DENY,
        )
        assert any("has no members" in error for error in errors)

    def test_a_default_route_member_does_not_count_as_a_constraint(self) -> None:
        """A group whose only member is ``0.0.0.0/0`` matches everything."""
        errors, _ = findings(
            rule(
                100,
                "allow-to-internet-group",
                from_zone=INTERNAL,
                to_zone=DMZ,
                sources=[INTERNAL_NETWORKS],
                destinations=[INTERNET],
                profile="standard-security",
            ),
            DEFAULT_DENY,
        )
        assert any("to any destination on any service" in error for error in errors)

    def test_trust_inversion_needs_both_constraints(self) -> None:
        """Permitting into a more trusted zone without a service constraint is an error."""
        errors, _ = findings(
            rule(
                100,
                "allow-dmz-to-database",
                from_zone=DMZ,
                to_zone=DATABASE,
                sources=[INTERNAL_NETWORKS],
                destinations=[WEB_SERVERS],
                profile="server-security",
            ),
            DEFAULT_DENY,
        )
        assert any("into 'database'" in error for error in errors)

    def test_an_inbound_rule_into_a_lower_trust_zone_is_fine(self) -> None:
        """External into DMZ with a named destination and service is ordinary, not an inversion."""
        errors, _ = findings(
            rule(
                100,
                "allow-internet-to-dmz-web",
                from_zone=EXTERNAL,
                to_zone=DMZ,
                sources=[INTERNET],
                destinations=[WEB_SERVERS],
                services=[WEB_SERVICES],
                profile="dmz-security",
            ),
            DEFAULT_DENY,
        )
        assert errors == []

    def test_a_shadowed_rule_is_reported_as_unreachable(self) -> None:
        """A later rule covered on every dimension by an earlier one can never match."""
        broad = rule(
            100,
            "allow-internal-web",
            from_zone=INTERNAL,
            to_zone=DMZ,
            sources=[INTERNAL_NETWORKS],
            destinations=[WEB_SERVERS],
            services=[WEB_SERVICES],
            profile="standard-security",
        )
        narrow = rule(
            200,
            "allow-internal-https-only",
            from_zone=INTERNAL,
            to_zone=DMZ,
            sources=[INTERNAL_NETWORKS],
            destinations=[WEB_SERVERS],
            services=[service_group("https-only", service("https", "tcp", 443))],
            profile="standard-security",
        )
        errors, _ = findings(broad, narrow, DEFAULT_DENY)
        assert any("is unreachable" in error for error in errors)

    def test_rules_on_different_zone_pairs_do_not_shadow(self) -> None:
        """Coverage is per zone pair; two unrelated permits are both reachable."""
        errors, _ = findings(
            rule(
                100,
                "allow-internal-to-dmz",
                from_zone=INTERNAL,
                to_zone=DMZ,
                sources=[INTERNAL_NETWORKS],
                destinations=[WEB_SERVERS],
                services=[WEB_SERVICES],
                profile="standard-security",
            ),
            rule(
                200,
                "allow-internal-to-database",
                from_zone=INTERNAL,
                to_zone=DATABASE,
                sources=[INTERNAL_NETWORKS],
                destinations=[WEB_SERVERS],
                services=[WEB_SERVICES],
                profile="server-security",
            ),
            DEFAULT_DENY,
        )
        assert errors == []

    async def test_a_missing_default_deny_fails_the_firewall(self) -> None:
        """Without a terminal catch-all the outcome depends on the platform's implicit default."""
        check = CheckSecurityPolicy(branch="main")
        assert await check.run(data=firewall(clean_rules()[0])) is False
        assert any("ends in an explicit deny-all" in log["message"] for log in check.errors)

    async def test_one_catch_all_covers_a_firewall_carrying_several_policies(self) -> None:
        """The terminal deny belongs to the rulebase, not to each policy on it."""
        data = firewall(*clean_rules())
        node = data["SecurityFirewall"]["edges"][0]["node"]
        node["policies"]["edges"].append(
            {
                "node": {
                    "id": "policy-2",
                    "name": attr("dmz-policy"),
                    "description": attr("A second policy with no catch-all of its own"),
                    "rules": many(
                        rule(
                            100,
                            "allow-dmz-web",
                            from_zone=INTERNAL,
                            to_zone=DMZ,
                            sources=[INTERNAL_NETWORKS],
                            destinations=[WEB_SERVERS],
                            services=[WEB_SERVICES],
                            profile="dmz-security",
                        )
                    ),
                }
            }
        )

        check = CheckSecurityPolicy(branch="main")
        assert await check.run(data=data) is True

    def test_a_permit_after_the_catch_all_is_reported(self) -> None:
        """A catch-all that is not last leaves dead rules behind it."""
        errors, _ = findings(
            rule(100, "deny-everything", action="deny"),
            rule(
                200,
                "allow-internal-web",
                from_zone=INTERNAL,
                to_zone=DMZ,
                sources=[INTERNAL_NETWORKS],
                destinations=[WEB_SERVERS],
                services=[WEB_SERVICES],
                profile="standard-security",
            ),
        )
        assert any("is unreachable" in error for error in errors)

    def test_unlogged_and_unprofiled_permits_are_notes_not_failures(self) -> None:
        """Audit-trail and profile gaps are reported without failing the pipeline."""
        errors, notes = findings(
            rule(
                100,
                "allow-internal-to-dmz-web",
                log=False,
                from_zone=INTERNAL,
                to_zone=DMZ,
                sources=[INTERNAL_NETWORKS],
                destinations=[WEB_SERVERS],
                services=[WEB_SERVICES],
            ),
            DEFAULT_DENY,
        )
        assert errors == []
        assert any("without logging" in note for note in notes)
        assert any("without a security profile" in note for note in notes)

    @pytest.mark.parametrize(
        ("rules", "expected_pass"),
        [(clean_rules(), True), ([rule(100, "allow-everything"), DEFAULT_DENY], False)],
    )
    async def test_check_run_reports_pass_and_fail(self, rules: list[dict[str, Any]], expected_pass: bool) -> None:
        """``InfrahubCheck.run`` turns the findings into a pass or fail."""
        check = CheckSecurityPolicy(branch="main")
        assert await check.run(data=firewall(*rules)) is expected_pass

    async def test_a_firewall_with_no_policy_passes(self) -> None:
        """A firewall with nothing attached has nothing to get wrong."""
        data = firewall()
        data["SecurityFirewall"]["edges"][0]["node"]["policies"] = many()
        check = CheckSecurityPolicy(branch="main")
        assert await check.run(data=data) is True


# --- the transform -------------------------------------------------------------------------------


class TestPanosTransform:
    """Rendering in ``transforms/panos.py`` and ``templates/configs/panos.j2``."""

    @staticmethod
    async def render(*rules: dict[str, Any]) -> str:
        """Render a firewall built around the given rules.

        Args:
            *rules: The policy's rules.

        Returns:
            The PAN-OS configuration.
        """
        transform = build_transform()
        return str(await transform.transform(firewall(*rules)))

    async def test_shared_objects_and_rules_render(self) -> None:
        """Addresses, groups, services and the rule itself all reach the output."""
        config = await self.render(*clean_rules())

        assert "set shared address web-server-01 ip-netmask 10.1.1.10/32" in config
        assert "set shared address-group web-servers static [ web-server-01 ]" in config
        assert "set shared service https protocol tcp port 443" in config
        assert "set shared service-group web-services members [" in config
        assert (
            "set device-group DG-corporate-firewall-policy pre-rulebase security rules "
            "allow-internal-to-dmz-web action allow"
        ) in config

    async def test_the_device_template_stack_renders(self) -> None:
        """Hostname, data-plane interface and zones land in the per-device template stack."""
        config = await self.render(*clean_rules())

        assert "set template TS-dc1-pan-fw-01 config deviceconfig system hostname dc1-pan-fw-01" in config
        assert (
            "set template TS-dc1-pan-fw-01 config network interface ethernet ethernet1/1 layer3 ip 10.1.0.1/30"
        ) in config
        assert "set template TS-dc1-pan-fw-01 config vsys vsys1 zone internal network layer3" in config

    async def test_the_management_interface_is_not_a_data_plane_interface(self) -> None:
        """PAN-OS configures management under ``deviceconfig``, not as ``ethernet``."""
        config = await self.render(*clean_rules())

        assert "set template TS-dc1-pan-fw-01 config deviceconfig system ip-address 10.0.0.9" in config
        assert "interface ethernet management" not in config

    async def test_rules_render_in_index_order(self) -> None:
        """A PAN-OS rulebase is evaluated top-down, so emission order is the policy."""
        config = await self.render(DEFAULT_DENY, *clean_rules())

        assert config.index("rules allow-internal-to-dmz-web") < config.index("rules default-deny-all")

    async def test_an_unconstrained_rule_renders_as_any(self) -> None:
        """The catch-all deny reaches the device as ``any`` on every dimension."""
        config = await self.render(DEFAULT_DENY)

        for dimension in ("from", "to", "source", "destination", "application", "service"):
            assert (
                f"set device-group DG-corporate-firewall-policy pre-rulebase security rules "
                f"default-deny-all {dimension} [ any ]"
            ) in config

    async def test_a_default_route_group_renders_as_any_not_as_an_address(self) -> None:
        """``0.0.0.0/0`` is spelled ``any`` in PAN-OS; emitting it as an object would be misleading."""
        config = await self.render(
            rule(
                100,
                "allow-internet-to-dmz-web",
                from_zone=EXTERNAL,
                to_zone=DMZ,
                sources=[INTERNET],
                destinations=[WEB_SERVERS],
                services=[WEB_SERVICES],
            ),
            DEFAULT_DENY,
        )

        assert "0.0.0.0/0" not in config
        assert "rules allow-internet-to-dmz-web source [ any ]" in config

    async def test_icmp_is_surfaced_rather_than_dropped(self) -> None:
        """PAN-OS has no port-based ICMP service, and silently losing the rule would be worse."""
        config = await self.render(
            rule(
                100,
                "allow-ping",
                from_zone=INTERNAL,
                to_zone=DMZ,
                sources=[INTERNAL_NETWORKS],
                destinations=[WEB_SERVERS],
                services=[service_group("icmp-services", service("ping", "icmp", 0))],
            ),
            DEFAULT_DENY,
        )

        assert "matches this as an application" in config
        assert "set shared service ping" not in config

    async def test_profiles_and_schedules_render(self) -> None:
        """A rule's profile group and schedule reach the device as shared objects plus a reference."""
        config = await self.render(
            rule(
                100,
                "allow-business-email",
                from_zone=INTERNAL,
                to_zone=EXTERNAL,
                sources=[INTERNAL_NETWORKS],
                destinations=[WEB_SERVERS],
                services=[WEB_SERVICES],
                profile="standard-security",
                schedule="extended-hours",
            ),
            DEFAULT_DENY,
        )

        assert "set shared profile-group standard-security virus [ default ]" in config
        assert "set shared schedule extended-hours schedule-type recurring weekly monday [ 08:00-18:00 ]" in config
        assert "rules allow-business-email profile-setting group [ standard-security ]" in config
        assert "rules allow-business-email schedule extended-hours" in config

    async def test_a_firewall_with_no_device_returns_a_marker(self) -> None:
        """An empty result renders a comment rather than raising inside the task worker."""
        transform = build_transform()
        assert await transform.transform({"SecurityFirewall": {"edges": []}}) == "# No firewall device found"
