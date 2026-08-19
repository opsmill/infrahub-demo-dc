"""Proposed-change behaviour when the review finds something wrong.

Every other module in the suite drives a proposed change that is expected to succeed, which only ever
exercises the happy path. The review workflow's whole purpose, though, is to stop a bad change -- so
the interesting assertions are about a change that must *not* sail through: a branch that touches the
same attribute ``main`` has since moved, and a proposed change that gets closed instead of merged.

This runs last and deliberately writes to ``main``, on a standalone bootstrap device rather than any
generated fabric device, so nothing it does can perturb the topology assertions in earlier modules.

Conflict *resolution* is intentionally out of scope: picking a side goes through a diff-conflict
mutation whose shape has moved between Infrahub releases, and pinning it here would make the suite
report a version difference as a demo defect. Detection is the part that must never regress.
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

BRANCH = c.CONFLICT_BRANCH
DEVICE = "cisco-switch-01"
BRANCH_DESCRIPTION = "Branch side of the conflict integration test"
MAIN_DESCRIPTION = "Main side of the conflict integration test"
PC_NAME = f"Conflict demo: {BRANCH}"

CLEAN_BRANCH = "clean-change-arista-switch"
CLEAN_DEVICE = "arista-switch-01"
CLEAN_DESCRIPTION = "Conflict-free change from the integration test"


async def _diff_tree(client: InfrahubClient, branch: str) -> dict[str, Any]:
    """Read the diff summary for a branch, including its conflict count.

    Args:
        client: Client pointed at the default branch.
        branch: Branch to summarize.

    Returns:
        The ``DiffTree`` summary fields.
    """
    query = """
    query DiffSummary($branch: String!) {
      DiffTree(branch: $branch) {
        num_added
        num_updated
        num_removed
        num_conflicts
      }
    }
    """
    result = await client.execute_graphql(query=query, variables={"branch": branch})
    return dict(result["DiffTree"])


@pytest.mark.dependency(name="clean_diff")
async def test_01_clean_branch_has_no_conflicts(
    async_client_main: InfrahubClient,
    infrahub_bootstrap: dict[str, Any],
) -> None:
    """A branch whose change nobody else touched reports zero conflicts.

    This is the control for the conflict test that follows: without it, a ``num_conflicts`` of zero
    could not be distinguished from conflict detection being switched off entirely.
    """
    assert infrahub_bootstrap["repository_id"]
    client = async_client_main

    await h.ensure_branch(client, CLEAN_BRANCH)

    device = await client.get(kind="DcimDevice", name__value=CLEAN_DEVICE, branch=CLEAN_BRANCH)
    device.description.value = CLEAN_DESCRIPTION
    await device.save()

    await h.update_diff(client, branch=CLEAN_BRANCH)
    summary = await _diff_tree(client, branch=CLEAN_BRANCH)
    log.info("Diff summary for %s: %s", CLEAN_BRANCH, summary)

    assert summary["num_updated"], f"Diff for {CLEAN_BRANCH} reports no updates despite the edit: {summary}"
    assert summary["num_conflicts"] == 0, (
        f"Diff for {CLEAN_BRANCH} reports {summary['num_conflicts']} conflict(s), but nothing else "
        f"changed {CLEAN_DEVICE} on main: {summary}"
    )


@pytest.mark.dependency(name="conflict_created", depends=["clean_diff"])
async def test_02_create_a_conflicting_change(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Change the same device differently on a branch and on ``main``.

    This mirrors ``uv run invoke demo-conflict``, so the scenario the docs tell a user to try is the
    scenario under test.
    """
    client = async_client_main
    await h.ensure_branch(client, BRANCH)

    on_branch = await client.get(kind="DcimDevice", name__value=DEVICE, branch=BRANCH)
    on_branch.description.value = BRANCH_DESCRIPTION
    await on_branch.save()
    module_state["device_id"] = on_branch.id

    in_main = await client.get(kind="DcimDevice", name__value=DEVICE, branch="main")
    in_main.description.value = MAIN_DESCRIPTION
    await in_main.save()

    reread_branch = await client.get(kind="DcimDevice", name__value=DEVICE, branch=BRANCH)
    reread_main = await client.get(kind="DcimDevice", name__value=DEVICE, branch="main")
    assert reread_branch.description.value == BRANCH_DESCRIPTION
    assert reread_main.description.value == MAIN_DESCRIPTION


@pytest.mark.dependency(name="conflict_in_diff", depends=["conflict_created"])
async def test_03_diff_reports_the_conflict(async_client_main: InfrahubClient) -> None:
    """The branch diff counts the divergent attribute as a conflict."""
    client = async_client_main

    await h.update_diff(client, branch=BRANCH)
    summary = await _diff_tree(client, branch=BRANCH)
    log.info("Diff summary for %s: %s", BRANCH, summary)

    assert summary["num_conflicts"], (
        f"Diff for {BRANCH} reports no conflicts, but {DEVICE}'s description was changed to two "
        f"different values on {BRANCH} and on main: {summary}"
    )


@pytest.mark.dependency(name="conflict_in_pc", depends=["conflict_in_diff"])
async def test_04_data_integrity_validator_surfaces_the_conflict(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """A proposed change over the conflict finishes its review and reports a data check for it.

    The distinction that matters: the validators must *complete*, and one of them must *fail*. A review
    that hangs and a review that passes are both broken, in opposite directions.
    """
    client = async_client_main

    pc_id = await h.create_proposed_change(client, name=PC_NAME, source_branch=BRANCH)
    module_state["pc_id"] = pc_id

    validators = await h.wait_for_validations(client, pc_id=pc_id)
    log.info("Validators for the conflicting change:\n%s", h.summarize_validators(validators))

    failing = [validator for validator in validators if validator.conclusion.value != "success"]
    assert failing, (
        f"Every validator on {PC_NAME} concluded successfully, but the branch conflicts with main.\n"
        f"{h.summarize_validators(validators)}"
    )

    data_checks = await client.filters(kind="CoreDataCheck", branch="main")
    with_conflicts = [check for check in data_checks if check.conflicts.value]
    assert with_conflicts, (
        f"A validator failed but no CoreDataCheck carries conflict detail, so the review cannot tell "
        f"a reviewer what to resolve. Data checks found: {len(data_checks)}"
    )
    log.info("Conflict detail recorded on %d data check(s)", len(with_conflicts))


@pytest.mark.dependency(depends=["conflict_in_pc"])
async def test_05_closing_a_proposed_change_leaves_main_alone(
    async_client_main: InfrahubClient,
    module_state: dict[str, Any],
) -> None:
    """Closing the proposed change abandons the branch's version of the change.

    Closing is the other half of the review workflow and nothing else in the suite covers it. The
    assertion that ``main`` still holds its own value is the point: a close must not quietly merge.
    """
    client = async_client_main

    pc = await client.get(kind="CoreProposedChange", id=module_state["pc_id"])
    pc.state.value = "closed"
    await pc.save()

    reread = await client.get(kind="CoreProposedChange", id=module_state["pc_id"])
    assert reread.state.value == "closed", f"Proposed change is in state {reread.state.value!r} after closing."

    device = await client.get(kind="DcimDevice", name__value=DEVICE, branch="main")
    assert device.description.value == MAIN_DESCRIPTION, (
        f"{DEVICE} in main now reads {device.description.value!r}; closing the proposed change should "
        f"have left main's own value in place."
    )
