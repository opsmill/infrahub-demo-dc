"""Virtualization VM Artifact Group Assignment Generator.

Triggered whenever a VirtualizationVirtualMachine is created (see the
`virtualization_vms` group in .infrahub.yml). Derives the VM's
hypervisor from cluster.cluster_type and adds the VM to the matching
per-hypervisor artifact group (proxmox_vms / kvm_vms / hyperv_vms /
esxi_vms) plus the umbrella vm_userdata_targets group, so artifact
definitions target the right VMs without static member_of_groups
entries duplicating what cluster_type already encodes.

Idempotent: membership is diffed before extending, so re-runs never
add a VM to the same group twice. cluster_type values without a
mapped group (e.g. "other") only join the umbrella group.
"""

from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]
from infrahub_sdk.protocols import CoreStandardGroup  # type: ignore[import-not-found]

from .common import clean_data

CLUSTER_TYPE_TO_GROUP = {
    "proxmox": "proxmox_vms",
    "kvm": "kvm_vms",
    "hyperv": "hyperv_vms",
    "vmware": "esxi_vms",
}
USERDATA_GROUP = "vm_userdata_targets"


class VMArtifactGroupsGenerator(InfrahubGenerator):
    """Place a VM in its per-hypervisor artifact group and the user-data group."""

    async def generate(self, data: dict) -> None:
        """Assign artifact groups for the VM in `data`.

        Args:
            data: GraphQL query result containing one VirtualizationVirtualMachine
        """
        cleaned_data = clean_data(data)
        if not isinstance(cleaned_data, dict):
            raise ValueError("clean_data() did not return a dictionary")

        vms = cleaned_data.get("VirtualizationVirtualMachine", [])
        if not vms:
            self.logger.warning("No VirtualizationVirtualMachine data found in query result")
            return

        vm = vms[0]  # Generator runs per-VM
        vm_name = vm.get("name", "unknown")
        vm_id = vm.get("id")
        cluster = vm.get("cluster") or {}
        cluster_type = cluster.get("cluster_type")

        group_names = [USERDATA_GROUP]
        hypervisor_group = CLUSTER_TYPE_TO_GROUP.get(cluster_type) if cluster_type else None
        if hypervisor_group:
            group_names.append(hypervisor_group)
        else:
            self.logger.warning(f"- {vm_name}: cluster_type {cluster_type!r} has no artifact group mapping")

        for group_name in group_names:
            group = await self.client.get(
                kind=CoreStandardGroup,
                branch=self.branch,
                name__value=group_name,
            )
            await group.members.fetch()
            existing_ids = {peer.id for peer in group.members.peers}
            if vm_id in existing_ids:
                self.logger.info(f"- {vm_name} already in {group_name}, skipping")
                continue
            group.members.extend([vm_id])  # type: ignore[list-item]
            await group.save(allow_upsert=True)
            self.logger.info(f"- Added {vm_name} to {group_name}")
