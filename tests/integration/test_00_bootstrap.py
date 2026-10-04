"""The bootstrap the rest of the suite stands on.

Every later module assumes the deployment reached the state ``uv run invoke bootstrap`` produces.
This module asserts that state directly, so a broken bootstrap fails here with a message naming what
is missing, instead of surfacing four modules later as an unexplained empty topology.

The definition assertions are the highest-value checks in the suite for a dependency bump: if an
Infrahub release changes how a repository is imported, or how ``.infrahub.yml`` is parsed, the
generators and transforms silently stop being registered and everything downstream fails for
reasons that look unrelated.
"""

from __future__ import annotations

from typing import Any

import pytest
from infrahub_sdk import InfrahubClient

from . import constants as c
from . import helpers as h

pytestmark = pytest.mark.core


async def test_schema_loaded(async_client_main: InfrahubClient, infrahub_bootstrap: dict[str, Any]) -> None:
    """The project schema is present, covering every namespace the demo builds on."""
    assert infrahub_bootstrap["address"]

    schemas = await async_client_main.schema.all()
    for kind in (
        "TopologyDataCenter",
        "TopologyColocationCenter",
        "ServiceNetworkSegment",
        "DesignTopology",
        "DesignElement",
        "DcimDevice",
        "DcimVirtualDevice",
        "SecurityFirewall",
        "IpamPrefix",
        "RoutingAutonomousSystem",
    ):
        assert kind in schemas, f"Schema kind {kind!r} missing after schema load. Loaded: {len(schemas)} kinds."


async def test_menu_loaded(async_client_main: InfrahubClient) -> None:
    """The custom menu from ``menus/menu-full.yml`` was applied."""
    menu_items = await async_client_main.all(kind="CoreMenuItem")
    assert menu_items, "No CoreMenuItem objects found; `infrahubctl menu load` had no effect."


@pytest.mark.parametrize(
    ("kind", "names"),
    [
        ("CoreStandardGroup", ["topologies_dc", "topologies_pop", "topologies_clab", "leafs", "spines", "racks"]),
        ("DesignTopology", ["PHYSICAL DC ARISTA S", "PHYSICAL DC CISCO S WITH BORDER LEAFS", "VIRTUAL POP S"]),
    ],
)
async def test_bootstrap_objects_loaded(async_client_main: InfrahubClient, kind: str, names: list[str]) -> None:
    """The bootstrap objects the generators depend on by name are present."""
    found = {node.name.value for node in await async_client_main.all(kind=kind)}
    missing = sorted(set(names) - found)
    assert not missing, f"{kind} objects missing after bootstrap: {missing}. Found {len(found)}."


@pytest.mark.parametrize(
    "kind",
    ["DcimDeviceType", "DcimPlatform", "OrganizationManufacturer", "IpamPrefix", "RoutingAutonomousSystem"],
)
async def test_bootstrap_inventory_loaded(async_client_main: InfrahubClient, kind: str) -> None:
    """The bootstrap inventory a design resolves against is non-empty."""
    count = await async_client_main.count(kind=kind)
    assert count, f"No {kind} objects after loading {c.BOOTSTRAP_OBJECTS_PATH}."


@pytest.mark.parametrize("kind", ["SecurityZone", "SecurityPolicy", "SecurityPolicyRule", "SecurityService"])
async def test_security_objects_loaded(async_client_main: InfrahubClient, kind: str) -> None:
    """The security demo data loaded alongside the bootstrap objects."""
    count = await async_client_main.count(kind=kind)
    assert count, f"No {kind} objects after loading {c.SECURITY_OBJECTS_PATH}."


async def test_repository_in_sync(async_client_main: InfrahubClient, infrahub_bootstrap: dict[str, Any]) -> None:
    """The repository imported without error, and stays healthy across its periodic re-syncs.

    Deliberately a wait rather than a snapshot assertion. Infrahub re-syncs registered repositories on
    a schedule, so ``sync_status`` legitimately returns to ``syncing`` long after the initial import
    completed -- reading it at an arbitrary moment and demanding ``in-sync`` fails roughly whenever a
    re-sync happens to be in flight. The helper still fails fast on an import error, which is the
    condition actually worth asserting here.
    """
    assert infrahub_bootstrap["repository_id"]
    repository = await h.wait_for_repository_sync(async_client_main, name=c.REPOSITORY_NAME)
    assert repository.id == infrahub_bootstrap["repository_id"]


@pytest.mark.parametrize(
    ("kind", "names"),
    [
        ("CoreGeneratorDefinition", c.GENERATOR_DEFINITIONS),
        ("CoreTransformation", c.TRANSFORM_DEFINITIONS),
        ("CoreCheckDefinition", c.CHECK_DEFINITIONS),
    ],
)
async def test_definitions_imported(async_client_main: InfrahubClient, kind: str, names: list[str]) -> None:
    """Every generator, transform and check declared in ``.infrahub.yml`` was registered.

    A wait rather than a one-shot read, because ``in-sync`` and "definitions queryable" are not the
    same instant: the preceding test only establishes that the import finished without error. Reading
    once immediately after it asserts the stronger claim, and the cost of being wrong is a flake in
    the tier that gates every pull request. The wait is free when there is no race -- ``wait_for``
    evaluates the check before its first sleep and returns on the spot -- so this only ever spends
    time in the case the one-shot read would have failed.
    """
    await h.wait_for_definitions(async_client_main, kind=kind, names=names)


