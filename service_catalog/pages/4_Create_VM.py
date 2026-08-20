"""Infrahub Service Catalog - Create VM Page.

This page provides a form-based interface for creating new virtualization
VirtualMachines in Infrahub. It creates a branch, adds the VM, and creates
a proposed change for review.
"""

import time
from typing import Any, Dict

import streamlit as st  # type: ignore[import-untyped]
from utils import (
    DEFAULT_BRANCH,
    INFRAHUB_ADDRESS,
    INFRAHUB_API_TOKEN,
    INFRAHUB_UI_URL,
    InfrahubClient,
    display_error,
    display_success,
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


def wait_for_processing(duration: int = 15) -> None:
    """Wait for Infrahub to process the VM with a progress indicator.

    Args:
        duration: Wait duration in seconds (default: 15, to cover the
            secure_virtualization_vm generator allocating an IP and
            registering it in the HTTPS-only address group)
    """
    progress_bar = st.progress(0, text="Processing...")
    time_display = st.empty()

    for i in range(duration + 1):
        progress = i / duration
        percentage = int(progress * 100)

        progress_bar.progress(progress, text=f"Processing... {percentage}% complete")

        remaining = duration - i
        elapsed = i

        time_display.markdown(f"**Time:** {elapsed}s elapsed / {remaining}s remaining")

        if i < duration:
            time.sleep(1)

    progress_bar.progress(1.0, text="Processing complete!")
    time_display.markdown("**Processing time completed**")

    time.sleep(1)
    progress_bar.empty()
    time_display.empty()


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
        "branch_created": False,
        "vm_created": False,
        "pc_created": False,
        "error": None,
        "pc_url": None,
    }


