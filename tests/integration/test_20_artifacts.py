"""Artifact generation over the merged fabric.

Artifacts are where the demo's value actually lands: the point of modelling a fabric is to render
device configuration, a containerlab topology and a cabling matrix out of it. They are also the part
most sensitive to an Infrahub upgrade, because generating one crosses every subsystem at once -- a
GraphQL query, a Python or Jinja2 transform executed inside the task worker, and the object store.

Each test generates one definition's artifacts, waits for them to settle, and reads the rendered
content back. Reading it back is the part that matters: an artifact can reach ``Ready`` while
rendering to an empty file, and an empty configuration is indistinguishable from a working one until
someone tries to deploy it. Content assertions stay deliberately shallow -- a marker or two that
proves the transform saw real fabric data -- because pinning exact configuration text would turn
every template edit into a test failure.
"""

from __future__ import annotations

import csv
import io
import json
import logging

import pytest
from infrahub_sdk import InfrahubClient

from . import constants as c
from . import helpers as h

pytestmark = [pytest.mark.core, pytest.mark.dependency(depends=["dc_merged"], scope="session")]

log = logging.getLogger(__name__)

TOPOLOGY = c.DC_ARISTA_NAME


async def _generate_and_read(client: InfrahubClient, definition: str) -> list[tuple[str, str]]:
    """Generate a definition's artifacts and return ``(name, content)`` for each.

    The wait expects one artifact per target, so the returned list covers every target rather than
    whichever artifacts happened to exist first.

    Args:
        client: Client pointed at the default branch.
        definition: Name of the ``CoreArtifactDefinition``.

    Returns:
        One ``(artifact name, rendered content)`` pair per artifact.
    """
    targets = await h.artifact_target_members(client, definition_name=definition)

    await h.generate_artifacts(client, definition_name=definition)
    artifacts = await h.wait_for_artifacts(client, definition_name=definition, expected=len(targets))
    log.info("Definition %r produced %d artifact(s) for %d target(s)", definition, len(artifacts), len(targets))

    return [(artifact.name.value, await h.read_artifact(client, artifact)) for artifact in artifacts]


@pytest.mark.parametrize(
    ("definition", "expected_in_content"),
    [
        ("leaf_config", "interface"),
        ("spine_config", "interface"),
        ("borderleaf_config", "interface"),
    ],
)
async def test_device_configs_render(
    async_client_main: InfrahubClient,
    definition: str,
    expected_in_content: str,
) -> None:
    """Device configuration artifacts render with interface configuration in them.

    The target groups are populated by the generator, so an empty result here means the fabric never
    joined its groups -- which is a different failure from a transform raising, and worth telling
    apart. A definition whose target group is legitimately empty is skipped rather than failed.
    """
    client = async_client_main
    targets = await h.artifact_target_members(client, definition_name=definition)
    if not targets:
        pytest.skip(f"Artifact definition {definition!r} has no target-group members on main.")

    rendered = await _generate_and_read(client, definition)
    assert len(rendered) >= len(targets), (
        f"{definition!r} targets {len(targets)} object(s) but only {len(rendered)} artifact(s) exist."
    )

    for name, content in rendered:
        assert expected_in_content in content.lower(), (
            f"Artifact {name!r} from {definition!r} rendered {len(content)} characters without "
            f"{expected_in_content!r} anywhere in it. First 500 characters:\n{content[:500]}"
        )


async def test_openconfig_leaf_artifact_is_valid_json(async_client_main: InfrahubClient) -> None:
    """The OpenConfig leaf artifact is declared as JSON and parses as JSON.

    Its content type is ``application/json``, so a malformed render is a contract break rather than
    a cosmetic one -- anything consuming it will fail to parse.
    """
    rendered = await _generate_and_read(async_client_main, "openconfig_leaf_config")

    for name, content in rendered:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"Artifact {name!r} is declared application/json but does not parse: {exc}\n"
                f"First 500 characters:\n{content[:500]}"
            ) from exc
        assert parsed, f"Artifact {name!r} parsed to an empty JSON document."


async def test_containerlab_topology_artifact(async_client_main: InfrahubClient) -> None:
    """The containerlab topology artifact names the fabric's devices.

    This is the artifact ``uv run invoke containerlab`` consumes, so it also stands in for the
    emulation path documented in ``docs/docs/containerlab-deployment.mdx``.
    """
    client = async_client_main
    rendered = await _generate_and_read(client, "Containerlab Topology")

    devices = await client.filters(kind="DcimDevice", topology__name__value=TOPOLOGY, branch="main")
    fabric_names = [device.name.value for device in devices if device.role.value in ("spine", "leaf", "border_leaf")]
    assert fabric_names, f"No fabric devices in {TOPOLOGY} on main to look for in the topology file."

    combined = "\n".join(content for _, content in rendered)
    missing = [name for name in fabric_names if name not in combined]
    assert not missing, (
        f"Containerlab topology artifact does not mention {len(missing)} fabric device(s): {sorted(missing)}"
    )


async def test_cabling_artifact_is_a_populated_csv(async_client_main: InfrahubClient) -> None:
    """The cable matrix artifact is parseable CSV with one row per cable.

    Its content type is ``text/csv``, and the cabling matrix is what a field engineer patches from,
    so an unparseable or empty file is a real defect rather than a cosmetic one.
    """
    client = async_client_main
    rendered = await _generate_and_read(client, "Cable matrix for Topology")

    for name, content in rendered:
        rows = list(csv.reader(io.StringIO(content)))
        assert len(rows) > 1, f"Artifact {name!r} has {len(rows)} CSV row(s); expected a header plus cable rows."

        widths = {len(row) for row in rows if row}
        assert len(widths) == 1, f"Artifact {name!r} has ragged CSV rows (column counts seen: {sorted(widths)})."


async def test_rack_elevation_artifact_is_svg(async_client_main: InfrahubClient) -> None:
    """The rack elevation artifact renders SVG.

    Declared as ``image/svg+xml``, and produced by a Python transform rather than a template, so it
    exercises a different code path from the Jinja2 config artifacts.
    """
    rendered = await _generate_and_read(async_client_main, "rack_elevation")

    for name, content in rendered:
        assert "<svg" in content.lower(), (
            f"Artifact {name!r} is declared image/svg+xml but has no <svg> element. "
            f"First 300 characters:\n{content[:300]}"
        )
