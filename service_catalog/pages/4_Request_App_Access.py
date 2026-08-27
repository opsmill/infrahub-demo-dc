"""Infrahub Service Catalog - Request Application Access Page.

Self-service firewall access: pick the addresses that need to reach other addresses, say which
ports, and submit. The page creates a branch, builds the service objects the request needs, adds a
SecurityPolicyRule and opens a proposed change.

Nothing here decides whether the request is safe. That is the point -- the rule lands on a branch
where `validate_security_policy` grades it, so a request that is too broad is rejected before a
human reads it, and one that is fine arrives at review already justified.
"""

from typing import Any, Dict, List, Optional, Tuple

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

VALID_PROTOCOLS = ("tcp", "udp")

# Initialize session state
if "selected_branch" not in st.session_state:
    st.session_state.selected_branch = DEFAULT_BRANCH

if "infrahub_url" not in st.session_state:
    st.session_state.infrahub_url = INFRAHUB_ADDRESS


def slug(value: str) -> str:
    """Reduce a label to something safe for an object or branch name.

    Args:
        value: Free text.

    Returns:
        Lowercased text with runs of non-alphanumerics collapsed to single hyphens.
    """
    out = "".join(char if char.isalnum() else "-" for char in value.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def parse_extra_ports(raw: str) -> Tuple[List[Tuple[str, int]], List[str]]:
    """Parse the free-text port box into protocol/port pairs.

    Accepts a comma-separated list of ``proto/port``, e.g. ``tcp/8443, udp/5514``.

    Args:
        raw: The text the requester typed.

    Returns:
        The parsed pairs, and a message for every entry that could not be used.
    """
    pairs: List[Tuple[str, int]] = []
    errors: List[str] = []

    for chunk in (part.strip() for part in raw.split(",")):
        if not chunk:
            continue
        if "/" not in chunk:
            errors.append(f"'{chunk}' is not in protocol/port form, e.g. tcp/8443")
            continue

        protocol, _, port_text = chunk.partition("/")
        protocol = protocol.strip().lower()
        port_text = port_text.strip()

        if protocol not in VALID_PROTOCOLS:
            errors.append(f"'{chunk}': protocol must be one of {', '.join(VALID_PROTOCOLS)}")
            continue
        if not port_text.isdigit():
            errors.append(f"'{chunk}': '{port_text}' is not a port number")
            continue

        port = int(port_text)
        if not 1 <= port <= 65535:
            errors.append(f"'{chunk}': port must be between 1 and 65535")
            continue

        pair = (protocol, port)
        if pair not in pairs:
            pairs.append(pair)

    return pairs, errors


def service_label(service: Dict[str, Any]) -> str:
    """Render a service for the picker.

    Args:
        service: A service dict from ``get_services``.

    Returns:
        A label such as ``https (tcp/443)``.
    """
    return f"{service['name']} ({service['protocol']}/{service['port']})"


def group_label(group: Dict[str, Any]) -> str:
    """Render an address group for the picker, flagging the ones that constrain nothing.

    A group with no members renders as ``any`` on the device, so the requester needs to see that
    before choosing it rather than after the check rejects the rule.

    Args:
        group: An address group dict from ``get_address_groups``.

    Returns:
        A label carrying the zone and a warning where the group is empty.
    """
    zone = group.get("zone") or "no zone"
    if not group.get("member_count"):
        return f"{group['name']} — {zone} ⚠ empty"
    return f"{group['name']} — {zone} ({group['member_count']} members)"


def build_service_group_name(pairs: List[Tuple[str, int]]) -> str:
    """Name a service group after its contents, so repeat requests reuse it.

    Args:
        pairs: Protocol/port pairs the group holds.

    Returns:
        A deterministic name such as ``svc-tcp443-tcp3306``.
    """
    return "svc-" + "-".join(f"{protocol}{port}" for protocol, port in sorted(pairs))


def initialize_request_state(form_data: Dict[str, Any]) -> None:
    """Set up session state for the request workflow.

    Args:
        form_data: Everything the form collected.
    """
    source = slug(form_data["source_name"])
    destination = slug(form_data["destination_name"])

    st.session_state.access_request = {
        "active": True,
        "step": 1,
        "form_data": form_data,
        # The index is unique within the policy, so it keeps the branch name unique too.
        "branch_name": f"access-{source}-to-{destination}-{form_data['index']}",
        "rule_name": f"req-{source}-to-{destination}",
        "service_group_id": None,
        "error": None,
        "pc_url": None,
    }


def render_progress_tracker() -> None:
    """Render the progress tracker based on current state."""
    if "access_request" not in st.session_state or not st.session_state.access_request.get("active"):
        return

    current_step = st.session_state.access_request["step"]
    steps = [
        "Creating branch",
        "Building service objects",
        "Creating policy rule",
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


def execute_request_step(client: InfrahubClient) -> None:
    """Execute the current step of the access request workflow.

    Args:
        client: InfrahubClient instance.
    """
    state = st.session_state.access_request
    step = state["step"]
    branch_name = state["branch_name"]
    form_data = state["form_data"]

    try:
        if step == 1:
            with st.status("Creating branch...", expanded=True) as status:
                st.write(f"Creating branch: {branch_name}")
                branch = client.create_branch(branch_name, from_branch="main")
                st.write(f"Branch created: {branch['name']}")
                status.update(label="Branch created!", state="complete")
                state["step"] = 2
                st.rerun()

        elif step == 2:
            with st.status("Building service objects...", expanded=True) as status:
                service_ids = list(form_data["existing_service_ids"])

                for protocol, port in form_data["extra_ports"]:
                    name = f"{protocol}-{port}"
                    st.write(f"Adding service: {name}")
                    service_ids.append(client.upsert_service(branch_name, name=name, protocol=protocol, port=port))

                group_name = form_data["service_group_name"]
                st.write(f"Building service group: {group_name}")
                state["service_group_id"] = client.upsert_service_group(
                    branch_name,
                    name=group_name,
                    service_ids=service_ids,
                    description=f"Ports requested for {state['rule_name']}",
                )
                st.write(f"Service group ready with {len(service_ids)} service(s)")
                status.update(label="Service objects ready!", state="complete")
                state["step"] = 3
                st.rerun()

        elif step == 3:
            with st.status("Creating policy rule...", expanded=True) as status:
                st.write(f"Creating rule '{state['rule_name']}' at index {form_data['index']}")
                rule = client.create_policy_rule(
                    branch_name,
                    {
                        "index": form_data["index"],
                        "name": state["rule_name"],
                        "policy": form_data["policy_id"],
                        "source_addresses": [form_data["source_id"]],
                        "destination_addresses": [form_data["destination_id"]],
                        "services": [state["service_group_id"]],
                        "source_zone": form_data.get("source_zone_id"),
                        "destination_zone": form_data.get("destination_zone_id"),
                        "action": "permit",
                        "log": True,
                    },
                )
                st.write(f"Rule created: {rule['name']}")
                status.update(label="Policy rule created!", state="complete")
                state["step"] = 4
                st.rerun()

        elif step == 4:
            with st.status("Creating Proposed Change...", expanded=True) as status:
                pc_name = f"Access request: {form_data['source_name']} to {form_data['destination_name']}"
                pc_description = (
                    f"Requested by {form_data['requester'] or 'the service catalog'}.\n\n"
                    f"{form_data['source_name']} needs to reach {form_data['destination_name']} on "
                    f"{form_data['ports_summary']}.\n\n"
                    f"Justification: {form_data['justification'] or 'not supplied'}"
                )
                st.write(f"Creating Proposed Change: {pc_name}")
                pc = client.create_proposed_change(branch_name, pc_name, pc_description)
                state["pc_url"] = client.get_proposed_change_url(pc["id"])
                st.write("Proposed Change created")
                status.update(label="Proposed Change created!", state="complete")
                state["step"] = 5
                st.rerun()

        elif step == 5:
            state["active"] = False
            st.markdown("---")
            display_success("Access request submitted!")

            st.markdown(f"""
            ### What happens next

            The rule was added to branch `{branch_name}` and a Proposed Change is open.

            **Proposed Change URL:**
            [{state["pc_url"]}]({state["pc_url"]})

            The `validate_security_policy` check runs against every firewall carrying this policy.
            If the request is broader than it needs to be, the check fails and the change cannot
            merge until the rule is narrowed.
            """)

    except (
        InfrahubConnectionError,
        InfrahubHTTPError,
        InfrahubGraphQLError,
        InfrahubAPIError,
    ) as e:
        state["error"] = str(e)
        state["active"] = False

        failures = {
            1: ("Failed to create branch", f"Branch: {branch_name}"),
            2: (
                "Failed to build the service objects",
                f"The branch '{branch_name}' was created but the ports could not be recorded.",
            ),
            3: (
                "Failed to create the policy rule",
                f"The service objects exist in branch '{branch_name}' but the rule was not created.",
            ),
            4: (
                "Failed to create the Proposed Change",
                f"The rule was created in branch '{branch_name}'. Open a Proposed Change for that "
                f"branch in the Infrahub UI to get it reviewed.",
            ),
        }
        title, detail = failures.get(step, ("Access request failed", ""))
        display_error(title, f"{detail}\n\n{str(e)}")


def load_reference_data(client: InfrahubClient) -> None:
    """Fetch the pickers' contents once per session.

    Args:
        client: InfrahubClient instance.
    """
    loaders = [
        ("access_address_groups", client.get_address_groups, "address groups"),
        ("access_services", client.get_services, "services"),
        ("access_policies", client.get_security_policies, "security policies"),
    ]
    for key, loader, label in loaders:
        if key not in st.session_state:
            with st.spinner(f"Loading {label}..."):
                try:
                    st.session_state[key] = loader()
                except Exception as e:
                    display_error(f"Unable to load {label}", str(e))
                    st.stop()


def main() -> None:
    """Render the Request Application Access page."""
    st.title("Request Application Access")

    request_active = "access_request" in st.session_state and st.session_state.access_request.get("active")

    if not request_active:
        st.markdown(
            "Say which addresses need to reach which other addresses, and on what ports. "
            "The request becomes a firewall rule on its own branch, with a proposed change for review."
        )
    else:
        st.info("Access request in progress... Form is read-only during execution.")

    client = InfrahubClient(
        st.session_state.infrahub_url,
        api_token=INFRAHUB_API_TOKEN or None,
        ui_url=INFRAHUB_UI_URL,
    )

    load_reference_data(client)

    groups: List[Dict[str, Any]] = st.session_state.access_address_groups
    services: List[Dict[str, Any]] = st.session_state.access_services
    policies: List[Dict[str, Any]] = st.session_state.access_policies

    if not groups:
        display_error("No address groups found", "Load objects/security/ before using this page.")
        st.stop()

    # A policy attached to no firewall renders nowhere and is never checked, so those are last.
    policies = sorted(policies, key=lambda p: (not p["firewalls"], p["name"] or ""))
    group_by_label = {group_label(g): g for g in groups}
    service_by_label = {service_label(s): s for s in services}

    st.markdown("---")

    with st.form("access_request_form"):
        st.subheader("What needs to talk to what")

        col1, col2 = st.columns(2)
        with col1:
            source_label = st.selectbox(
                "Source *",
                options=list(group_by_label),
                help="The addresses initiating the connection",
                disabled=request_active,
            )
        with col2:
            destination_label = st.selectbox(
                "Destination *",
                options=list(group_by_label),
                index=min(1, len(group_by_label) - 1),
                help="The addresses being reached",
                disabled=request_active,
            )

        st.markdown("---")
        st.subheader("Ports")

        selected_service_labels = st.multiselect(
            "Known services",
            options=list(service_by_label),
            help="Pick from the services already modelled",
            disabled=request_active,
        )
        extra_ports_raw = st.text_input(
            "Additional ports",
            placeholder="tcp/8443, udp/5514",
            help="Anything not in the list above. Comma-separated, protocol/port.",
            disabled=request_active,
        )

        st.markdown("---")
        st.subheader("Request details")

        policy_labels = [
            f"{p['name']} — {', '.join(p['firewalls']) if p['firewalls'] else 'no firewall attached'}" for p in policies
        ]
        policy_by_label = dict(zip(policy_labels, policies))
        policy_label = st.selectbox(
            "Policy *",
            options=policy_labels,
            help="Which policy the rule joins. A policy with no firewall attached renders nowhere.",
            disabled=request_active,
        )

        col3, col4 = st.columns(2)
        with col3:
            requester = st.text_input("Requested by", placeholder="your name", disabled=request_active)
        with col4:
            justification = st.text_input(
                "Justification",
                placeholder="e.g. order service needs the reporting database",
                disabled=request_active,
            )

        st.markdown("---")
        submitted = st.form_submit_button(
            "Submit request",
            type="primary",
            use_container_width=True,
            disabled=request_active,
        )

        if submitted:
            source = group_by_label[source_label]
            destination = group_by_label[destination_label]
            policy = policy_by_label[policy_label]

            extra_ports, port_errors = parse_extra_ports(extra_ports_raw)
            chosen = [service_by_label[label] for label in selected_service_labels]

            errors = list(port_errors)
            if source["id"] == destination["id"]:
                errors.append("Source and destination are the same group")
            if not chosen and not extra_ports:
                errors.append(
                    "Pick at least one service or add a port. A rule with no service permits "
                    "everything, and the policy check will reject it."
                )

            index: Optional[int] = None
            if not errors:
                try:
                    index = client.next_rule_index(policy["name"])
                except InfrahubAPIError as e:
                    errors.append(str(e))

            if errors:
                display_error("Request cannot be submitted", "\n".join(f"* {error}" for error in errors))
            else:
                all_pairs = [(s["protocol"], s["port"]) for s in chosen] + extra_ports
                ports_summary = ", ".join(f"{protocol}/{port}" for protocol, port in sorted(all_pairs))

                initialize_request_state(
                    {
                        "source_id": source["id"],
                        "source_name": source["name"],
                        "source_zone_id": source.get("zone_id"),
                        "destination_id": destination["id"],
                        "destination_name": destination["name"],
                        "destination_zone_id": destination.get("zone_id"),
                        "policy_id": policy["id"],
                        "policy_name": policy["name"],
                        "existing_service_ids": [s["id"] for s in chosen],
                        "extra_ports": extra_ports,
                        "service_group_name": build_service_group_name(all_pairs),
                        "ports_summary": ports_summary,
                        "index": index,
                        "requester": requester,
                        "justification": justification,
                    }
                )
                st.rerun()

    # Warn outside the form so the message reacts to the current selection.
    if not request_active:
        source = group_by_label[source_label]
        destination = group_by_label[destination_label]
        if not source.get("zone_id") or not destination.get("zone_id"):
            st.warning(
                "One of the selected groups has no zone. The rule will be created without zones, "
                "which renders as `from any to any` on the device -- the policy check will say so."
            )
        if not source.get("member_count") or not destination.get("member_count"):
            st.warning(
                "One of the selected groups has no members. An empty group matches every address, "
                "so this request is broader than it looks and the policy check will reject it."
            )

    st.markdown("---")
    progress_section = st.container()

    if request_active:
        with progress_section:
            st.markdown("## Access Request Progress")
            st.markdown("")
            render_progress_tracker()
            st.markdown("---")
            st.markdown("### Status Updates")
            st.markdown("")
            execute_request_step(client)


if __name__ == "__main__":
    main()
