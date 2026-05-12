# Generator Specification: [GENERATOR NAME]

> **Workflow type**: Infrahub Generator (design-driven automation)
> **Skill**: Use the `infrahub:generator-creator` skill to implement this specification.

**Feature Branch**: `[###-generator-name]`
**Created**: [DATE]
**Status**: Draft
**Input**: User description: "$ARGUMENTS"

## Generator Overview

<!--
  Describe what this generator does at a high level. Generators follow the
  design-driven automation pattern: a "design" object in Infrahub triggers
  automatic creation of downstream infrastructure objects.

  Key question: What design object drives this generator, and what infrastructure
  does it produce?
-->

**Design Object (Source)**: [THE INFRAHUB MODEL THAT TRIGGERS GENERATION, e.g., TopologyDataCenter, ServiceNetworkSegment]

**Generated Objects (Targets)**: [THE INFRAHUB MODELS CREATED BY THIS GENERATOR, e.g., DcimDevice, IpamVLAN, InfraInterface]

**Target Group**: [THE CoreGeneratorGroup THAT CONTAINS THE TRIGGERING OBJECTS, e.g., topologies_dc, network_segments]

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.

  Example generator user stories for inspiration:
  - "As a network engineer, I want devices to be auto-created when I define a new POP topology,
     so I don't have to manually provision each device."
  - "As an operator, I want interfaces generated from a device template, so that every device of
     a given role has a consistent interface layout."
  - "As a network planner, I want network segments to auto-provision VLANs and VxLAN config on
     leaf switches, so service deployment is hands-free."
  - "As an infrastructure team lead, I want IP address pools created automatically from a site
     design, so addressing is consistent across all locations."
-->

### User Story 1 - [Brief Title] (Priority: P1)

[Describe this user journey in plain language. Focus on what design object the user creates and what infrastructure appears automatically.]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently - e.g., "Can be fully tested by creating a single [DESIGN OBJECT] in Infrahub and verifying that [GENERATED OBJECTS] are created"]

**Acceptance Scenarios**:

1. **Given** a [DESIGN OBJECT] exists in the target group, **When** the generator runs, **Then** [GENERATED OBJECTS] are created with correct attributes
2. **Given** the generator has already run for a [DESIGN OBJECT], **When** it runs again with no changes, **Then** no duplicate objects are created (idempotent behavior)

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 3 - [Brief Title] (Priority: P3)

[Describe this user journey in plain language]

**Why this priority**: [Explain the value and why it has this priority level]

**Independent Test**: [Describe how this can be tested independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: Fill out generator-specific edge cases.
  Consider the scenarios below and add any that are relevant.
-->

- What happens when the design object has missing or optional fields (e.g., no subnet assigned)?
- What happens when generated objects already exist from a previous run (idempotent upsert)?
- What happens when a design object is removed from the target group (cleanup of orphaned objects)?
- What happens when the GraphQL query returns an empty result set?
- What happens when a referenced relationship (e.g., location, device_type) does not exist in Infrahub?
- What happens when the generator runs on a branch vs. on main?
- What happens when multiple design objects in the target group reference overlapping resources?
- What happens when the design object is updated (e.g., quantity changes) -- are previously generated objects cleaned up?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements for your generator.
-->

### Functional Requirements

**Generator Class & Method**:

- **FR-001**: Generator MUST inherit from `infrahub_sdk.generator.InfrahubGenerator`
- **FR-002**: Generator MUST implement `async generate(self, data: dict) -> None`
- **FR-003**: Generator MUST use `self.client` for all Infrahub SDK operations (creating, querying objects)

**GraphQL Query**:

- **FR-004**: A GraphQL query file MUST exist in `queries/` that fetches all data the generator needs from the design object
- **FR-005**: The query MUST accept a `$name: String!` parameter (or the parameter defined in the `.infrahub.yml` parameters mapping)
- **FR-006**: The query MUST fetch [DESCRIBE WHAT DATA THE QUERY NEEDS TO RETRIEVE]

**Object Creation**:

- **FR-007**: Generator MUST create [LIST THE OBJECT KINDS TO BE CREATED, e.g., DcimDevice, InfraInterface]
- **FR-008**: All `save()` calls MUST use `allow_upsert=True` to ensure idempotent create-or-update behavior
- **FR-009**: Objects MUST be created in dependency order (e.g., devices before interfaces, prefixes before IP addresses)
- **FR-010**: [ADDITIONAL CREATION REQUIREMENTS SPECIFIC TO YOUR GENERATOR]

**Tracking & Cleanup**:

- **FR-011**: Generator SHOULD support `delete_unused_nodes` behavior so that objects removed from the design are automatically cleaned up
- **FR-012**: [DESCRIBE ANY SPECIFIC CLEANUP REQUIREMENTS]

**Registration**:

- **FR-013**: Generator MUST be registered in `.infrahub.yml` under `generator_definitions` with correct `name`, `file_path`, `query`, `targets`, `class_name`, and `parameters`
- **FR-014**: The corresponding GraphQL query MUST be registered in `.infrahub.yml` under `queries`

*Mark unclear requirements:*

- **FR-0XX**: Generator MUST [NEEDS CLARIFICATION: describe what needs to be clarified]

### Key Entities *(include if feature involves data)*

- **[DESIGN OBJECT KIND]**: [The source object that drives generation -- describe its key attributes and relationships]
- **[GENERATED OBJECT KIND 1]**: [What it represents, how it relates to the design object]
- **[GENERATED OBJECT KIND 2]**: [What it represents, relationships to other generated objects]

### Key Files

| File | Purpose |
|------|---------|
| `generators/[GENERATOR_FILE].py` | Python generator class with `generate()` method |
| `queries/[QUERY_FILE].gql` | GraphQL query that fetches design data |
| `.infrahub.yml` | Registration of generator and query definitions |
| `generators/__init__.py` | Package init (must exist for Python imports) |
| `generators/common.py` | Shared helpers if needed (data cleaning, batch creation) |

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be specific to the generator's behavior.
-->

### Measurable Outcomes

- **SC-001**: When a [DESIGN OBJECT] is added to the target group, the generator creates all expected [GENERATED OBJECTS] with correct attributes and relationships
- **SC-002**: Running the generator multiple times on the same design object produces no duplicates (idempotent upsert via allow_upsert=True)
- **SC-003**: When a design object is modified, re-running the generator updates the generated objects to match the new design
- **SC-004**: The generator can be tested locally with `infrahubctl generator [GENERATOR_NAME] --target [TARGET_OBJECT_NAME]`
- **SC-005**: [ADDITIONAL SUCCESS CRITERIA SPECIFIC TO YOUR GENERATOR]
