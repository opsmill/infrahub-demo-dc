"""One Infrahub deployment, bootstrapped once, shared by every integration module.

``infrahub_testcontainers.helpers.TestInfrahubDocker`` declares its fixtures at class scope, so a
suite built from it starts and stops a full Infrahub stack -- Neo4j, RabbitMQ, Redis, two API
servers, two task workers, Prefect and its Postgres -- once per test class, and then has to reload
the schema, the bootstrap objects and the repository into each fresh deployment. That is several
minutes of setup per class before a single assertion runs, which caps how many workflows the suite
can afford to cover inside the CI timeout.

This module instead drives ``InfrahubDockerCompose`` directly from session-scoped fixtures declared
at conftest level, so all of them resolve to a single instance no matter which class requests them.
The stack starts once, :func:`infrahub_bootstrap` loads the schema and demo data once, and each
workflow module then does its work on its own branch. Everything else -- the compose lifecycle, the
log dump when a test fails, the port mapping -- mirrors what ``TestInfrahubDocker`` does.

The cost of sharing is that modules are no longer independent: they run in file-name order, and the
ones that build on merged ``main`` data say so with a session-scoped ``pytest.mark.dependency``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess  # noqa: S404 - a throwaway container is the only way to reclaim root-owned mounts
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk import Config, InfrahubClient, InfrahubClientSync
from infrahub_sdk.testing.repository import GitRepo
from infrahub_testcontainers import __version__ as testcontainers_version
from infrahub_testcontainers.container import PROJECT_ENV_VARIABLES, InfrahubDockerCompose

from . import constants as c
from . import helpers as h
from .repo_source import prepare_repo_source

if TYPE_CHECKING:
    from collections.abc import Generator

TEST_DIRECTORY = Path(__file__).parent
PROJECT_DIRECTORY = TEST_DIRECTORY.parent.parent

log = logging.getLogger(__name__)


# --- the deployment ------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def infrahub_version() -> str:
    """Infrahub image tag under test.

    Returns:
        ``INFRAHUB_TESTING_IMAGE_VER`` when set, otherwise the installed
        ``infrahub-testcontainers`` version. The latter is the case the dependency-bump PRs
        exercise: the package version is the version of Infrahub being validated.
    """
    return os.getenv("INFRAHUB_TESTING_IMAGE_VER") or testcontainers_version


@pytest.fixture(scope="session")
def deployment_type(request: pytest.FixtureRequest) -> str | None:
    """Deployment topology requested through the ``infrahub-deployment-type`` plugin option.

    Args:
        request: Pytest request, used to read the command-line option.

    Returns:
        The requested deployment type, or ``None`` for the plugin default.
    """
    return request.config.getoption(name="infrahub_deployment_type", default=None)


def _reclaim_bind_mount_ownership(directory: Path, image: str) -> None:
    """Hand the stack directory back to the host user so pytest can delete it.

    Infrahub runs as root inside its container and writes into the bind-mounted repositories
    directory -- pushing branch refs leaves ``repos/<name>/.git/logs/refs/heads`` owned by
    ``root:root``. pytest's temp-directory cleanup runs as the host user, hits ``Operation not
    permitted`` on those paths, and gives up, so **every run leaks its entire temp directory**. On a
    tmpfs-backed ``/tmp`` that fills the filesystem; in CI, where ``PYTEST_DEBUG_TEMPROOT`` points at
    the runner's disk, it leaks there across every job on a long-lived self-hosted runner.

    Fixed the same way anyone fixes root-owned bind-mount leftovers: a throwaway container, running as
    root, chowns the tree back. The image is the one the stack just ran, so it is already local and
    this costs no pull.

    Failure here is logged and swallowed: it wastes disk, but it must never turn a passing suite red.

    Args:
        directory: The stack working directory to reclaim.
        image: A locally available image to run ``chown`` from.
    """
    # Mandatory guard, not defensive padding: `docker run -v <src>:/target` *creates* a missing source
    # path, owned by root. Reclaiming a directory that no longer exists would therefore recreate it --
    # and its parents -- as root, which is worse than the leak this function exists to prevent: pytest
    # can no longer create temp directories under that root at all, so every later run dies in setup.
    if not directory.exists():
        log.debug("Stack directory %s already gone; nothing to reclaim", directory)
        return

    try:
        result = subprocess.run(  # noqa: S603
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "chown",
                "-v",
                f"{directory}:/target",
                image,
                "-R",
                f"{os.getuid()}:{os.getgid()}",
                "/target",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            log.warning("Could not reclaim ownership of %s: %s", directory, result.stderr.strip())
    except Exception as exc:  # noqa: BLE001 - cleanup must never fail the run
        log.warning("Could not reclaim ownership of %s: %s", directory, exc)


@pytest.fixture(scope="session")
def stack_directory(tmp_path_factory: pytest.TempPathFactory, infrahub_version: str) -> Generator[Path, None, None]:
    """Working directory holding the generated compose file, env file and bind mounts.

    Torn down last of the stack fixtures, which is what lets it reclaim ownership of the bind mounts
    after compose has stopped.

    Args:
        tmp_path_factory: Pytest temporary-path factory.
        infrahub_version: Image tag under test, reused for the ownership-reclaiming container.

    Yields:
        A session-unique directory.
    """
    directory = tmp_path_factory.mktemp("infrahub_stack")
    yield directory

    image = f"{PROJECT_ENV_VARIABLES['INFRAHUB_TESTING_DOCKER_IMAGE']}:{infrahub_version}"
    _reclaim_bind_mount_ownership(directory, image=image)


@pytest.fixture(scope="session")
def remote_repos_dir(stack_directory: Path) -> Path:
    """Host directory the container mounts at ``/remote`` and clones repositories from.

    Created before compose starts, so the bind mount inherits the right ownership.

    Args:
        stack_directory: The stack working directory.

    Returns:
        The repositories directory.
    """
    directory = stack_directory / PROJECT_ENV_VARIABLES["INFRAHUB_TESTING_LOCAL_REMOTE_GIT_DIRECTORY"]
    directory.mkdir(exist_ok=True)
    return directory


@pytest.fixture(scope="session")
def remote_backups_dir(stack_directory: Path) -> Path:
    """Host directory the container mounts for database backups.

    Args:
        stack_directory: The stack working directory.

    Returns:
        The backups directory.
    """
    directory = stack_directory / PROJECT_ENV_VARIABLES["INFRAHUB_TESTING_LOCAL_DB_BACKUP_DIRECTORY"]
    directory.mkdir(exist_ok=True)
    return directory


@pytest.fixture(scope="session")
def infrahub_compose(
    stack_directory: Path,
    remote_repos_dir: Path,  # noqa: ARG001 - must exist before compose creates the bind mount
    remote_backups_dir: Path,  # noqa: ARG001 - same
    infrahub_version: str,
    deployment_type: str | None,
) -> InfrahubDockerCompose:
    """Compose project for the Infrahub stack under test.

    Args:
        stack_directory: Directory to write the compose and env files into.
        remote_repos_dir: Repositories bind mount, requested for ordering only.
        remote_backups_dir: Backups bind mount, requested for ordering only.
        infrahub_version: Image tag to run.
        deployment_type: Deployment topology, or ``None`` for the default.

    Returns:
        An initialized, not yet started compose project.
    """
    return InfrahubDockerCompose.init(
        directory=stack_directory,
        version=infrahub_version,
        deployment_type=deployment_type,
    )


@pytest.fixture(scope="session")
def infrahub_app(
    request: pytest.FixtureRequest,
    infrahub_compose: InfrahubDockerCompose,
) -> Generator[dict[str, int], None, None]:
    """Start the stack for the session and expose its published ports.

    Args:
        request: Pytest request, used to detect failures at teardown.
        infrahub_compose: The compose project to start.

    Yields:
        Mapping of service name to published host port.

    Raises:
        Exception: If compose fails to start, with the compose logs attached.
    """
    try:
        infrahub_compose.start()
    except Exception as exc:
        stdout, stderr = infrahub_compose.get_logs()
        raise Exception(f"Failed to start docker compose:\nStdout:\n{stdout}\nStderr:\n{stderr}") from exc

    yield infrahub_compose.get_services_port()

    # The stack is shared, so this is the only chance to capture logs for the whole run. Attaching
    # them on any failure is what makes a CI-only failure diagnosable without a local reproduction.
    if request.session.testsfailed:
        stdout, stderr = infrahub_compose.get_logs("infrahub-server", "task-worker")
        warnings.warn(f"Container logs:\nStdout:\n{stdout}\nStderr:\n{stderr}", stacklevel=2)

    infrahub_compose.stop()


@pytest.fixture(scope="session")
def infrahub_port(infrahub_app: dict[str, int]) -> int:
    """Published port of the Infrahub API load balancer.

    Args:
        infrahub_app: Service port mapping.

    Returns:
        The host port serving the API.
    """
    return infrahub_app["server"]


@pytest.fixture(scope="session")
def task_manager_port(infrahub_app: dict[str, int]) -> int:
    """Published port of the Prefect task manager.

    Args:
        infrahub_app: Service port mapping.

    Returns:
        The host port serving the task manager.
    """
    return infrahub_app["task-manager"]


@pytest.fixture(scope="session")
def infrahub_address(infrahub_port: int) -> str:
    """Base URL of the Infrahub server under test.

    Args:
        infrahub_port: Published API port.

    Returns:
        The base URL, for the SDK clients and for ``infrahubctl``.
    """
    return f"http://localhost:{infrahub_port}"


# --- clients -------------------------------------------------------------------------------------
#
# Function-scoped on purpose. Constructing a client is cheap, and each async test runs in its own
# event loop, so a shared async client would be bound to a loop that has already closed.


@pytest.fixture(scope="session")
def infrahub_api_token() -> str:
    """Admin token the test deployment is seeded with.

    Passed to every client explicitly rather than left to ``Config`` picking ``INFRAHUB_API_TOKEN`` up
    from the environment. The implicit route works in CI only because ``ci.yml`` happens to set that
    variable to the same literal that ``infrahub-testcontainers`` seeds the deployment with -- so the
    suite silently fails to authenticate anywhere that variable is not exported, and would break just
    as silently if testcontainers ever changed its default token.

    Returns:
        The bearer token for the seeded admin account.
    """
    return PROJECT_ENV_VARIABLES["INFRAHUB_TESTING_INITIAL_ADMIN_TOKEN"]


@pytest.fixture
def client_main(infrahub_address: str, infrahub_api_token: str) -> InfrahubClientSync:
    """Synchronous client on the default branch.

    Args:
        infrahub_address: Base URL of the server.
        infrahub_api_token: Admin token for the deployment.

    Returns:
        A fresh synchronous client.
    """
    return InfrahubClientSync(
        config=Config(address=infrahub_address, api_token=infrahub_api_token, timeout=c.CLIENT_TIMEOUT)
    )


@pytest.fixture
def async_client_main(infrahub_address: str, infrahub_api_token: str) -> InfrahubClient:
    """Asynchronous client on the default branch.

    Args:
        infrahub_address: Base URL of the server.
        infrahub_api_token: Admin token for the deployment.

    Returns:
        A fresh asynchronous client, bound to the current test's event loop on first use.
    """
    return InfrahubClient(
        config=Config(address=infrahub_address, api_token=infrahub_api_token, timeout=c.CLIENT_TIMEOUT)
    )


# --- bootstrap -----------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def repo_source_directory(stack_directory: Path) -> Path:
    """A pruned copy of this repository for Infrahub to clone.

    Args:
        stack_directory: The stack working directory.

    Returns:
        Path to the prepared copy. See :mod:`tests.integration.repo_source` for what is dropped.
    """
    return prepare_repo_source(
        root_directory=PROJECT_DIRECTORY,
        destination=stack_directory / "repo_source" / "infrahub-demo-dc",
    )


@pytest.fixture(scope="session")
def infrahub_bootstrap(
    infrahub_address: str,
    infrahub_api_token: str,
    remote_repos_dir: Path,
    repo_source_directory: Path,
) -> dict[str, Any]:
    """Bring the deployment to the state ``invoke bootstrap`` leaves it in, once for the session.

    Mirrors ``scripts/bootstrap.py``: schema, menu, bootstrap objects, security objects, then the
    repository, then the event actions and trigger rules. The event objects come last because the
    generator actions they reference only exist once the repository has imported ``.infrahub.yml``.

    Loading the trigger rules is what lets the workflow modules exercise the event-driven path the
    user walkthrough describes -- create a topology object and the matching generator fires on its
    own -- rather than only the explicit ``CoreGeneratorDefinitionRun`` call.

    Args:
        infrahub_address: Base URL of the server.
        infrahub_api_token: Admin token for the deployment.
        remote_repos_dir: Host directory the container clones repositories from.
        repo_source_directory: Pruned copy of this repository to serve.

    Returns:
        A summary of what was loaded, for the bootstrap module to assert against.
    """
    address = infrahub_address

    h.assert_ctl_succeeded(
        h.infrahubctl(f"schema load {c.SCHEMA_PATH} --wait 60", address=address, timeout=c.SCHEMA_LOAD_TIMEOUT),
        "Loading schemas",
    )
    h.assert_ctl_succeeded(h.infrahubctl(f"menu load {c.MENU_PATH}", address=address), "Loading menu")
    h.load_objects(c.BOOTSTRAP_OBJECTS_PATH, address=address)
    h.load_objects(c.SECURITY_OBJECTS_PATH, address=address)

    repository = GitRepo(
        name=c.REPOSITORY_NAME,
        src_directory=repo_source_directory,
        dst_directory=remote_repos_dir,
    )

    async def register_repository() -> str:
        client = InfrahubClient(config=Config(address=address, api_token=infrahub_api_token, timeout=c.CLIENT_TIMEOUT))
        response = await repository.add_to_infrahub(client=client)
        created = response.get(f"{repository.type.value}Create", {}).get("ok")
        assert created, f"Failed to register repository {c.REPOSITORY_NAME!r}: {response}"

        node = await h.wait_for_repository_sync(client, name=c.REPOSITORY_NAME)
        return str(node.id)

    repository_id = asyncio.run(register_repository())

    # Event actions reference the generators the repository just published, so they can only load
    # after the sync above completed.
    h.load_objects(c.EVENT_OBJECTS_PATH, address=address)

    return {
        "address": address,
        "repository_id": repository_id,
        "repository_name": c.REPOSITORY_NAME,
    }


@pytest.fixture(autouse=True)
def bootstrapped_deployment(request: pytest.FixtureRequest) -> None:
    """Guarantee the bootstrap has run before any integration test does.

    Most modules request :func:`infrahub_bootstrap` from their first step and then rely on collection
    order, which is fine for a whole-suite run but leaves a single test selected with ``-k`` running
    against an empty deployment. Requesting it here makes every integration test self-sufficient.

    The value is pulled in lazily rather than declared as a parameter so that a test marked ``offline``
    -- one that only reads files from the repository -- does not drag a container stack into scope.

    Args:
        request: Pytest request, used to check for the ``offline`` marker and to resolve the fixture.
    """
    if request.node.get_closest_marker("offline"):
        return
    request.getfixturevalue("infrahub_bootstrap")
