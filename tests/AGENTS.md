# AGENTS.md - Tests

> Guidance for AI coding assistants working in the `tests/` directory.
> Parent: [../AGENTS.md](../AGENTS.md)

## Overview

Three suites, distinguished by what they need to run:

| Suite | Needs | Runtime |
| ----- | ----- | ------- |
| `tests/unit/` | Nothing. Files on disk and mocks. | Under a second |
| `tests/smoke/` | Nothing. Infrahub SDK pytest plugin specs. | Under a second |
| `tests/integration/` | Docker. Starts a real Infrahub deployment. | 30 min (core) to 2 h (full) |

The integration suite does **not** use `invoke start`. It starts its own throwaway deployment through
`infrahub-testcontainers`, so it never touches a local development instance.

## Test Commands

CI runs these through `invoke` (`.github/workflows/ci.yml`), so the same commands reproduce CI
locally:

```bash
# Everything, unit and integration (starts a real Infrahub deployment)
uv run invoke test

# No container needed - seconds, not minutes
uv run invoke test-unit

# The tier every pull request runs
uv run invoke test-integration            # --tier=core (default)

# The tier an Infrahub version bump also runs
uv run invoke test-integration --tier=full

# One integration module, or any other pytest option the tasks above do not expose
uv run pytest tests/integration/test_10_dc_workflow.py -vv --log-cli-level=INFO
```

## Directory Structure

```text
tests/
├── conftest.py                    # Path fixtures only; no container
├── unit/                          # Fast, isolated
│   ├── test_*.py
│   ├── test_j2_transforms.yml     # SDK plugin: Jinja2 transform specs
│   └── simulators/                # Mock data
├── smoke/
│   └── test_graphql.yml           # SDK plugin: syntax check per .infrahub.yml query
└── integration/
    ├── conftest.py                # Session-scoped deployment + bootstrap
    ├── constants.py               # Timeouts, object paths, expectations
    ├── helpers.py                 # Workflow helpers (load, generate, review, merge)
    ├── repo_source.py             # Pruned repo copy for Infrahub to clone
    └── test_<NN>_<workflow>.py    # One module per workflow, run in file-name order
```

## The integration suite

### One deployment, shared

`integration/conftest.py` declares the deployment at **session** scope, so the whole suite starts one
Infrahub stack and bootstraps it once. That is deliberate: `TestInfrahubDocker` from
`infrahub-testcontainers` scopes its fixtures per class, which would mean a fresh stack and a fresh
schema load for every workflow and would put most of this coverage out of reach of the CI timeout.

The trade-off is that modules are **not independent**. They run in file-name order and share state:

```text
test_00_bootstrap        schema, menu, objects, repository, event actions
test_10_dc_workflow      Arista DC -> generator -> proposed change -> merge to main
test_20_artifacts        artifacts over the merged fabric
test_30_graphql          every registered query, executed against the live schema
test_40_dc_vendors       second DC (Cisco, with border leafs) on its own branch
test_50_pop              POP topology, virtual devices
test_60_segment          network segment service over the merged Arista fabric
test_70_day2             device edit, then scale the fabric out
test_80_proposed_change  conflict detection, closing a proposed change
```

Consequences to respect when editing:

- **Do not randomise or reorder collection.** No `pytest-randomly`, no `-p no:cacheprovider` tricks.
- **Give each module its own branch.** Never do exploratory writes to `main` except in
  `test_80_proposed_change.py`, which runs last for that reason.
- **Declare cross-module dependencies.** A module that needs the merged fabric marks it:
  `pytest.mark.dependency(depends=["dc_merged"], scope="session")`, or calls
  `pytest_dependency.depends(request, ["dc_merged"], scope="session")` when it also has an
  intra-module dependency.
- **Mark a file-only test `offline`.** The autouse `bootstrapped_deployment` fixture pulls the whole
  deployment into scope for every integration test, so a check that only reads files from the
  repository needs `pytest.mark.offline` to stay runnable without Docker.

### Tiers

Modules are marked `core` or `extended`:

- `core` runs on every pull request.
- `extended` runs on a pull request whose branch bumps the Infrahub version (`update-infrahub-*`,
  but not `update-infrahub-sdk-*`), and on a manual run of the CI workflow with its `tier` input set
  to `full`. The `scope` step in `.github/workflows/ci.yml` picks the tier.

The extended tier runs under `continue-on-error` and so does not block a merge, and that is a
**temporary concession**, not the design. A version bump is the change most likely to break a
workflow and the least likely to be caught by reading the diff, so it is exactly what this tier
exists to cover. It cannot block while the upstream fault below stands: an intermittent failure
would leave every bump pull request red, which trains everyone to ignore it. Delete the
`continue-on-error` line in `ci.yml` once that fault is fixed.

