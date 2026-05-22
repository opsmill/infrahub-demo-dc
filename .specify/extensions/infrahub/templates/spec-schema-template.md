# Schema Design Specification: [SCHEMA FEATURE NAME]

> **This is a schema design spec.** The implementing agent MUST use the `infrahub:schema-creator` skill to build and validate all schema definitions.

**Feature Branch**: `[###-schema-feature-name]`
**Created**: [DATE]
**Status**: Draft
**Input**: User description: "$ARGUMENTS"

## Schema Files

All schema definitions live in `schemas/*.yml`. Each file must start with:

```yaml
---
# yaml-language-server: $schema=https://schema.infrahub.app/infrahub/schema/latest.json
version: "1.0"
```

---

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable schema that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.

  Example user stories for schema design:
  - "Define a new device model with device types, so that operators can track hardware inventory"
  - "Add a location hierarchy (Region > Site > Room > Rack), so that infrastructure has physical context"
  - "Create a generic for shared attributes across all organization types"
  - "Model network interfaces with L2/L3 capabilities using multiple generics"
  - "Add IPAM nodes inheriting from BuiltinIPAddress and BuiltinIPPrefix"
  - "Extend an existing node from another schema file with new relationships"
-->

### User Story 1 - [Brief Title] (Priority: P1)

[Describe what data model this story introduces and why it is needed]

**Why this priority**: [Explain the modeling value and why it has this priority level]

**Independent Test**: [Describe how this schema can be validated independently - e.g., "Can be checked with `infrahubctl schema check schemas/` and delivers a usable node in the UI"]

**Acceptance Scenarios**:

1. **Given** [no schema exists for this domain], **When** [the schema is loaded], **Then** [the node appears in Infrahub with the expected attributes and relationships]
2. **Given** [the node exists], **When** [a user creates an instance], **Then** [mandatory fields are enforced and display_label renders correctly]

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe what data model this story introduces and why it is needed]

**Why this priority**: [Explain the modeling value and why it has this priority level]

