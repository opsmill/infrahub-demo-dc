"""Timeouts, names and expectations shared by the integration suite.

Every duration here is a ceiling, not a target. The helpers in :mod:`tests.integration.helpers`
poll and return as soon as their condition holds, so a generous timeout costs nothing while the
system under test is healthy. They exist because an Infrahub or testcontainers upgrade can make
an operation slower without breaking it, and a test that hangs until the CI job is killed is far
harder to diagnose than one that fails naming what it waited for.

The object paths and group names are the ones the README and the user walkthrough tell a human to
use. Keeping them in one place means a rename in ``objects/`` surfaces as a single edit here rather
than as a scatter of broken string literals.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent.parent
"""Repository root. Every path the suite passes to ``infrahubctl`` is relative to it."""

# --- polling -------------------------------------------------------------------------------------

CLIENT_TIMEOUT = 600
"""Per-request timeout handed to every SDK client and ``infrahubctl`` invocation, in seconds.

Set explicitly rather than left to `INFRAHUB_TIMEOUT` in the environment. The SDK defaults to 60s and
``infrahubctl schema load`` to 120s, both of which a cold deployment loading this schema (200+ kinds,
with migrations) can exceed -- so the suite used to depend on ``ci.yml`` exporting a larger value, and
failed anywhere that variable was absent. Same trap as the API token: a default that works in exactly
one environment is worse than no default, because nothing reveals the dependency until it moves.
"""

DEFAULT_POLL_INTERVAL = 5
"""Seconds between polls for conditions that usually settle in well under a minute."""

SLOW_POLL_INTERVAL = 10
"""Seconds between polls for conditions gated on a container-side task queue."""

QUIESCENCE_ROUNDS = 3
"""Consecutive unchanged polls that count as "the generator has stopped working".

At :data:`SLOW_POLL_INTERVAL` that is 30 seconds of no new objects. Generator phases follow each
other within seconds, so a gap that long means the run is over rather than between phases.
"""

# --- timeouts (seconds) --------------------------------------------------------------------------

SCHEMA_LOAD_TIMEOUT = 900
"""``infrahubctl schema load`` on the full ``schemas/`` tree, including migrations."""

OBJECT_LOAD_TIMEOUT = 900
"""``infrahubctl object load`` on a directory of object files."""

REPO_SYNC_TIMEOUT = 900
"""Clone plus import of every generator, transform, check and artifact definition in the repo."""

DEFINITION_TIMEOUT = 600
"""How long a definition may take to appear after the repository reports itself in sync."""

GENERATOR_TIMEOUT = 2400
"""A full DC fabric run: sites, racks, devices, interfaces, cables, IPAM, BGP and OSPF."""

DIFF_TIMEOUT = 900
"""Computing a branch diff over a freshly generated fabric."""

VALIDATION_TIMEOUT = 1800
"""Proposed-change validators: data integrity, artifact regeneration and repository checks."""

MERGE_TIMEOUT = 900
"""Merging a proposed change and propagating the result into ``main``."""

ARTIFACT_TIMEOUT = 1800
"""Generating every artifact for a target group after a proposed change opens."""

# --- repository ----------------------------------------------------------------------------------

REPOSITORY_NAME = "demo_repo"
"""Name the suite registers this repository under inside Infrahub."""

# --- bootstrap inputs ----------------------------------------------------------------------------

SCHEMA_PATH = "schemas"
MENU_PATH = "menus/menu-full.yml"
BOOTSTRAP_OBJECTS_PATH = "objects/bootstrap"
SECURITY_OBJECTS_PATH = "objects/security"
EVENT_OBJECTS_PATH = "objects/events"

# --- topology fixtures ---------------------------------------------------------------------------

DC_ARISTA_BRANCH = "add-dc-arista"
DC_ARISTA_OBJECT = "objects/dc/dc-arista-s.yml"
DC_ARISTA_NAME = "dc-arista"
DC_ARISTA_DESIGN = "PHYSICAL DC ARISTA S"
DC_ARISTA_SCALE_DESIGN = "PHYSICAL DC ARISTA S WITH BORDER LEAFS"

DC_CISCO_BRANCH = "add-dc-cisco"
DC_CISCO_OBJECT = "objects/dc/dc-cisco-s-border-leafs.yml"
DC_CISCO_DESIGN = "PHYSICAL DC CISCO S WITH BORDER LEAFS"

POP_BRANCH = "add-pop-1"
POP_OBJECT = "objects/pop/pop-1.yml"
POP_NAME = "POP-1"
POP_DESIGN = "VIRTUAL POP S"

SEGMENT_BRANCH = "add-segment-opsmill"
SEGMENT_OBJECT = "objects/segments/segment-opsmill.yml"

DAY2_BRANCH = "day2-scale-out-dc-arista"
CONFLICT_BRANCH = "conflict-dc-arista"

# --- definitions the repository must publish -----------------------------------------------------

GENERATOR_DEFINITIONS = ["create_dc", "create_pop", "create_segment"]
"""Every entry under ``generator_definitions`` in ``.infrahub.yml``."""

TRANSFORM_DEFINITIONS = [
    "rack_elevation",
    "topology_cabling",
    "leaf",
    "openconfig_leaf",
    "spine",
    "loadbalancer",
    "edge",
    "equinix_pop",
    "juniper_firewall",
    "topology_clab",
]
"""Every entry under ``python_transforms`` and ``jinja2_transforms`` in ``.infrahub.yml``."""

CHECK_DEFINITIONS = ["validate_spine", "validate_leaf", "validate_edge", "validate_loadbalancer"]
"""Every entry under ``check_definitions`` in ``.infrahub.yml``."""

# --- artifact states -----------------------------------------------------------------------------

TRANSIENT_ARTIFACT_STATES = frozenset({"pending", "processing"})
"""Artifact states that mean "not finished yet", lowercased.

Infrahub's ``ArtifactStatus`` is ``Error | Pending | Processing | Ready`` (verified against
``backend/infrahub/core/constants/__init__.py`` in the image under test). Both non-terminal states
have to be waited out: treating only ``Processing`` as transient makes a slow artifact that is still
``Pending`` look like a permanent failure.
"""

# --- device kinds --------------------------------------------------------------------------------

FABRIC_ACTIVITY_KINDS = [
    "DcimGenericDevice",
    "DcimCable",
    "InterfaceVirtual",
    "ServiceBGP",
    "ServiceOSPF",
    "RoutingAutonomousSystem",
    "LocationRack",
]
"""Kinds whose counts are watched to decide a topology generator has finished.

A generator builds a fabric in phases -- devices, then racking, then cabling, then loopbacks, then
the routing protocols -- and the event-driven path gives no task handle to wait on. Matching device
counts against the design therefore proves the right generator *started*, not that it finished; a
test that snapshots the fabric at that moment reads a half-cabled, unpeered topology.

Watching counts across every phase's output until they stop moving is a completion signal that does
not depend on knowing which phase runs last, so it survives the generator being reordered.
"""

DEVICE_KINDS = ["DcimDevice", "DcimVirtualDevice", "SecurityFirewall"]
"""The three concrete kinds ``generators/common.py`` splits a design's devices across.

``DcimGenericDevice`` carries ``topology`` and so covers all three for plain counting, but ``role``
is declared on each concrete kind, so role-aware assertions have to visit them one by one.
"""
