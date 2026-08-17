"""The POP topology workflow.

``create_pop`` is a second, structurally different generator: a colocation center has no rack
hierarchy, no VXLAN overlay and no spine-leaf mesh, and its design is built entirely from virtual
device templates. That last part is what makes it worth its own module -- it is the only workflow in
the suite that exercises the ``DcimVirtualDevice`` path through ``generators/common.py``, and the only
one where the design's devices land in a different concrete kind from the DC designs.

The full proposed-change cycle runs here too, on a much smaller fabric than the DC workflow, so a
regression in merge behaviour has a cheap and fast reproduction.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from infrahub_sdk import InfrahubClient

from . import constants as c
from . import helpers as h

pytestmark = pytest.mark.extended

log = logging.getLogger(__name__)

BRANCH = c.POP_BRANCH
TOPOLOGY = c.POP_NAME
DESIGN = "VIRTUAL POP S"
PC_NAME = f"Add {TOPOLOGY}"


@pytest.fixture(scope="module")
def pop_state() -> dict[str, Any]:
    """State carried between this module's ordered steps.

    Returns:
        A mutable dict the steps populate as they go.
    """
    return {}


@pytest.mark.dependency(name="pop_loaded")
async def test_01_load_pop(
    async_client_main: InfrahubClient,
    infrahub_address: str,
    infrahub_bootstrap: dict[str, Any],
    pop_state: dict[str, Any],
) -> None:
    """Load the virtual POP design onto a fresh branch."""
    assert infrahub_bootstrap["repository_id"]
    await h.ensure_branch(async_client_main, BRANCH)
    h.load_objects(c.POP_OBJECT, address=infrahub_address, branch=BRANCH)

    topology = await async_client_main.get(
        kind="TopologyColocationCenter",
        name__value=TOPOLOGY,
        branch=BRANCH,
        include=["design"],
        prefetch_relationships=True,
    )
    assert topology.is_virtual.value is True, f"{TOPOLOGY} should be a virtual POP."
    assert topology.design.peer.name.value == DESIGN
    pop_state["topology_id"] = topology.id


@pytest.mark.dependency(name="pop_generated", depends=["pop_loaded"])
async def test_02_pop_devices_match_design(
    async_client_main: InfrahubClient,
    pop_state: dict[str, Any],
) -> None:
    """The ``pop-on-create`` trigger rule builds the POP from its design."""
    counts = await h.wait_for_topology(
        async_client_main,
        topology_name=TOPOLOGY,
        design_name=DESIGN,
        branch=BRANCH,
    )
    pop_state["role_counts"] = counts
    log.info("POP %s built: %s", TOPOLOGY, counts)


@pytest.mark.dependency(name="pop_virtual", depends=["pop_generated"])
async def test_03_devices_are_virtual(async_client_main: InfrahubClient) -> None:
    """Every device in a virtual POP is created as ``DcimVirtualDevice``.

    ``generators/common.py`` routes a design element to a concrete kind by inspecting its template's
    typename. This design is built from virtual templates only, so any device landing in
    ``DcimDevice`` means that routing broke.
    """
    client = async_client_main

    virtual = await client.filters(kind="DcimVirtualDevice", topology__name__value=TOPOLOGY, branch=BRANCH)
    assert virtual, f"No DcimVirtualDevice objects created for {TOPOLOGY}."

    physical = await client.filters(kind="DcimDevice", topology__name__value=TOPOLOGY, branch=BRANCH)
    assert not physical, (
        f"{TOPOLOGY} is built from virtual templates but produced physical devices: "
        f"{sorted(device.name.value for device in physical)}"
    )


@pytest.mark.dependency(depends=["pop_virtual"])
async def test_04_pop_devices_have_loopbacks_and_addresses(async_client_main: InfrahubClient) -> None:
    """POP devices get a management address and, where the role routes, a loopback.

    ``create_pop`` calls ``create_loopback('loopback0')``, which covers the routing roles only, so this
    asserts loopbacks for those and management addressing for all.
    """
    client = async_client_main
    devices = await client.filters(kind="DcimVirtualDevice", topology__name__value=TOPOLOGY, branch=BRANCH)

    unaddressed = [device.name.value for device in devices if not device.primary_address.id]
    assert not unaddressed, f"POP devices without a management address: {sorted(unaddressed)}"

    routing = [device for device in devices if device.role.value in ("spine", "leaf", "border_leaf", "edge")]
    assert routing, f"No routing-role devices in {TOPOLOGY}; the loopback assertion would be vacuous."

    loopbacks = await client.filters(
        kind="InterfaceVirtual",
        device__ids=[device.id for device in routing],
        branch=BRANCH,
    )
    with_loopback0 = {loopback.device.id for loopback in loopbacks if loopback.name.value == "loopback0"}
    missing = [device.name.value for device in routing if device.id not in with_loopback0]
    assert not missing, f"Routing devices in {TOPOLOGY} without loopback0: {sorted(missing)}"


@pytest.mark.dependency(name="pop_merged", depends=["pop_virtual"], scope="session")
async def test_05_pop_merges_to_main(async_client_main: InfrahubClient, pop_state: dict[str, Any]) -> None:
    """The POP branch goes through diff, proposed change and merge."""
    client = async_client_main

    await h.update_diff(client, branch=BRANCH)
    pc_id = await h.create_proposed_change(client, name=PC_NAME, source_branch=BRANCH)

    validators = await h.wait_for_validations(client, pc_id=pc_id)
    log.info("Validators for %s:\n%s", PC_NAME, h.summarize_validators(validators))

    await h.merge_proposed_change(client, pc_id=pc_id, validators=validators)

    counts = await h.actual_role_counts(client, topology_name=TOPOLOGY, branch="main")
    assert counts == pop_state["role_counts"], (
        f"POP differs after merge.\n  On {BRANCH}: {pop_state['role_counts']}\n  On main:  {counts}"
    )


@pytest.mark.dependency(depends=["pop_merged"])
async def test_06_equinix_pop_artifact_renders(async_client_main: InfrahubClient) -> None:
    """The Equinix POP artifact renders for the merged POP.

    ``equinix_pop`` is the only artifact definition targeting a colocation center, so it is the only
    one that exercises a transform over the POP data model.
    """
    client = async_client_main
    await h.generate_artifacts(client, definition_name="equinix_pop_config")
    artifacts = await h.wait_for_artifacts(client, definition_name="equinix_pop_config")

    for artifact in artifacts:
        content = await h.read_artifact(client, artifact)
        log.info("Artifact %s rendered %d characters", artifact.name.value, len(content))