async def test_artifact_definitions_imported(async_client_main: InfrahubClient) -> None:
    """Artifact definitions were registered, each bound to a transformation and a target group."""
    definitions = await async_client_main.all(
        kind="CoreArtifactDefinition",
        include=["transformation", "targets"],
        prefetch_relationships=True,
    )
    assert definitions, "No CoreArtifactDefinition objects imported from .infrahub.yml."

    unbound = [
        definition.name.value
        for definition in definitions
        if not definition.transformation.peer or not definition.targets.peer
    ]
    assert not unbound, f"Artifact definitions imported without a transformation or target group: {unbound}."


async def test_event_actions_and_triggers_loaded(async_client_main: InfrahubClient) -> None:
    """The generator actions and trigger rules that drive the event-based workflow are in place.

    These are what make loading a topology object enough to build a fabric, which is the behaviour
    the user walkthrough documents and :mod:`tests.integration.test_40_dc_vendors` relies on.
    """
    actions = {node.name.value for node in await async_client_main.all(kind="CoreGeneratorAction")}
    assert {"dc", "pop", "segment"} <= actions, (
        f"Generator actions missing: {sorted({'dc', 'pop', 'segment'} - actions)}"
    )

    rules = await async_client_main.all(kind="CoreNodeTriggerRule")
    rule_names = {node.name.value for node in rules}
    expected_rules = {"dc-on-create", "pop-on-create", "segment-on-create"}
    assert expected_rules <= rule_names, f"Trigger rules missing: {sorted(expected_rules - rule_names)}"


ROLE_PERMISSIONS_QUERY = """
query {
  CoreAccountRole {
    edges {
      node {
        name { value }
        permissions {
          edges {
            node {
              ... on CoreObjectPermission {
                namespace { value }
                name { value }
                action { value }
                decision { value }
              }
              ... on CoreGlobalPermission {
                action { value }
                decision { value }
              }
            }
          }
        }
      }
    }
  }
}
"""
"""Every role with its permissions, each rendered by :func:`_permission_key`."""

EXPECTED_ROLE_PERMISSIONS = {
    "read-only-role": {"*:*:view:6", "*:*:create:1", "*:*:update:1", "*:*:delete:1"},
    "schema-reviewer-role": {"manage_schema:6", "review_proposed_change:6", "edit_default_branch:6", "*:*:any:6"},
    # Built-in roles Infrahub creates on first start that share permissions with the demo roles.
    # Loading the demo roles must leave these links in place.
    "General Access": {"*:*:view:6", "manage_schema:6"},
    "Proposed Change Reviewer": {"edit_default_branch:6", "review_proposed_change:6"},
    "Anonymous User": {"*:*:view:6"},
}
"""Permissions each role must hold after the RBAC objects load; built-in roles may hold more."""


def _permission_key(permission: dict[str, Any]) -> str:
    """Render a permission as its attribute values joined by colons, in query order.

    Args:
        permission: One permission node from :data:`ROLE_PERMISSIONS_QUERY`.

    Returns:
        ``namespace:name:action:decision`` for an object permission, ``action:decision`` for a global one.
    """
    return ":".join(str(attribute["value"]) for attribute in permission.values())


async def test_rbac_objects_load_idempotently(
    async_client_main: InfrahubClient, infrahub_bootstrap: dict[str, Any]
) -> None:
    """The demo accounts load from ``objects/rbac`` twice, leaving the built-in roles intact.

    The second load proves the file can be rerun by ``invoke bootstrap``. The built-in role
    assertions guard against the file setting ``roles`` on a permission, which replaces the links
    Infrahub created on first start instead of adding to them.
    """
    for _ in range(2):
        h.load_objects(c.RBAC_OBJECTS_PATH, address=infrahub_bootstrap["address"])

    result = await async_client_main.execute_graphql(query=ROLE_PERMISSIONS_QUERY)
    held = {
        edge["node"]["name"]["value"]: {
            _permission_key(permission["node"]) for permission in edge["node"]["permissions"]["edges"]
        }
        for edge in result["CoreAccountRole"]["edges"]
    }
    for role, expected in EXPECTED_ROLE_PERMISSIONS.items():
        missing = sorted(expected - held.get(role, set()))
        assert not missing, f"Role {role!r} is missing permissions after loading {c.RBAC_OBJECTS_PATH}: {missing}"

    accounts = await async_client_main.filters(
        kind="CoreAccount", name__values=["emma", "otto"], include=["member_of_groups"], prefetch_relationships=True
    )
    groups = {
        account.name.value: {group.peer.name.value for group in account.member_of_groups.peers} for account in accounts
    }
    assert groups == {"emma": {"read-only-users"}, "otto": {"schema-reviewers"}}, f"Unexpected memberships: {groups}"
