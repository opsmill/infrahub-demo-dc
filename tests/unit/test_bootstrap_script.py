"""Tests for scripts/bootstrap.py: the repository it registers and the wait for its import.

The public sandbox runs the bootstrap against a remote Infrahub, with the repository URL and ref
of the sandbox, and needs the bootstrap to stop when the import fails rather than go on without
the event actions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

SCRIPT = next(
    parent / "scripts" / "bootstrap.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "scripts" / "bootstrap.py").exists()
)
GITHUB_REPO_FILE = SCRIPT.parent.parent / "objects" / "git-repo" / "github.yml"
_spec = importlib.util.spec_from_file_location("dc_bootstrap", SCRIPT)
assert _spec
assert _spec.loader
bootstrap = importlib.util.module_from_spec(_spec)
sys.modules["dc_bootstrap"] = bootstrap
_spec.loader.exec_module(bootstrap)


def _repository(path: str) -> dict[str, str]:
    return yaml.safe_load(Path(path).read_text())["spec"]["data"][0]


def test_no_override_loads_the_committed_file() -> None:
    """Without INFRAHUB_REPO_URL or INFRAHUB_REPO_REF the committed file is loaded as it is."""
    assert bootstrap.repository_file(str(GITHUB_REPO_FILE), "", "") == str(GITHUB_REPO_FILE)


def test_the_url_and_ref_replace_those_of_the_committed_file() -> None:
    """A fork registers its own URL and ref; the name stays, so the bootstrap waits for it."""
    rendered = bootstrap.repository_file(
        str(GITHUB_REPO_FILE), "https://github.com/example/infrahub-demo-dc.git", "sandbox"
    )
    repository = _repository(rendered)
    assert repository["location"] == "https://github.com/example/infrahub-demo-dc.git"
    assert repository["ref"] == "sandbox"
    assert repository["name"] == bootstrap.repository_name(str(GITHUB_REPO_FILE)) == "demo-dc"
    assert _repository(str(GITHUB_REPO_FILE))["ref"] == "main"


def _client(*statuses: str | None) -> MagicMock:
    """A client whose repository reports each status in turn; None means not created yet."""
    client = MagicMock()
    client.get.side_effect = [SimpleNamespace(sync_status=SimpleNamespace(value=s)) if s else None for s in statuses]
    return client


def test_the_wait_ends_when_the_repository_is_in_sync() -> None:
    """The wait polls through the import and returns once the repository is in sync."""
    client = _client(None, "syncing", "in-sync")
    with patch.object(bootstrap, "InfrahubClientSync", return_value=client), patch.object(bootstrap.time, "sleep"):
        assert bootstrap.wait_for_repository_sync("demo-dc", timeout=60, poll_interval=0) is True
    assert client.get.call_count == 3


def test_a_failed_import_stops_the_wait() -> None:
    """An import error is final: the wait returns at once instead of running out its time."""
    client = _client("syncing", "error-import")
    with patch.object(bootstrap, "InfrahubClientSync", return_value=client), patch.object(bootstrap.time, "sleep"):
        assert bootstrap.wait_for_repository_sync("demo-dc", timeout=60, poll_interval=0) is False
    assert client.get.call_count == 2


def test_the_wait_gives_up_after_its_timeout() -> None:
    """A repository that never ends its import makes the bootstrap fail after the timeout."""
    client = MagicMock()
    client.get.return_value = SimpleNamespace(sync_status=SimpleNamespace(value="syncing"))
    with patch.object(bootstrap, "InfrahubClientSync", return_value=client), patch.object(bootstrap.time, "sleep"):
        assert bootstrap.wait_for_repository_sync("demo-dc", timeout=0, poll_interval=0) is False
