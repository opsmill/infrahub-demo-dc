"""What reaches the repository copy Infrahub clones, and what must never reach it.

Marked ``offline``: this exercises the pruning over a temporary tree, with no deployment involved.
``prepare_repo_source`` is the only thing standing between a developer's working tree and a git
history inside the container, so the exclusions are asserted rather than trusted -- a reordered or
mistyped entry in ``EXCLUDED`` is otherwise invisible until a token has already been committed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .repo_source import prepare_repo_source

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.offline


@pytest.fixture
def working_tree(tmp_path: Path) -> Path:
    """A miniature working tree holding one file from each interesting class.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        The root of the tree to copy from.
    """
    root = tmp_path / "repository"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "tests" / "integration").mkdir(parents=True)
    (root / "tests" / "integration" / "test_00_bootstrap.py").write_text("", encoding="utf-8")
    (root / "generators").mkdir()
    (root / "generators" / "x.py").write_text("# a generator Infrahub must import\n", encoding="utf-8")
    (root / ".env").write_text("INFRAHUB_API_TOKEN=not-a-real-token\n", encoding="utf-8")
    (root / ".envrc").write_text("export INFRAHUB_ADDRESS=http://localhost:8000\n", encoding="utf-8")
    (root / ".infrahub.yml").write_text("---\ngenerator_definitions: []\n", encoding="utf-8")
    return root


def test_credentials_never_reach_the_copy(working_tree: Path, tmp_path: Path) -> None:
    """``GitRepo`` commits this copy, so a copied ``.env`` is a token in a git history.

    ``.envrc`` matters for the same reason: direnv files hold the same secrets in another spelling.
    """
    copied = prepare_repo_source(working_tree, tmp_path / "prepared")
    assert not (copied / ".env").exists()
    assert not (copied / ".envrc").exists()


def test_virtualenv_never_reaches_the_copy(working_tree: Path, tmp_path: Path) -> None:
    """A copied ``.venv`` is gigabytes Infrahub has to clone and walk before importing anything."""
    copied = prepare_repo_source(working_tree, tmp_path / "prepared")
    assert not (copied / ".venv").exists()


def test_the_suite_itself_never_reaches_the_copy(working_tree: Path, tmp_path: Path) -> None:
    """Leaving ``tests/`` out is what stops a new test file changing what the container sees."""
    copied = prepare_repo_source(working_tree, tmp_path / "prepared")
    assert not (copied / "tests").exists()


def test_repository_content_infrahub_reads_survives(working_tree: Path, tmp_path: Path) -> None:
    """Pruning that dropped a generator would be worse than no pruning at all.

    The working tree is the source rather than ``HEAD``, so an uncommitted edit to a generator is
    what the suite exercises -- which only holds while the generator is copied.
    """
    copied = prepare_repo_source(working_tree, tmp_path / "prepared")
    assert (copied / "generators" / "x.py").is_file()
    assert (copied / ".infrahub.yml").is_file()


def test_the_prepared_path_is_returned(working_tree: Path, tmp_path: Path) -> None:
    """The return value is what ``GitRepo(src_directory=...)`` is handed."""
    destination = tmp_path / "prepared"
    assert prepare_repo_source(working_tree, destination) == destination
