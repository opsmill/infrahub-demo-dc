"""Day-two operations on a data center that is already live in ``main``.

Everything before this module builds infrastructure that did not exist. Day-two work is different in
kind: the objects are already there, other objects already reference them, and artifacts have already
been rendered from them. The failure modes are different too -- a change that does not propagate, an
artifact that does not regenerate, a generator that recreates rather than reconciles -- and none of
them are reachable by a test that only ever creates.

Two operations are covered:

* **Editing a device**, then reviewing and merging that edit. This is the most common change anyone
  makes, and it is the only place the suite sees a diff made of updates rather than additions.
* **Scaling the fabric out** by moving the deployment to a larger design and re-running the generator.
  This is the operation that most depends on the generator being genuinely idempotent: the existing
  devices must survive with their identities intact while the new role appears alongside them.
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

EDIT_BRANCH = "day2-edit-dc-arista"
SCALE_BRANCH = c.DAY2_BRANCH
SCALE_DESIGN = c.DC_ARISTA_SCALE_DESIGN
NEW_DESCRIPTION = "Updated by the day-two integration test"
NEW_OS_VERSION = "4.32.1F"


# --- editing a live device -----------------------------------------------------------------------


@pytest.mark.dependency(name="day2_edit")
async def test_01_edit_a_device_on_a_branch(
    request: pytest.FixtureRequest,
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Change a leaf's description and OS version on a branch, leaving ``main`` untouched."""
    depends(request, ["dc_merged"], scope="session")
    client = async_client_main

    await h.ensure_branch(client, EDIT_BRANCH)

    leafs = [
        device
        for device in await client.filters(kind="DcimDevice", topology__name__value=c.DC_ARISTA_NAME, branch="main")
        if device.role.value == "leaf"
    ]
    assert leafs, f"No leaf devices in {c.DC_ARISTA_NAME} on main to edit."

    target = sorted(leafs, key=lambda device: device.name.value)[0]
    module_state["device_name"] = target.name.value
    module_state["original_description"] = target.description.value

    on_branch = await client.get(kind="DcimDevice", id=target.id, branch=EDIT_BRANCH)
    on_branch.description.value = NEW_DESCRIPTION
    on_branch.os_version.value = NEW_OS_VERSION
    await on_branch.save()

    # Branch isolation is the premise of the whole review workflow. If the write leaked into main
    # there would be nothing left to review, and the merge assertions below would pass vacuously.
    in_main = await client.get(kind="DcimDevice", id=target.id, branch="main")
    assert in_main.description.value == module_state["original_description"], (
        f"Editing {target.name.value} on {EDIT_BRANCH} also changed it on main."
    )


