"""Validate that a VM's compute allocation fits its host and its cluster.

Nothing stops a VM from asking for more vCPUs, memory or disk than the hardware
underneath it has: the allocation lives on the VM, the capacity on the host, and
no schema construct compares a sum of children against a parent. This check does
that comparison in the proposed change, on two levels.

**Host** - does the VM fit where it is placed? An error, because a VM that does
not fit its own host cannot start.

**Cluster** - does everything still fit if a host is lost? Reported as
information rather than an error: exceeding N+1 headroom is a capacity-planning
signal, not a reason to block a change. Exceeding the cluster's *total* capacity
is an error, since nothing could place those VMs.

Overcommit is normal in virtualization, so the limits are not the raw hardware
numbers. Each hypervisor family carries the ratio it is run at on
VirtualizationHypervisorType, as a percentage because Number is integer-only:
400 is 4:1, 100 is no overcommit. A family with no value set is treated as 100.

Powered-off VMs are counted for disk but not for vCPU or memory. A stopped VM
still occupies its disk image, while its vCPUs and memory are only claimed when
it runs.
"""

from typing import Any

from infrahub_sdk.checks import InfrahubCheck

from .common import get_data

DEFAULT_OVERCOMMIT_PERCENT = 100
RUNNING_STATUS = "active"

# field, unit, the overcommit attribute on the hypervisor type, and whether a
# powered-off VM still consumes it.
RESOURCES: tuple[tuple[str, str, str, bool], ...] = (
    ("vcpus", "vCPU", "vcpu_overcommit_percent", False),
    ("memory", "GB RAM", "memory_overcommit_percent", False),
    ("disk", "GB disk", "disk_overcommit_percent", True),
)


def allocated(vms: list[dict[str, Any]], field: str, counts_when_off: bool) -> int:
    """Sum what a set of VMs allocates of one resource.

    Args:
        vms: Cleaned VM dictionaries.
        field: Resource attribute to sum (vcpus, memory or disk).
        counts_when_off: Whether a VM that is not running still consumes it.

    Returns:
        The total allocated, treating an unset value as zero.
    """
    return sum(vm.get(field) or 0 for vm in vms if counts_when_off or vm.get("status") == RUNNING_STATUS)


def as_percent(value: int, of: int) -> str:
    """Render `value` as a percentage of `of`, for a check message.

    Args:
        value: The allocated amount.
        of: The capacity it is measured against.

    Returns:
        A percentage like "113%", or "n/a" when the capacity is unknown.
    """
    if not of:
        return "n/a"
    return f"{round(value * 100 / of)}%"


class CheckVirtualizationCapacity(InfrahubCheck):
    """Check a VM's allocation against its host and cluster capacity."""

    query = "virtualization_capacity"

    def validate(self, data: Any) -> None:
        """Validate the VM's allocation at host and cluster level."""
        vm = get_data(data)

        vm_name = vm.get("name", "unknown")
        host = vm.get("host") or {}
        cluster = vm.get("cluster") or {}
        overcommit = cluster.get("hypervisor_type") or {}

        if not host.get("name"):
            # The host relationship is mandatory, so this only happens on a
            # partially written VM; validate_virtualization_vm reports that.
            return

        self._check_host(vm_name, host, overcommit)
        self._check_cluster(vm_name, cluster, overcommit)

    def _check_host(self, vm_name: str, host: dict[str, Any], overcommit: dict[str, Any]) -> None:
        """Compare the host's allocation against its own capacity.

        Args:
            vm_name: Name of the VM under review, for the message.
            host: Cleaned host dictionary, including its virtual_machines.
            overcommit: Cleaned hypervisor type dictionary holding the ratios.
        """
        host_name = host.get("name", "unknown")
        vms = host.get("virtual_machines") or []

        for field, unit, ratio_field, counts_when_off in RESOURCES:
            capacity = host.get(field) or 0
            percent = overcommit.get(ratio_field) or DEFAULT_OVERCOMMIT_PERCENT
            limit = capacity * percent // 100
            used = allocated(vms, field, counts_when_off)

            self.log_info(
                message=(
                    f"{host_name}: {used}/{limit} {unit} allocated "
                    f"({as_percent(used, limit)} of the limit, {capacity} physical at {percent}%)"
                )
            )
            if capacity and used > limit:
                self.log_error(
                    message=(
                        f"{host_name} is oversubscribed after {vm_name}: {used} {unit} allocated "
                        f"against a limit of {limit} ({capacity} physical at {percent}% overcommit)"
                    )
                )

    def _check_cluster(self, vm_name: str, cluster: dict[str, Any], overcommit: dict[str, Any]) -> None:
        """Compare the cluster's allocation against its total and N+1 capacity.

        Args:
            vm_name: Name of the VM under review, for the message.
            cluster: Cleaned cluster dictionary, including hosts and virtual_machines.
            overcommit: Cleaned hypervisor type dictionary holding the ratios.
        """
        cluster_name = cluster.get("name")
        hosts = cluster.get("hosts") or []
        if not cluster_name or not hosts:
            return

        vms = cluster.get("virtual_machines") or []

        for field, unit, ratio_field, counts_when_off in RESOURCES:
            capacities = [h.get(field) or 0 for h in hosts]
            total = sum(capacities)
            percent = overcommit.get(ratio_field) or DEFAULT_OVERCOMMIT_PERCENT
            limit = total * percent // 100
            # Headroom for losing the single largest host, which is what a
            # cluster needs to survive maintenance or a failure.
            survivable = (total - max(capacities)) * percent // 100
            used = allocated(vms, field, counts_when_off)

            # A single-host cluster has no host to lose, so the N+1 figure would
            # always read as zero available and say nothing.
            host_count = f"{len(hosts)} host{'s' if len(hosts) != 1 else ''}"
            headroom = (
                f", {as_percent(used, survivable)} of the {survivable} available with the largest host lost"
                if len(hosts) > 1
                else ", no N+1 headroom to measure on a single-host cluster"
            )
            self.log_info(
                message=(
                    f"{cluster_name}: {used}/{limit} {unit} allocated across {host_count} "
                    f"({as_percent(used, limit)}){headroom}"
                )
            )
            if total and used > limit:
                self.log_error(
                    message=(
                        f"{cluster_name} is oversubscribed after {vm_name}: {used} {unit} allocated "
                        f"against a cluster limit of {limit}"
                    )
                )
            elif len(hosts) > 1 and survivable and used > survivable:
                self.log_info(
                    message=(
                        f"{cluster_name} has no N+1 headroom for {unit}: {used} allocated, "
                        f"{survivable} available if the largest host is lost"
                    )
                )
