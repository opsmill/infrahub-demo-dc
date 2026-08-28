"""The security policy this repository ships must pass its own check.

``validate_security_policy`` runs on every proposed change that touches a firewall, and
``corporate-firewall-policy`` is attached to both bootstrap firewalls -- so if the shipped data
trips the check, every proposed change in the demo opens red and the check stops meaning anything.

The object files are read and resolved here rather than queried, so the guarantee holds without a
deployment and an edit to the demo data fails in seconds instead of in the integration suite.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from checks.reachability import analyse as analyse_reachability  # noqa: E402
from checks.reachability import build_graph  # noqa: E402
from checks.security_policy import analyse_policy, ends_in_catch_all_deny, normalise_rule  # noqa: E402

SECURITY_OBJECTS = ROOT / "objects" / "security"

CHECKED_POLICIES = ["corporate-firewall-policy"]
"""Policies attached to a firewall in ``objects/bootstrap/18_devices.yml``, and so under the check."""


def load_objects() -> dict[str, dict[str, dict[str, Any]]]:
    """Read every security object file, indexed by kind and then by name.

    Entries without a ``name`` are skipped: ``01_ipam_for_security.yml`` creates IPAM prefixes and
    addresses keyed by their value, and nothing here looks those up by name.

    Returns:
        ``{kind: {name: object}}`` across every YAML document in ``objects/security``.
    """
    objects: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(SECURITY_OBJECTS.glob("*.yml")):
        for document in yaml.safe_load_all(path.read_text()):
            if not document or document.get("kind") != "Object":
                continue
            spec = document["spec"]
            bucket = objects.setdefault(spec["kind"], {})
            for entry in spec.get("data") or []:
                if "name" in entry:
                    bucket[entry["name"]] = entry
    return objects


def _attr(value: Any) -> dict[str, Any]:
    """Wrap a scalar as an Infrahub attribute.

    Args:
        value: The value.

    Returns:
        The wrapped attribute.
    """
    return {"value": value}


def _many(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap nodes as an Infrahub cardinality-many relationship.

    Args:
        nodes: The related nodes.

    Returns:
        The wrapped relationship.
    """
    return {"edges": [{"node": node} for node in nodes]}


def _names(entry: dict[str, Any], key: str) -> list[str]:
    """Read a relationship's referenced names from an object file entry.

    Object files spell a cardinality-many reference as a list of single-element lists.

    Args:
        entry: The object entry.
        key: Relationship name.

    Returns:
        The referenced names.
    """
    return [reference[0] if isinstance(reference, list) else reference for reference in entry.get(key) or []]


