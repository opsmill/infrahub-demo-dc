"""Virtualization VM Artifact Group Assignment Generator.

Triggered whenever a VirtualizationVirtualMachine is created (see the
`virtualization_vms` group in .infrahub.yml). Derives the VM's
hypervisor from cluster.cluster_type and adds the VM to the matching
per-hypervisor artifact group (proxmox_vms / kvm_vms / hyperv_vms /
esxi_vms) plus the umbrella vm_userdata_targets group, so artifact
definitions target the right VMs without static member_of_groups
entries duplicating what cluster_type already encodes.

Idempotent and concurrency-safe: membership is added through the
RelationshipAdd mutation, which adds only the named peer and tolerates
a peer that is already a member. cluster_type values without a mapped
group (e.g. "other") only join the umbrella group.
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
            # add_relationships issues a RelationshipAdd mutation, which touches
            # only the peers it names. `members.extend()` + `save()` writes back
            # the whole member list as it was read moments earlier, and one of
            # these generators runs per VM concurrently - so each run would
            # clobber the members its siblings added in between, and a group like
            # vm_userdata_targets would end up with a fraction of the VMs.
            await group.add_relationships(relation_to_update="members", related_nodes=[vm_id])
            self.logger.info(f"- Added {vm_name} to {group_name}")
