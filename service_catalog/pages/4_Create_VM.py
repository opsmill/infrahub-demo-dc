"""Infrahub Service Catalog - Create VM Page.

This page provides a form-based interface for creating new virtualization
VirtualMachines in Infrahub. It creates a branch, adds the VM, and creates
a proposed change for review.
"""

from typing import Any, Dict

import streamlit as st  # type: ignore[import-untyped]
from utils import (
    DEFAULT_BRANCH,
    INFRAHUB_ADDRESS,
    INFRAHUB_API_TOKEN,
    INFRAHUB_UI_URL,
    InfrahubClient,
    cached_fetch,
    display_error,
    display_success,
    render_progress_tracker,
)
from utils.api import (
    InfrahubAPIError,
    InfrahubConnectionError,
    InfrahubGraphQLError,
    InfrahubHTTPError,
)

# Initialize session state
if "selected_branch" not in st.session_state:
    st.session_state.selected_branch = DEFAULT_BRANCH

if "infrahub_url" not in st.session_state:
    st.session_state.infrahub_url = INFRAHUB_ADDRESS

VM_CREATION_STEPS = [
    "Creating branch",
    "Creating virtual machine",
    "Processing",
    "Creating proposed change",
    "Rendering artifacts",
    "Complete",
]


def initialize_vm_creation_state(form_data: Dict[str, Any]) -> None:
    """Initialize session state for VM creation workflow."""
    vm_name = form_data["name"]
    branch_name = f"add-vm-{vm_name.lower().replace(' ', '-')}"

    st.session_state.vm_creation = {
        "active": True,
        "step": 1,
        "vm_name": vm_name,
        "branch_name": branch_name,
        "form_data": form_data,
        "vm_id": None,
        "pc_url": None,
        "artifacts": [],
    }


