"""Every GraphQL query in ``.infrahub.yml``, executed against the live schema.

``tests/smoke/test_graphql.yml`` checks these queries parse. That is a different guarantee from
executing them: a query can be syntactically perfect and still be rejected because an attribute was
renamed, a relationship's cardinality changed, or a kind moved namespace in an Infrahub release. Since
every transform, artifact and check in this repository is driven by one of these queries, a query that
stops executing takes the whole demo down -- and it does so with an error raised inside a task worker,
which is a much worse place to discover it than here.

Each query takes exactly one variable, either a device name or an object name. Where the populated
instance has a matching object the real name is used, so the query is validated against actual data;
where it does not, a placeholder is passed, because execution against the schema is the property being
tested and an empty result set is a valid answer.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
import yaml
from infrahub_sdk import InfrahubClient

from . import constants as c

pytestmark = [pytest.mark.core, pytest.mark.dependency(depends=["dc_merged"], scope="session")]

log = logging.getLogger(__name__)


NO_SUCH_OBJECT = "does-not-exist-in-this-test-run"
"""Placeholder for queries whose target kind has no instance yet. Execution is what is under test."""

QUERY_SUBJECTS: dict[str, tuple[str, str, str | None]] = {
    # query name: (variable, kind to draw a name from, device role to prefer)
    "leaf_config": ("device", "DcimDevice", "leaf"),
    "openconfig_leaf_config": ("device", "DcimDevice", "leaf"),
    "spine_config": ("device", "DcimDevice", "spine"),
    "edge_config": ("device", "DcimDevice", "edge"),
    "loadbalancer_config": ("device", "DcimVirtualDevice", None),
    "loadbalancer_validation": ("device", "DcimVirtualDevice", None),
    "juniper_firewall_config": ("device", "SecurityFirewall", None),
    "topology_dc": ("name", "TopologyDataCenter", None),
    "topology_simulator": ("name", "TopologyDataCenter", None),
    "topology_cabling": ("name", "TopologyDataCenter", None),
    "topology_pop": ("name", "TopologyColocationCenter", None),
    "equinix_pop_config": ("name", "TopologyColocationCenter", None),
    "segment": ("name", "ServiceNetworkSegment", None),
    "rack_elevation_query": ("name", "LocationRack", None),
    "virtualization_host_cabling": ("name", "VirtualizationPhysicalHost", None),
    "virtualization_vm_security": ("name", "VirtualizationVirtualMachine", None),
    "vm_artifact_groups": ("name", "VirtualizationVirtualMachine", None),
    "dc_firewall_policy": ("device", "SecurityFirewall", "dc_firewall"),
    "vm_config": ("name", "VirtualizationVirtualMachine", None),
    "virtualization_vm_validation": ("device", "VirtualizationVirtualMachine", None),
    "virtualization_host_validation": ("device", "VirtualizationPhysicalHost", None),
}
"""How to supply each query's single variable. Keyed by the name used in ``.infrahub.yml``."""


def declared_queries() -> list[str]:
    """Read the query names ``.infrahub.yml`` registers.

    Returns:
        The names, in declaration order.
    """
    config = yaml.safe_load((c.PROJECT_DIRECTORY / ".infrahub.yml").read_text())
    return [entry["name"] for entry in config.get("queries", [])]


@pytest.mark.offline
@pytest.mark.dependency()
def test_every_declared_query_has_a_subject() -> None:
    """Guard the mapping above against ``.infrahub.yml`` growing a query this module ignores.

    Without this, adding a query to the repository would silently add nothing to the suite.

    Two marker overrides, both so this stays a pure file check: ``offline`` keeps the deployment out of
    scope, and the empty ``dependency`` marker replaces the module-level one so the result is reported
    rather than skipped when an earlier workflow step failed.
    """
    declared = set(declared_queries())
    mapped = set(QUERY_SUBJECTS)

    assert declared == mapped, (
        "QUERY_SUBJECTS is out of step with .infrahub.yml.\n"
        f"  Declared but unmapped: {sorted(declared - mapped)}\n"
        f"  Mapped but not declared: {sorted(mapped - declared)}"
    )


async def _subject_name(client: InfrahubClient, kind: str, role: str | None) -> tuple[str, bool]:
    """Find a name to run a query against.

    Args:
        client: Client pointed at the default branch.
        kind: Kind to draw a name from.
        role: Device role to prefer, when the kind has a role attribute.

    Returns:
        A tuple of the name to use and whether it belongs to a real object.
    """
    nodes = await client.all(kind=kind, branch="main", limit=50)
    if role:
        matching = [node for node in nodes if node.role.value == role]
        nodes = matching or nodes
    if not nodes:
        return NO_SUCH_OBJECT, False
    return str(nodes[0].name.value), True


@pytest.mark.parametrize("query_name", declared_queries())
async def test_registered_query_executes(async_client_main: InfrahubClient, query_name: str) -> None:
    """A query registered from the repository executes against the current schema.

    Raises:
        AssertionError: If the query was never registered, or Infrahub rejects it.
    """
    client = async_client_main

    registered = await client.get(
        kind="CoreGraphQLQuery",
        name__value=query_name,
        branch="main",
        raise_when_missing=False,
    )
    assert registered, (
        f"Query {query_name!r} is declared in .infrahub.yml but no CoreGraphQLQuery was registered "
        f"for it. The repository imported without error, so this query failed on its own."
    )

    variable, kind, role = QUERY_SUBJECTS[query_name]
    name, is_real = await _subject_name(client, kind=kind, role=role)

    try:
        result: dict[str, Any] = await client.execute_graphql(
            query=registered.query.value,
            variables={variable: name},
            branch_name="main",
        )
    except Exception as exc:
        raise AssertionError(
            f"Query {query_name!r} failed to execute against main.\n"
            f"  Variable: ${variable} = {name!r} (from {kind}"
            f"{f', role {role}' if role else ''})\n"
            f"  Error: {exc}"
        ) from exc

    assert result, f"Query {query_name!r} executed but returned an empty response body."

    if is_real:
        # A real subject exists, so the query should have found it. An empty edge list here means the
        # query's filters no longer match the data the generators produce.
        payloads = [value for value in result.values() if isinstance(value, dict)]
        found = any(payload.get("edges") for payload in payloads)
        assert found, (
            f"Query {query_name!r} returned no edges for the existing {kind} {name!r}. Response keys: {sorted(result)}"
        )


async def test_fabric_is_reachable_over_graphql(async_client_main: InfrahubClient) -> None:
    """A hand-written query traverses the relationships the config transforms depend on.

    The registered queries above are generated artefacts of this repository. This one is written here
    so the suite also fails if the *shape* of the graph changes -- a device losing its interfaces, or
    interfaces losing their IP addresses -- rather than only if a query text stops parsing.
    """
    query = """
    query FabricShape($topology: String!) {
      DcimDevice(topology__name__value: $topology, role__value: "leaf") {
        count
        edges {
          node {
            name { value }
            primary_address { node { id } }
            interfaces { count }
          }
        }
      }
    }
    """
    result = await async_client_main.execute_graphql(
        query=query,
        variables={"topology": c.DC_ARISTA_NAME},
        branch_name="main",
    )

    leafs = result["DcimDevice"]["edges"]
    assert leafs, f"No leaf devices found for topology {c.DC_ARISTA_NAME!r} on main."

    for edge in leafs:
        node = edge["node"]
        name = node["name"]["value"]
        assert node["primary_address"], f"Leaf {name} has no primary address."
        assert node["interfaces"]["count"], f"Leaf {name} has no interfaces."