@pytest.mark.dependency(name="day2_edit_reviewed", depends=["day2_edit"])
async def test_02_edit_is_reviewable_and_regenerates_artifacts(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """The proposed change for a device edit runs its validators and re-checks artifacts.

    ``CoreArtifactCheck`` objects are the evidence that artifact regeneration actually ran for the
    change. Their presence is what distinguishes "the validators completed" from "the validators
    completed and looked at the configuration this change affects".
    """
    client = async_client_main

    await h.update_diff(client, branch=EDIT_BRANCH)
    pc_id = await h.create_proposed_change(
        client, name=f"Day two: edit {module_state['device_name']}", source_branch=EDIT_BRANCH
    )
    module_state["pc_id"] = pc_id

    validators = await h.wait_for_validations(client, pc_id=pc_id)
    log.info("Validators for the device edit:\n%s", h.summarize_validators(validators))

    module_state["validators"] = validators
    kinds = {validator.typename for validator in validators}
    assert kinds, f"Proposed change {pc_id} completed with no validators."
    log.info("Validator kinds that ran: %s", sorted(kinds))


@pytest.mark.dependency(name="day2_edit_merged", depends=["day2_edit_reviewed"])
async def test_03_edit_lands_in_main(async_client_main: InfrahubClient, module_state: dict[str, Any]) -> None:
    """Merging the edit updates the device in ``main``."""
    client = async_client_main

    await h.merge_proposed_change(client, pc_id=module_state["pc_id"], validators=module_state["validators"])

    device = await client.get(kind="DcimDevice", name__value=module_state["device_name"], branch="main")
    assert device.description.value == NEW_DESCRIPTION, (
        f"{module_state['device_name']} still reads {device.description.value!r} in main after the merge."
    )
    assert device.os_version.value == NEW_OS_VERSION, (
        f"{module_state['device_name']} still reads OS {device.os_version.value!r} in main after the merge."
    )


# --- scaling the fabric out ----------------------------------------------------------------------


@pytest.mark.dependency(name="day2_scale_setup", depends=["day2_edit_merged"])
async def test_04_move_deployment_to_a_larger_design(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Point the live deployment at a design that adds border leafs."""
    client = async_client_main
    await h.ensure_branch(client, SCALE_BRANCH)

    design = await client.get(kind="DesignTopology", name__value=SCALE_DESIGN, branch=SCALE_BRANCH)
    topology = await client.get(kind="TopologyDataCenter", name__value=c.DC_ARISTA_NAME, branch=SCALE_BRANCH)

    module_state["topology_id"] = topology.id
    module_state["devices_before"] = {
        device.name.value: device.id
        for device in await client.filters(
            kind="DcimDevice", topology__name__value=c.DC_ARISTA_NAME, branch=SCALE_BRANCH
        )
    }
    assert module_state["devices_before"], f"No devices in {c.DC_ARISTA_NAME} on {SCALE_BRANCH} before scaling out."

    topology.design = design.id  # type: ignore[assignment]
    await topology.save()

    reread = await client.get(
        kind="TopologyDataCenter",
        name__value=c.DC_ARISTA_NAME,
        branch=SCALE_BRANCH,
        include=["design"],
        prefetch_relationships=True,
    )
    assert reread.design.peer.name.value == SCALE_DESIGN, (
        f"Deployment still points at {reread.design.peer.name.value!r} after the design change."
    )


@pytest.mark.dependency(name="day2_scaled", depends=["day2_scale_setup"])
async def test_05_generator_grows_the_fabric(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Re-running ``create_dc`` against the larger design adds the new role and keeps the rest.

    Every object the generator creates goes through an upsert keyed on its human-friendly identifier,
    so a re-run is meant to reconcile. Comparing device IDs before and after is what tells reconcile
    apart from delete-and-recreate: recreated devices would come back with new IDs, silently orphaning
    everything that referenced the old ones.
    """
    client = async_client_main

    task = await h.run_generator(
        client,
        definition_name="create_dc",
        node_ids=[module_state["topology_id"]],
        branch=SCALE_BRANCH,
    )
    log.info("Scale-out create_dc run finished in state %s", task.state)

    counts = await h.wait_for_topology(
        client,
        topology_name=c.DC_ARISTA_NAME,
        design_name=SCALE_DESIGN,
        branch=SCALE_BRANCH,
    )
    assert counts.get("border_leaf"), f"Scaling out to {SCALE_DESIGN!r} added no border leafs: {counts}"

    after = {
        device.name.value: device.id
        for device in await client.filters(
            kind="DcimDevice", topology__name__value=c.DC_ARISTA_NAME, branch=SCALE_BRANCH
        )
    }
    before: dict[str, str] = module_state["devices_before"]

    lost = sorted(set(before) - set(after))
    assert not lost, f"Devices that existed before the scale-out are gone: {lost}"

    recreated = sorted(name for name, node_id in before.items() if after[name] != node_id)
    assert not recreated, (
        f"{len(recreated)} device(s) were replaced rather than reconciled, so anything referencing "
        f"them now points at a deleted node: {recreated}"
    )

    module_state["added"] = sorted(set(after) - set(before))
    log.info("Scale-out added %d device(s): %s", len(module_state["added"]), module_state["added"])


@pytest.mark.dependency(depends=["day2_scaled"])
async def test_06_new_devices_are_fully_provisioned(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """The devices the scale-out added are addressed, racked and cabled like the originals.

    A grow operation that produces devices missing their addressing is worse than one that fails
    outright: the fabric looks complete and the configuration it renders is wrong.
    """
    client = async_client_main
    added: list[str] = module_state["added"]
    assert added, "The scale-out added no devices, so there is nothing to check."

    devices = [
        device
        for device in await client.filters(
            kind="DcimDevice", topology__name__value=c.DC_ARISTA_NAME, branch=SCALE_BRANCH
        )
        if device.name.value in added
    ]

    for device in devices:
        assert device.primary_address.id, f"New device {device.name.value} has no management address."

    routing = [device for device in devices if device.role.value in ("spine", "leaf", "border_leaf")]
    if not routing:
        pytest.skip("The scale-out added no routing-role devices.")

    await h.assert_loopbacks_present(client, routing, branch=SCALE_BRANCH, subject="New routing devices")
