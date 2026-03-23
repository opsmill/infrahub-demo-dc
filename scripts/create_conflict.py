#!/usr/bin/env python3
"""
Create an Infrahub branch with a data conflict for testing and demonstration.

This script creates a conflict by modifying the same object differently on a
branch and on main. The result is a branch that, when a Proposed Change is
created, will show data conflicts requiring resolution.

Conflict Strategy:
==================
1. Pick an existing device loaded during bootstrap (e.g. "cisco-switch-01")
2. Create a new branch
3. Modify the device's description on the branch
4. Modify the device's description differently on main
5. Optionally create a Proposed Change so the conflict is visible in the UI

Usage:
======
    python scripts/create_conflict.py
    python scripts/create_conflict.py --branch my-conflict-branch
    python scripts/create_conflict.py --device cisco-switch-01
    uv run invoke demo-conflict
"""

import argparse
import asyncio
import sys

from infrahub_sdk import InfrahubClient
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# Default values for the conflict scenario
DEFAULT_BRANCH = "conflict-demo"
DEFAULT_DEVICE = "cisco-switch-01"

MAIN_DESCRIPTION = "Updated from main branch - production config"
BRANCH_DESCRIPTION = "Updated from feature branch - new deployment"

MAIN_STATUS = "provisioning"
BRANCH_STATUS = "maintenance"


