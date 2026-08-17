"""Workflow helpers shared by the integration modules.

Each helper wraps one step a human performs when following the README or the user walkthrough --
load objects, wait for a generator, open a proposed change, merge it -- and raises with enough
context to diagnose the failure without re-reading container logs. That matters most for the case
these tests exist to catch: a testcontainers or Infrahub upgrade that changes the shape or the
timing of one step. A bare ``assert devices`` says the fabric is missing; these helpers say which
step stalled, what state it reached, and what it was compared against.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess  # noqa: S404 - infrahubctl is the documented interface for these steps
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from infrahub_sdk import InfrahubClient
from infrahub_sdk.graphql import Mutation
from infrahub_sdk.task.models import TaskState
from infrahub_testcontainers.container import PROJECT_ENV_VARIABLES

from . import constants as c

T = TypeVar("T")

log = logging.getLogger(__name__)

PROJECT_DIRECTORY = Path(__file__).parent.parent.parent
"""Repository root. Every path the suite passes to ``infrahubctl`` is relative to it."""


# --- infrahubctl ---------------------------------------------------------------------------------


def infrahubctl(command: str, address: str, timeout: int = c.OBJECT_LOAD_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run an ``infrahubctl`` command against the test container.

    Args:
        command: The command to run, without the ``infrahubctl`` prefix.
        address: Base URL of the Infrahub server under test.
        timeout: Seconds to allow before killing the subprocess.

    Returns:
        The completed process, including captured stdout and stderr.

    Raises:
        AssertionError: If the command did not finish within ``timeout``.
    """
    env = os.environ.copy()
    env.update(
        {
            "INFRAHUB_ADDRESS": address,
            "INFRAHUB_API_TOKEN": PROJECT_ENV_VARIABLES["INFRAHUB_TESTING_INITIAL_ADMIN_TOKEN"],
            "INFRAHUB_MAX_CONCURRENT_EXECUTION": "10",
            # Overrides infrahubctl's 120s default, which a cold schema load exceeds. See
            # constants.CLIENT_TIMEOUT for why this is set here instead of inherited.
            "INFRAHUB_TIMEOUT": str(c.CLIENT_TIMEOUT),
        }
    )

    log.info("infrahubctl %s", command)
    try:
        return subprocess.run(  # noqa: S602
            f"infrahubctl {command}",
            shell=True,
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=timeout,
            # Pinned to the repository root so `object load objects/...` resolves regardless of where
            # pytest was invoked from.
            cwd=PROJECT_DIRECTORY,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"`infrahubctl {command}` did not finish within {timeout}s") from exc


