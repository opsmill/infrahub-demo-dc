"""Validate a virtualization physical host against its cluster.

A host names the hypervisor family it runs, and so does the cluster it joins.
Nothing in the schema forces the two to agree - a relationship picker cannot be
filtered by another field's value - so this check fails a proposed change when a
host joins a cluster of a different family (a Hyper-V host in a Proxmox cluster,
say), or when a host that runs its workload directly on the hardware is made a
cluster member at all.

Both sides pointing at one VirtualizationHypervisorType is what makes this an
identity comparison rather than a platform-to-family compatibility matrix.
"""

from typing import Any

from infrahub_sdk.checks import InfrahubCheck

from .common import get_data


class CheckVirtualizationHost(InfrahubCheck):
    """Check that a host's hypervisor type matches its cluster's."""

    query = "virtualization_host_validation"

    def validate(self, data: Any) -> None:
        """Validate the host's hypervisor type against its cluster."""
        host = get_data(data)

        host_name = host.get("name", "unknown")
        cluster = host.get("cluster") or {}
        cluster_name = cluster.get("name")

        # A host does not have to belong to a cluster - a standalone hypervisor
        # is valid, and there is nothing to disagree with yet.
        if not cluster_name:
            return

        host_type = host.get("hypervisor_type") or {}
        cluster_type_node = cluster.get("hypervisor_type") or {}

        if host.get("role") == "compute":
            self.log_error(
                message=(
                    f"{host_name}: role is compute, which runs its workload directly on the "
                    f"hardware, so it cannot be a member of cluster {cluster_name}"
                )
            )

        if not host_type.get("id"):
            self.log_error(message=f"{host_name}: no hypervisor type set, but it is a member of {cluster_name}")
        elif not cluster_type_node.get("id"):
            self.log_error(message=f"{host_name}: cluster {cluster_name} has no hypervisor type set")
        elif host_type["id"] != cluster_type_node["id"]:
            self.log_error(
                message=(
                    f"{host_name}: runs {host_type.get('name', 'none')} but its cluster "
                    f"{cluster_name} is {cluster_type_node.get('name', 'none')}"
                )
            )
