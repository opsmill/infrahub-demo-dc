"""Validate a virtualization virtual machine.

A VM stores both its host and its cluster, and the host itself belongs to
a cluster - two paths to the same fact, with nothing in the schema forcing
them to agree. This check fails a proposed change when a VM names a
different cluster than the one its host is a member of (e.g. the VM says
FRA1-KVM-CLUSTER while sitting on fra1-pve-01).
"""

from typing import Any

from infrahub_sdk.checks import InfrahubCheck

from .common import get_data


class CheckVirtualizationVM(InfrahubCheck):
    """Check that a VM's cluster matches its host's cluster."""

    query = "virtualization_vm_validation"

    def validate(self, data: Any) -> None:
        """Validate the VM's cluster/host agreement."""
        errors = []
        vm = get_data(data)

        vm_name = vm.get("name", "unknown")
        vm_cluster = vm.get("cluster") or {}
        host = vm.get("host") or {}
        host_cluster = host.get("cluster") or {}

        if not host_cluster.get("id"):
            errors.append(f"{vm_name}: host {host.get('name', 'unknown')} is not a member of any cluster")
        elif vm_cluster.get("id") != host_cluster.get("id"):
            errors.append(
                f"{vm_name}: cluster is {vm_cluster.get('name', 'none')} but its host "
                f"{host.get('name', 'unknown')} belongs to {host_cluster.get('name', 'none')}"
            )

        if errors:
            for error in errors:
                self.log_error(message=error)