**Independent Test**: [Describe how this schema can be validated independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### User Story 3 - [Brief Title] (Priority: P3)

[Describe what data model this story introduces and why it is needed]

**Why this priority**: [Explain the modeling value and why it has this priority level]

**Independent Test**: [Describe how this schema can be validated independently]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases for your schema design.
  Consider the schema-specific scenarios below.
-->

- What happens when a mandatory attribute has no default_value and existing data is present?
- How does the schema handle circular relationship references between nodes?
- What if two nodes in different schema files define relationships to each other without matching identifiers?
- What happens when a hierarchical node has no parent set (is it the root, or is it an error)?
- How should the schema behave when a Component relationship exists without a matching Parent on the child node?
- What if a Dropdown attribute is missing its choices list?
- What if uniqueness_constraints reference an attribute without the __value suffix?
- How are schema migrations handled when renaming or removing an existing attribute (state: absent)?
- What happens when a relationship peer references a kind that does not exist in any loaded schema file?
- What if a node inherits from a generic that has not been defined yet?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements for your schema.
-->

### Functional Requirements

#### Nodes & Generics

- **FR-001**: Schema MUST define [NODE NAMES] as concrete nodes under the [NAMESPACE] namespace
- **FR-002**: Schema MUST define [GENERIC NAMES] as generics to hold shared attributes for [PURPOSE]
- **FR-003**: Nodes MUST inherit from their respective generics using `inherit_from`
- **FR-004**: All node names MUST be PascalCase (pattern: `^[A-Z][a-zA-Z0-9]+$`, 2-32 chars)
- **FR-005**: All namespaces MUST start with an uppercase letter followed by lowercase (pattern: `^[A-Z][a-z0-9]+$`, 3-32 chars)

#### Attributes

- **FR-010**: Each node MUST have a [PRIMARY IDENTIFIER ATTRIBUTE, e.g., "name"] attribute of kind Text with `unique: true`
- **FR-011**: [LIST ADDITIONAL REQUIRED ATTRIBUTES, their kinds, and whether they are optional]
- **FR-012**: All Dropdown attributes MUST include a `choices` list with at least `name` for each choice
- **FR-013**: Attributes that are mandatory MUST either have `optional: false` (the default) or provide a `default_value`
- **FR-014**: All attribute names MUST be snake_case (pattern: `^[a-z0-9\_]+$`, 3-32 chars)
- **FR-015**: Attribute types MUST use valid kinds: Text, Number, Boolean, Dropdown, IPHost, IPNetwork, DateTime, URL, Email, JSON, etc. (MUST NOT use deprecated `String` kind)

*Example of marking unclear requirements:*

- **FR-016**: [ATTRIBUTE NAME] MUST be of kind [NEEDS CLARIFICATION: should this be Text, Dropdown, or Number?]
- **FR-017**: [ATTRIBUTE NAME] MUST have validation [NEEDS CLARIFICATION: what regex pattern or min/max constraints apply?]

#### Relationships

- **FR-020**: [NODE A] MUST have a relationship to [NODE B] with cardinality [one/many] and kind [Generic/Attribute/Component/Parent]
- **FR-021**: All Component/Parent relationship pairs MUST use matching `identifier` values on both sides
- **FR-022**: All relationship `peer` values MUST use the full kind (Namespace + Name, e.g., `DcimDeviceType`)
- **FR-023**: All relationship names MUST be snake_case (pattern: `^[a-z0-9\_]+$`, 3-32 chars)
- **FR-024**: [LIST ANY CROSS-FILE RELATIONSHIPS THAT REQUIRE extensions BLOCK]

#### Hierarchy (if applicable)

- **FR-030**: [GENERIC NAME] MUST set `hierarchical: true` to enable location-style tree behavior
- **FR-031**: [ROOT NODE] MUST set `parent: null` to mark it as the hierarchy root
- **FR-032**: Each hierarchy level MUST define both `parent` and `children` to form the chain: [ROOT] > [LEVEL 1] > [LEVEL 2] > [LEAF]
- **FR-033**: Leaf nodes MUST NOT define `children`

#### Display & Identification

- **FR-040**: Each user-facing node MUST define `human_friendly_id` using attribute paths (e.g., `["name__value"]` or `["parent__name__value", "name__value"]`)
- **FR-041**: Each node MUST define `display_label` as either an attribute name or a Jinja2 template
- **FR-042**: Attributes MUST use `order_weight` following the convention: 900-999 for primary relationships, 1000-1099 for identifiers, 1100-1499 for secondary attributes, 1500-1999 for tertiary, 2000+ for computed/metadata, 3000+ for tags

#### Uniqueness Constraints

- **FR-050**: [NODE NAME] MUST define uniqueness_constraints as [CONSTRAINT DEFINITION, e.g., `[["name__value"]]` or `[["rack", "name__value"]]`]
- **FR-051**: Uniqueness constraints MUST use `__value` suffix for attribute references and bare names for relationship references

#### Migration (if modifying existing schema)

- **FR-060**: Removed attributes MUST use `state: absent` rather than being deleted from the YAML
- **FR-061**: New mandatory attributes on existing nodes MUST either include a `default_value` or be added as `optional: true` first

### Key Entities

<!--
  List the nodes and generics this schema introduces, with their purpose
  and key relationships. Do NOT include implementation details like YAML -
  just describe the data model conceptually.
-->

- **[Generic/Node Name]**: [What it represents, key attributes, relationships to other entities]
- **[Generic/Node Name]**: [What it represents, key attributes, relationships to other entities]

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria for this schema design.
-->

### Measurable Outcomes

- **SC-001**: `infrahubctl schema check schemas/` passes with zero validation errors
- **SC-002**: All defined nodes appear in the Infrahub UI with correct labels and icons after loading
- **SC-003**: `human_friendly_id` renders a readable identifier for each node instance (e.g., "Region-A / Site-01")
- **SC-004**: Uniqueness constraints prevent duplicate entries as specified (e.g., no two devices in the same rack position)
- **SC-005**: Component/Parent relationships correctly enforce ownership (deleting a parent cascades or blocks as configured)
- **SC-006**: [ADDITIONAL SCHEMA-SPECIFIC SUCCESS CRITERIA]
