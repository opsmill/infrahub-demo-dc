#!/usr/bin/env python3
"""
Bootstrap Infrahub with schemas, data, and configurations.

This script automates the complete setup of an Infrahub instance with all necessary
data for the demo-dc demonstration environment. It performs a sequential bootstrap
process with visual feedback and error handling.

Bootstrap Process (7 steps):
==========================
1. Load Schemas - Define data models (DCIM, IPAM, Topology, Security)
2. Load Menu Definitions - Configure UI navigation structure
3. Load Bootstrap Data - Create foundation objects (locations, platforms, roles,
                         manufacturers, device types, ASNs, IP prefixes, pools, designs)
4. Load Security Data - Create security zones, policies, and rules
5. Create Users & Roles - Load user accounts (emma, otto), roles and permissions from objects/rbac
6. Add Repository - Register demo-dc Git repository (local or GitHub)
7. Load Event Actions - Configure automation triggers (optional, may need repository sync)

Features:
=========
- Rich terminal UI with color-coded progress indicators
- Automatic Infrahub readiness checking with retry logic
- Beautiful progress bars showing time elapsed and remaining
- Structured error handling with helpful diagnostic messages
- Support for local development (INFRAHUB_GIT_LOCAL=true) or GitHub repository
- Repository sync waiting period to ensure generators/transforms are available
- Step-by-step visual feedback with emoji icons and status colors

Error Handling:
===============
- Checks Infrahub availability before starting (30 retry attempts)
- Validates each step completion before proceeding
- Provides clear error messages with remediation instructions
- Gracefully handles already-existing repository without failure
- Allows event actions to fail (optional step) if repository isn't synced

Usage:
======
    python scripts/bootstrap.py              # Use main branch
    python scripts/bootstrap.py --branch dev # Use specific branch
    uv run invoke bootstrap                  # Via invoke task (recommended)

Environment Variables:
======================
    INFRAHUB_ADDRESS: Infrahub to load the demo into (default: http://localhost:8000).
    INFRAHUB_API_TOKEN: Token for that Infrahub.
    INFRAHUB_GIT_LOCAL: Set to 'true' to use local repository (/upstream mount)
                       instead of GitHub. Requires docker-compose.override.yml
                       volume mount configuration.
    INFRAHUB_REPO_URL: Repository to register when INFRAHUB_GIT_LOCAL is off
                       (default: the opsmill repository on GitHub). A fork sets its own.
    INFRAHUB_REPO_REF: Git ref of that repository (default: main).

Exit Codes:
===========
    0: Bootstrap completed successfully
    1: Bootstrap failed (Infrahub not ready or step failure)
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import yaml
from infrahub_sdk import InfrahubClientSync
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule

# ============================================================================
# CONFIGURATION AND SETUP
# ============================================================================

# Initialize Rich console for beautiful terminal output
console = Console()

# Infrahub connection settings: the local stack unless INFRAHUB_ADDRESS names another Infrahub,
# such as a public sandbox
INFRAHUB_ADDRESS = os.getenv("INFRAHUB_ADDRESS", "http://localhost:8000")

# Repository mode: local development or GitHub
# When True, uses /upstream mount for local generator/transform development
# When False, uses GitHub repository (read-only)
INFRAHUB_GIT_LOCAL = os.getenv("INFRAHUB_GIT_LOCAL", "false").lower() == "true"

# Repository and ref to register in GitHub mode. Empty keeps the values in objects/git-repo/github.yml
INFRAHUB_REPO_URL = os.getenv("INFRAHUB_REPO_URL", "")
INFRAHUB_REPO_REF = os.getenv("INFRAHUB_REPO_REF", "")

# How long the repository import may take before the bootstrap stops, and how often it is checked
REPOSITORY_SYNC_TIMEOUT = 900
REPOSITORY_SYNC_POLL_INTERVAL = 5


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def check_infrahub_ready(max_retries: int = 30, sleep_time: int = 2) -> bool:
    """
    Check if Infrahub API is ready to accept requests.

    This function polls the Infrahub /api/schema endpoint to verify the service
    is fully started and responsive. It's essential to wait for Infrahub before
    attempting to load data, as the API may not be ready immediately after
    container startup.

    The function displays a visual progress bar showing:
    - Spinning dots animation (indicates activity)
    - Percentage of retry attempts completed
    - Time elapsed since start

    Args:
        max_retries: Maximum number of connection attempts (default: 30)
        sleep_time: Seconds to wait between attempts (default: 2)
                   Total wait time = max_retries * sleep_time (default: 60 seconds)

    Returns:
        True if Infrahub responds with HTTP 200, False if all retries exhausted

    Example:
        >>> if check_infrahub_ready():
        ...     # Proceed with bootstrap
        ... else:
        ...     # Exit with error
    """
    console.print()  # Add blank line for spacing

    # Create a Rich progress bar with multiple columns for visual feedback
    with Progress(
        SpinnerColumn(spinner_name="dots12", style="bold bright_magenta"),  # Animated spinner
        TextColumn("[progress.description]{task.description}", style="bold white"),  # Task description
        BarColumn(  # Progress bar showing completion percentage
            bar_width=60,
            style="magenta",  # In-progress color
            complete_style="bright_green",  # Completed color
            finished_style="bold bright_green",  # Final state color
            pulse_style="bright_magenta",  # Pulsing animation color
        ),
        TextColumn("[bold bright_cyan]{task.percentage:>3.0f}%"),  # Numeric percentage
        TextColumn("•", style="dim"),  # Separator
        TimeElapsedColumn(),  # Time elapsed counter
        console=console,
    ) as progress:
        task = progress.add_task("→ Checking if Infrahub is ready", total=max_retries)

        # Retry loop: attempt to connect to Infrahub API
        for attempt in range(max_retries):
            try:
                # Test connectivity by requesting the schema endpoint
                # This endpoint is lightweight and indicates API readiness
                response = requests.get(f"{INFRAHUB_ADDRESS}/api/schema", timeout=2)
                if response.status_code == 200:
                    # Success! Infrahub is ready
                    progress.update(task, completed=max_retries)  # Complete the progress bar
                    console.print("[bold green]✓ Infrahub is ready![/bold green]\n")
                    return True
            except requests.exceptions.RequestException:
                # Connection failed (connection refused, timeout, etc.)
                # This is expected during startup, so we continue retrying
                pass

            # Update progress bar and wait before next attempt
            progress.update(task, advance=1)
            time.sleep(sleep_time)

    # All retries exhausted - Infrahub is not responding
    console.print()
    console.print(
        Panel(
            "[red]✗ ERROR: Infrahub is not responding[/red]\n\n"
            "[dim]Please ensure Infrahub is running with:[/dim]\n"
            "  [bold]uv run invoke start[/bold]\n\n"
            "[dim]Check container status with:[/dim]\n"
            "  [bold]docker ps[/bold]",
            title="Connection Error",
            border_style="red",
            box=box.SIMPLE,
        )
    )
    return False


def run_command(command: str, description: str, step: str, color: str = "cyan", icon: str = "") -> bool:
    """
    Execute a shell command with visual feedback and error handling.

    This function runs infrahubctl commands (schema load, object load, etc.) and
    provides clear visual feedback about the operation status. Commands are executed
    via subprocess with real-time output streaming to the terminal.

    Visual Elements:
    - Step number and color-coded header (e.g., "[1/7] 📋 Loading schemas")
    - Real-time command output (streamed to terminal)
    - Success or failure indicator with matching icon
    - Color-coded completion message

    Args:
        command: Shell command to execute (e.g., "uv run infrahubctl schema load...")
        description: Human-readable description of the operation
        step: Step indicator (e.g., "[1/7]", "[2/7]")
        color: Rich color name for visual theming (default: "cyan")
        icon: Emoji icon for visual identification (default: "")

    Returns:
        True if command succeeded (exit code 0), False if it failed

    Example:
        >>> success = run_command(
        ...     "uv run infrahubctl schema load schemas",
        ...     "Loading schemas",
        ...     "[1/7]",
        ...     "blue",
        ...     "📋"
        ... )
        >>> if not success:
        ...     print("Bootstrap failed!")
    """
    icon_display = f"{icon} " if icon else ""

    # Display step header with color-coded styling
    console.print(
        f"\n[bold {color} on black]{step}[/bold {color} on black] {icon_display}[bold white]{description}[/bold white]"
    )

    try:
        # Execute the command
        # capture_output=False allows real-time output streaming to terminal
        # check=True raises CalledProcessError if command fails (non-zero exit code)
        subprocess.run(command, shell=True, check=True, capture_output=False, text=True)

        # Command succeeded - display success message
        msg = "[bold bright_green on black]✓[/bold bright_green on black] "
        msg += f"{icon_display}[bold {color}]{description} completed[/bold {color}]"
        console.print(msg)
        return True
    except subprocess.CalledProcessError as e:
        # Command failed - display error message
        console.print(f"[bold red]✗[/bold red] {icon_display}[red]Failed: {description}[/red]")
        console.print(f"[dim]Error: {e}[/dim]")
        return False


def repository_file(template: str, url: str, ref: str) -> str:
    """
    Return the repository object file to load, with the URL and ref replaced when given.

    Args:
        template: Path of the repository object file in objects/git-repo/
        url: Repository URL to register instead of the one in the file, or "" to keep it
        ref: Git ref to register instead of the one in the file, or "" to keep it

    Returns:
        The template itself when nothing is replaced, else a temporary copy with the new values
    """
    if not url and not ref:
        return template
    spec = yaml.safe_load(Path(template).read_text())
    repository = spec["spec"]["data"][0]
    if url:
        repository["location"] = url
    if ref:
        repository["ref"] = ref
    with tempfile.NamedTemporaryFile("w", suffix=".yml", prefix="git-repo-", delete=False) as rendered:
        yaml.safe_dump(spec, rendered, sort_keys=False)
    return rendered.name


def repository_name(repo_file: str) -> str:
    """Return the name of the repository that a repository object file registers."""
    return str(yaml.safe_load(Path(repo_file).read_text())["spec"]["data"][0]["name"])


def check_repository_configuration(repo_file: str, branch: str) -> bool:
    """Allow fresh registration or an unchanged repository, without updating its configuration.

    Object load uses upsert, which does not schedule the read-only repository update's import
    in Infrahub 1.11.8. Its previous in-sync status cannot prove the changed configuration was
    imported. Reject a change before loading the object rather than accepting that stale status.
    """
    spec = yaml.safe_load(Path(repo_file).read_text())["spec"]
    desired = spec["data"][0]
    client = InfrahubClientSync(address=INFRAHUB_ADDRESS)
    repository = client.get(
        kind="CoreGenericRepository",
        name__value=desired["name"],
        branch=branch,
        fragment=True,
        raise_when_missing=False,
    )
    if repository is None:
        return True

    ref_field = "ref" if spec["kind"] == "CoreReadOnlyRepository" else "default_branch"
    if repository.get_kind() == spec["kind"] and all(
        getattr(repository, field).value == desired[field] for field in ("location", ref_field)
    ):
        return True

    console.print(
        f"Repository {desired['name']} on Infrahub branch {branch} has a different kind, URL or ref. "
        "Bootstrap cannot verify a new import after changing an existing repository. "
        "Restore INFRAHUB_GIT_LOCAL, INFRAHUB_REPO_URL and INFRAHUB_REPO_REF to match the repository "
        "shown in Infrahub, or use a fresh Infrahub instance to register the new configuration.",
        style="bold red",
        markup=False,
    )
    return False


def wait_for_repository_sync(
    name: str,
    timeout: int = REPOSITORY_SYNC_TIMEOUT,
    poll_interval: int = REPOSITORY_SYNC_POLL_INTERVAL,
    branch: str = "main",
) -> bool:
    """
    Wait until Infrahub has imported the repository.

    After adding a Git repository, Infrahub clones it and imports .infrahub.yml: the generators,
    transforms and checks. The event actions load only once that import has ended, so wait for
    the repository's sync status instead of a fixed time: the import takes longer on a remote
    Infrahub than on a laptop.

    Args:
        name: Name of the repository in Infrahub
        timeout: Seconds to wait before giving up
        poll_interval: Seconds between two checks
        branch: Infrahub branch containing the repository (default: "main")

    Returns:
        True when the repository is in sync, False when the import failed or timed out
    """
    client = InfrahubClientSync(address=INFRAHUB_ADDRESS)
    deadline = time.monotonic() + timeout
    status = "unknown"
    with console.status(f"[bold bright_yellow]🔄 Waiting for the import of repository {name}[/bold bright_yellow]"):
        while time.monotonic() < deadline:
            repository = client.get(
                kind="CoreGenericRepository", name__value=name, branch=branch, raise_when_missing=False
            )
            status = repository.sync_status.value if repository else "missing"  # type: ignore[attr-defined]
            if status == "in-sync":
                msg = "[bold bright_green on black]✓[/bold bright_green on black] "
                msg += "🔄 [bold bright_yellow]Repository sync complete[/bold bright_yellow]\n"
                console.print(msg)
                return True
            if status.startswith("error"):
                console.print(f"[bold red]✗[/bold red] 🔄 [red]Repository {name} import failed: {status}[/red]")
                return False
            time.sleep(poll_interval)
    console.print(f"[bold red]✗[/bold red] 🔄 [red]Repository {name} still {status} after {timeout} s[/red]")
    return False


def main(branch: str = "main") -> int:
    """
    Execute the complete Infrahub bootstrap process.

    This is the primary orchestrator function that coordinates all bootstrap steps
    in the correct sequence. It provides a complete, automated setup of Infrahub
    with all necessary schemas, data, and configurations for the demo-dc demo.

    Bootstrap Sequence:
    ===================
    1. Display welcome panel with branch information
    2. Check Infrahub readiness (wait up to 60 seconds)
    3. Load schemas (data models for DCIM, IPAM, Topology, Security)
    4. Load menu definitions (UI navigation structure)
    5. Load bootstrap data (locations, platforms, roles, devices, etc.)
    6. Load security data (zones, policies, rules)
    7. Load user accounts and roles (emma, otto) from objects/rbac
    8. Add demo-dc Git repository (local or GitHub)
    9. Wait until the repository import has ended
    10. Load event actions
    11. Display success message with next steps

    Visual Feedback:
    ================
    - Color-coded step headers with emoji icons
    - Real-time command output streaming
    - Success/failure indicators for each step
    - Visual separators between steps
    - Progress bars for waiting operations
    - Completion panel with next steps

    Error Handling:
    ===============
    - Exits immediately if Infrahub is not ready
    - Exits immediately if any required step fails
    - Allows repository addition to fail gracefully (if already exists)
    - Exits if the repository import fails or the event actions do not load

    Args:
        branch: Infrahub branch to load data into (default: "main")
               All schemas, data, and objects will be loaded to this branch.

    Returns:
        0 if bootstrap completed successfully
        1 if bootstrap failed (Infrahub not ready or required step failed)

    Example:
        >>> # Bootstrap to main branch
        >>> exit_code = main(branch="main")
        >>> if exit_code == 0:
        ...     print("Bootstrap succeeded!")

        >>> # Bootstrap to development branch
        >>> exit_code = main(branch="dev")

    Environment Variables:
        INFRAHUB_GIT_LOCAL: When set to "true", uses local repository mount
                           at /upstream instead of GitHub repository.
                           Requires docker-compose.override.yml configuration.

    Next Steps After Bootstrap:
        - Demo DC creation: uv run invoke demo-dc-arista
        - Create proposed changes via Infrahub UI
        - Access Infrahub at http://localhost:8000
    """
    console.print()
    console.print(
        Panel(
            f"[bold bright_blue]🚀 Infrahub demo-dc Bootstrap[/bold bright_blue]\n"
            f"[bright_cyan]Branch:[/bright_cyan] [bold yellow]{branch}[/bold yellow]\n\n"
            "[dim]This will load:[/dim]\n"
            "  [blue]•[/blue] Schemas\n"
            "  [magenta]•[/magenta] Menu definitions\n"
            "  [yellow]•[/yellow] Bootstrap data\n"
            "  [green]•[/green] Security data\n"
            "  [bright_magenta]•[/bright_magenta] demo-dc repository",
            border_style="bright_blue",
            box=box.SIMPLE,
            title="[bold bright_blue]Bootstrap Process[/bold bright_blue]",
        )
    )

    # Check if Infrahub is ready before proceeding with bootstrap
    # This prevents attempting to load data before the API is available
    if not check_infrahub_ready():
        return 1

    # Define all required bootstrap steps with visual theming
    # Each step includes: step number, description, command, color, and icon
    steps = [
        {
            "step": "[1/7]",
            "description": "Loading schemas",
            "command": f"uv run infrahubctl schema load schemas --branch {branch}",
            "color": "blue",
            "icon": "📋",
        },
        {
            "step": "[2/7]",
            "description": "Loading menu definitions",
            "command": f"uv run infrahubctl menu load menus/menu-full.yml --branch {branch}",
            "color": "magenta",
            "icon": "📑",
        },
        {
            "step": "[3/7]",
            "description": "Loading bootstrap data (locations, platforms, roles, etc.)",
            "command": f"uv run infrahubctl object load objects/bootstrap/ --branch {branch}",
            "color": "yellow",
            "icon": "📦",
        },
        {
            "step": "[4/7]",
            "description": "Loading security data (zones, policies, rules)",
            "command": f"uv run infrahubctl object load objects/security/ --branch {branch}",
            "color": "green",
            "icon": "🔒",
        },
        # {
        #     "step": "[5/7]",
        #     "description": "Populating security relationships",
        #     "command": "uv run python scripts/populate_security_relationships.py",
        #     "color": "cyan",
        #     "icon": "🔗",
        # },
        {
            "step": "[5/7]",
            "description": "Creating user accounts and roles",
            # No --branch: accounts, account groups and global permissions are branch-agnostic
            "command": "uv run infrahubctl object load objects/rbac/",
            "color": "bright_blue",
            "icon": "👥",
        },
    ]

    # Execute all required bootstrap steps in sequence
    # Each step must succeed before proceeding to the next
    for i, step_info in enumerate(steps):
        if not run_command(
            step_info["command"],
            step_info["description"],
            step_info["step"],
            step_info["color"],
            step_info["icon"],
        ):
            console.print("\n[bold red]✗ Bootstrap failed![/bold red]")
            return 1

        # Add visual separator after each step (except the last one)
        if i < len(steps) - 1:
            console.print(Rule(style=f"dim {step_info['color']}"))

    # ========================================================================
    # Step 6: Add Git Repository
    # ========================================================================
    # This step adds the demo-dc Git repository to Infrahub, which contains
    # Python generators and transforms needed for topology creation and
    # configuration generation. This step may fail gracefully if the repository
    # already exists from a previous bootstrap run.
    msg = "\n[bold bright_magenta on black][6/7][/bold bright_magenta on black] "
    msg += "📚 [bold white]Adding demo-dc repository[/bold white]"
    console.print(msg)

    # Choose repository source based on INFRAHUB_GIT_LOCAL environment variable
    # Local mode: Uses /upstream mount for development (requires docker-compose.override.yml)
    # GitHub mode: Uses read-only GitHub repository (production/demo mode)
    if INFRAHUB_GIT_LOCAL:
        repo_file = "objects/git-repo/local-dev.yml"
        console.print("[dim]Using local repository: /upstream[/dim]")
    else:
        repo_file = repository_file("objects/git-repo/github.yml", INFRAHUB_REPO_URL, INFRAHUB_REPO_REF)
        location = INFRAHUB_REPO_URL or "https://github.com/opsmill/infrahub-demo-dc.git"
        console.print(f"[dim]Using GitHub repository: {location} ({INFRAHUB_REPO_REF or 'main'})[/dim]")

    if not check_repository_configuration(repo_file, branch=branch):
        return 1

    # Execute repository addition command
    # capture_output=True prevents streaming to terminal (we handle output manually)
    result = subprocess.run(
        f"uv run infrahubctl object load {repo_file} --branch {branch}",
        shell=True,
        capture_output=True,
        text=True,
    )

    # Handle repository addition result with graceful failure for duplicates
    if result.returncode == 0:
        msg = "[bold bright_green on black]✓[/bold bright_green on black] "
        msg += "📚 [bold bright_magenta]Repository added[/bold bright_magenta]"
        console.print(msg)
    else:
        if "already exists" in result.stderr.lower() or "already exists" in result.stdout.lower():
            msg = "[bold yellow on black]⚠[/bold yellow on black] "
            msg += "📚 [bold bright_magenta]Repository already exists, skipping...[/bold bright_magenta]"
            console.print(msg)
        else:
            console.print("[bold red]✗[/bold red] 📚 [red]Failed to add repository[/red]")
            console.print(f"[dim]{result.stderr}[/dim]")
            return 1

    console.print(Rule(style="dim bright_magenta"))

    # ========================================================================
    # Repository Import
    # ========================================================================
    # After adding the repository, Infrahub clones it and imports its generators, transforms and
    # checks. The event actions reference those generators, so wait until the import has ended.
    console.print()  # Add spacing
    if not wait_for_repository_sync(repository_name(repo_file), branch=branch):
        console.print("\n[bold red]✗ Bootstrap failed![/bold red]")
        return 1

    console.print(Rule(style="dim bright_yellow"))

    # ========================================================================
    # Step 7: Load Event Actions
    # ========================================================================
    # Event actions define automated triggers and responses in Infrahub, such as the generator
    # that builds a data center when its topology object is created.
    if not run_command(
        f"uv run infrahubctl object load objects/events/ --branch {branch}",
        "Loading event actions",
        "[7/7]",
        "bright_cyan",
        "⚡",
    ):
        console.print("\n[bold red]✗ Bootstrap failed![/bold red]")
        return 1

    console.print(Rule(style="dim bright_cyan"))

    # Display completion message
    console.print()
    console.print(
        Panel(
            f"[bold bright_green]🎉 Bootstrap Complete![/bold bright_green]\n\n"
            f"[dim]All data has been loaded into Infrahub[/dim]\n"
            f"[bright_cyan]Branch:[/bright_cyan] [bold yellow]{branch}[/bold yellow]\n\n"
            "[bold bright_magenta]Next steps:[/bold bright_magenta]\n"
            "  [green]•[/green] Demo a DC design: [bold bright_cyan]uv run invoke demo-dc-arista[/bold bright_cyan]\n"
            "  [green]•[/green] Create a Proposed Change",
            title="[bold bright_green]✓ Success[/bold bright_green]",
            border_style="bright_green",
            box=box.SIMPLE,
        )
    )

    return 0


# ============================================================================
# COMMAND-LINE INTERFACE
# ============================================================================
# This section handles script execution from the command line with argument
# parsing. The script can be run directly or via invoke tasks.

if __name__ == "__main__":
    # Set up argument parser for command-line options
    parser = argparse.ArgumentParser(description="Bootstrap Infrahub with schemas, data, and configurations")

    # Add --branch argument to allow targeting specific Infrahub branches
    # Default is "main" branch, but users can specify development branches
    parser.add_argument(
        "--branch",
        "-b",
        type=str,
        default="main",
        help="Branch to load data into (default: main)",
    )

    # Parse command-line arguments
    args = parser.parse_args()

    # Execute main bootstrap function and exit with its return code
    # Return code 0 = success, 1 = failure
    sys.exit(main(branch=args.branch))