def assert_ctl_succeeded(result: subprocess.CompletedProcess[str], step: str) -> None:
    """Assert an ``infrahubctl`` invocation succeeded, quoting its output when it did not.

    Args:
        result: The completed ``infrahubctl`` process.
        step: Human-readable description of the step, used in the failure message.

    Raises:
        AssertionError: If the command returned a non-zero exit status.
    """
    assert result.returncode == 0, (
        f"{step} failed (exit {result.returncode}).\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def load_objects(path: str, address: str, branch: str | None = None) -> subprocess.CompletedProcess[str]:
    """Load an object file or directory, on ``main`` unless a branch is given.

    Args:
        path: Repository-relative path passed to ``infrahubctl object load``.
        address: Base URL of the Infrahub server under test.
        branch: Branch to load into. ``None`` loads into the default branch.

    Returns:
        The completed process.
    """
    command = f"object load {path}"
    if branch:
        command += f" --branch {branch}"
    result = infrahubctl(command, address=address)
    assert_ctl_succeeded(result, f"Loading {path}" + (f" into {branch}" if branch else ""))
    return result


# --- polling -------------------------------------------------------------------------------------


async def wait_for(
    check: Callable[[], Awaitable[tuple[bool, T]]],
    description: str,
    timeout: int,
    interval: int = c.DEFAULT_POLL_INTERVAL,
) -> T:
    """Poll ``check`` until it reports done, then return its payload.

    Args:
        check: Coroutine returning ``(done, payload)``. The payload of the final call is returned,
            so a check can hand back the diagnostic state it observed on the way to timing out.
        description: What is being waited for, used in log lines and the timeout message.
        timeout: Total seconds to wait before raising.
        interval: Seconds between attempts.

    Returns:
        The payload from the first call that reported done.

    Raises:
        AssertionError: If ``timeout`` elapses first, or the check raises one. The message includes
            the last payload -- or the last transient error -- observed.
    """
    attempts = max(1, timeout // interval)
    last: Any = None
    for attempt in range(1, attempts + 1):
        try:
            done, last = await check()
        except AssertionError:
            # A check raises AssertionError for a terminal condition it has already diagnosed, such
            # as a repository that failed to import. Retrying that only delays the report.
            raise
        except Exception as exc:  # noqa: BLE001 - see below
            # Anything else is treated as transient. The deployment under test is starting workers,
            # importing a repository and running generators while these polls happen, and it will
            # occasionally answer with a 503 from the load balancer or drop a connection. Aborting the
            # run on the first one turns a momentary blip into a red build on a dependency bump --
            # which is the exact failure mode this suite exists to avoid producing. A genuinely broken
            # deployment keeps failing and still trips the timeout below, with the error attached.
            last = f"{type(exc).__name__}: {exc}"
            log.info("Transient error while waiting for %s (%d/%d): %s", description, attempt, attempts, last)
            await asyncio.sleep(interval)
            continue

        if done:
            return last
        if attempt % 6 == 0 or attempt == 1:
            log.info("Waiting for %s (%d/%d, last=%s)", description, attempt, attempts, last)
        await asyncio.sleep(interval)

    raise AssertionError(f"Timed out after {timeout}s waiting for {description}. Last observed state: {last!r}")


# --- branches ------------------------------------------------------------------------------------


async def ensure_branch(client: InfrahubClient, name: str) -> None:
    """Create ``name`` if it does not exist yet, then assert it is present.

    Args:
        client: Client pointed at the default branch.
        name: Branch name to create.

    Raises:
        AssertionError: If the branch is still absent after the create call.
    """
    branches = await client.branch.all()
    if name not in branches:
        await client.branch.create(name, wait_until_completion=True)

    branches = await client.branch.all()
    assert name in branches, f"Branch {name!r} missing after creation. Present: {sorted(branches)}"


# --- repository and definitions ------------------------------------------------------------------


async def wait_for_repository_sync(client: InfrahubClient, name: str, timeout: int = c.REPO_SYNC_TIMEOUT) -> Any:
    """Wait until a repository reports ``in-sync``, failing fast on an import error.

    Args:
        client: Client pointed at the default branch.
        name: Repository name inside Infrahub.
        timeout: Seconds to wait.

    Returns:
        The synchronized repository node.

    Raises:
        AssertionError: On an import error, or if the repository never reaches ``in-sync``.
    """

    async def check() -> tuple[bool, Any]:
        repository = await client.get(kind="CoreRepository", name__value=name)
        status = repository.sync_status.value
        # An import error is terminal: the repository will not retry on its own, and waiting out
        # the full timeout only delays a failure whose cause is already known.
        assert "error" not in status, (
            f"Repository {name!r} failed to import (sync_status={status!r}). "
            f"A generator, transform or check in .infrahub.yml most likely failed to import."
        )
        # The payload is the status, not the node: it is what the progress logging and the timeout
        # message report, and a node repr just prints the repository name back at you.
        return status == "in-sync", {"sync_status": status}

    await wait_for(check, f"repository {name!r} to sync", timeout=timeout, interval=c.SLOW_POLL_INTERVAL)
    return await client.get(kind="CoreRepository", name__value=name)


async def wait_for_definitions(
    client: InfrahubClient,
    kind: str,
    names: list[str],
    branch: str = "main",
    timeout: int = c.DEFINITION_TIMEOUT,
) -> None:
    """Wait until every named definition has been imported from the repository.

    Args:
        client: Client pointed at the default branch.
        kind: Definition kind, for example ``CoreGeneratorDefinition``.
        names: Definition names that ``.infrahub.yml`` declares.
        branch: Branch the definitions are expected on.
        timeout: Seconds to wait.

    Raises:
        AssertionError: If any definition is still missing when the timeout elapses.
    """
    expected = set(names)

    async def check() -> tuple[bool, Any]:
        found = {node.name.value for node in await client.all(kind=kind, branch=branch)}
        missing = sorted(expected - found)
        return not missing, {"missing": missing, "found": sorted(found)}

    await wait_for(check, f"{kind} definitions {sorted(expected)}", timeout=timeout, interval=c.SLOW_POLL_INTERVAL)


# --- generators ----------------------------------------------------------------------------------


async def run_generator(
    client: InfrahubClient,
    definition_name: str,
    node_ids: list[str],
    branch: str,
    timeout: int = c.GENERATOR_TIMEOUT,
) -> Any:
    """Run a generator definition explicitly and wait for its task to finish.

    This exercises the ``CoreGeneratorDefinitionRun`` mutation, which is the path the CLI and the
    web interface use. The event-driven path -- a trigger rule firing the same generator on object
    creation -- is covered separately by :func:`wait_for_topology`.

    Args:
        client: Client pointed at the default branch.
        definition_name: Generator definition name, for example ``create_dc``.
        node_ids: Target node IDs to run the generator against.
        branch: Branch to run on.
        timeout: Seconds to wait for the task.

    Returns:
        The finished task.

    Raises:
        AssertionError: If the definition is missing, the task does not finish in time, or the
            generator itself failed.
    """
    definition = await client.get(kind="CoreGeneratorDefinition", name__value=definition_name, branch="main")

    mutation = Mutation(
        mutation="CoreGeneratorDefinitionRun",
        input_data={"data": {"id": definition.id, "nodes": node_ids}, "wait_until_completion": False},
        query={"ok": None, "task": {"id": None}},
    )
    response = await client.execute_graphql(query=mutation.render(), branch_name=branch)
    task_id = response["CoreGeneratorDefinitionRun"]["task"]["id"]
    log.info("Generator %s started as task %s on %s", definition_name, task_id, branch)

    task = await client.task.wait_for_completion(id=task_id, timeout=timeout)

    # A generator that crashes part way through leaves a plausible-looking fabric behind: it runs in
    # six phases, so dying in phase 3 still produces the right device count and nothing else. Without
    # this assertion the failure surfaces several tests later as a missing loopback, which is how a
    # KeyError in rack assignment came to look like an addressing bug.
    assert task.state == TaskState.COMPLETED, (
        f"Generator {definition_name!r} finished in state {task.state} on branch {branch!r} "
        f"(task {task_id}).\n{await task_log_tail(client, task_id)}"
    )

    return task


async def expected_role_counts(client: InfrahubClient, design_name: str, branch: str) -> dict[str, int]:
    """Read a design and return how many devices of each role it calls for.

    Deriving the expectation from the design rather than hard-coding it means these tests assert
    the property that actually matters -- the generator honours the design it was given -- and keeps
    working when ``objects/bootstrap/15_designs.yml`` changes.

    Args:
        client: Client pointed at the default branch.
        design_name: Name of the ``DesignTopology`` object.
        branch: Branch to read the design from.

    Returns:
        Mapping of device role to expected device count.

    Raises:
        AssertionError: If the design has no elements.
    """
    design = await client.get(
        kind="DesignTopology",
        name__value=design_name,
        branch=branch,
        include=["elements"],
        prefetch_relationships=True,
    )

    counts: Counter[str] = Counter()
    for element in design.elements.peers:
        counts[element.peer.role.value] += element.peer.quantity.value

    assert counts, f"Design {design_name!r} has no elements; the expectation would be trivially met."
    return dict(counts)


async def actual_role_counts(client: InfrahubClient, topology_name: str, branch: str) -> dict[str, int]:
    """Count the devices a topology owns, grouped by role, across all three device kinds.

    Args:
        client: Client pointed at the default branch.
        topology_name: Name of the topology deployment.
        branch: Branch to count on.

    Returns:
        Mapping of device role to created device count. Roles with no devices are absent.
    """
    counts: Counter[str] = Counter()
    for kind in c.DEVICE_KINDS:
        devices = await client.filters(kind=kind, topology__name__value=topology_name, branch=branch)
        for device in devices:
            counts[device.role.value] += 1
    return dict(counts)


async def wait_for_quiescence(
    client: InfrahubClient,
    branch: str,
    kinds: list[str] | None = None,
    stable_rounds: int = c.QUIESCENCE_ROUNDS,
    timeout: int = c.GENERATOR_TIMEOUT,
) -> dict[str, int]:
    """Wait until a generator stops creating objects on a branch.

    A topology generator works in phases -- devices, racking, cabling, loopbacks, routing -- and the
    event-driven path exposes no task to wait on. Without this, a test that reads the fabric as soon
    as the device count matches the design catches the generator mid-run and fails on a fabric that
    would have been complete moments later. Watching the counts stop moving avoids hard-coding which
    phase happens to run last.

    Args:
        client: Client pointed at the default branch.
        branch: Branch to watch.
        kinds: Kinds whose counts to watch. Defaults to :data:`constants.FABRIC_ACTIVITY_KINDS`.
        stable_rounds: Consecutive identical polls required.
        timeout: Seconds to wait.

    Returns:
        The final counts per kind.

    Raises:
        AssertionError: If the counts never settle.
    """
    watched = kinds or c.FABRIC_ACTIVITY_KINDS
    history: list[dict[str, int]] = []

    async def check() -> tuple[bool, Any]:
        snapshot = {kind: await client.count(kind=kind, branch=branch) for kind in watched}
        history.append(snapshot)
        recent = history[-stable_rounds:]
        settled = len(recent) == stable_rounds and all(entry == recent[0] for entry in recent)
        return settled, snapshot

    return await wait_for(
        check,
        f"object creation on {branch} to settle for {stable_rounds} consecutive polls",
        timeout=timeout,
        interval=c.SLOW_POLL_INTERVAL,
    )


async def wait_for_topology(
    client: InfrahubClient,
    topology_name: str,
    design_name: str,
    branch: str,
    timeout: int = c.GENERATOR_TIMEOUT,
) -> dict[str, int]:
    """Wait until a topology matches its design and the generator has finished, then return counts.

    Used for both generator paths. Two gates, because they prove different things: matching the design
    proves the right generator ran, and quiescence proves it ran to completion. Callers can then
    assert on a settled fabric instead of racing the remaining phases.

    Args:
        client: Client pointed at the default branch.
        topology_name: Name of the topology deployment.
        design_name: Name of the design it was built from.
        branch: Branch the topology lives on.
        timeout: Seconds to wait for each gate.

    Returns:
        The per-role device counts once they match the design.

    Raises:
        AssertionError: If the counts never match the design, or the generator never settles.
    """
    expected = await expected_role_counts(client, design_name=design_name, branch=branch)

    async def check() -> tuple[bool, Any]:
        actual = await actual_role_counts(client, topology_name=topology_name, branch=branch)
        return actual == expected, {"expected": expected, "actual": actual}

    await wait_for(
        check,
        f"{topology_name} devices to match design {design_name!r} ({expected})",
        timeout=timeout,
        interval=c.SLOW_POLL_INTERVAL,
    )

    settled = await wait_for_quiescence(client, branch=branch, timeout=timeout)
    log.info("Generator for %s settled at %s", topology_name, settled)

    return expected


# --- proposed changes ----------------------------------------------------------------------------


async def update_diff(client: InfrahubClient, branch: str, timeout: int = c.DIFF_TIMEOUT) -> Any:
    """Compute the diff for a branch and wait for the task to finish.

    Args:
        client: Client pointed at the default branch.
        branch: Branch to diff against its parent.
        timeout: Seconds to wait for the task.

    Returns:
        The finished task.

    Raises:
        AssertionError: If the diff task does not complete.
    """
    mutation = Mutation(
        mutation="DiffUpdate",
        input_data={"data": {"name": f"diff-for-{branch}", "branch": branch, "wait_for_completion": False}},
        query={"ok": None, "task": {"id": None}},
    )
    response = await client.execute_graphql(query=mutation.render())
    task_id = response["DiffUpdate"]["task"]["id"]
    task = await client.task.wait_for_completion(id=task_id, timeout=timeout)

    assert task.state == TaskState.COMPLETED, (
        f"Diff for branch {branch!r} finished in state {task.state} (task {task_id})."
    )
    return task


async def create_proposed_change(
    client: InfrahubClient,
    name: str,
    source_branch: str,
    destination_branch: str = "main",
) -> str:
    """Open a proposed change and return its ID.

    Args:
        client: Client pointed at the default branch.
        name: Name for the proposed change.
        source_branch: Branch holding the changes.
        destination_branch: Branch to merge into.

    Returns:
        The ID of the created proposed change.
    """
    mutation = Mutation(
        mutation="CoreProposedChangeCreate",
        input_data={
            "data": {
                "name": {"value": name},
                "source_branch": {"value": source_branch},
                "destination_branch": {"value": destination_branch},
            }
        },
        query={"ok": None, "object": {"id": None}},
    )
    response = await client.execute_graphql(query=mutation.render())
    pc_id = response["CoreProposedChangeCreate"]["object"]["id"]
    log.info("Proposed change %r created as %s (%s -> %s)", name, pc_id, source_branch, destination_branch)
    return str(pc_id)


async def wait_for_validations(
    client: InfrahubClient,
    pc_id: str,
    timeout: int = c.VALIDATION_TIMEOUT,
    stable_rounds: int = c.QUIESCENCE_ROUNDS,
) -> list[Any]:
    """Wait until every validator on a proposed change has finished, then return them.

    Completion is asserted; the conclusions are returned for the caller to judge. A validator that
    fails is a finding about the demo data, whereas a validator that never completes is a finding
    about Infrahub -- these tests need to tell those apart.

    Args:
        client: Client pointed at the default branch.
        pc_id: ID of the proposed change.
        timeout: Seconds to wait.
        stable_rounds: Consecutive polls the validator count must hold steady.

    Returns:
        The finished validator nodes.

    Raises:
        AssertionError: If no validator ever appears, or one never completes.
    """

    # Validators are created progressively as Infrahub works out what the change touches: the data
    # validator appears almost immediately, then artifact validators, generator validators, the
    # repository validator and finally the user checks. Returning as soon as "every validator seen so
    # far is complete" therefore returns after the first one, long before the rest exist -- and the
    # caller merges a proposed change whose checks have not run. Observed: 1 validator reported, 21
    # actually created, one of them failing, and the merge refused for "failing checks" a moment
    # later. So the set has to stop growing as well as finish.
    history: list[int] = []

    async def check() -> tuple[bool, Any]:
        pc = await client.get(
            kind="CoreProposedChange",
            id=pc_id,
            include=["validations"],
            exclude=["reviewers", "approved_by", "created_by"],
            prefetch_relationships=True,
            populate_store=True,
        )
        validators = [validation.peer for validation in pc.validations.peers]
        if not validators:
            return False, {"validators": 0}

        history.append(len(validators))
        recent = history[-stable_rounds:]
        settled = len(recent) == stable_rounds and len(set(recent)) == 1

        states = Counter(validator.state.value for validator in validators)
        done = all(validator.state.value == "completed" for validator in validators)
        return (done and settled), {"states": dict(states), "count": len(validators)}

    await wait_for(
        check,
        f"validators on proposed change {pc_id} to complete and stop appearing",
        timeout=timeout,
        interval=c.SLOW_POLL_INTERVAL,
    )

    pc = await client.get(
        kind="CoreProposedChange",
        id=pc_id,
        include=["validations"],
        exclude=["reviewers", "approved_by", "created_by"],
        prefetch_relationships=True,
        populate_store=True,
    )
    return [validation.peer for validation in pc.validations.peers]


def summarize_validators(validators: list[Any]) -> str:
    """Render validators as ``label: conclusion`` lines for use in assertion messages.

    Args:
        validators: Validator nodes.

    Returns:
        A newline-separated summary, indented for embedding in a multi-line message.
    """
    lines = []
    for validator in validators:
        label = validator.label.value if validator.label.value else validator.id
        lines.append(f"    {label}: state={validator.state.value} conclusion={validator.conclusion.value}")
    return "\n".join(lines) or "    (none)"


async def task_log_tail(client: InfrahubClient, task_id: str, lines: int = 10) -> str:
    """Fetch the last log lines of a task, for embedding in a failure message.

    Infrahub reports *why* a task failed only in its logs, so without this a failed merge surfaces as
    a bare state with no cause -- which is the difference between a CI failure that explains itself
    and one that needs a local reproduction.

    Args:
        client: Client pointed at the default branch.
        task_id: ID of the task.
        lines: How many trailing log lines to include.

    Returns:
        Indented log lines, or a note explaining why none could be read.
    """
    query = """
    query TaskLogs($id: String!) {
      InfrahubTask(ids: [$id]) {
        edges { node { state logs { edges { node { message severity } } } } }
      }
    }
    """
    try:
        result = await client.execute_graphql(query=query, variables={"id": task_id})
        edges = result["InfrahubTask"]["edges"]
        if not edges:
            return f"    (task {task_id} not found)"
        entries = edges[0]["node"]["logs"]["edges"]
        return "\n".join(f"    {e['node']['severity']}: {e['node']['message']}" for e in entries[-lines:])
    except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the original failure
        return f"    (could not read task logs: {exc})"


async def failing_check_details(client: InfrahubClient, validators: list[Any], limit: int = 6) -> str:
    """Describe the individual checks behind validators that did not conclude successfully.

    A validator's conclusion says *that* something failed; only its checks say *what*. The conflict
    payload on a check names the schema path or the data element at fault, which is the difference
    between "Schema Integrity failed" and a report someone can act on.

    Args:
        client: Client pointed at the default branch.
        validators: Validators to inspect. Successful ones are ignored.
        limit: Maximum number of checks to describe per validator.

    Returns:
        Indented lines describing the failing checks, or a note that there were none.
    """
    lines: list[str] = []
    for validator in validators:
        if validator.conclusion.value == "success":
            continue
        label = validator.label.value or validator.id
        try:
            reread = await client.get(kind=str(validator.typename), id=validator.id, include=["checks"])
            checks = list(reread.checks.peers)
        except Exception as exc:  # noqa: BLE001 - diagnostics must not mask the original failure
            lines.append(f"    {label}: (could not read checks: {exc})")
            continue

        lines.append(f"    {label} ({len(checks)} check(s)):")
        for peer in checks[:limit]:
            check = await client.get(kind=str(peer.typename), id=peer.id)
            conclusion = check.conclusion.value
            if conclusion == "success":
                continue
            conflicts = getattr(check, "conflicts", None)
            detail = conflicts.value if conflicts is not None else check.message.value
            lines.append(f"      {check.kind.value}/{check.severity.value}: {str(detail)[:400]}")

    return "\n".join(lines) or "    (no failing checks recorded)"


async def merge_proposed_change(
    client: InfrahubClient,
    pc_id: str,
    validators: list[Any] | None = None,
    timeout: int = c.MERGE_TIMEOUT,
) -> str:
    """Merge a proposed change, asserting it landed, and return the state it settled in.

    The proposed change's own state is the authority on whether the merge landed: the merge task can
    report a failure from post-merge bookkeeping after the data has already moved.

    When it did not land, the message carries the merge task's own logs and the validator conclusions.
    Infrahub refuses to merge a proposed change with failing checks, so the validator that failed is
    almost always the answer, and reading it out of the assertion beats going back to the container.

    Args:
        client: Client pointed at the default branch.
        pc_id: ID of the proposed change.
        validators: Validators observed before merging, quoted in the failure message.
        timeout: Seconds to wait for the merge task.

    Returns:
        The proposed change state after the merge task finished.

    Raises:
        AssertionError: If the proposed change is not ``merged`` or ``closed`` afterwards.
    """
    mutation = Mutation(
        mutation="CoreProposedChangeMerge",
        input_data={"data": {"id": pc_id}, "wait_until_completion": False},
        query={"ok": None, "task": {"id": None}},
    )
    response = await client.execute_graphql(query=mutation.render())
    task_id = response["CoreProposedChangeMerge"]["task"]["id"]
    task = await client.task.wait_for_completion(id=task_id, timeout=timeout)
    log.info("Merge task %s finished in state %s", task_id, task.state)

    pc = await client.get(kind="CoreProposedChange", id=pc_id)
    state = str(pc.state.value)

    if state not in ("merged", "closed"):
        failing = [v for v in (validators or []) if v.conclusion.value != "success"]
        raise AssertionError(
            f"Proposed change {pc_id} is in state {state!r} after merging (expected 'merged').\n"
            f"  Merge task {task_id} finished in state {task.state}.\n"
            f"  Merge task logs:\n{await task_log_tail(client, task_id)}\n"
            f"  Validators that did not conclude successfully ({len(failing)}):\n"
            f"{summarize_validators(failing)}\n"
            f"  What those validators actually objected to:\n"
            f"{await failing_check_details(client, failing)}\n"
            f"  All validators:\n{summarize_validators(validators or [])}"
        )

    return state


# --- artifacts -----------------------------------------------------------------------------------


async def generate_artifacts(client: InfrahubClient, definition_name: str, branch: str = "main") -> None:
    """Trigger generation of every artifact for a definition.

    Args:
        client: Client pointed at the default branch.
        definition_name: Name of the ``CoreArtifactDefinition``.
        branch: Branch to generate on.
    """
    definition = await client.get(kind="CoreArtifactDefinition", name__value=definition_name, branch=branch)
    await definition.generate()
    log.info("Requested generation of artifacts for %r on %s", definition_name, branch)


async def artifact_target_members(client: InfrahubClient, definition_name: str, branch: str = "main") -> list[Any]:
    """List the objects an artifact definition targets.

    One artifact is expected per member, so this is what turns "some artifacts appeared" into a
    countable expectation.

    Args:
        client: Client pointed at the default branch.
        definition_name: Name of the ``CoreArtifactDefinition``.
        branch: Branch to read on.

    Returns:
        The members of the definition's target group.
    """
    definition = await client.get(
        kind="CoreArtifactDefinition",
        name__value=definition_name,
        branch=branch,
        include=["targets"],
        prefetch_relationships=True,
    )
    target = definition.targets.peer
    assert target, f"Artifact definition {definition_name!r} has no target group."

    # Read the group back through its own concrete kind rather than assuming CoreStandardGroup: the
    # demo uses standard groups today, but the relationship's peer is the CoreGroup generic.
    group = await client.get(
        kind=str(target.typename),
        id=target.id,
        branch=branch,
        include=["members"],
    )
    return list(group.members.peers)


async def wait_for_artifacts(
    client: InfrahubClient,
    definition_name: str,
    branch: str = "main",
    expected: int | None = None,
    timeout: int = c.ARTIFACT_TIMEOUT,
) -> list[Any]:
    """Wait until a definition has one settled artifact per target, then return them.

    The expected count defaults to the size of the definition's target group. Waiting for "at least
    one" instead would return as soon as the first artifact lands: the rest have not been created yet,
    so nothing is in a transient state and the wait looks satisfied. The test then passes having
    inspected a fraction of the artifacts -- which is worse than failing, because it reads as coverage
    that is not there. (Observed: 2 of 6 leaf configs.)

    Args:
        client: Client pointed at the default branch.
        definition_name: Name of the ``CoreArtifactDefinition``.
        branch: Branch the artifacts live on.
        expected: Number of artifacts to wait for. Defaults to the target group's member count.
        timeout: Seconds to wait.

    Returns:
        The artifact nodes.

    Raises:
        AssertionError: If too few artifacts appear, or any settles in a non-ready state.
    """
    if expected is None:
        expected = len(await artifact_target_members(client, definition_name=definition_name, branch=branch))
        log.info("Definition %r targets %d object(s)", definition_name, expected)

    async def check() -> tuple[bool, Any]:
        # Looked up inside the poll rather than once up front, so a momentary GraphQL error on this
        # read is retried like any other. Reads placed before the loop get no such protection, and a
        # busy deployment answers with the occasional error even when it is perfectly healthy.
        definition = await client.get(kind="CoreArtifactDefinition", name__value=definition_name, branch=branch)
        artifacts = await client.filters(kind="CoreArtifact", definition__ids=[definition.id], branch=branch)
        states = Counter(artifact.status.value for artifact in artifacts)
        settled = all(artifact.status.value.lower() not in c.TRANSIENT_ARTIFACT_STATES for artifact in artifacts)
        return (len(artifacts) >= expected and settled), {"count": len(artifacts), "states": dict(states)}

    await wait_for(
        check,
        f"{expected} settled artifact(s) for {definition_name!r} on {branch}",
        timeout=timeout,
        interval=c.SLOW_POLL_INTERVAL,
    )

    definition = await client.get(kind="CoreArtifactDefinition", name__value=definition_name, branch=branch)
    artifacts = await client.filters(kind="CoreArtifact", definition__ids=[definition.id], branch=branch)
    failed = [artifact for artifact in artifacts if artifact.status.value.lower() != "ready"]
    assert not failed, (
        f"{len(failed)} of {len(artifacts)} artifacts for {definition_name!r} are not ready: "
        + ", ".join(f"{artifact.name.value}={artifact.status.value}" for artifact in failed)
    )
    return artifacts


async def read_artifact(client: InfrahubClient, artifact: Any) -> str:
    """Fetch an artifact's rendered content from the object store.

    Args:
        client: Client pointed at the default branch.
        artifact: A ``CoreArtifact`` node.

    Returns:
        The stored content.

    Raises:
        AssertionError: If the artifact has no ``storage_id``, or the stored content is empty.
    """
    storage_id = artifact.storage_id.value
    assert storage_id, f"Artifact {artifact.name.value!r} is ready but has no storage_id."

    content = await client.object_store.get(identifier=storage_id)
    assert content.strip(), f"Artifact {artifact.name.value!r} rendered to empty content."
    return content