Dispatch the tier deliberately when changing a generator or a transform, and read any failure
against the known fault before concluding the demo is broken.

Keep `core` self-sufficient. It must pass with `-m "not extended"`, so it may not depend on anything
an extended module produces.

### Writing an integration test

Use the helpers rather than raw GraphQL. They already carry the polling and the failure messages:

```python
from . import constants as c
from . import helpers as h

pytestmark = pytest.mark.extended


@pytest.mark.dependency(name="my_thing_loaded")
async def test_01_load(async_client_main, infrahub_address, infrahub_bootstrap) -> None:
    """One sentence on the property under test."""
    await h.ensure_branch(async_client_main, "my-branch")
    h.load_objects("objects/dc/dc-juniper-s.yml", address=infrahub_address, branch="my-branch")
```

Conventions that matter here more than in the unit suite:

1. **Derive expectations from the data model, not from literals.** `h.expected_role_counts` reads a
   design and returns what it calls for; `h.artifact_target_members` reads an artifact definition's
   target group. A hard-coded `assert len(devices) == 12` breaks whenever
   `objects/bootstrap/15_designs.yml` changes and asserts nothing about the generator.
2. **Poll through `h.wait_for`.** Never `asyncio.sleep` a fixed duration and hope. The timeout message
   includes the last state observed, which is usually the whole diagnosis. It also retries through
   transient errors, so a single 503 from a busy deployment does not end the run.
3. **Never treat "the first result appeared" as "the work finished".** This is the mistake this suite
   is most prone to, and the expensive kind is the one that *passes*. Every asynchronous step here
   produces results progressively:

   | Step | What arrives first | What the whole set is |
   | ---- | ------------------ | --------------------- |
   | A topology generator | devices (phase 3 of 6) | + racking, cabling, loopbacks, routing |
   | Artifact generation | one artifact | one per target-group member |
   | Proposed-change review | the data validator | ~21 validators incl. artifacts, checks, repository |

   Waiting for "all of what exists is done" is satisfied immediately in every one of those cases. Use
   `h.wait_for_quiescence` (counts must hold steady across `QUIESCENCE_ROUNDS` polls) for generators,
   pass an `expected` count to `h.wait_for_artifacts`, and rely on `h.wait_for_validations` requiring
   the validator count to settle. Getting this wrong once produced a green run that had checked 2 of
   6 artifacts, and another that merged with 1 of 21 validators complete.
4. **Say what failed in the assertion message.** These tests fail in CI, on a machine nobody can log
   into, so `assert devices` is close to useless. Include the branch, the expectation and what was
   found. `h.merge_proposed_change` shows the shape: it quotes the merge task's own logs and the
   validators that did not conclude successfully.
5. **Assert completion and conclusion separately.** A validator that never finishes is an Infrahub
   problem; a validator that finishes and fails is a demo-data problem. Do not collapse the two.
6. **Check structure, not just counts.** A generator can create the right number of devices and leave
   them unaddressed. Assert the addressing, cabling and peering that make a fabric usable.

### Diagnosing a failure

Container logs for `infrahub-server` and `task-worker` are attached as a warning at the end of any
run that had a failure, so the CI log already contains them. Locally, add `--log-cli-level=INFO` to
see each step as it happens.

Before concluding that a failure is a defect in the demo or in Infrahub, check how the deployment was
resourced. The stack defaults to two API servers and two task workers; running it trimmed down, or
alongside another Infrahub stack on the same host, produces load-shedding that looks like product
failure — 503s from the load balancer, and schema-constraint checks concluding `failure` on a branch
that is actually fine. Reproduce at default sizing before filing anything.

`Unable to find the class <Name>` in a check or transform failure is **not** an import error.
Infrahub wraps any exception raised inside a check or transform in `CheckError`/`TransformError`
carrying that message, so the class name in the message is a red herring. The real cause is the
`AttributeError`, `KeyError` or `TypeError` in the traceback above it in the `task-worker` log.

### Defects this coverage found

Three demo defects surfaced the first time the extended tier ran, all of them latent because nothing
exercised the path. They are fixed, and they are worth reading as a guide to what this suite is for:

| Defect | Cause | Why nothing caught it |
| ------ | ----- | --------------------- |
| POP merge blocked: the `edge_config` artifact and `validate_edge` check both concluded `failure` | `queries/config/edge.gql` queried `DcimDevice`, but POP edges are `DcimVirtualDevice` and still join the `edges` group this artifact targets (`generators/common.py`: `group_name = f"{role}s"`). The query matched nothing, `get_data()` returned `[]`, and the caller called `.get()` on a list. | Only DC edges, which are physical, had ever been rendered |
| Scaling a deployment out crashed the generator with `KeyError: 0` in `assign_devices_to_racks` | `middle_start` is `(total_racks // 2) - (middle_device_count // 2)`, which goes negative when a design has more infrastructure devices than leaf racks. The Arista and Sonic "with border leafs" designs place 6 into 4, centering the range on the nonexistent rack 0. | Both broken designs were unused; the Cisco equivalents have 8 leaf racks and land inside the row |
| `create_segment` never ran from its trigger rule | `objects/segments/segment-opsmill.yml` did not join the `network_segments` group that the generator definition targets, so Infrahub refused: `Target ... is not part of the group`. DC and POP objects declare their group; the segment did not. | The segment service had no test |

