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
from unittest.mock import MagicMock, call, patch

import pytest
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
    client.get.assert_called_with(
        kind="CoreGenericRepository", name__value="demo-dc", branch="main", raise_when_missing=False
    )


def test_the_wait_polls_the_selected_branch() -> None:
    """Every poll queries the selected Infrahub branch, including while the repository is missing."""
    client = _client(None, "syncing", "in-sync")
    with patch.object(bootstrap, "InfrahubClientSync", return_value=client), patch.object(bootstrap.time, "sleep"):
        assert bootstrap.wait_for_repository_sync("demo-dc", branch="dev", timeout=60, poll_interval=0) is True
    assert (
        client.get.call_args_list
        == [call(kind="CoreGenericRepository", name__value="demo-dc", branch="dev", raise_when_missing=False)] * 3
    )


@pytest.mark.parametrize("branch", ["main", "dev"])
def test_main_waits_for_the_repository_on_the_branch_it_loaded(branch: str) -> None:
    """Bootstrap polls the same branch used to load the repository."""
    with (
        patch.object(bootstrap, "check_infrahub_ready", return_value=True),
        patch.object(bootstrap, "run_command", return_value=True),
        patch.object(bootstrap, "INFRAHUB_GIT_LOCAL", False),
        patch.object(bootstrap, "repository_file", return_value=str(GITHUB_REPO_FILE)),
        patch.object(bootstrap, "InfrahubClientSync", return_value=_client(None)),
        patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as load,
        patch.object(bootstrap, "wait_for_repository_sync", return_value=True) as wait,
    ):
        assert bootstrap.main(branch=branch) == 0
    load.assert_called_once_with(
        f"uv run infrahubctl object load {GITHUB_REPO_FILE} --branch {branch}",
        shell=True,
        capture_output=True,
        text=True,
    )
    wait.assert_called_once_with("demo-dc", branch=branch)


@pytest.mark.parametrize("branch", ["main", "dev"])
@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected_status"),
    [
        (1, "", "Permission denied", 1),
        (1, "Repository Already Exists", "", 0),
        (1, "", "Repository Already Exists", 0),
        (0, "", "", 0),
    ],
    ids=["registration-failed", "duplicate-stdout", "duplicate-stderr", "registered"],
)
def test_main_only_waits_and_loads_events_after_registration(
    branch: str, returncode: int, stdout: str, stderr: str, expected_status: int
) -> None:
    """Failed registration stops bootstrap; duplicates and success proceed to import and events."""
    with (
        patch.object(bootstrap, "check_infrahub_ready", return_value=True),
        patch.object(bootstrap, "run_command", return_value=True) as command,
        patch.object(bootstrap, "INFRAHUB_GIT_LOCAL", False),
        patch.object(bootstrap, "repository_file", return_value=str(GITHUB_REPO_FILE)),
        patch.object(bootstrap, "InfrahubClientSync", return_value=_client(None)),
        patch.object(
            bootstrap.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr),
        ),
        patch.object(bootstrap, "wait_for_repository_sync", return_value=True) as wait,
    ):
        status = bootstrap.main(branch=branch)
    event_load = call(
        f"uv run infrahubctl object load objects/events/ --branch {branch}",
        "Loading event actions",
        "[7/7]",
        "bright_cyan",
        "⚡",
    )
    if expected_status == 1:
        wait.assert_not_called()
        assert event_load not in command.call_args_list
    else:
        wait.assert_called_once_with("demo-dc", branch=branch)
        assert command.call_args_list[-1] == event_load
    assert status == expected_status


def _existing_repository() -> MagicMock:
    """Return a previously imported repository matching the committed GitHub configuration."""
    data = _repository(str(GITHUB_REPO_FILE))
    repository = MagicMock()
    repository.get_kind.return_value = "CoreReadOnlyRepository"
    repository.location.value = data["location"]
    repository.ref.value = data["ref"]
    repository.sync_status.value = "in-sync"
    return repository


@pytest.mark.parametrize("branch", ["main", "dev"])
@pytest.mark.parametrize("changed", ["location", "ref", "kind"])
def test_changed_repository_is_rejected_before_loading_despite_stale_in_sync(branch: str, changed: str) -> None:
    """Changed configuration cannot reuse a previous import's success or load events."""
    repository = _existing_repository()
    if changed == "kind":
        repository.get_kind.return_value = "CoreRepository"
    else:
        getattr(repository, changed).value = "previous-value"
    client = MagicMock()
    client.get.return_value = repository
    with (
        patch.object(bootstrap, "check_infrahub_ready", return_value=True),
        patch.object(bootstrap, "run_command", return_value=True) as command,
        patch.object(bootstrap, "INFRAHUB_GIT_LOCAL", False),
        patch.object(bootstrap, "repository_file", return_value=str(GITHUB_REPO_FILE)),
        patch.object(bootstrap, "InfrahubClientSync", return_value=client),
        patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as load,
        patch.object(bootstrap, "wait_for_repository_sync", return_value=True) as wait,
        patch.object(bootstrap.console, "print") as output,
    ):
        assert bootstrap.main(branch=branch) == 1
    load.assert_not_called()
    wait.assert_not_called()
    assert len(command.call_args_list) == 5
    assert not any("objects/events/" in args.args[0] for args in command.call_args_list)
    client.get.assert_called_once_with(
        kind="CoreGenericRepository", name__value="demo-dc", branch=branch, fragment=True, raise_when_missing=False
    )
    message = " ".join(str(args.args[0]) for args in output.call_args_list if args.args)
    assert "Restore" in message
    assert "fresh Infrahub" in message


@pytest.mark.parametrize("branch", ["main", "dev"])
@pytest.mark.parametrize("existing", [True, False])
def test_unchanged_rerun_and_fresh_import_load_events_only_after_sync(branch: str, existing: bool) -> None:
    """Unchanged reruns and first imports succeed, even if a fast import skips the syncing poll."""
    repository = _existing_repository()
    client = MagicMock()
    client.get.side_effect = [repository if existing else None, repository]
    order = MagicMock()
    order.attach_mock(client.get, "get")
    with (
        patch.object(bootstrap, "check_infrahub_ready", return_value=True),
        patch.object(bootstrap, "run_command", return_value=True) as command,
        patch.object(bootstrap, "INFRAHUB_GIT_LOCAL", False),
        patch.object(bootstrap, "repository_file", return_value=str(GITHUB_REPO_FILE)),
        patch.object(bootstrap, "InfrahubClientSync", return_value=client),
        patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as load,
    ):
        order.attach_mock(load, "load")
        order.attach_mock(command, "command")
        assert bootstrap.main(branch=branch) == 0
    assert client.get.call_args_list == [
        call(
            kind="CoreGenericRepository", name__value="demo-dc", branch=branch, fragment=True, raise_when_missing=False
        ),
        call(kind="CoreGenericRepository", name__value="demo-dc", branch=branch, raise_when_missing=False),
    ]
    assert [entry[0] for entry in order.mock_calls[5:]] == ["get", "load", "get", "command"]
    assert "objects/events/" in command.call_args_list[-1].args[0]


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