def execute_vm_creation_step(client: InfrahubClient) -> None:
    """Execute the current step of VM creation workflow."""
    state = st.session_state.vm_creation
    step = state["step"]
    branch_name = state["branch_name"]
    vm_name = state["vm_name"]
    form_data = state["form_data"]

    try:
        if step == 1:
            # Step 1: Create branch
            with st.status("Creating branch...", expanded=True) as status:
                st.write(f"Creating branch: {branch_name}")
                branch = client.create_branch(branch_name, from_branch="main")
                st.write(f"Branch created: {branch['name']}")
                status.update(label="Branch created!", state="complete")
                state["step"] = 2
                st.rerun()

        elif step == 2:
            # Step 2: Create virtual machine
            # Artifact group membership (proxmox_vms / kvm_vms / hyperv_vms /
            # esxi_vms / vm_userdata_targets) is owned by the
            # assign_vm_artifact_groups generator, not this form. The form only
            # adds the VM to the generator trigger group.
            group_names = ["virtualization_vms"]

            vm_data = {
                "name": form_data["name"],
                "host": form_data["host"],
                "cluster": form_data.get("cluster"),
                "description": form_data.get("description", ""),
                "os_version": form_data.get("os_version", ""),
                "platform": form_data.get("platform"),
                "ssh_public_key": form_data.get("ssh_public_key"),
                "status": form_data["status"],
                "vcpus": form_data.get("vcpus"),
                "memory": form_data.get("memory"),
                "disk": form_data.get("disk"),
                "vmid": form_data.get("vmid"),
                "customer": form_data.get("customer"),
                "group_names": group_names,
            }

            with st.status("Creating virtual machine...", expanded=True) as status:
                st.write(f"Creating VM: {vm_name} on host {form_data['host_name']}")
                vm = client.create_virtual_machine(branch_name, vm_data)
                st.write(f"VM created: {vm['name']['value']}")
                status.update(label="Virtual machine created!", state="complete")
                state["vm_id"] = vm["id"]
                state["step"] = 3
                st.rerun()

        elif step == 3:
            # Step 3: wait for the secure_virtualization_vm generator to
            # allocate an IP and register it in the HTTPS-only address group, so
            # the proposed change created next carries the complete diff.
            #
            # Polled rather than slept through: the allocated address is the
            # condition being waited for, so the step ends when it lands instead
            # of holding the Streamlit run for a fixed interval every time.
            with st.status("Processing...", expanded=True) as status:
                st.write("Waiting for Infrahub to assign an IP and apply the HTTPS-only security policy...")
                if client.wait_for_vm_ip(state["vm_id"], branch_name, timeout=30):
                    st.write("Processing complete")
                    status.update(label="Processing complete!", state="complete")
                else:
                    st.write("No IP allocated yet - continuing anyway.")
                    status.update(label="Processing timed out", state="error")
                state["step"] = 4
                st.rerun()

        elif step == 4:
            # Step 4: Create proposed change
            with st.status("Creating Proposed Change...", expanded=True) as status:
                pc_name = f"Add Virtual Machine: {vm_name}"
                pc_description = f"Proposed change to add new virtual machine '{vm_name}'"
                st.write(f"Creating Proposed Change: {pc_name}")
                pc = client.create_proposed_change(branch_name, pc_name, pc_description)
                pc_id = pc["id"]
                pc_url = client.get_proposed_change_url(pc_id)
                st.write("Proposed Change created")
                status.update(label="Proposed Change created!", state="complete")
                state["pc_url"] = pc_url
                state["step"] = 5
                st.rerun()

        elif step == 5:
            # Step 5: Render provisioning artifacts
            provisioning_def = form_data.get("hypervisor", {}).get("artifact_definition")
            definition_names = ["vm_userdata"] + ([provisioning_def] if provisioning_def else [])

            with st.status("Rendering provisioning artifacts...", expanded=True) as status:
                try:
                    # The IP was already waited for in step 3.
                    st.write(f"Generating: {', '.join(definition_names)}")
                    artifacts = client.generate_and_wait_for_artifacts(state["vm_id"], definition_names, branch_name)
                    for artifact in artifacts:
                        artifact["content"] = client.get_artifact_content(artifact["storage_id"])
                    status.update(label="Artifacts rendered", state="complete")
                except Exception as e:
                    artifacts = []
                    status.update(label="Artifact rendering did not finish", state="error")
                    st.warning(
                        f"Artifacts were not ready in time - view them in the Infrahub UI "
                        f"on branch {branch_name}. ({e})"
                    )

            state["artifacts"] = artifacts
            state["step"] = 6
            st.rerun()

        elif step == 6:
            # Step 6: Complete - show success message
            state["active"] = False
            # The VM was created successfully, so the cached used-vmid map is
            # stale - drop it so the next form load refetches and offers the
            # right "next free" suggestion / duplicate check.
            st.session_state.pop("vm_used_vmids", None)
            st.markdown("---")
            display_success(f"Virtual Machine '{vm_name}' created successfully!")

            st.markdown(f"""
            ### Next Steps

            Your virtual machine has been created in branch `{branch_name}` and a Proposed Change has been created.

            **Proposed Change URL:**
            [{state["pc_url"]}]({state["pc_url"]})

            Click the link above to review and merge your changes in Infrahub.
            """)

            for artifact in state.get("artifacts", []):
                language = (
                    "yaml"
                    if artifact["definition_name"] == "vm_userdata"
                    else (form_data.get("hypervisor", {}).get("script_language") or "bash")
                )
                with st.expander(f"Artifact: {artifact['name']}", expanded=False):
                    st.code(artifact["content"], language=language)
                    st.download_button(
                        "Download",
                        data=artifact["content"],
                        file_name=f"{form_data['name']}-{artifact['definition_name']}.txt",
                        key=f"dl-{artifact['id']}",
                    )

    except (
        InfrahubConnectionError,
        InfrahubHTTPError,
        InfrahubGraphQLError,
        InfrahubAPIError,
    ) as e:
        state["active"] = False

        if step == 1:
            display_error("Failed to create branch", f"Branch: {branch_name}\n\n{str(e)}")
        elif step == 2:
            display_error(
                "Failed to create virtual machine",
                f"The branch '{branch_name}' was created but the VM could not be created.\n\n{str(e)}",
            )
        elif step == 4:
            display_error(
                "Failed to create Proposed Change",
                f"The VM '{vm_name}' was created successfully in branch '{branch_name}', "
                f"but the Proposed Change could not be created.\n\n{str(e)}\n\n"
                f"You can manually create a Proposed Change for branch '{branch_name}' in the Infrahub UI.",
            )
            st.warning(
                f"Virtual Machine '{vm_name}' was created in branch '{branch_name}', "
                f"but you'll need to manually create a Proposed Change."
            )


def handle_vm_creation(form_data: Dict[str, Any]) -> None:
    """Initialize the VM creation workflow.

    Args:
        form_data: Dictionary containing form data
    """
    initialize_vm_creation_state(form_data)
    st.rerun()