Two of these produced *plausible* wreckage rather than an obvious error, which is the lesson. The
generator crash happened in phase 3 of 6, so the device count still matched the design and only the
loopbacks created in phase 5 were missing — the failure surfaced two assertions later as an
addressing bug. `h.run_generator` now asserts the task concluded `COMPLETED` for that reason: check
that the work succeeded before checking what it produced.

### The one failure that is not ours

`test_60_segment::test_05_segment_merges_to_main` is marked `xfail`. Infrahub runs `create_dc` as a
check on that proposed change, because the segment's diff touches the DC fabric, and the run fails on
Infrahub's own bookkeeping: `CoreGeneratorGroupUpsert` reports `NODE_NOT_FOUND` for a
`CoreGeneratorGroup` or `CoreGraphQLQueryGroup` node. The same fault appears as an HTTP 500 on
`CoreGeneratorGroup(...).members`, and as a `KeyError` from `query_peers` in `core/manager.py`.

Nothing in this repository creates or deletes those nodes, so there is nothing here to fix. Two
things follow for anyone touching this:

- The marker is **not** `strict`, so the test reports `XPASS` as soon as Infrahub resolves those nodes
  correctly. That is the signal to delete the marker — check for it before assuming the bug is live.
- Do not generalise the marker. It is on one test because one workflow reaches that code path. If a
  second test starts failing the same way, confirm the signature in the `task-worker` log first; a
  merge blocked by a *demo* data problem looks similar from the outside and must not be waved through.

### An intermittent failure in the core tier

`test_10_dc_workflow::test_04_fabric_is_complete` occasionally fails in CI with
`Expected at least 8 cables for a 2x4 spine-leaf mesh, found 0` and passes on rerun. It was seen
twice on 2026-08-21, once on a commit and once on a tree without it, with a pass on the same
feature history in between - so before blaming a change, check whether the failure reproduces.
`test_03`'s two gates (device counts plus task quiescence) both pass, which means `create_dc`
completed its device phases while its cabling phase either had not landed or failed silently.
The blind spot that keeps the root cause invisible: the DC generator logs batch creation
failures at DEBUG (`generators/common.py`), so a failed cable batch looks like a successful run
in the CI log. Rerun the job to confirm the flake; the durable fix is raising those failures to
errors so the next occurrence shows its cause.

### Authentication

Clients get their token from the `infrahub_api_token` fixture, which reads the token
`infrahub-testcontainers` seeds the deployment with. Do not rely on `INFRAHUB_API_TOKEN` being set in
the environment: `Config` will pick it up silently, which is how the suite used to authenticate in CI
and nowhere else.

## Unit tests

Fast and isolated. Mock every external dependency:

```python
from unittest.mock import AsyncMock, MagicMock

def test_my_function() -> None:
    """Test description explaining what is being tested."""
    client = MagicMock()
    client.execute_graphql = AsyncMock(return_value={"data": {}})
    assert my_function(client) == expected
```

Store mock GraphQL responses in `tests/unit/simulators/`.

## SDK plugin specs

`test_*.yml` files with an `infrahub_tests` key are collected by the Infrahub SDK's pytest plugin,
not by any code in this repository. `tests/smoke/test_graphql.yml` holds one `graphql-query-smoke`
entry per query in `.infrahub.yml`; keep the two in step, since
`tests/integration/test_30_graphql.py::test_every_declared_query_has_a_subject` fails when they drift.

## Test Requirements

1. **Type hints required** on every test and fixture signature
2. **Docstrings required**, Google-style, saying what property is asserted
3. **Descriptive names** — `test_generator_rerun_is_idempotent`, not `test_generator_2`
4. **Run the linters** — `uv run invoke lint` covers ruff, mypy, yamllint and rumdl

## Common Pitfalls

1. **Adding a class-scoped container fixture** — it would start a second Infrahub stack. Use the
   session fixtures in `integration/conftest.py`.
2. **Writing to `main` from an integration module** — it perturbs every module that runs after it.
3. **Hard-coded sleeps** — use `h.wait_for`.
4. **A new module without a `core`/`extended` marker** — it lands in the core tier by default and
   slows down every pull request.
5. **Hardcoded paths** — use the `root_dir` fixture.
6. **Slow tests in `unit/`** — move them to `integration/` if they need a real Infrahub.
