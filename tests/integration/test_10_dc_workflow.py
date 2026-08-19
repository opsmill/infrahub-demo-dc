"""The data center workflow from the user walkthrough, end to end.

Follows ``docs/docs/user-walkthrough.mdx`` step for step: branch, load the Arista DC design, let the
generator build the fabric, review the diff, open a proposed change, wait for its validators, merge,
and confirm the result landed in ``main``.

Two things are deliberately checked beyond "objects exist":

* **Both generator paths.** Loading the design fires ``create_dc`` through the trigger rule, which is
  what the walkthrough describes and what most users will hit. Running the definition explicitly
  afterwards exercises the ``CoreGeneratorDefinitionRun`` mutation the CLI and web interface use --
  and, because it runs against an already-built fabric, doubles as the idempotency check that a
  generator re-run reconciles rather than duplicates.
* **Fabric structure, not just device count.** A generator can create the right number of devices and
  still leave them unaddressed, uncabled or unpeered. The structural test asserts the properties a
  fabric is useless without, so a partial run fails here rather than in a config transform later.

Merging is the last step for a reason: :mod:`tests.integration.test_20_artifacts` and everything in
the extended tier read this fabric from ``main``.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

import pytest
from infrahub_sdk import InfrahubClient

from . import constants as c
from . import helpers as h

pytestmark = pytest.mark.core

log = logging.getLogger(__name__)

BRANCH = c.DC_ARISTA_BRANCH
TOPOLOGY = c.DC_ARISTA_NAME
DESIGN = c.DC_ARISTA_DESIGN
PC_NAME = f"Add {TOPOLOGY}"

FABRIC_ROLES = ("spine", "leaf", "border_leaf")
"""Roles that get loopbacks, fabric cabling and a routing protocol session."""


@pytest.mark.dependency(name="dc_branch")
async def test_01_create_branch(async_client_main: InfrahubClient, infrahub_bootstrap: dict[str, Any]) -> None:
    """Create the branch the design will be loaded onto."""
    assert infrahub_bootstrap["repository_id"]
    await h.ensure_branch(async_client_main, BRANCH)


@pytest.mark.dependency(name="dc_design", depends=["dc_branch"])
async def test_02_load_design(
    async_client_main: InfrahubClient,
    infrahub_address: str,
    module_state: dict[str, Any],
) -> None:
    """Load the Arista DC design onto the branch and confirm the topology object exists."""
    h.load_objects(c.DC_ARISTA_OBJECT, address=infrahub_address, branch=BRANCH)

    topology = await async_client_main.get(kind="TopologyDataCenter", name__value=TOPOLOGY, branch=BRANCH)
    assert topology.name.value == TOPOLOGY
    module_state["topology_id"] = topology.id


@pytest.mark.dependency(name="dc_generated", depends=["dc_design"])
async def test_03_trigger_rule_builds_fabric(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """The ``dc-on-create`` trigger rule runs ``create_dc`` without anyone asking it to.

    There is no task ID to follow on this path, so the created devices are the signal. The expected
    counts come from the design itself, which is what makes this an assertion about the generator
    honouring its input rather than a restatement of a hard-coded number.
    """
    counts = await h.wait_for_topology(
        async_client_main,
        topology_name=TOPOLOGY,
        design_name=DESIGN,
        branch=BRANCH,
    )
    log.info("Fabric built from %s: %s", DESIGN, counts)
    module_state["role_counts"] = counts


@pytest.mark.dependency(name="dc_structure", depends=["dc_generated"])
async def test_04_fabric_is_complete(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """The fabric is addressed, sited, cabled and peered, not merely populated."""
    client = async_client_main
    counts: dict[str, int] = module_state["role_counts"]
    spines, leafs = counts.get("spine", 0), counts.get("leaf", 0)

    # Site hierarchy: the generator builds building -> pod -> row -> one rack per leaf.
    building = await client.get(kind="LocationBuilding", name__value=TOPOLOGY, branch=BRANCH)
    assert building.id

    rack_names = {rack.name.value for rack in await client.all(kind="LocationRack", branch=BRANCH)}
    expected_racks = {f"{TOPOLOGY}-Rack-{i}" for i in range(1, leafs + 1)}
    assert expected_racks <= rack_names, (
        f"Expected one rack per leaf ({leafs}). Missing: {sorted(expected_racks - rack_names)}"
    )

    # Management addressing: every device gets an IP from the management pool. A device without one
    # cannot be reached, and the config transforms have nothing to render into.
    unaddressed = []
    for kind in c.DEVICE_KINDS:
        devices = await client.filters(kind=kind, topology__name__value=TOPOLOGY, branch=BRANCH)
        unaddressed += [device.name.value for device in devices if not device.primary_address.id]
    assert not unaddressed, f"Devices created without a management address: {sorted(unaddressed)}"

    # Fabric cabling: spine-leaf is a full mesh, so at minimum one cable per spine-leaf pair, plus
    # the out-of-band management and console cabling on top.
    cables = await client.count(kind="DcimCable", branch=BRANCH)
    assert cables >= spines * leafs, (
        f"Expected at least {spines * leafs} cables for a {spines}x{leafs} spine-leaf mesh, found {cables}."
    )

    # Dual loopbacks: loopback0 carries the underlay, loopback1 is the VTEP and overlay address.
    fabric_devices = [
        device
        for device in await client.filters(kind="DcimDevice", topology__name__value=TOPOLOGY, branch=BRANCH)
        if device.role.value in FABRIC_ROLES
    ]
    assert fabric_devices, f"No {FABRIC_ROLES} devices found in {TOPOLOGY}."

    loopbacks = await client.filters(
        kind="InterfaceVirtual",
        device__ids=[device.id for device in fabric_devices],
        branch=BRANCH,
    )
    per_device: Counter[str] = Counter()
    for loopback in loopbacks:
        if loopback.name.value in ("loopback0", "loopback1"):
            per_device[str(loopback.device.id)] += 1

    missing = [device.name.value for device in fabric_devices if per_device[str(device.id)] < 2]
    assert not missing, f"Fabric devices missing loopback0 and/or loopback1: {sorted(missing)}"

    # Routing: dc-arista uses the ospf-ibgp strategy, so OSPF carries the underlay and iBGP the
    # EVPN overlay. Both services must exist for the fabric to converge.
    assert await client.count(kind="ServiceOSPF", branch=BRANCH), (
        "No ServiceOSPF objects; the ospf-ibgp underlay was not created."
    )
    assert await client.count(kind="ServiceBGP", branch=BRANCH), (
        "No ServiceBGP objects; the iBGP EVPN overlay was not created."
    )
    assert await client.count(kind="RoutingAutonomousSystem", branch=BRANCH), "No ASN created for the overlay."


@pytest.mark.dependency(name="dc_rerun", depends=["dc_structure"])
async def test_05_generator_rerun_is_idempotent(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Running ``create_dc`` explicitly over the built fabric reconciles instead of duplicating.

    This covers the ``CoreGeneratorDefinitionRun`` mutation as a distinct API surface, and pins the
    behaviour that matters for day-two work: a generator is safe to re-run.
    """
    client = async_client_main
    before = await client.count(kind="DcimGenericDevice", branch=BRANCH)

    task = await h.run_generator(
        client,
        definition_name="create_dc",
        node_ids=[module_state["topology_id"]],
        branch=BRANCH,
    )
    log.info("Explicit create_dc re-run finished in state %s", task.state)

    # The task reports done once the generator flow returns, which is not necessarily once every
    # batched write it queued has landed. Settle before comparing, or a slow write shows up as a
    # spurious difference.
    await h.wait_for_quiescence(client, branch=BRANCH)

    after = await h.actual_role_counts(client, topology_name=TOPOLOGY, branch=BRANCH)
    assert after == module_state["role_counts"], (
        f"Re-running create_dc changed the fabric.\n  Before: {module_state['role_counts']}\n  After:  {after}"
    )

    total_after = await client.count(kind="DcimGenericDevice", branch=BRANCH)
    assert total_after == before, f"Device total changed on re-run: {before} -> {total_after}"


