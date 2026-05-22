# Transform Specification: [TRANSFORM NAME]

> **Workflow**: Infrahub Transform
> **Skill**: Use the `infrahub:transform-creator` skill to implement this spec.

**Feature Branch**: `[###-transform-name]`
**Created**: [DATE]
**Status**: Draft
**Input**: User description: "$ARGUMENTS"

## Transform Type

<!--
  Identify the transform approach. Choose one:
  - **Python**: Use InfrahubTransform class when you need data manipulation, filtering, aggregation, or structured JSON output
  - **Jinja2**: Use a .j2 template when you need text-based output with simple data substitution
  - **Hybrid**: Use Python data preparation + Jinja2 rendering when you need both logic and templated text output (e.g., device configs)
-->

- **Approach**: [Python / Jinja2 / Hybrid]
- **Output Format**: [JSON / CSV / text/plain / device config / YAML]
- **Target Nodes**: [INFRAHUB NODE KIND, e.g., DcimDevice, TopologyDataCenter]

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.

  Example transform user stories for inspiration:
  - "Generate device configurations for all spine switches in a datacenter"
  - "Export a CSV inventory of all devices with their management IPs and roles"
  - "Create a cable matrix report for a given topology"
  - "Render ContainerLab topology files from datacenter data"
  - "Produce a JSON summary of BGP peering sessions per device"
-->

### User Story 1 - [Brief Title] (Priority: P1)

[Describe the transform use case in plain language. What data goes in, what output comes out, and who consumes the result.]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently - e.g., "Can be validated by running `infrahubctl transforms [TRANSFORM_NAME] --param value` and verifying the output matches expected format"]

**Acceptance Scenarios**:

1. **Given** [data exists in Infrahub, e.g., "a device with interfaces and IP addresses"], **When** [the transform is executed with the device name parameter], **Then** [expected output, e.g., "a valid device configuration is returned"]
2. **Given** [alternative data state], **When** [the transform is executed], **Then** [expected behavior]

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe this transform journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 3 - [Brief Title] (Priority: P3)

[Describe this transform journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases for your transform.
-->

- What happens when the GraphQL query returns no matching nodes (empty edges list)?
- What happens when an optional relationship field is null (e.g., device has no primary_address)?
- What happens when a node attribute has a null value instead of the expected string?
- How does the transform handle nodes with missing or empty nested relationships (e.g., device with no interfaces)?
- What happens when the query returns multiple nodes but the transform expects exactly one?
- How are special characters in data handled for the output format (e.g., commas in CSV fields, reserved characters in config syntax)?
- What happens when the transform is run against a branch where the target data has been modified or deleted?
- [ADD TRANSFORM-SPECIFIC EDGE CASES HERE]

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements for your transform.
-->

### Functional Requirements

#### GraphQL Query

- **FR-001**: A GraphQL query MUST be created in `queries/[QUERY_PATH].gql` that retrieves all data needed by the transform
- **FR-002**: The query MUST accept parameters matching the artifact definition (e.g., `$device: String!` for per-device transforms)
- **FR-003**: The query MUST retrieve [LIST THE SPECIFIC DATA FIELDS NEEDED: e.g., "device name, interfaces with IPs, BGP sessions, OSPF configuration"]

#### Transform Logic

- **FR-004**: The transform MUST be implemented as [a Python class inheriting from InfrahubTransform / a Jinja2 template / a hybrid Python+Jinja2 pattern]
- **FR-005**: The transform MUST set `query = "[QUERY_NAME]"` to reference the GraphQL query
- **FR-006**: The transform MUST return [OUTPUT FORMAT DESCRIPTION: e.g., "a valid JSON dict with keys: hostname, interfaces, routing", or "a text string containing valid device configuration syntax", or "a CSV string with header row and one data row per connection"]
- **FR-007**: [TRANSFORM-SPECIFIC REQUIREMENT: e.g., "The transform MUST sort interfaces alphabetically by name"]
- **FR-008**: [TRANSFORM-SPECIFIC REQUIREMENT: e.g., "The transform MUST strip subnet masks from router-id addresses"]

#### Jinja2 Template *(include if using Jinja2 or Hybrid approach)*

- **FR-009**: The template MUST be created at `transforms/templates/[TEMPLATE_PATH].j2`
- **FR-010**: The template MUST use netutils filters via `jinja2_convenience_function()` where applicable (e.g., IP address manipulation)
- **FR-011**: [TEMPLATE-SPECIFIC REQUIREMENT: e.g., "The template MUST generate valid Arista EOS configuration syntax"]

#### Shared Utilities *(include if reusing common patterns)*

- **FR-012**: Shared data extraction helpers SHOULD be placed in `transforms/common.py`
- **FR-013**: [UTILITY REQUIREMENT: e.g., "A clean_data() function MUST normalize nested Infrahub API response structures"]

#### Artifact & Registration

- **FR-014**: The transform MUST be registered in `.infrahub.yml` under [python_transforms / jinja2_transforms]
- **FR-015**: The query MUST be registered in `.infrahub.yml` under `queries`
- **FR-016**: An artifact definition MUST be created in `.infrahub.yml` with content_type `[text/plain / text/csv / application/json]`
- **FR-017**: The artifact definition MUST target the correct group: `[TARGET_GROUP_NAME]`
- **FR-018**: The artifact definition MUST map parameters correctly (e.g., `device: name__value`)

*Example of marking unclear requirements:*

- **FR-019**: The transform MUST output data for [NEEDS CLARIFICATION: which specific node types/roles should be included?]
- **FR-020**: The artifact MUST target [NEEDS CLARIFICATION: target group not yet defined in schema]

### Key Files

| File | Purpose |
|------|---------|
| `queries/[QUERY_PATH].gql` | GraphQL query to fetch data from Infrahub |
| `transforms/[TRANSFORM_NAME].py` | Python transform class |
| `transforms/templates/[TEMPLATE_PATH].j2` | Jinja2 template *(if applicable)* |
| `transforms/common.py` | Shared utility functions *(if applicable)* |
| `transforms/__init__.py` | Package init |
| `.infrahub.yml` | Transform, query, and artifact registration |

### Key Entities *(include if feature involves data)*

- **[SOURCE NODE KIND]**: [What Infrahub data it represents, e.g., "DcimDevice - network device with interfaces, services, and management addressing"]
- **[RELATED NODE KIND]**: [Related data, e.g., "ServiceBGP - BGP routing service attached to a device"]
- **[OUTPUT ENTITY]**: [What the transform produces, e.g., "Device configuration file per spine switch"]

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be specific to the transform output and verifiable.
-->

### Measurable Outcomes

- **SC-001**: Running `infrahubctl transforms [TRANSFORM_NAME] --param [VALUE]` produces valid [OUTPUT FORMAT] output
- **SC-002**: The transform output contains all required data fields: [LIST FIELDS]
- **SC-003**: The transform handles missing optional data gracefully without raising exceptions
- **SC-004**: The artifact definition generates output files automatically for all nodes in the target group
- **SC-005**: [OUTPUT-SPECIFIC CRITERION: e.g., "Generated device configs pass syntax validation for the target platform"]
- **SC-006**: [COVERAGE CRITERION: e.g., "All device roles (spine, leaf, border) produce correct output"]
