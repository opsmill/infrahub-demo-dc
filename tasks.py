"""Tasks for the infrahub-demo-dc project."""

import os
import sys
import time
from pathlib import Path

from invoke import Context, task  # type: ignore[import-not-found]
from rich import box  # type: ignore[import-not-found]
from rich.console import Console  # type: ignore[import-not-found]
from rich.panel import Panel  # type: ignore[import-not-found]
from rich.progress import (  # type: ignore[import-not-found]
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table  # type: ignore[import-not-found]

console = Console()


INFRAHUB_VERSION = os.getenv("INFRAHUB_VERSION", "stable")
INFRAHUB_ENTERPRISE = os.getenv("INFRAHUB_ENTERPRISE", "false").lower() == "true"
INFRAHUB_SERVICE_CATALOG = os.getenv("INFRAHUB_SERVICE_CATALOG", "false").lower() == "true"
INFRAHUB_GIT_LOCAL = os.getenv("INFRAHUB_GIT_LOCAL", "false").lower() == "true"
MAIN_DIRECTORY_PATH = Path(__file__).parent
INFRAHUB_PROJECT_NAME = os.getenv("INFRAHUB_PROJECT_NAME", MAIN_DIRECTORY_PATH.name)


# Download compose file and use with override
def get_compose_command() -> str:
    """Generate docker compose command with override support.

    Supports layered compose files:
    1. docker-compose.yml (base, downloaded or local)
    2. docker-compose.override.yml (committed overrides, uses env vars for ports)

    The project name is controlled by INFRAHUB_PROJECT_NAME env var,
    defaulting to the directory name.
    """
    local_compose_file = MAIN_DIRECTORY_PATH / "docker-compose.yml"
    override_file = MAIN_DIRECTORY_PATH / "docker-compose.override.yml"

    base_cmd = f"docker compose -p {INFRAHUB_PROJECT_NAME}"

    # If we have a local docker-compose.yml, use it directly
    if local_compose_file.exists():
        cmd = f"{base_cmd} -f {local_compose_file}"
        if override_file.exists():
            cmd += f" -f {override_file}"
        return cmd

    # Fall back to downloading from infrahub.opsmill.io
    if INFRAHUB_ENTERPRISE:
        base_url = f"https://infrahub.opsmill.io/enterprise/{INFRAHUB_VERSION}"
    else:
        base_url = f"https://infrahub.opsmill.io/{INFRAHUB_VERSION}"

    cmd = f"curl -s {base_url} | {base_cmd} -f -"
    if override_file.exists():
        cmd += f" -f {override_file}"
    return cmd


def get_compose_source() -> str:
    """Get a human-readable description of the compose file source."""
    local_compose_file = MAIN_DIRECTORY_PATH / "docker-compose.yml"
    if local_compose_file.exists():
        return "Local (docker-compose.yml)"

    edition = "Enterprise" if INFRAHUB_ENTERPRISE else "Community"
    return f"infrahub.opsmill.io ({edition} {INFRAHUB_VERSION})"


COMPOSE_COMMAND = get_compose_command()
COMPOSE_SOURCE = get_compose_source()
CURRENT_DIRECTORY = Path(__file__).resolve()
DOCUMENTATION_DIRECTORY = CURRENT_DIRECTORY.parent / "docs"


@task(name="list")
def list_tasks(context: Context) -> None:
    """List all available invoke tasks with descriptions."""
    import inspect

    current_module = inspect.getmodule(inspect.currentframe())

    tasks_info = []

    # Get all task objects from the current module
    for name, obj in inspect.getmembers(current_module):
        if hasattr(obj, "__wrapped__") or (hasattr(obj, "__class__") and "Task" in obj.__class__.__name__):
            display_name = getattr(obj, "name", name)
            if display_name.startswith("_"):
                continue
            if obj.__doc__:
                description = obj.__doc__.strip().split("\n")[0]
            else:
                description = "No description available"
            tasks_info.append((display_name, description))

    # Sort by task name
    tasks_info.sort(key=lambda x: x[0])

    # Create a Rich table
    table = Table(
        title="Available Invoke Tasks",
        box=box.SIMPLE,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Task", style="green", no_wrap=True)
    table.add_column("Description", style="white")

    for name, desc in tasks_info:
        table.add_row(name, desc)

    console.print()
    console.print(table)
    console.print()


@task
def info(context: Context) -> None:
    """Show current Infrahub configuration."""
    edition = "Enterprise" if INFRAHUB_ENTERPRISE else "Community"

    info_msg = (
        f"[cyan]Edition:[/cyan] {edition}\n"
        f"[cyan]Version:[/cyan] {INFRAHUB_VERSION}\n"
        f"[cyan]Project Name:[/cyan] {INFRAHUB_PROJECT_NAME}\n"
        f"[cyan]Compose Source:[/cyan] {COMPOSE_SOURCE}\n"
        f"[cyan]Service Catalog:[/cyan] {'Enabled' if INFRAHUB_SERVICE_CATALOG else 'Disabled'}\n"
        f"[cyan]Local Git Repository:[/cyan] {'Enabled' if INFRAHUB_GIT_LOCAL else 'Disabled'}\n"
        f"[cyan]Command:[/cyan] [dim]{COMPOSE_COMMAND}[/dim]"
    )

    info_panel = Panel(
        info_msg,
        title="[bold]Infrahub Configuration[/bold]",
        border_style="blue",
        box=box.SIMPLE,
    )
    console.print()
    console.print(info_panel)
    console.print()


@task(optional=["rebuild"])
def start(context: Context, rebuild: bool = False) -> None:
    """Start all containers (use --rebuild to force rebuild images)."""
    edition = "Enterprise" if INFRAHUB_ENTERPRISE else "Community"

    # Get infrahub-sdk version
    try:
        import importlib.metadata

        sdk_version = importlib.metadata.version("infrahub-sdk")
    except Exception:
        sdk_version = "unknown"

    # Build the compose command with optional service catalog profile
    compose_cmd = COMPOSE_COMMAND
    if INFRAHUB_SERVICE_CATALOG:
        compose_cmd = f"{compose_cmd} --profile service-catalog"

    console.print()
    status_msg = (
        f"[green]Starting Infrahub {edition}[/green] [dim]({INFRAHUB_VERSION})[/dim]\n"
        f"[dim]Compose:[/dim] {COMPOSE_SOURCE}\n"
        f"[dim]Infrahub SDK:[/dim] {sdk_version}"
    )
    if INFRAHUB_SERVICE_CATALOG:
        status_msg += "\n[cyan]Service Catalog:[/cyan] Enabled"
    if INFRAHUB_GIT_LOCAL:
        status_msg += "\n[cyan]Local Git Repository:[/cyan] Enabled"
    if rebuild:
        status_msg += "\n[yellow]Rebuild:[/yellow] Enabled"

    console.print(Panel(status_msg, border_style="green", box=box.SIMPLE))

    build_flag = "--build" if rebuild else ""
    context.run(f"{compose_cmd} up -d {build_flag}")

    console.print("[green]✓[/green] Infrahub started successfully")
    if INFRAHUB_SERVICE_CATALOG:
        console.print("[green]✓[/green] Service Catalog available at http://localhost:8501")


@task(optional=["branch"], name="bootstrap")
def bootstrap_py(context: Context, branch: str = "main") -> None:
    """Run the complete bootstrap process."""
    context.run(f"uv run python scripts/bootstrap.py --branch {branch}", pty=True)


@task(optional=["branch"], name="demo-dc-arista")
def demo_dc_arista(context: Context, branch: str = "add-dc3") -> None:
    """Create branch and load Arista DC demo topology."""
    console.print()
    console.print(
        Panel(
            f"[bold cyan]Arista Data Center Demo[/bold cyan]\n[dim]Branch:[/dim] {branch}",
            border_style="cyan",
            box=box.SIMPLE,
        )
    )

    console.print(f"\n[cyan]→[/cyan] Creating branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl branch create {branch}")

    console.print("\n[yellow]→[/yellow] Waiting for branch to be ready...")
    time.sleep(10)

    console.print(f"\n[cyan]→[/cyan] Loading DC Arista topology to branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl object load objects/dc/dc-arista-s.yml --branch {branch}")

    console.print(f"\n[green]✓[/green] DC Arista topology loaded to branch '[bold green]{branch}[/bold green]'")

    # Wait for generator to finish creating the data
    console.print("\n[yellow]→[/yellow] Waiting for generator to complete data creation...")
    wait_seconds = 60  # Wait 60 seconds for generator to process

    with Progress(
        SpinnerColumn(spinner_name="dots12", style="bold bright_yellow"),
        TextColumn("[progress.description]{task.description}", style="bold white"),
        BarColumn(
            bar_width=40,
            style="yellow",
            complete_style="bright_green",
            finished_style="bold bright_green",
            pulse_style="bright_yellow",
        ),
        TextColumn("[bold bright_cyan]{task.percentage:>3.0f}%"),
        TextColumn("•", style="dim"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("⏳ Generator processing", total=wait_seconds)
        for _ in range(wait_seconds):
            time.sleep(1)
            progress.update(task, advance=1)

    console.print("[green]✓[/green] Generator processing complete")

    # Create proposed change
    console.print(
        f"\n[bright_magenta]→[/bright_magenta] Creating proposed change for branch '[bold]{branch}[/bold]'..."
    )
    context.run(f"uv run python scripts/create_proposed_change.py --branch {branch}", pty=True)

    console.print()


@task(optional=["branch"], name="demo-dc-juniper")
def demo_dc_juniper(context: Context, branch: str = "add-dc5") -> None:
    """Create branch and load Juniper DC demo topology."""
    console.print()
    console.print(
        Panel(
            f"[bold cyan]Juniper Data Center Demo[/bold cyan]\n[dim]Branch:[/dim] {branch}",
            border_style="cyan",
            box=box.SIMPLE,
        )
    )

    console.print(f"\n[cyan]→[/cyan] Creating branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl branch create {branch}")

    console.print(f"\n[cyan]→[/cyan] Loading DC Juniper topology to branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl object load objects/dc/dc-juniper-s.yml --branch {branch}")

    console.print(f"\n[green]✓[/green] DC Juniper topology loaded to branch '[bold green]{branch}[/bold green]'")

    # Wait for generator to finish creating the data
    console.print("\n[yellow]→[/yellow] Waiting for generator to complete data creation...")
    wait_seconds = 60  # Wait 60 seconds for generator to process

    with Progress(
        SpinnerColumn(spinner_name="dots12", style="bold bright_yellow"),
        TextColumn("[progress.description]{task.description}", style="bold white"),
        BarColumn(
            bar_width=40,
            style="yellow",
            complete_style="bright_green",
            finished_style="bold bright_green",
            pulse_style="bright_yellow",
        ),
        TextColumn("[bold bright_cyan]{task.percentage:>3.0f}%"),
        TextColumn("•", style="dim"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("⏳ Generator processing", total=wait_seconds)
        for _ in range(wait_seconds):
            time.sleep(1)
            progress.update(task, advance=1)

    console.print("[green]✓[/green] Generator processing complete")

    # Create proposed change
    console.print(
        f"\n[bright_magenta]→[/bright_magenta] Creating proposed change for branch '[bold]{branch}[/bold]'..."
    )
    context.run(f"uv run python scripts/create_proposed_change.py --branch {branch}", pty=True)

    console.print()


@task(optional=["branch"], name="demo-dc-cisco")
def demo_dc_cisco(context: Context, branch: str = "add-dc2") -> None:
    """Create branch and load Cisco DC demo topology."""
    console.print()
    console.print(
        Panel(
            f"[bold cyan]Cisco Data Center Demo[/bold cyan]\n[dim]Branch:[/dim] {branch}",
            border_style="cyan",
            box=box.SIMPLE,
        )
    )

    console.print(f"\n[cyan]→[/cyan] Creating branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl branch create {branch}")

    console.print(f"\n[cyan]→[/cyan] Loading DC Cisco topology to branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl object load objects/dc/dc-cisco-s.yml --branch {branch}")

    console.print(f"\n[green]✓[/green] DC Cisco topology loaded to branch '[bold green]{branch}[/bold green]'")

    # Wait for generator to finish creating the data
    console.print("\n[yellow]→[/yellow] Waiting for generator to complete data creation...")
    wait_seconds = 60  # Wait 60 seconds for generator to process

    with Progress(
        SpinnerColumn(spinner_name="dots12", style="bold bright_yellow"),
        TextColumn("[progress.description]{task.description}", style="bold white"),
        BarColumn(
            bar_width=40,
            style="yellow",
            complete_style="bright_green",
            finished_style="bold bright_green",
            pulse_style="bright_yellow",
        ),
        TextColumn("[bold bright_cyan]{task.percentage:>3.0f}%"),
        TextColumn("•", style="dim"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("⏳ Generator processing", total=wait_seconds)
        for _ in range(wait_seconds):
            time.sleep(1)
            progress.update(task, advance=1)

    console.print("[green]✓[/green] Generator processing complete")

    # Create proposed change
    console.print(
        f"\n[bright_magenta]→[/bright_magenta] Creating proposed change for branch '[bold]{branch}[/bold]'..."
    )
    context.run(f"uv run python scripts/create_proposed_change.py --branch {branch}", pty=True)

    console.print()


@task(optional=["branch"], name="demo-vpn-opsmill")
def demo_vpn_opsmill(context: Context, branch: str = "add-vpn-opsmill") -> None:
    """Create branch and load OpsMill VPN segment demo."""
    console.print()
    console.print(
        Panel(
            f"[bold cyan]OpsMill VxLAN VPN Segment Demo[/bold cyan]\n[dim]Branch:[/dim] {branch}",
            border_style="cyan",
            box=box.SIMPLE,
        )
    )

    console.print(f"\n[cyan]→[/cyan] Creating branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl branch create {branch}")

    console.print(f"\n[cyan]→[/cyan] Loading OpsMill VPN segment to branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl object load objects/segments/segment-opsmill.yml --branch {branch}")

    console.print(f"\n[green]✓[/green] OpsMill VPN segment loaded to branch '[bold green]{branch}[/bold green]'")

    # Wait for generator to finish creating the data
    console.print("\n[yellow]→[/yellow] Waiting for segment generator to complete...")
    wait_seconds = 30  # Segment processing is faster than DC topology

    with Progress(
        SpinnerColumn(spinner_name="dots12", style="bold bright_yellow"),
        TextColumn("[progress.description]{task.description}", style="bold white"),
        BarColumn(
            bar_width=40,
            style="yellow",
            complete_style="bright_green",
            finished_style="bold bright_green",
            pulse_style="bright_yellow",
        ),
        TextColumn("[bold bright_cyan]{task.percentage:>3.0f}%"),
        TextColumn("•", style="dim"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("⏳ Segment generator processing", total=wait_seconds)
        for _ in range(wait_seconds):
            time.sleep(1)
            progress.update(task, advance=1)

    console.print("[green]✓[/green] Segment generator processing complete")

    # Create proposed change
    console.print(
        f"\n[bright_magenta]→[/bright_magenta] Creating proposed change for branch '[bold]{branch}[/bold]'..."
    )
    context.run(f"uv run python scripts/create_proposed_change.py --branch {branch}", pty=True)

    console.print()


@task(optional=["branch", "topology"])
def containerlab(context: Context, branch: str = "add-dc3", topology: str = "DC-3") -> None:
    """Generate configs and deploy containerlab topology."""
    console.print()
    console.print(
        Panel(
            f"[bold magenta]Containerlab Deployment[/bold magenta]\n"
            f"[dim]Branch:[/dim] {branch}\n"
            f"[dim]Topology:[/dim] {topology}",
            border_style="magenta",
            box=box.SIMPLE,
        )
    )

    console.print(f"\n[magenta]→[/magenta] Generating configurations from branch: [bold]{branch}[/bold]")
    context.run(f"uv run scripts/get_configs.py --branch {branch}", pty=True)

    topology_file = f"generated-configs/clab/{topology}.clab.yml"
    console.print(f"\n[magenta]→[/magenta] Deploying containerlab topology: [bold]{topology_file}[/bold]")
    context.run(f"sudo -E containerlab deploy -t {topology_file}")

    console.print(
        f"\n[green]✓[/green] Containerlab topology '[bold green]{topology}[/bold green]' deployed successfully"
    )
    console.print()


@task
def destroy(context: Context) -> None:
    """Destroy all containers."""
    console.print()
    console.print(
        Panel(
            "[red]Destroying all containers and volumes[/red]",
            border_style="red",
            box=box.SIMPLE,
        )
    )
    # Include all profiles to ensure profile-based containers are destroyed
    context.run(f"{COMPOSE_COMMAND} --profile service-catalog down -v")
    console.print("[green]✓[/green] All containers and volumes destroyed")


@task
def stop(context: Context) -> None:
    """Stop all containers."""
    console.print()
    console.print(
        Panel(
            "[yellow]Stopping all containers[/yellow]",
            border_style="yellow",
            box=box.SIMPLE,
        )
    )
    # Include all profiles to ensure profile-based containers are stopped
    context.run(f"{COMPOSE_COMMAND} --profile service-catalog down")
    console.print("[green]✓[/green] All containers stopped")


@task(name="restart-containers")
def restart_containers(context: Context, component: str = "") -> None:
    """Restart Docker containers (without destroying data)."""
    if component:
        console.print()
        console.print(
            Panel(
                f"[yellow]Restarting component:[/yellow] [bold]{component}[/bold]",
                border_style="yellow",
                box=box.SIMPLE,
            )
        )
        context.run(f"{COMPOSE_COMMAND} restart {component}")
        console.print(f"[green]✓[/green] Component '{component}' restarted")
        return

    console.print()
    console.print(
        Panel(
            "[yellow]Restarting all containers[/yellow]",
            border_style="yellow",
            box=box.SIMPLE,
        )
    )
    context.run(f"{COMPOSE_COMMAND} restart")
    console.print("[green]✓[/green] All containers restarted")


@task
def init(context: Context) -> None:
    """Initialize Infrahub: destroy, start, bootstrap, and load demo DC."""
    console.print()
    console.print(
        Panel(
            "[bold magenta]Initialize Infrahub[/bold magenta]\n"
            "[dim]This will destroy all data and rebuild from scratch[/dim]\n\n"
            "[yellow]Steps:[/yellow]\n"
            "  1. Destroy all containers and volumes\n"
            "  2. Start Infrahub\n"
            "  3. Bootstrap (schemas, data, repository)\n"
            "  4. Load Arista DC demo",
            border_style="magenta",
            box=box.SIMPLE,
        )
    )
    console.print()

    # Step 1: Destroy
    console.print("[bold magenta]Step 1/4:[/bold magenta] Destroying containers...")
    destroy(context)
    console.print()

    # Step 2: Start
    console.print("[bold magenta]Step 2/4:[/bold magenta] Starting Infrahub...")
    start(context)
    console.print()

    # Step 3: Bootstrap
    console.print("[bold magenta]Step 3/4:[/bold magenta] Running bootstrap...")
    bootstrap_py(context)
    console.print()

    # Step 4: Demo
    console.print("[bold magenta]Step 4/4:[/bold magenta] Loading Arista DC demo...")
    demo_dc_arista(context)

    console.print()
    console.print(
        Panel(
            "[bold green]✓ Infrahub initialized successfully[/bold green]\n\n"
            "[cyan]Infrahub UI:[/cyan] http://localhost:8000\n"
            + ("[cyan]Service Catalog:[/cyan] http://localhost:8501\n" if INFRAHUB_SERVICE_CATALOG else "")
            + "[cyan]Branch:[/cyan] add-dc3",
            border_style="green",
            box=box.SIMPLE,
        )
    )
    console.print()


@task(name="run-tests")
def run_tests(context: Context) -> None:
    """Run all tests."""
    console.print()
    console.print(Panel("[bold cyan]Running Tests[/bold cyan]", border_style="cyan", box=box.SIMPLE))
    context.run("pytest -vv tests")
    console.print("[green]✓[/green] Tests completed")


@task(name="format")
def format_code(context: Context) -> None:
    """Run ruff to format all Python files."""
    console.print()
    console.print(Panel("[bold green]Formatting Code[/bold green]", border_style="green", box=box.SIMPLE))
    exec_cmds = ["ruff format .", "ruff check . --fix"]
    with context.cd(MAIN_DIRECTORY_PATH):
        for cmd in exec_cmds:
            console.print(f"\n[green]→[/green] Running {cmd}...")
            context.run(cmd)
    console.print("\n[green]✓[/green] Formatting completed!")
    console.print()


@task(name="_lint-markdown")
def lint_markdown(context: Context) -> None:
    """Run Linter to check all Markdown files."""
    print(" - Check code with rumdl")
    exec_cmd = "rumdl check ."
    with context.cd(MAIN_DIRECTORY_PATH):
        context.run(exec_cmd)


@task(name="_lint-yaml")
def lint_yaml(context: Context) -> None:
    """Run Linter to check all YAML files."""
    print(" - Check code with yamllint")
    exec_cmd = "yamllint ."
    with context.cd(MAIN_DIRECTORY_PATH):
        context.run(exec_cmd)


@task(name="_lint-mypy")
def lint_mypy(context: Context) -> None:
    """Run mypy to check all Python files."""
    print(" - Check code with mypy")
    exec_cmd = "mypy --show-error-codes ."
    with context.cd(MAIN_DIRECTORY_PATH):
        context.run(exec_cmd)


@task(name="_lint-ruff")
def lint_ruff(context: Context) -> None:
    """Run ruff to check all Python files."""
    print(" - Check code with ruff")
    exec_cmd = "ruff check ."
    with context.cd(MAIN_DIRECTORY_PATH):
        context.run(exec_cmd)


@task(name="lint")
def lint_all(context: Context) -> None:
    """Run all linters."""
    console.print()
    console.print(
        Panel(
            "[bold yellow]Running All Linters[/bold yellow]\n[dim]Markdown → YAML → Ruff → Mypy[/dim]",
            border_style="yellow",
            box=box.SIMPLE,
        )
    )

    console.print("\n[yellow]→[/yellow] Running rumdl...")
    lint_markdown(context)

    console.print("\n[yellow]→[/yellow] Running yamllint...")
    lint_yaml(context)

    console.print("\n[yellow]→[/yellow] Running ruff...")
    lint_ruff(context)

    console.print("\n[yellow]→[/yellow] Running mypy...")
    lint_mypy(context)

    console.print("\n[green]✓[/green] All linters completed!")
    console.print()


@task(optional=["branch"], name="demo-cloud")
def demo_cloud(context: Context, branch: str = "demo-cloud") -> None:
    """Load cloud provider demo data (AWS, GCP, Azure)."""
    console.print()
    console.print(
        Panel(
            f"[bold cyan]Cloud Provider Demo[/bold cyan]\n"
            f"[dim]Branch:[/dim] {branch}\n"
            f"[dim]Providers:[/dim] AWS, GCP, Azure",
            border_style="cyan",
            box=box.SIMPLE,
        )
    )

    console.print(f"\n[cyan]→[/cyan] Creating branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl branch create {branch}")

    console.print(f"\n[cyan]→[/cyan] Loading cloud schema to branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl schema load schemas/ --branch {branch}")

    console.print(f"\n[cyan]→[/cyan] Loading cloud objects to branch: [bold]{branch}[/bold]")
    context.run(f"uv run infrahubctl object load objects/cloud/ --branch {branch}")

    console.print(f"\n[green]✓[/green] Cloud demo loaded to branch '[bold green]{branch}[/bold green]'")

    cloud_url = f"http://localhost:8000/objects/CloudResource?branch={branch}"
    console.print()
    console.print(
        Panel(
            f"[bold green]✓ Cloud demo ready[/bold green]\n\n[cyan]Cloud Resources:[/cyan] {cloud_url}",
            border_style="green",
            box=box.SIMPLE,
        )
    )
    console.print()


@task(optional=["branch", "device"], name="demo-conflict")
def demo_conflict(context: Context, branch: str = "conflict-demo", device: str = "cisco-switch-01") -> None:
    """Create a branch with a data conflict for testing conflict resolution."""
    console.print()
    console.print(
        Panel(
            f"[bold bright_red]Conflict Demo[/bold bright_red]\n"
            f"[dim]Branch:[/dim] {branch}\n"
            f"[dim]Device:[/dim] {device}",
            border_style="bright_red",
            box=box.SIMPLE,
        )
    )

    context.run(f"uv run python scripts/create_conflict.py --branch {branch} --device {device}", pty=True)


# ===========================================================================
# Webhook Firewall Demo (profile: webhook-demo)
# ---------------------------------------------------------------------------
# The demo adds a custom-webhook-driven pipeline: rule changes in Infrahub's
# SecurityPolicy trigger a rendered VyOS config artifact, which fires a
# CoreCustomWebhook, which the listener container receives and applies to a
# containerlab-hosted VyOS firewall (fw1) via ansible. All webhook-demo
# services live on compose profile `webhook-demo` so non-demo invoke tasks
# are unaffected. See docs/docs/webhook-firewall-demo.mdx for the presenter
# quickstart.
# ===========================================================================

WEBHOOK_DEMO_PROFILE = "webhook-demo"
WEBHOOK_LAB_TOPOLOGY = "lab/demo-webhook.clab.yml"
WEBHOOK_LAB_MGMT_IP = "172.20.20.11"
WEBHOOK_LISTENER_CONTAINER = "webhook-listener"
WEBHOOK_LAB_CONTAINER = "clab-cwdemo-webhook-demo-fw1"


def _webhook_compose_cmd() -> str:
    """Compose command with the webhook-demo profile enabled."""
    return f"{COMPOSE_COMMAND} --profile {WEBHOOK_DEMO_PROFILE}"


def _wait_for_http_ok(context: Context, url: str, timeout_s: int = 60) -> bool:
    """Poll `url` until it returns HTTP 200 or timeout; return success bool."""
    import httpx  # lazy import; keeps top-level import surface unchanged

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


@task(name="demo-webhook-lab-up")
def demo_webhook_lab_up(context: Context) -> None:
    """Deploy the VyOS containerlab topology for the webhook demo (fw1)."""
    console.print()
    console.print(
        Panel(
            f"[bold cyan]Webhook demo — lab up[/bold cyan]\n"
            f"[dim]Topology:[/dim] {WEBHOOK_LAB_TOPOLOGY}\n"
            f"[dim]fw1 mgmt:[/dim] {WEBHOOK_LAB_MGMT_IP}",
            border_style="cyan",
            box=box.SIMPLE,
        )
    )
    # Strip any stale host key for fw1 — containerlab generates a fresh SSH
    # host key on every deploy; paramiko inside the listener container honors
    # known_hosts regardless of ANSIBLE_HOST_KEY_CHECKING (FR-012).
    context.run(f"ssh-keygen -R {WEBHOOK_LAB_MGMT_IP} 2>/dev/null || true", warn=True)
    context.run(f"sudo -n $(command -v containerlab) deploy -t {WEBHOOK_LAB_TOPOLOGY}")
    console.print(f"[green]✓[/green] fw1 up at {WEBHOOK_LAB_MGMT_IP}")


@task(name="demo-webhook-lab-down")
def demo_webhook_lab_down(context: Context) -> None:
    """Tear down the VyOS containerlab topology for the webhook demo."""
    console.print()
    console.print(
        Panel(
            "[bold yellow]Webhook demo — lab down[/bold yellow]",
            border_style="yellow",
            box=box.SIMPLE,
        )
    )
    context.run(
        f"sudo -n $(command -v containerlab) destroy -t {WEBHOOK_LAB_TOPOLOGY} --cleanup",
        warn=True,
    )
    context.run(f"ssh-keygen -R {WEBHOOK_LAB_MGMT_IP} 2>/dev/null || true", warn=True)
    console.print("[green]✓[/green] fw1 destroyed")


@task(name="demo-webhook-listener-up")
def demo_webhook_listener_up(context: Context) -> None:
    """Start the webhook listener container (requires lab up first)."""
    console.print()
    console.print(
        Panel(
            "[bold cyan]Webhook demo — listener up[/bold cyan]",
            border_style="cyan",
            box=box.SIMPLE,
        )
    )
    context.run(f"{_webhook_compose_cmd()} up -d {WEBHOOK_LISTENER_CONTAINER}")
    # Prime the container's known_hosts with fw1's current key so paramiko
    # doesn't hit a mismatch on the first ansible run (FR-012).
    console.print("\n[cyan]→[/cyan] Priming listener known_hosts with fw1 key")
    context.run(
        f"docker exec {WEBHOOK_LISTENER_CONTAINER} bash -c "
        f"'mkdir -p /root/.ssh && ssh-keyscan -t ed25519,rsa {WEBHOOK_LAB_MGMT_IP} "
        f">> /root/.ssh/known_hosts 2>/dev/null || true'",
        warn=True,
    )
    # Wait for /healthz.
    listener_port = os.getenv("WEBHOOK_LISTENER_PORT", "8001")
    health_url = f"http://127.0.0.1:{listener_port}/healthz"
    if _wait_for_http_ok(context, health_url, timeout_s=60):
        console.print(f"[green]✓[/green] Listener healthy at {health_url}")
    else:
        console.print(f"[red]✗[/red] Listener did not become healthy at {health_url}")
        sys.exit(1)


@task(name="demo-webhook-listener-down")
def demo_webhook_listener_down(context: Context) -> None:
    """Stop the webhook listener container."""
    console.print()
    console.print(
        Panel(
            "[bold yellow]Webhook demo — listener down[/bold yellow]",
            border_style="yellow",
            box=box.SIMPLE,
        )
    )
    context.run(f"{_webhook_compose_cmd()} stop {WEBHOOK_LISTENER_CONTAINER}")
    context.run(f"{_webhook_compose_cmd()} rm -f {WEBHOOK_LISTENER_CONTAINER}", warn=True)
    console.print("[green]✓[/green] Listener stopped")


@task(name="demo-webhook-sync")
def demo_webhook_sync(context: Context) -> None:
    """Re-apply the current main vyos-firewall-config artifact to fw1."""
    console.print()
    console.print(
        Panel(
            "[bold cyan]Webhook demo — sync fw1 with main[/bold cyan]",
            border_style="cyan",
            box=box.SIMPLE,
        )
    )
    context.run("uv run python scripts/webhook_demo/sync_firewall.py", pty=True, warn=True)


@task(name="demo-webhook-seed")
def demo_webhook_seed(context: Context) -> None:
    """Seed fw1 + SecurityPolicy + 3 base rules, then add 17 more via PC merge."""
    console.print()
    console.print(
        Panel(
            "[bold cyan]Webhook demo — seed + bootstrap PC[/bold cyan]\n"
            "[dim]Seeds 3 base rules on main, then adds 17 more via a proposed-change merge.[/dim]",
            border_style="cyan",
            box=box.SIMPLE,
        )
    )
    console.print("\n[cyan]→[/cyan] Seeding base data + 3 rules")
    context.run("uv run python scripts/webhook_demo/seed_webhook_demo.py", pty=True)
    console.print("\n[cyan]→[/cyan] Registering CoreCustomWebhook")
    context.run("uv run python scripts/webhook_demo/register_webhook.py", pty=True)
    console.print("\n[cyan]→[/cyan] Creating branch + 17 rules + PC + merge")
    context.run("uv run python scripts/webhook_demo/bootstrap_pc_rules.py", pty=True)
    console.print("\n[green]✓[/green] Demo seeded — 20 rules on main, fw1 deployed")


@task(name="demo-webhook-up")
def demo_webhook_up(context: Context) -> None:
    """Bring up the full webhook firewall demo (lab + listener + seed data).

    Assumes `invoke start` and `invoke bootstrap` have already run. The webhook
    demo requires INFRAHUB_GIT_LOCAL=true so the task-worker sees this repo's
    working tree (with the webhook-demo transforms in .infrahub.yml) via the
    already-configured /upstream mount.
    """
    console.print()
    console.print(
        Panel(
            "[bold bright_cyan]Webhook Firewall Demo — up[/bold bright_cyan]\n"
            "[dim]Lab → Listener → Seed data (requires Infrahub already running)[/dim]",
            border_style="bright_cyan",
            box=box.SIMPLE,
        )
    )
    if not INFRAHUB_GIT_LOCAL:
        console.print(
            "[yellow]⚠[/yellow] INFRAHUB_GIT_LOCAL is not set to true. The webhook demo "
            "requires the /upstream mount so Infrahub can read this repo's local "
            ".infrahub.yml. Run:\n"
            "    export INFRAHUB_GIT_LOCAL=true && invoke destroy && invoke start && invoke bootstrap\n"
            "then re-run demo-webhook-up."
        )
        sys.exit(1)
    demo_webhook_lab_up(context)
    demo_webhook_listener_up(context)
    demo_webhook_seed(context)
    console.print()
    console.print(
        Panel(
            "[bold green]✓ Webhook demo ready[/bold green]\n\n"
            "[cyan]Infrahub UI:[/cyan] http://localhost:8000\n"
            "[cyan]Listener:[/cyan]   http://localhost:8001/healthz\n"
            "[cyan]fw1:[/cyan]        ssh admin@172.20.20.11",
            border_style="green",
            box=box.SIMPLE,
        )
    )


@task(name="demo-webhook-down")
def demo_webhook_down(context: Context) -> None:
    """Stop the webhook demo (preserves Infrahub data)."""
    console.print()
    console.print(
        Panel(
            "[bold yellow]Webhook Firewall Demo — down[/bold yellow]",
            border_style="yellow",
            box=box.SIMPLE,
        )
    )
    demo_webhook_listener_down(context)
    demo_webhook_lab_down(context)
    console.print("\n[green]✓[/green] Webhook demo stopped (Infrahub data preserved)")


@task(name="demo-webhook-destroy")
def demo_webhook_destroy(context: Context) -> None:
    """Destroy the webhook demo — stop containers and remove any webhook-demo volumes."""
    console.print()
    console.print(
        Panel(
            "[bold red]Webhook Firewall Demo — destroy[/bold red]",
            border_style="red",
            box=box.SIMPLE,
        )
    )
    demo_webhook_down(context)
    # The listener has no persistent volume today, but if any future
    # webhook-demo service adds one this ensures it's wiped.
    context.run(f"{_webhook_compose_cmd()} down -v", warn=True)
    console.print("[green]✓[/green] Webhook demo destroyed")


@task(name="docs")
def docs_build(context: Context) -> None:
    """Build documentation website."""
    console.print()
    console.print(
        Panel(
            f"[bold blue]Building Documentation Website[/bold blue]\n[dim]Directory:[/dim] {DOCUMENTATION_DIRECTORY}",
            border_style="blue",
            box=box.SIMPLE,
        )
    )

    exec_cmd = "npm run build"

    with context.cd(DOCUMENTATION_DIRECTORY):
        output = context.run(exec_cmd)

    if output and output.exited != 0:
        console.print("[red]✗[/red] Documentation build failed")
        sys.exit(-1)

    console.print("[green]✓[/green] Documentation built successfully")
    console.print()
