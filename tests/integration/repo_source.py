"""Build the repository tree that Infrahub clones during the integration run.

``infrahub_sdk.testing.repository.GitRepo`` copies its ``src_directory`` wholesale with
``shutil.copytree`` and only ignores ``.git``. Handing it the repository root therefore copies the
working tree as it stands -- including ``.venv``, ``docs/node_modules``, every ``__pycache__`` and
any ``generated-configs`` left over from a local run -- and then commits the result so the Infrahub
container can clone it. On a developer machine that is gigabytes of payload that Infrahub has to
clone and walk before it can import a single generator.

Pruning first keeps what Infrahub actually reads (``.infrahub.yml`` and everything it references)
and drops the rest. The working tree is still the source, not ``HEAD``, so uncommitted edits to a
generator or transform are exercised by the suite exactly as a developer expects.
"""

from __future__ import annotations

import shutil
from pathlib import Path

EXCLUDED = (
    # Virtualenvs and caches: large, machine-specific, and never read by Infrahub.
    ".venv",
    "venv",
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".dev",
    # Documentation site build inputs and outputs.
    "node_modules",
    ".docusaurus",
    "build",
    # Local artefacts from `invoke containerlab` / `scripts/get_configs.py`.
    "generated-configs",
    "clab-*",
    ".clab.*",
    # The suite itself. Infrahub has no reason to import our test code, and leaving it out means a
    # new test file cannot change what the container sees.
    "tests",
)
"""Names and glob patterns dropped from the copy, as :func:`shutil.ignore_patterns` arguments."""


def prepare_repo_source(root_directory: Path, destination: Path) -> Path:
    """Copy the repository into ``destination``, dropping everything Infrahub does not read.

    Args:
        root_directory: The repository root to copy from.
        destination: Directory to create for the copy. Must not already exist.

    Returns:
        The path to the prepared copy, ready to be passed to ``GitRepo(src_directory=...)``.
    """
    shutil.copytree(
        src=root_directory,
        dst=destination,
        ignore=shutil.ignore_patterns(*EXCLUDED),
    )
    return destination