async def create_conflict(
    branch_name: str,
    device_name: str,
    create_pc: bool = True,
) -> int:
    """Create a branch with a data conflict on the specified device.

    The conflict is created by changing the device's description and status
    to different values on main vs the branch.

    Args:
        branch_name: Name of the branch to create.
        device_name: Name of the device to create a conflict on.
        create_pc: Whether to create a Proposed Change after creating the conflict.

    Returns:
        0 on success, 1 on failure.
    """
    console.print()
    console.print(
        Panel(
            f"[bold bright_red]Creating Branch with Conflict[/bold bright_red]\n\n"
            f"[bright_cyan]Branch:[/bright_cyan]  [bold yellow]{branch_name}[/bold yellow]\n"
            f"[bright_cyan]Device:[/bright_cyan]  [bold]{device_name}[/bold]\n\n"
            f"[dim]Will modify the device differently on main and the branch[/dim]",
            border_style="bright_red",
            box=box.SIMPLE,
        )
    )

    # Step 1: Connect to Infrahub
    console.print("\n[cyan]→[/cyan] Connecting to Infrahub...")
    try:
        client = InfrahubClient()
        console.print(f"[green]✓[/green] Connected to Infrahub at [bold]{client.address}[/bold]")
    except Exception as e:
        console.print(f"[red]✗ Failed to connect to Infrahub:[/red] {e}")
        return 1

    # Step 2: Verify the device exists on main
    console.print(f"\n[cyan]→[/cyan] Looking up device [bold]{device_name}[/bold] on main...")
    try:
        devices = await client.filters(kind="DcimDevice", name__value=device_name)
        if not devices:
            console.print(f"[red]✗ Device '{device_name}' not found on main[/red]")
            console.print("[dim]Available devices are loaded during bootstrap (invoke bootstrap)[/dim]")
            return 1
        device_main = devices[0]
        console.print(f"[green]✓[/green] Found device [bold]{device_name}[/bold] (id: {device_main.id})")
    except Exception as e:
        console.print(f"[red]✗ Failed to query device:[/red] {e}")
        return 1

    # Step 3: Create the branch
    console.print(f"\n[cyan]→[/cyan] Creating branch [bold]{branch_name}[/bold]...")
    try:
        await client.branch.create(branch_name=branch_name, description=f"Conflict demo branch for {device_name}")
        console.print(f"[green]✓[/green] Branch [bold]{branch_name}[/bold] created")
    except Exception as e:
        if "already exists" in str(e).lower():
            console.print(f"[yellow]⚠[/yellow] Branch [bold]{branch_name}[/bold] already exists, reusing it")
        else:
            console.print(f"[red]✗ Failed to create branch:[/red] {e}")
            return 1

    # Step 4: Modify device on the branch
    console.print(f"\n[yellow]→[/yellow] Modifying device on branch [bold]{branch_name}[/bold]...")
    try:
        branch_devices = await client.filters(
            kind="DcimDevice",
            name__value=device_name,
            branch=branch_name,
        )
        device_branch = branch_devices[0]
        device_branch.description.value = BRANCH_DESCRIPTION
        device_branch.status.value = BRANCH_STATUS
        await device_branch.save()
        console.print(
            f"[green]✓[/green] Set description=[bold]{BRANCH_DESCRIPTION}[/bold], "
            f"status=[bold]{BRANCH_STATUS}[/bold] on branch"
        )
    except Exception as e:
        console.print(f"[red]✗ Failed to modify device on branch:[/red] {e}")
        return 1

    # Step 5: Modify same device differently on main
    console.print("\n[yellow]→[/yellow] Modifying same device on [bold]main[/bold]...")
    try:
        main_devices = await client.filters(kind="DcimDevice", name__value=device_name)
        device_main = main_devices[0]
        device_main.description.value = MAIN_DESCRIPTION
        device_main.status.value = MAIN_STATUS
        await device_main.save()
        console.print(
            f"[green]✓[/green] Set description=[bold]{MAIN_DESCRIPTION}[/bold], "
            f"status=[bold]{MAIN_STATUS}[/bold] on main"
        )
    except Exception as e:
        console.print(f"[red]✗ Failed to modify device on main:[/red] {e}")
        return 1

    # Step 6: Optionally create a Proposed Change
    pc_id = None
    if create_pc:
        console.print(
            f"\n[bright_magenta]→[/bright_magenta] Creating proposed change "
            f"for [bold]{branch_name}[/bold]..."
        )
        try:
            proposed_change = await client.create(
                kind="CoreProposedChange",
                data={
                    "name": {"value": f"Conflict demo: {branch_name}"},
                    "description": {"value": f"Proposed change with conflict on {device_name}"},
                    "source_branch": {"value": branch_name},
                    "destination_branch": {"value": "main"},
                },
            )
            await proposed_change.save()
            pc_id = proposed_change.id
            console.print(f"[green]✓[/green] Proposed change created (id: {pc_id})")
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] Could not create proposed change: {e}")

    # Summary
    console.print()
    summary = Table(
        title="Conflict Summary",
        box=box.SIMPLE,
        show_header=True,
        header_style="bold bright_cyan",
    )
    summary.add_column("Property", style="bright_cyan", no_wrap=True)
    summary.add_column("Main", style="green")
    summary.add_column(f"Branch ({branch_name})", style="yellow")

    summary.add_row("Description", MAIN_DESCRIPTION, BRANCH_DESCRIPTION)
    summary.add_row("Status", MAIN_STATUS, BRANCH_STATUS)

    console.print(summary)

    if pc_id:
        pc_url = f"{client.address}/proposed-changes/{pc_id}"
        console.print()
        console.print(
            Panel(
                f"[bold bright_white]View Proposed Change (with conflicts):[/bold bright_white]\n\n"
                f"[bright_blue]{pc_url}[/bright_blue]",
                border_style="bright_red",
                box=box.SIMPLE,
            )
        )

    console.print()
    console.print("[bold bright_green]✓ Conflict created successfully![/bold bright_green]")
    console.print()

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create an Infrahub branch with a data conflict",
    )
    parser.add_argument(
        "--branch",
        "-b",
        type=str,
        default=DEFAULT_BRANCH,
        help=f"Branch name to create (default: {DEFAULT_BRANCH})",
    )
    parser.add_argument(
        "--device",
        "-d",
        type=str,
        default=DEFAULT_DEVICE,
        help=f"Device name to create conflict on (default: {DEFAULT_DEVICE})",
    )
    parser.add_argument(
        "--no-pc",
        action="store_true",
        help="Skip creating a Proposed Change",
    )

    args = parser.parse_args()
    exit_code = asyncio.run(create_conflict(args.branch, args.device, create_pc=not args.no_pc))
    sys.exit(exit_code)