def main() -> None:
    """Main function to render the Create VM page."""

    # Page title
    st.title("Create VM")

    # Check if VM creation is in progress
    vm_creation_active = "vm_creation" in st.session_state and st.session_state.vm_creation.get("active")

    if not vm_creation_active:
        st.markdown(
            "Fill in the form below to create a new Virtual Machine in Infrahub. "
            "This will create a branch, add the VM (assigning it an IP and applying the "
            "HTTPS-only security policy automatically), and create a proposed change for review."
        )
    else:
        st.info("Virtual machine creation in progress... Form is read-only during execution.")

    # Initialize API client
    client = InfrahubClient(
        st.session_state.infrahub_url,
        api_token=INFRAHUB_API_TOKEN or None,
        ui_url=INFRAHUB_UI_URL,
    )

    # Reference data the form is built from, fetched once per session. Hosts
    # are fatal: without them there is nothing to create a VM on.
    cached_fetch("physical_hosts", "physical hosts", client.get_physical_hosts, fatal=True)
    cached_fetch(
        "vm_customers",
        "customers",
        lambda: [org for org in client.get_organizations() if org.get("type") == "OrganizationCustomer"],
        fallback=[],
    )
    cached_fetch(
        "vm_used_vmids",
        "used VM IDs",
        client.get_used_vmids,
        fallback={"by_cluster": {}, "next_free": 100},
    )

    # VM Creation Form
    st.markdown("---")
    st.subheader("Virtual Machine Information")

    # Host and Guest OS selection are rendered outside st.form: widgets
    # inside a Streamlit form do not rerender until submit, but both the
    # host -> hypervisor family -> VM ID field and the Guest OS -> OS version
    # options need to update live as the user changes these two selections.
    host_options = [h["name"] for h in st.session_state.physical_hosts]
    host_map = {h["name"]: h for h in st.session_state.physical_hosts}

    if not host_options:
        st.warning("No physical hosts found. Load objects/virtualization/ first.")
        host_name = None
        selected_host = None
    else:
        host_name = st.selectbox(
            "Host *",
            options=host_options,
            help="Physical hypervisor host this VM will run on",
            disabled=vm_creation_active,
        )
        selected_host = host_map.get(host_name)

    # The cluster's hypervisor family, carried on the host by
    # get_physical_hosts(). Empty when the host has no cluster, or its cluster
    # no hypervisor_type - either way the form degrades to no VM ID field and
    # no provisioning artifact rather than guessing.
    hypervisor: Dict[str, Any] = {}
    if selected_host and selected_host.get("cluster"):
        cluster = selected_host["cluster"]
        hypervisor = cluster.get("hypervisor") or {}
        family = hypervisor.get("label") or hypervisor.get("name") or "unknown hypervisor"
        st.caption(f"Cluster: {cluster['name']} ({family})")
    elif selected_host:
        st.warning("This host is not part of a cluster - a VM requires a clustered host.")

    # Guest OS family drives the platform relationship and the cloud-image
    # naming convention (tpl-<os_version slug>).
    guest_os = st.selectbox(
        "Guest OS *",
        options=["Linux", "Windows"],
        help="Guest OS family - selects cloud-init (Linux) or cloudbase-init (Windows)",
        disabled=vm_creation_active,
    )
    os_version_options = (
        ["Ubuntu 22.04", "Ubuntu 24.04"] if guest_os == "Linux" else ["Windows Server 2022", "Windows Server 2025"]
    )

    with st.form("vm_creation_form"):
        col1, col2 = st.columns(2)

        with col1:
            # VM Name
            vm_name = st.text_input(
                "VM Name *",
                placeholder="e.g., fra1-vm-web03",
                help="Name for this virtual machine",
                disabled=vm_creation_active,
            )

            # Description
            description = st.text_input(
                "Description",
                placeholder="e.g., Web server VM",
                disabled=vm_creation_active,
            )

            # OS Version (options depend on the Guest OS selected above)
            os_version = st.selectbox(
                "OS Version *",
                options=os_version_options,
                disabled=vm_creation_active,
            )

            # SSH public key (optional, installed by cloud-init / cloudbase-init)
            ssh_public_key = st.text_area(
                "SSH Public Key",
                placeholder="ssh-ed25519 AAAA... user@host",
                help="Installed into the guest by cloud-init / cloudbase-init (optional)",
                height=70,
                disabled=vm_creation_active,
            )

            # Customer (optional)
            customer_options = ["None"] + [
                c.get("display_label") or c.get("name", {}).get("value", "Unknown")
                for c in st.session_state.vm_customers
            ]
            customer_map = {
                (c.get("display_label") or c.get("name", {}).get("value", "Unknown")): c.get("id")
                for c in st.session_state.vm_customers
            }
            customer_name = st.selectbox(
                "Customer",
                options=customer_options,
                help="Customer that uses this VM (optional)",
                disabled=vm_creation_active,
            )
            customer_id = customer_map.get(customer_name)

        with col2:
            # Status
            status_options = ["active", "provisioning", "maintenance", "drained"]
            status = st.selectbox(
                "Status *",
                options=status_options,
                index=0,
                disabled=vm_creation_active,
            )

            # vCPUs
            vcpus = st.number_input(
                "vCPUs",
                min_value=1,
                max_value=128,
                value=4,
                disabled=vm_creation_active,
            )

            # Memory
            memory = st.number_input(
                "Memory (GB)",
                min_value=1,
                max_value=1024,
                value=8,
                disabled=vm_creation_active,
            )

            # Disk
            disk = st.number_input(
                "Disk Size (GB)",
                min_value=1,
                max_value=10000,
                value=80,
                disabled=vm_creation_active,
            )

            # VMID: only some hypervisors key VMs by a numeric ID. Which ones,
            # and whether it is mandatory, is vmid_requirement on the
            # hypervisor family (objects/bootstrap/07_hypervisor_types.yml).
            vmid_requirement = hypervisor.get("vmid_requirement") or "unused"
            vmid = None
            if vmid_requirement in ("required", "optional"):
                suggested_vmid = st.session_state.vm_used_vmids["next_free"]
                required_marker = "*" if vmid_requirement == "required" else "(optional)"
                vmid = st.number_input(
                    f"VM ID {required_marker}",
                    min_value=100,
                    max_value=999999,
                    value=suggested_vmid,
                    help=(
                        "Numeric ID used by the hypervisor to identify this VM. "
                        f"Unique per cluster - {suggested_vmid} is the next unused ID."
                    ),
                    disabled=vm_creation_active,
                )
            else:
                st.caption("VM ID: not used by this hypervisor (identified by name/UUID).")

        # Submit button
        st.markdown("---")
        submitted = st.form_submit_button(
            "Create VM",
            type="primary",
            use_container_width=True,
            disabled=vm_creation_active,
        )

        if submitted:
            # Validate required fields
            errors = []

            if not vm_name:
                errors.append("VM Name is required")
            if not selected_host:
                errors.append("Host is required")
            elif not selected_host.get("cluster"):
                errors.append("Selected host is not part of a cluster (VM.cluster is mandatory)")

            if vmid_requirement == "required" and vmid is None:
                family = hypervisor.get("label") or hypervisor.get("name") or "this hypervisor"
                errors.append(f"VM ID is required for {family} clusters")
            if vmid is not None and selected_host and selected_host.get("cluster"):
                cluster_id = selected_host["cluster"]["id"]
                used_vmids = st.session_state.vm_used_vmids["by_cluster"].get(cluster_id, set())
                if vmid in used_vmids:
                    errors.append(
                        f"VM ID {vmid} is already used in cluster {selected_host['cluster']['name']} - "
                        f"the next unused ID is {st.session_state.vm_used_vmids['next_free']}"
                    )

            if errors:
                display_error(
                    "Form validation failed",
                    "\n".join(f"* {error}" for error in errors),
                )
            elif selected_host is not None:
                # Store form data in session state for processing
                cluster = selected_host.get("cluster")
                form_data = {
                    "name": vm_name,
                    "host": selected_host["id"],
                    "host_name": host_name,
                    "cluster": cluster["id"] if cluster else None,
                    "hypervisor": (cluster.get("hypervisor") or {}) if cluster else {},
                    "description": description,
                    "os_version": os_version,
                    "platform": ["Generic", guest_os],
                    "ssh_public_key": ssh_public_key.strip() or None,
                    "status": status,
                    "vcpus": vcpus,
                    "memory": memory,
                    "disk": disk,
                    "vmid": vmid,
                    "customer": customer_id,
                }

                handle_vm_creation(form_data)

    # Create placeholder for progress section
    st.markdown("---")
    progress_section = st.container()

    # Render progress section if VM creation is active
    if vm_creation_active:
        with progress_section:
            st.markdown("## Virtual Machine Creation Progress")
            st.markdown("")

            render_progress_tracker("vm_creation", VM_CREATION_STEPS)

            st.markdown("---")
            st.markdown("### Status Updates")
            st.markdown("")

            execute_vm_creation_step(client)


if __name__ == "__main__":
    main()