def render_progress_tracker() -> None:
    """Render the progress tracker based on current state."""
    if "vm_creation" not in st.session_state or not st.session_state.vm_creation.get("active"):
        return

    state = st.session_state.vm_creation
    current_step = state["step"]

    steps = [
        "Creating branch",
        "Creating virtual machine",
        "Processing",
        "Creating proposed change",
        "Complete",
    ]

    progress_md = "### Progress\n\n"
    for i, step_name in enumerate(steps, 1):
        if i < current_step:
            progress_md += f"* {step_name}\n\n"
        elif i == current_step:
            progress_md += f"-> **{step_name}**\n\n"
        else:
            progress_md += f"- {step_name}\n\n"

    st.markdown(progress_md)


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
                state["branch_created"] = True
                state["step"] = 2
                st.rerun()

        elif step == 2:
            # Step 2: Create virtual machine
            group_names = ["virtualization_vms"]
            if form_data.get("cluster_type") == "proxmox":
                group_names.append("proxmox_vms")

            vm_data = {
                "name": form_data["name"],
                "host": form_data["host"],
                "cluster": form_data.get("cluster"),
                "description": form_data.get("description", ""),
                "os_version": form_data.get("os_version", ""),
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
                state["vm_created"] = True
                state["step"] = 3
                st.rerun()

        elif step == 3:
            # Step 3: Wait for processing
            with st.status("Processing...", expanded=True) as status:
                st.write("Waiting for Infrahub to assign an IP and apply the HTTPS-only security policy...")
                wait_for_processing(15)
                st.write("Processing complete")
                status.update(label="Processing complete!", state="complete")
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
                state["pc_created"] = True
                state["pc_url"] = pc_url
                state["step"] = 5
                st.rerun()

        elif step == 5:
            # Step 5: Complete - show success message
            state["active"] = False
            st.markdown("---")
            display_success(f"Virtual Machine '{vm_name}' created successfully!")

            st.markdown(f"""
            ### Next Steps

            Your virtual machine has been created in branch `{branch_name}` and a Proposed Change has been created.

            **Proposed Change URL:**
            [{state["pc_url"]}]({state["pc_url"]})

            Click the link above to review and merge your changes in Infrahub.
            """)

    except (
        InfrahubConnectionError,
        InfrahubHTTPError,
        InfrahubGraphQLError,
        InfrahubAPIError,
    ) as e:
        state["error"] = str(e)
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


def handle_vm_creation(client: InfrahubClient, form_data: Dict[str, Any]) -> None:
    """Initialize the VM creation workflow.

    Args:
        client: InfrahubClient instance
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

    # Fetch physical hosts (cache in session state)
    if "physical_hosts" not in st.session_state:
        with st.spinner("Loading physical hosts..."):
            try:
                st.session_state.physical_hosts = client.get_physical_hosts()
            except Exception as e:
                display_error(
                    "Unable to load physical hosts",
                    f"Failed to fetch VirtualizationPhysicalHost objects from Infrahub.\n\n{str(e)}",
                )
                st.stop()

    # Fetch customers (cache in session state)
    if "vm_customers" not in st.session_state:
        with st.spinner("Loading customers..."):
            try:
                organizations = client.get_organizations()
                st.session_state.vm_customers = [
                    org for org in organizations if org.get("type") == "OrganizationCustomer"
                ]
            except Exception as e:
                st.warning(f"Could not load customers: {e}")
                st.session_state.vm_customers = []

    # Fetch VM IDs already in use (cache in session state)
    if "vm_used_vmids" not in st.session_state:
        with st.spinner("Loading used VM IDs..."):
            try:
                st.session_state.vm_used_vmids = client.get_used_vmids()
            except Exception as e:
                st.warning(f"Could not load used VM IDs: {e}")
                st.session_state.vm_used_vmids = {"by_cluster": {}, "next_free": 100}

    # VM Creation Form
    st.markdown("---")

    with st.form("vm_creation_form"):
        st.subheader("Virtual Machine Information")

        col1, col2 = st.columns(2)

        with col1:
            # VM Name
            vm_name = st.text_input(
                "VM Name *",
                placeholder="e.g., fra1-vm-web03",
                help="Name for this virtual machine",
                disabled=vm_creation_active,
            )

            # Host selection
            host_options = [h["name"]["value"] for h in st.session_state.physical_hosts]
            host_map = {h["name"]["value"]: h for h in st.session_state.physical_hosts}

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

            if selected_host and selected_host.get("cluster"):
                cluster = selected_host["cluster"]
                st.caption(f"Cluster: {cluster['name']} ({cluster['cluster_type']})")
            elif selected_host:
                st.warning("This host is not part of a cluster - a VM requires a clustered host.")

            # Description
            description = st.text_input(
                "Description",
                placeholder="e.g., Web server VM",
                disabled=vm_creation_active,
            )

            # OS Version
            os_version = st.text_input(
                "OS Version",
                value="Ubuntu 22.04",
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

            # VMID (mandatory, prefilled with the next ID free in every cluster)
            suggested_vmid = st.session_state.vm_used_vmids["next_free"]
            vmid = st.number_input(
                "VM ID *",
                min_value=100,
                max_value=999999,
                value=suggested_vmid,
                help=(
                    "Numeric ID used by the hypervisor to identify this VM (e.g. Proxmox VMID). "
                    f"Unique per cluster - {suggested_vmid} is the next unused ID."
                ),
                disabled=vm_creation_active,
            )

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

            if vmid is None:
                errors.append("VM ID is required")
            elif selected_host and selected_host.get("cluster"):
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
                    "cluster_type": cluster["cluster_type"] if cluster else None,
                    "description": description,
                    "os_version": os_version,
                    "status": status,
                    "vcpus": vcpus,
                    "memory": memory,
                    "disk": disk,
                    "vmid": vmid,
                    "customer": customer_id,
                }

                handle_vm_creation(client, form_data)

    # Create placeholder for progress section
    st.markdown("---")
    progress_section = st.container()

    # Render progress section if VM creation is active
    if vm_creation_active:
        with progress_section:
            st.markdown("## Virtual Machine Creation Progress")
            st.markdown("")

            render_progress_tracker()

            st.markdown("---")
            st.markdown("### Status Updates")
            st.markdown("")

            execute_vm_creation_step(client)


if __name__ == "__main__":
    main()
