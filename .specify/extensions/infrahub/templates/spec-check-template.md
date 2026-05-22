# Check Specification: [CHECK NAME]

> **This is an Infrahub validation check spec.** To implement this check, use the `infrahub:check-creator` skill.

**Feature Branch**: `[###-check-name]`
**Created**: [DATE]
**Status**: Draft
**Input**: User description: "$ARGUMENTS"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable check that delivers validation value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.

  Below are EXAMPLE user stories for common Infrahub checks. Replace them entirely with
  stories relevant to your actual check.
-->

### User Story 1 - [Brief Title] (Priority: P1)

[Describe the validation scenario in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently - e.g., "Can be fully tested by creating a proposed change with conflicting data and verifying the check blocks the merge"]

**Acceptance Scenarios**:

1. **Given** [data state in Infrahub], **When** [a proposed change is created], **Then** [check passes or logs specific errors]
2. **Given** [invalid data state], **When** [the check runs in the pipeline], **Then** [self.log_error is called with a descriptive message and the proposed change is blocked]

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe the validation scenario in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [data state], **When** [proposed change is created], **Then** [expected validation outcome]

---

### User Story 3 - [Brief Title] (Priority: P3)

[Describe the validation scenario in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [data state], **When** [proposed change is created], **Then** [expected validation outcome]

---

[Add more user stories as needed, each with an assigned priority]

#### Example User Stories for Reference

<!--
  Delete these examples after writing your own stories above. They illustrate
  common validation check patterns in Infrahub.
-->

- **Validate rack unit collisions**: Ensure no two devices occupy the same rack unit position in the same rack. A global check that fetches all devices and their rack positions, then detects overlapping unit ranges.
- **Enforce naming standards**: Verify that objects of a given type follow a naming convention (e.g., lowercase alphanumeric with hyphens). A global check using regex validation against queried name fields.
- **Check interface assignments**: Ensure devices in a target group have required interfaces configured with IP addresses. A targeted check that queries per-device interface data and validates loopback and uplink presence.
- **Validate VLAN uniqueness per site**: Ensure no duplicate VLAN IDs exist within the same site. A global check that groups VLANs by site and detects ID conflicts.
- **Check BGP session configuration**: Verify that devices with a BGP role have at least two BGP peer sessions configured. A targeted check scoped to a device group.

### Edge Cases

<!--
  ACTION REQUIRED: Replace or extend these placeholders with edge cases specific
  to your validation check.
-->

- What happens when the GraphQL query returns zero objects (empty edges list)?
- What happens when an object has null or missing attribute values that the check expects?
- What happens when a relationship (e.g., rack, device_type) is unset and returns null?
- How does the check behave when data is only partially present (e.g., device exists but has no interfaces)?
- What happens when the check runs against a proposed change that deletes the objects being validated?
- For targeted checks: what happens when the target group is empty (no members)?
- Does the check handle objects with default/fallback values correctly (e.g., u_height defaults to 1)?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: Replace these placeholders with the actual functional requirements
  for your validation check. Use Infrahub-specific terminology.
-->

### Functional Requirements

- **FR-001**: Check MUST inherit from `InfrahubCheck` (from `infrahub_sdk.checks`) and implement the `validate(self, data: dict)` method
- **FR-002**: Check MUST define a `query` class attribute matching the query name registered in `.infrahub.yml`
- **FR-003**: Check MUST call `self.log_error(message=...)` for each validation failure, providing a human-readable description of the problem
- **FR-004**: Check MAY call `self.log_info(message=...)` for non-blocking warnings or informational messages
- **FR-005**: GraphQL query MUST fetch all attributes and relationships needed for the validation logic
- **FR-006**: [VALIDATION RULE - e.g., "Check MUST detect when two devices in the same rack have overlapping rack unit positions"]
- **FR-007**: [VALIDATION RULE - e.g., "Check MUST include the object name and rack name in every error message for traceability"]
- **FR-008**: [VALIDATION RULE - e.g., "Check MUST skip objects with null position values without logging an error"]
- **FR-009**: Check MUST be registered in `.infrahub.yml` under `check_definitions` with the correct `class_name` and `file_path`
- **FR-010**: [SCOPE - e.g., "Check MUST run as a global check (no targets) so it validates all objects on every proposed change" OR "Check MUST be targeted to the [GROUP_NAME] group with appropriate query parameters"]

*Mark unclear requirements:*

- **FR-011**: [NEEDS CLARIFICATION: e.g., "Should the check fail on warnings or only on errors?"]
- **FR-012**: [NEEDS CLARIFICATION: e.g., "Which Infrahub schema types should the query cover?"]

### Check Architecture

- **Check Type**: [GLOBAL or TARGETED]
  - Global: Runs on every proposed change, validates all objects of a type. Query has no variables.
  - Targeted: Runs per member of a target group. Query accepts a variable (e.g., `$device`) mapped via `parameters` in `.infrahub.yml`.
- **Target Group**: [GROUP_NAME or "N/A for global checks"]
- **Query Parameters**: [LIST OF GRAPHQL VARIABLES or "None for global checks"]

### Key Files

- **Python check**: `[CHECK_FILENAME].py` (at repo root or in a `checks/` directory)
- **GraphQL query**: `queries/[QUERY_FILENAME].gql`
- **Configuration**: `.infrahub.yml` (under `check_definitions` and `queries` sections)
- **Shared utilities** *(optional)*: `checks/common.py` for reusable functions like `clean_data()`, `get_data()`, or shared validation helpers

### Key Entities *(include if check involves specific Infrahub schema types)*

- **[SCHEMA_TYPE_1]**: [What it represents and which attributes the check validates - e.g., "DcimDevice: validates rack_u_position, rack_face, and device_type.u_height"]
- **[SCHEMA_TYPE_2]**: [Related type and its role in validation - e.g., "DcimRack: used to group devices by rack for collision detection"]

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria for this check.
-->

### Measurable Outcomes

- **SC-001**: Check runs without errors on a clean dataset (no false positives)
- **SC-002**: Check correctly detects and logs errors for [KNOWN INVALID DATA SCENARIO]
- **SC-003**: Check blocks proposed changes that introduce [SPECIFIC VIOLATION]
- **SC-004**: Check passes for proposed changes that contain only valid data
- **SC-005**: [PERFORMANCE - e.g., "Check completes within N seconds for a dataset of M objects"]
- **SC-006**: Check can be executed locally with `infrahubctl check [CHECK_NAME]` for development testing