def build_address_group(name: str, objects: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """Resolve a ``SecurityAddressGroup`` from the object files into query shape.

    Args:
        name: Group name.
        objects: Everything ``load_objects`` read.

    Returns:
        The group as the query would return it.
    """
    group = objects["SecurityAddressGroup"][name]
    return {
        "name": _attr(name),
        "ip_addresses": _many(
            [
                {
                    "name": _attr(member),
                    "ipam_ip_address": {
                        "node": {
                            "address": _attr(objects["SecurityIPAddress"][member]["ipam_ip_address"]["data"]["address"])
                        }
                    },
                }
                for member in _names(group, "ip_addresses")
            ]
        ),
        "prefixes": _many(
            [
                {
                    "name": _attr(member),
                    "ipam_prefix": {
                        "node": {"prefix": _attr(objects["SecurityPrefix"][member]["ipam_prefix"]["data"]["prefix"])}
                    },
                }
                for member in _names(group, "prefixes")
            ]
        ),
        "ip_ranges": _many(
            [
                {
                    "name": _attr(member),
                    "start": _attr(objects["SecurityIPRange"][member]["start"]),
                    "end": _attr(objects["SecurityIPRange"][member]["end"]),
                }
                for member in _names(group, "ip_ranges")
            ]
        ),
        "fqdns": _many(
            [
                {"name": _attr(member), "fqdn": _attr(objects["SecurityFQDN"][member]["fqdn"])}
                for member in _names(group, "fqdns")
            ]
        ),
    }


def build_service_group(name: str, objects: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """Resolve a ``SecurityServiceGroup`` from the object files into query shape.

    Args:
        name: Group name.
        objects: Everything ``load_objects`` read.

    Returns:
        The group as the query would return it.
    """
    group = objects["SecurityServiceGroup"][name]
    return {
        "name": _attr(name),
        "services": _many(
            [
                {
                    "name": _attr(member),
                    "port": _attr(objects["SecurityService"][member]["port"]),
                    "protocol": _attr(objects["SecurityService"][member]["protocol"]),
                }
                for member in _names(group, "services")
            ]
        ),
        "service_ranges": _many(
            [
                {
                    "name": _attr(member),
                    "start": _attr(objects["SecurityServiceRange"][member]["start"]),
                    "end": _attr(objects["SecurityServiceRange"][member]["end"]),
                    "protocol": _attr(objects["SecurityServiceRange"][member]["protocol"]),
                }
                for member in _names(group, "service_ranges")
            ]
        ),
    }


def build_zone(name: str | None, objects: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """Resolve a ``SecurityZone`` reference into query shape.

    Args:
        name: Zone name, or ``None`` when the rule leaves it unset.
        objects: Everything ``load_objects`` read.

    Returns:
        The cardinality-one relationship as the query would return it.
    """
    if not name:
        return {"node": None}
    zone = objects["SecurityZone"][name]
    return {"node": {"name": _attr(name), "trust_level": _attr(zone["trust_level"])}}


def build_rule(entry: dict[str, Any], objects: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """Resolve one ``SecurityPolicyRule`` object entry into query shape.

    Args:
        entry: The rule as written in the object file.
        objects: Everything ``load_objects`` read.

    Returns:
        The rule as the query would return it.
    """
    return {
        "index": _attr(entry["index"]),
        "name": _attr(entry["name"]),
        "action": _attr(entry["action"]),
        "log": _attr(entry.get("log", False)),
        "source_zone": build_zone(entry.get("source_zone"), objects),
        "destination_zone": build_zone(entry.get("destination_zone"), objects),
        "source_addresses": _many([build_address_group(name, objects) for name in _names(entry, "source_addresses")]),
        "destination_addresses": _many(
            [build_address_group(name, objects) for name in _names(entry, "destination_addresses")]
        ),
        "services": _many([build_service_group(name, objects) for name in _names(entry, "services")]),
        "applications": _many([{"name": _attr(name)} for name in _names(entry, "applications")]),
        "url_categories": _many([{"name": _attr(name)} for name in _names(entry, "url_categories")]),
        "security_profile": {
            "node": {"name": _attr(entry["security_profile"])} if entry.get("security_profile") else None
        },
        "schedule": {"node": {"name": _attr(entry["schedule"])} if entry.get("schedule") else None},
    }


@pytest.fixture(scope="module")
def security_objects() -> dict[str, dict[str, dict[str, Any]]]:
    """Every object in ``objects/security``, indexed by kind and name.

    Returns:
        ``{kind: {name: object}}``.
    """
    return load_objects()


def test_the_object_files_parse(security_objects: dict[str, dict[str, dict[str, Any]]]) -> None:
    """The kinds the rest of this module resolves against are all present."""
    for kind in ("SecurityZone", "SecurityAddressGroup", "SecurityServiceGroup", "SecurityPolicyRule"):
        assert security_objects.get(kind), f"No {kind} objects found under {SECURITY_OBJECTS}."


@pytest.mark.parametrize("policy_name", CHECKED_POLICIES)
def test_the_shipped_policy_passes_its_own_check(
    policy_name: str,
    security_objects: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """No rule in a firewall-attached policy is overly permissive, unreachable, or missing.

    A failure here means the demo would open every proposed change with a red check. The message
    names each finding, so the fix is to constrain the rule the message points at -- not to relax
    the control.
    """
    rules = [
        build_rule(entry, security_objects)
        for entry in security_objects["SecurityPolicyRule"].values()
        if entry.get("policy") == policy_name
    ]
    assert rules, f"Policy {policy_name!r} has no rules in the object files."

    normalised = [normalise_rule(rule) for rule in rules]

    errors, _ = analyse_policy(policy_name, normalised)
    assert errors == [], "The shipped policy fails validate_security_policy:\n  - " + "\n  - ".join(errors)

    assert ends_in_catch_all_deny(normalised), (
        f"Policy {policy_name!r} does not end in a catch-all deny, so the firewalls carrying it "
        f"fall back to each platform's implicit default."
    )


@pytest.mark.parametrize("policy_name", CHECKED_POLICIES)
def test_every_group_the_shipped_policy_references_has_members(
    policy_name: str,
    security_objects: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """No rule leans on an empty group, which would render as ``any`` on the device."""
    empty: list[str] = []
    for entry in security_objects["SecurityPolicyRule"].values():
        if entry.get("policy") != policy_name:
            continue
        normalised = normalise_rule(build_rule(entry, security_objects))
        empty.extend(f"rule {entry['index']} ({entry['name']}) -> {group}" for group in normalised["empty_groups"])

    assert empty == [], "Groups referenced by the shipped policy hold no members:\n  - " + "\n  - ".join(empty)


@pytest.mark.parametrize("policy_name", CHECKED_POLICIES)
def test_the_shipped_policy_opens_no_pivot_path(
    policy_name: str,
    security_objects: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """No untrusted zone reaches a sensitive one through an intermediate zone.

    `validate_reachability` runs on the same firewalls as the policy check, so the shipped permits
    have to leave headroom for a demo rule to be the thing that opens a path -- not arrive with one
    already open.
    """
    rules = [
        build_rule(entry, security_objects)
        for entry in security_objects["SecurityPolicyRule"].values()
        if entry.get("policy") == policy_name
    ]
    edges, trust = build_graph([{"name": {"value": policy_name}, "rules": {"edges": [{"node": r} for r in rules]}}])

    errors, _ = analyse_reachability(edges, trust)
    assert errors == [], "The shipped policy already permits a pivot path:\n  - " + "\n  - ".join(errors)
