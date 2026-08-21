"""Virtualization VM Artifact Group Assignment Generator.

Triggered whenever a VirtualizationVirtualMachine is created (see the
`virtualization_vms` group in .infrahub.yml). Adds the VM to the
artifact group its cluster's hypervisor type names, plus the umbrella
vm_userdata_targets group, so artifact definitions target the right VMs
without static member_of_groups entries duplicating what the cluster
already encodes.

The hypervisor-to-group mapping is data, not code: it lives on
VirtualizationHypervisorType (objects/bootstrap/07_hypervisor_types.yml)
and arrives through the query, so a new hypervisor needs a row there
rather than an edit here.

Idempotent and concurrency-safe: membership is added through the
RelationshipAdd mutation, which adds only the named peer and tolerates
a peer that is already a member. A hypervisor type with no artifact
group (e.g. "other") only joins the umbrella group.
"""

import asyncio

from infrahub_sdk.generator import InfrahubGenerator  # type: ignore[import-not-found]
from infrahub_sdk.protocols import CoreStandardGroup  # type: ignore[import-not-found]

from .common import extract_single_node

USERDATA_GROUP = "vm_userdata_targets"


class VMArtifactGroupsGenerator(InfrahubGenerator):
    """Place a VM in its per-hypervisor artifact group and the user-data group."""

    async def generate(self, data: dict) -> None:
        """Assign artifact groups for the VM in `data`.

        Args:
            data: GraphQL query result containing one VirtualizationVirtualMachine
        """
        vm = extract_single_node(data, "VirtualizationVirtualMachine")
        if vm is None:
            self.logger.warning("No VirtualizationVirtualMachine data found in query result")
            return

        vm_name = vm.get("name", "unknown")
        vm_id = vm["id"]
        hypervisor_type = (vm.get("cluster") or {}).get("hypervisor_type") or {}
        artifact_group = (hypervisor_type.get("artifact_group") or {}).get("name")

        group_names = [USERDATA_GROUP]
        if artifact_group:
            group_names.append(artifact_group)
        else:
            self.logger.warning(f"- {vm_name}: hypervisor type {hypervisor_type.get('name')!r} names no artifact group")

        # The groups are independent of each other, so both are fetched at once
        # rather than one after the other. raise_when_missing=False: a
        # hypervisor type naming a stale group must cost that one membership a
        # warning, not fail the generator for every VM of the family.
        groups = await asyncio.gather(
            *(
                self.client.get(
                    kind=CoreStandardGroup,
                    branch=self.branch,
                    name__value=group_name,
                    raise_when_missing=False,
                )
                for group_name in group_names
            )
        )
        for group_name, group in zip(group_names, groups):
            if group is None:
                self.logger.warning(f"- {vm_name}: group {group_name!r} does not exist, skipping that membership")
        # add_relationships issues a RelationshipAdd mutation, which touches
        # only the peers it names. `members.extend()` + `save()` writes back the
        # whole member list as it was read moments earlier, and one of these
        # generators runs per VM concurrently - so each run would clobber the
        # members its siblings added in between, and a group like
        # vm_userdata_targets would end up with a fraction of the VMs.
        await asyncio.gather(
            *(
                group.add_relationships(relation_to_update="members", related_nodes=[vm_id])
                for group in groups
                if group is not None
            )
        )
        for group_name, group in zip(group_names, groups):
            if group is not None:
                self.logger.info(f"- Added {vm_name} to {group_name}")