@pytest.mark.dependency(name="dc_diff", depends=["dc_rerun"])
async def test_06_diff(async_client_main: InfrahubClient) -> None:
    """The branch diff computes over the generated fabric."""
    await h.update_diff(async_client_main, branch=BRANCH)


@pytest.mark.dependency(name="dc_validated", depends=["dc_diff"])
async def test_07_proposed_change_validators_complete(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Opening a proposed change runs its validators to completion.

    Conclusions are reported but not asserted. A failing check is a statement about the demo data,
    which the checks themselves are there to make; a validator that never completes is a statement
    about Infrahub, and that is what this test is guarding.
    """
    pc_id = await h.create_proposed_change(async_client_main, name=PC_NAME, source_branch=BRANCH)
    module_state["pc_id"] = pc_id

    validators = await h.wait_for_validations(async_client_main, pc_id=pc_id)
    log.info("Validators for %s:\n%s", PC_NAME, h.summarize_validators(validators))

    assert validators, f"Proposed change {pc_id} completed with no validators at all."
    module_state["validator_nodes"] = validators


@pytest.mark.dependency(name="dc_merged", depends=["dc_validated"], scope="session")
async def test_08_merge(async_client_main: InfrahubClient, module_state: dict[str, Any]) -> None:
    """Merging the proposed change moves the fabric into ``main``."""
    await h.merge_proposed_change(
        async_client_main,
        pc_id=module_state["pc_id"],
        validators=module_state["validator_nodes"],
    )


@pytest.mark.dependency(depends=["dc_merged"], scope="session")
async def test_09_fabric_present_in_main(async_client_main: InfrahubClient, module_state: dict[str, Any]) -> None:
    """The merged fabric is readable from ``main`` with the same shape it had on the branch."""
    client = async_client_main

    topology = await client.get(kind="TopologyDataCenter", name__value=TOPOLOGY, branch="main")
    assert topology.id

    counts = await h.actual_role_counts(client, topology_name=TOPOLOGY, branch="main")
    assert counts == module_state["role_counts"], (
        f"Fabric differs after merge.\n  On {BRANCH}: {module_state['role_counts']}\n  On main:  {counts}"
    )
