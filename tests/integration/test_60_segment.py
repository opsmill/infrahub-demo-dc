"""The network segment service workflow.

``create_segment`` is the only generator in this repository that consumes an existing fabric rather
than building one from a design: a ``ServiceNetworkSegment`` names a deployment, and the generator
reaches into that deployment's leaf devices and attaches their customer-facing interfaces to the
segment. That makes it the suite's only coverage of the service-on-top-of-infrastructure pattern, and
the only generator whose output depends on data another generator produced.

It depends on the Arista fabric having merged to ``main``, because that is the deployment the segment
attaches to. That is the same order a user follows: build the data center, then sell services on it.
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

BRANCH = c.SEGMENT_BRANCH
PC_NAME = "Add OpsMill network segment"
SERVED_ROLES = ("leaf", "border_leaf")
"""Roles ``create_segment`` looks for customer interfaces on."""


async def _segment_interface_ids(client: InfrahubClient, segment_id: str, branch: str) -> set[str]:
    """Read the interfaces currently attached to a segment.

    Args:
        client: Client pointed at the default branch.
        segment_id: ID of the ``ServiceNetworkSegment``.
        branch: Branch to read on.

    Returns:
        The attached interface IDs.
    """
    segment = await client.get(
        kind="ServiceNetworkSegment",
        id=segment_id,
        branch=branch,
        include=["interfaces"],
    )
    return {str(peer.id) for peer in segment.interfaces.peers}


@pytest.mark.dependency(name="segment_loaded")
async def test_01_load_segment(
    request: pytest.FixtureRequest,
    async_client_main: InfrahubClient,
    infrahub_address: str,
    infrahub_bootstrap: dict[str, Any],
    module_state: dict[str, Any],
) -> None:
    """Load the OpsMill segment onto a branch, against the merged Arista deployment."""
    depends(request, ["dc_merged"], scope="session")
    assert infrahub_bootstrap["repository_id"]

    await h.ensure_branch(async_client_main, BRANCH)
    h.load_objects(c.SEGMENT_OBJECT, address=infrahub_address, branch=BRANCH)

    segments = await async_client_main.all(
        kind="ServiceNetworkSegment",
        branch=BRANCH,
        include=["deployment"],
        prefetch_relationships=True,
    )
    assert segments, f"No ServiceNetworkSegment created on {BRANCH} after loading {c.SEGMENT_OBJECT}."

    segment = segments[0]
    assert segment.deployment.peer.name.value == c.DC_ARISTA_NAME, (
        f"Segment attached to {segment.deployment.peer.name.value!r}, expected {c.DC_ARISTA_NAME!r}."
    )
    module_state["segment_id"] = segment.id
    module_state["vlan_id"] = segment.vlan_id.value
    log.info("Segment %s attached to %s with VLAN %s", segment.id, c.DC_ARISTA_NAME, module_state["vlan_id"])


@pytest.mark.dependency(name="segment_generated", depends=["segment_loaded"])
async def test_02_trigger_rule_attaches_customer_interfaces(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """The ``segment-on-create`` trigger rule runs ``create_segment``, which wires the service up.

    There is no task to follow on the event-driven path, so the segment's own interface relationship is
    the signal: it starts empty and the generator fills it.
    """
    client = async_client_main
    segment_id = module_state["segment_id"]

    async def check() -> tuple[bool, Any]:
        attached = await _segment_interface_ids(client, segment_id=segment_id, branch=BRANCH)
        return bool(attached), {"attached": len(attached)}

    await h.wait_for(
        check,
        f"create_segment to attach interfaces to segment {segment_id}",
        timeout=c.GENERATOR_TIMEOUT,
        interval=c.SLOW_POLL_INTERVAL,
    )

    # The first attachment only proves the generator started; it batches the rest. Settle before
    # recording the set that test_03 and test_04 compare against.
    await h.wait_for_quiescence(client, branch=BRANCH, kinds=["InterfacePhysical", "ServiceNetworkSegment"])

    attached = await _segment_interface_ids(client, segment_id=segment_id, branch=BRANCH)
    module_state["attached"] = attached
    log.info("Segment has %d interfaces attached", len(attached))


@pytest.mark.dependency(name="segment_correct", depends=["segment_generated"])
async def test_03_attached_interfaces_are_the_right_ones(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Every attached interface is a customer interface on a leaf of the segment's deployment.

    The generator selects by device role and interface role, so this asserts the selection rather than
    just its size -- attaching an uplink or an interface from the wrong data center would be a real
    misconfiguration that a count check would sail past.
    """
    client = async_client_main

    leafs = [
        device
        for device in await client.filters(kind="DcimDevice", topology__name__value=c.DC_ARISTA_NAME, branch=BRANCH)
        if device.role.value in SERVED_ROLES
    ]
    assert leafs, f"No {SERVED_ROLES} devices in {c.DC_ARISTA_NAME}; the selection check would be vacuous."

    eligible = await client.filters(
        kind="InterfacePhysical",
        device__ids=[device.id for device in leafs],
        role__value="customer",
        branch=BRANCH,
    )
    eligible_ids = {interface.id for interface in eligible}
    assert eligible_ids, "The Arista leaf template declares customer interfaces but none were found."

    attached: set[str] = module_state["attached"]
    unexpected = attached - eligible_ids
    assert not unexpected, (
        f"{len(unexpected)} interface(s) attached to the segment are not customer interfaces on a "
        f"{SERVED_ROLES} device of {c.DC_ARISTA_NAME}."
    )
    assert attached == eligible_ids, (
        f"create_segment attached {len(attached)} of {len(eligible_ids)} eligible customer interfaces; "
        f"it is expected to attach all of them."
    )


