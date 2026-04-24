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
5. Create Users & Roles - Set up user accounts (emma, otto) with permissions
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
    INFRAHUB_GIT_LOCAL: Set to 'true' to use local repository (/upstream mount)
                       instead of GitHub. Requires docker-compose.override.yml
                       volume mount configuration.

Exit Codes:
===========
    0: Bootstrap completed successfully
    1: Bootstrap failed (Infrahub not ready or step failure)
"""

import argparse
import os
import subprocess
import sys
import time

import requests
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.rule import Rule

# ============================================================================
# CONFIGURATION AND SETUP
# ============================================================================

# Initialize Rich console for beautiful terminal output
console = Console()

# Infrahub connection settings
INFRAHUB_ADDRESS = "http://localhost:8000"  # Local Infrahub instance

# Repository mode: local development or GitHub
# When True, uses /upstream mount for local generator/transform development
# When False, uses GitHub repository (read-only)
INFRAHUB_GIT_LOCAL = os.getenv("INFRAHUB_GIT_LOCAL", "false").lower() == "true"


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


def wait_for_repository_sync(seconds: int = 120) -> None:
    """
    Wait for repository synchronization with visual progress feedback.

    After adding a Git repository to Infrahub, the system needs time to:
    - Clone the repository contents
    - Process Python generators and transforms
    - Make these components available for execution

    This function provides a visual countdown timer to give the repository
    adequate time to sync before attempting to use its generators/transforms.

    The progress bar displays:
    - Spinning animation (indicates waiting activity)
    - Progress bar showing time remaining
    - Percentage completion
    - Time elapsed and time remaining counters

    Args:
        seconds: Number of seconds to wait (default: 120 seconds / 2 minutes)

    Note:
        The wait time may need adjustment based on repository size and
        network speed. For large repositories or slow connections, increase
        the wait time. For local development (INFRAHUB_GIT_LOCAL=true),
        syncing is typically faster.

    Example:
        >>> wait_for_repository_sync(120)  # Wait 2 minutes
        [7/7] 🔄 Waiting for repository sync ████████████ 100% • 0:02:00 • 0:00:00
        ✓ Repository sync complete
    """
    with Progress(
        SpinnerColumn(spinner_name="dots12", style="bold bright_yellow"),
        TextColumn("[progress.description]{task.description}", style="bold white"),
        BarColumn(
            bar_width=60,
            style="yellow",
            complete_style="bright_green",
            finished_style="bold bright_green",
            pulse_style="bright_yellow",
        ),
        TextColumn("[bold bright_cyan]{task.percentage:>3.0f}%"),
        TextColumn("•", style="dim"),
        TimeElapsedColumn(),
        TextColumn("•", style="dim"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[7/7] 🔄 Waiting for repository sync", total=seconds)

        for _ in range(seconds):
            time.sleep(1)
            progress.update(task, advance=1)

    msg = "[bold bright_green on black]✓[/bold bright_green on black] "
    msg += "🔄 [bold bright_yellow]Repository sync complete[/bold bright_yellow]\n"
    console.print(msg)


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
    7. Create user accounts and roles (emma, otto)
    8. Add demo-dc Git repository (local or GitHub)
    9. Wait for repository sync (120 seconds)
    10. Load event actions (optional - may fail if repo not synced)
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
    - Allows event actions to fail gracefully (optional step)

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
            "command": "uv run python scripts/create_users_roles.py",
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
        repo_file = "objects/git-repo/github.yml"
        console.print("[dim]Using GitHub repository: https://github.com/opsmill/infrahub-demo-dc.git[/dim]")

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

    console.print(Rule(style="dim bright_magenta"))

    # ========================================================================
    # Repository Sync Wait Period
    # ========================================================================
    # After adding the repository, Infrahub needs time to:
    # - Clone the Git repository
    # - Process Python generators and transforms
    # - Make these components available for execution
    # We wait 120 seconds to allow this synchronization to complete
    console.print()  # Add spacing
    wait_for_repository_sync(120)

    console.print(Rule(style="dim bright_yellow"))

    # ========================================================================
    # Step 7: Load Event Actions (Optional)
    # ========================================================================
    # Event actions define automated triggers and responses in Infrahub.
    # This step is optional because it may fail if the repository hasn't
    # fully synced yet. Users can manually load event actions later if needed.
    msg = "\n[bold bright_cyan on black][7/7][/bold bright_cyan on black] "
    msg += "⚡ [bold white]Loading event actions (optional)[/bold white]"
    console.print(msg)
    events_loaded = run_command(
        f"uv run infrahubctl object load objects/events/ --branch {branch}",
        "Event actions loading",
        "",
        "bright_cyan",
        "⚡",
    )

    if not events_loaded:
        msg = "[bold yellow on black]⚠[/bold yellow on black] "
        msg += "⚡ [bold bright_cyan]Event actions skipped (repository may need time to sync)[/bold bright_cyan]"
        console.print(msg)
        console.print(
            "[dim]Event actions can be loaded later with:[/dim]\n"
            f"  [bold]uv run infrahubctl object load objects/events/ --branch {branch}[/bold]"
        )

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
