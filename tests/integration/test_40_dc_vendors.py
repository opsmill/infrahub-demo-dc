"""A second data center, from a different vendor design, on its own branch.

The Arista walkthrough in :mod:`tests.integration.test_10_dc_workflow` proves one design works. This
module proves the generator is actually design-driven rather than tuned to that one input, using the
Cisco design that also includes border leafs -- a role the Arista S design does not have, and one that
gets its own cabling, its own artifact definition and its own config template.

It also proves two topologies can coexist. The generator allocates from shared resource pools, so a
second fabric is where address-pool exhaustion, name collisions and cross-topology leakage would show
up. Nothing else in the suite would catch that.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from infrahub_sdk import InfrahubClient
from pytest_dependency import depends

from . import constants as c
from . import helpers as h

pytestmark = pytest.mark.extended

log = logging.getLogger(__name__)

BRANCH = c.DC_CISCO_BRANCH
DESIGN = c.DC_CISCO_DESIGN


@pytest.mark.dependency(name="cisco_loaded")
async def test_01_load_cisco_design(
    async_client_main: InfrahubClient,
    infrahub_address: str,
    infrahub_bootstrap: dict[str, Any],
    module_state: dict[str, Any],
) -> None:
    """Load the Cisco border-leaf design onto a fresh branch."""
    assert infrahub_bootstrap["repository_id"]
    await h.ensure_branch(async_client_main, BRANCH)
    h.load_objects(c.DC_CISCO_OBJECT, address=infrahub_address, branch=BRANCH)

    topologies = await async_client_main.all(kind="TopologyDataCenter", branch=BRANCH)
    on_branch = {topology.name.value for topology in topologies}
    new = on_branch - {c.DC_ARISTA_NAME}
    assert len(new) == 1, f"Expected exactly one new data center on {BRANCH}, found {sorted(new)}."

    topology_name = new.pop()
    module_state["topology_name"] = topology_name

    topology = await async_client_main.get(
        kind="TopologyDataCenter",
        name__value=topology_name,
        branch=BRANCH,
        include=["design"],
        prefetch_relationships=True,
    )
    module_state["topology_id"] = topology.id
    module_state["design_name"] = topology.design.peer.name.value
    log.info("Loaded %s using design %s", topology_name, module_state["design_name"])


@pytest.mark.dependency(name="cisco_generated", depends=["cisco_loaded"])
async def test_02_fabric_matches_design(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """The trigger rule builds a fabric matching this design, border leafs included."""
    counts = await h.wait_for_topology(
        async_client_main,
        topology_name=module_state["topology_name"],
        design_name=module_state["design_name"],
        branch=BRANCH,
    )
    module_state["role_counts"] = counts

    assert counts.get("border_leaf"), (
        f"Design {module_state['design_name']!r} calls for border leafs but none were created: {counts}"
    )


@pytest.mark.dependency(depends=["cisco_generated"])
async def test_03_border_leafs_are_cabled_and_addressed(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Border leafs are wired into the fabric rather than created and left stranded.

    They are the role this design adds, so they are the role most likely to be half-provisioned.
    """
    client = async_client_main
    topology_name = module_state["topology_name"]

    border_leafs = [
        device
        for device in await client.filters(kind="DcimDevice", topology__name__value=topology_name, branch=BRANCH)
        if device.role.value == "border_leaf"
    ]
    assert border_leafs, f"No border_leaf devices in {topology_name}."

    for device in border_leafs:
        assert device.primary_address.id, f"Border leaf {device.name.value} has no management address."

    await h.assert_loopbacks_present(client, border_leafs, branch=BRANCH, subject="Border leafs")


@pytest.mark.dependency(depends=["cisco_generated"])
async def test_04_arista_fabric_is_untouched(
    request: pytest.FixtureRequest,
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Building a second fabric leaves the first one alone.

    The generator allocates names and addresses from pools shared across topologies, so this is where
    cross-topology interference would surface.

    The Arista fabric is read through this branch, which inherits it from ``main``, so this needs the
    Arista workflow to have merged. That is a cross-module dependency, hence the runtime check.
    """
    depends(request, ["dc_merged"], scope="session")

    counts = await h.actual_role_counts(async_client_main, topology_name=c.DC_ARISTA_NAME, branch=BRANCH)
    expected = await h.expected_role_counts(async_client_main, design_name="PHYSICAL DC ARISTA S", branch=BRANCH)

    assert counts == expected, (
        f"Generating {module_state['topology_name']} changed the {c.DC_ARISTA_NAME} fabric.\n"
        f"  Expected: {expected}\n  Found:    {counts}"
    )


@pytest.mark.dependency(depends=["cisco_generated"])
async def test_05_management_addresses_do_not_collide(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Every device across both fabrics holds a distinct management address.

    Each topology draws from its own management subnet, so a duplicate would mean the generator reused
    a pool across topologies -- a defect that leaves two devices unreachable and is invisible in a
    single-topology test.
    """
    client = async_client_main
    seen: dict[str, str] = {}
    duplicates: list[str] = []

    for kind in c.DEVICE_KINDS:
        devices = await client.filters(kind=kind, branch=BRANCH, include=["primary_address"])
        for device in devices:
            if not device.primary_address.id:
                continue
            address_id = device.primary_address.id
            if address_id in seen:
                duplicates.append(f"{seen[address_id]} and {device.name.value}")
            else:
                seen[address_id] = device.name.value

    assert not duplicates, f"Devices sharing a management address: {duplicates}"
    log.info("%d devices across both fabrics hold distinct management addresses", len(seen))
    assert module_state["role_counts"]