@pytest.mark.dependency(name="segment_idempotent", depends=["segment_correct"])
async def test_04_segment_generator_rerun_is_idempotent(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Re-running ``create_segment`` reconciles rather than attaching a second time.

    Service generators get re-run far more often than fabric generators -- every edit to a segment
    fires one -- so duplicate provisioning here would be a defect users hit immediately.
    """
    client = async_client_main

    task = await h.run_generator(
        client,
        definition_name="create_segment",
        node_ids=[module_state["segment_id"]],
        branch=BRANCH,
    )
    log.info("Explicit create_segment re-run finished in state %s", task.state)
    await h.wait_for_quiescence(client, branch=BRANCH, kinds=["InterfacePhysical", "ServiceNetworkSegment"])

    after = await _segment_interface_ids(client, segment_id=module_state["segment_id"], branch=BRANCH)
    assert after == module_state["attached"], (
        f"Re-running create_segment changed the attached interfaces: {len(module_state['attached'])} -> {len(after)}"
    )


@pytest.mark.dependency(depends=["segment_idempotent"])
@pytest.mark.xfail(
    reason=(
        "Blocked upstream, not by this repository. Infrahub runs create_dc as a check on this "
        "proposed change -- the segment's diff touches the DC fabric -- and that run fails on its own "
        "bookkeeping: CoreGeneratorGroupUpsert reports NODE_NOT_FOUND for a CoreGeneratorGroup / "
        "CoreGraphQLQueryGroup node, which nothing in this repository creates or deletes. The merge "
        "then refuses with 'Unable to merge proposed change containing failing checks'. Not strict, so "
        "this reports XPASS once Infrahub resolves those nodes correctly and the marker can go."
    ),
    strict=False,
)
async def test_05_segment_merges_to_main(async_client_main: InfrahubClient, module_state: dict[str, Any]) -> None:
    """The segment branch goes through diff, proposed change and merge.

    A segment modifies objects that already exist in ``main`` rather than only adding new ones, so this
    is the suite's coverage of merging a branch whose diff includes updates as well as additions.
    """
    client = async_client_main

    await h.update_diff(client, branch=BRANCH)
    pc_id = await h.create_proposed_change(client, name=PC_NAME, source_branch=BRANCH)

    validators = await h.wait_for_validations(client, pc_id=pc_id)
    log.info("Validators for %s:\n%s", PC_NAME, h.summarize_validators(validators))

    await h.merge_proposed_change(client, pc_id=pc_id, validators=validators)

    on_main = await _segment_interface_ids(client, segment_id=module_state["segment_id"], branch="main")
    assert on_main == module_state["attached"], (
        f"Segment interfaces differ after merge.\n"
        f"  On {BRANCH}: {len(module_state['attached'])}\n  On main:  {len(on_main)}"
    )
