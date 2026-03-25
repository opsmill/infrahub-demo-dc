"""Infrahub Service Catalog - Create VPN Page.

This page provides a form-based interface for creating new VPN Network Segments in Infrahub.
It creates a branch, adds the segment, and creates a proposed change for review.
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


def wait_for_processing(duration: int = 10) -> None:
    """Wait for Infrahub to process the segment with a progress indicator.

    Args:
        duration: Wait duration in seconds (default: 10)
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


def initialize_segment_creation_state(form_data: Dict[str, Any]) -> None:
    """Initialize session state for segment creation workflow."""
    customer_name = form_data["customer_name"]
    deployment_name = form_data["deployment_name"]
    branch_name = f"add-segment-{deployment_name.lower().replace(' ', '-')}-{customer_name.lower().replace(' ', '-')}"

    st.session_state.segment_creation = {
        "active": True,
        "step": 1,
        "customer_name": customer_name,
        "deployment_name": deployment_name,
        "branch_name": branch_name,
        "form_data": form_data,
        "branch_created": False,
        "segment_created": False,
        "pc_created": False,
        "error": None,
        "pc_url": None,
    }


def render_progress_tracker() -> None:
    """Render the progress tracker based on current state."""
    if "segment_creation" not in st.session_state or not st.session_state.segment_creation.get("active"):
        return

    state = st.session_state.segment_creation
    current_step = state["step"]

    steps = [
        "Creating branch",
        "Creating network segment",
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


def execute_segment_creation_step(client: InfrahubClient) -> None:
    """Execute the current step of segment creation workflow."""
    state = st.session_state.segment_creation
    step = state["step"]
    branch_name = state["branch_name"]
    customer_name = state["customer_name"]
    deployment_name = state["deployment_name"]
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
            # Step 2: Create network segment
            segment_data = {
                "customer_name": form_data["customer_name"],
                "environment": form_data["environment"],
                "segment_type": form_data["segment_type"],
                "tenant_isolation": form_data["tenant_isolation"],
                "vlan_id": form_data["vlan_id"],
                "deployment": form_data["deployment"],
                "owner": form_data["owner"],
                "external_routing": form_data.get("external_routing", False),
                "prefix": form_data.get("prefix"),
            }

            with st.status("Creating network segment...", expanded=True) as status:
                st.write(f"Creating segment: {customer_name} in {deployment_name}")
                st.write(f"Deployment ID: {segment_data['deployment']}")
                st.write(f"Owner ID: {segment_data['owner']}")
                segment = client.create_network_segment(branch_name, segment_data)
                st.write(f"Segment created: {segment['name']['value']}")
                status.update(label="Network segment created!", state="complete")
                state["segment_created"] = True
                state["step"] = 3
                st.rerun()

        elif step == 3:
            # Step 3: Wait for processing
            with st.status("Processing...", expanded=True) as status:
                st.write("Waiting for Infrahub to process the segment...")
                wait_for_processing(10)
                st.write("Processing complete")
                status.update(label="Processing complete!", state="complete")
                state["step"] = 4
                st.rerun()

        elif step == 4:
            # Step 4: Create proposed change
            with st.status("Creating Proposed Change...", expanded=True) as status:
                pc_name = f"Add Network Segment: {customer_name} in {deployment_name}"
                pc_description = (
                    f"Proposed change to add new network segment '{customer_name}' in deployment '{deployment_name}'"
                )
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
            display_success(f"Network Segment '{customer_name}' created successfully!")

            st.markdown(f"""
            ### Next Steps

            Your network segment has been created in branch `{branch_name}` and a Proposed Change has been created.

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
                "Failed to create network segment",
                f"The branch '{branch_name}' was created but the segment could not be created.\n\n{str(e)}",
            )
        elif step == 4:
            display_error(
                "Failed to create Proposed Change",
                f"The segment '{customer_name}' was created successfully in branch '{branch_name}', "
                f"but the Proposed Change could not be created.\n\n{str(e)}\n\n"
                f"You can manually create a Proposed Change for branch '{branch_name}' in the Infrahub UI.",
            )
            st.warning(
                f"Network Segment '{customer_name}' was created in branch '{branch_name}', "
                f"but you'll need to manually create a Proposed Change."
            )


def handle_segment_creation(client: InfrahubClient, form_data: Dict[str, Any]) -> None:
    """Initialize the segment creation workflow.

    Args:
        client: InfrahubClient instance
        form_data: Dictionary containing form data
    """
    initialize_segment_creation_state(form_data)
    st.rerun()


def main() -> None:
    """Main function to render the Create VPN page."""

    # Page title
    st.title("Create VPN")

    # Check if segment creation is in progress
    segment_creation_active = "segment_creation" in st.session_state and st.session_state.segment_creation.get("active")

    if not segment_creation_active:
        st.markdown(
            "Fill in the form below to create a new Network Segment in Infrahub. "
            "This will create a branch, add the segment, and create a proposed change for review."
        )
    else:
        st.info("Network segment creation in progress... Form is read-only during execution.")

    # Initialize API client
    client = InfrahubClient(
        st.session_state.infrahub_url,
        api_token=INFRAHUB_API_TOKEN or None,
        ui_url=INFRAHUB_UI_URL,
    )

    # Fetch deployments (cache in session state)
    if "deployments" not in st.session_state:
        with st.spinner("Loading deployments..."):
            try:
                st.session_state.deployments = client.get_deployments()
            except Exception as e:
                display_error(
                    "Unable to load deployments",
                    f"Failed to fetch TopologyDeployment objects from Infrahub.\n\n{str(e)}",
                )
                st.stop()

    # Fetch organizations (cache in session state)
    if "organizations" not in st.session_state:
        with st.spinner("Loading organizations..."):
            try:
                st.session_state.organizations = client.get_organizations()
            except Exception as e:
                display_error(
                    "Unable to load organizations",
                    f"Failed to fetch OrganizationGeneric objects from Infrahub.\n\n{str(e)}",
                )
                st.stop()

    # Fetch active prefixes for optional prefix assignment
    if "segment_prefixes" not in st.session_state:
        with st.spinner("Loading prefixes..."):
            try:
                st.session_state.segment_prefixes = client.get_active_prefixes()
            except Exception as e:
                st.warning(f"Could not load prefixes: {e}")
                st.session_state.segment_prefixes = []

    # Segment Creation Form
    st.markdown("---")

    with st.form("segment_creation_form"):
        st.subheader("Network Segment Information")

        col1, col2 = st.columns(2)

        with col1:
            # Customer/Segment Name
            customer_name = st.text_input(
                "Customer Segment Name *",
                placeholder="e.g., web-tier, database, dmz",
                help="Name for this network segment",
                disabled=segment_creation_active,
            )

            # Deployment selection
            deployment_options = [
                d.get("display_label") or d.get("name", {}).get("value", "Unknown")
                for d in st.session_state.deployments
            ]
            deployment_map = {
                d.get("display_label") or d.get("name", {}).get("value", "Unknown"): d.get("id")
                for d in st.session_state.deployments
            }

            if not deployment_options:
                st.warning("No deployments found. Please create a Data Center first.")
                deployment_name = None
                deployment_id = None
            else:
                deployment_name = st.selectbox(
                    "Deployment *",
                    options=deployment_options,
                    help="Data Center or Colocation Center where this segment will be deployed",
                    disabled=segment_creation_active,
                )
                deployment_id = deployment_map.get(deployment_name)

            # Owner selection
            owner_options = [
                o.get("display_label") or o.get("name", {}).get("value", "Unknown")
                for o in st.session_state.organizations
            ]
            owner_map = {
                o.get("display_label") or o.get("name", {}).get("value", "Unknown"): o.get("id")
                for o in st.session_state.organizations
            }

            if not owner_options:
                st.warning("No organizations found.")
                owner_name = None
                owner_id = None
            else:
                owner_name = st.selectbox(
                    "Owner *",
                    options=owner_options,
                    help="Organization that owns this network segment",
                    disabled=segment_creation_active,
                )
                owner_id = owner_map.get(owner_name)

            # VLAN ID
            vlan_id = st.number_input(
                "VLAN ID *",
                min_value=1,
                max_value=4094,
                value=100,
                help="VLAN ID for this segment (1-4094). VNI will be VLAN + 10000",
                disabled=segment_creation_active,
            )

        with col2:
            # Environment
            environment_options = ["production", "no-production"]
            environment_labels = {
                "production": "Production",
                "no-production": "No Production",
            }
            environment = st.selectbox(
                "Environment *",
                options=environment_options,
                format_func=lambda x: environment_labels.get(x, x),
                help="Customer environment type",
                disabled=segment_creation_active,
            )

            # Segment Type
            segment_type_options = ["l2_only", "l3_gateway", "l3_vrf"]
            segment_type_labels = {
                "l2_only": "L2 Only",
                "l3_gateway": "L3 Gateway",
                "l3_vrf": "L3 VRF",
            }
            segment_type = st.selectbox(
                "Segment Type *",
                options=segment_type_options,
                format_func=lambda x: segment_type_labels.get(x, x),
                help="Type of network segment",
                disabled=segment_creation_active,
            )

            # Tenant Isolation
            isolation_options = [
                "customer_dedicated",
                "shared_controlled",
                "public_shared",
            ]
            isolation_labels = {
                "customer_dedicated": "Customer Dedicated",
                "shared_controlled": "Shared Controlled",
                "public_shared": "Public Shared",
            }
            tenant_isolation = st.selectbox(
                "Tenant Isolation *",
                options=isolation_options,
                format_func=lambda x: isolation_labels.get(x, x),
                help="Level of tenant isolation for this segment",
                disabled=segment_creation_active,
            )

            # External Routing
            external_routing = st.checkbox(
                "External Routing",
                value=False,
                help="Enable routing outside the namespace",
                disabled=segment_creation_active,
            )

        # Optional: Prefix selection
        st.markdown("---")
        st.subheader("Optional Configuration")

        prefix_options = ["None (No prefix assigned)"]
        prefix_map = {"None (No prefix assigned)": None}

        for prefix in st.session_state.segment_prefixes:
            prefix_value = prefix.get("prefix", {}).get("value")
            prefix_id = prefix.get("id")
            if prefix_value:
                prefix_options.append(prefix_value)
                prefix_map[prefix_value] = prefix_id

        selected_prefix = st.selectbox(
            "IP Prefix (Optional)",
            options=prefix_options,
            help="Optionally assign an IP prefix to this segment",
            disabled=segment_creation_active,
        )
        prefix_id = prefix_map.get(selected_prefix)

        # Submit button
        st.markdown("---")
        submitted = st.form_submit_button(
            "Create VPN",
            type="primary",
            use_container_width=True,
            disabled=segment_creation_active,
        )

        if submitted:
            # Validate required fields
            errors = []

            if not customer_name:
                errors.append("Customer Segment Name is required")
            if not deployment_id:
                errors.append("Deployment is required")
            if not owner_id:
                errors.append("Owner is required")
            if not vlan_id or vlan_id < 1 or vlan_id > 4094:
                errors.append("VLAN ID must be between 1 and 4094")

            if errors:
                display_error(
                    "Form validation failed",
                    "\n".join(f"* {error}" for error in errors),
                )
            else:
                # Store form data in session state for processing
                form_data = {
                    "customer_name": customer_name,
                    "deployment": deployment_id,
                    "deployment_name": deployment_name,
                    "owner": owner_id,
                    "owner_name": owner_name,
                    "vlan_id": vlan_id,
                    "environment": environment,
                    "segment_type": segment_type,
                    "tenant_isolation": tenant_isolation,
                    "external_routing": external_routing,
                    "prefix": prefix_id,
                }

                handle_segment_creation(client, form_data)

    # Create placeholder for progress section
    st.markdown("---")
    progress_section = st.container()

    # Render progress section if segment creation is active
    if segment_creation_active:
        with progress_section:
            st.markdown("## Network Segment Creation Progress")
            st.markdown("")

            render_progress_tracker()

            st.markdown("---")
            st.markdown("### Status Updates")
            st.markdown("")

            execute_segment_creation_step(client)


if __name__ == "__main__":
    main()
