# Object Population Specification: [POPULATION NAME]

> **Workflow**: This spec targets Infrahub object data population. The implementing agent MUST use the `infrahub:object-creator` skill to generate all object files.

**Feature Branch**: `[###-population-name]`
**Created**: [DATE]
**Status**: Draft
**Input**: User description: "$ARGUMENTS"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.

  OBJECT POPULATION EXAMPLES (replace with your actual stories):
  - "Populate location hierarchy from region down to rack"
  - "Create device inventory with types, platforms, and rack positions"
  - "Define IP address allocations and prefix hierarchy"
  - "Set up tenant organizations and group memberships"
  - "Create module bay templates and module installations"
-->

### User Story 1 - [Brief Title] (Priority: P1)

[Describe the data population goal in plain language, e.g., "As an infrastructure engineer, I need to populate the full location hierarchy so that devices can be assigned to racks."]

**Why this priority**: [Explain why this data must exist first -- typically because other objects depend on it via relationships]

**Independent Test**: [Describe how to verify this population independently, e.g., "Query the Infrahub API for all LocationRegion objects and verify the full tree from region to rack is present"]

**Acceptance Scenarios**:

1. **Given** an empty Infrahub instance with the schema loaded, **When** the object files are loaded, **Then** [EXPECTED OBJECT COUNT] [NODE KIND] objects exist with correct attributes
2. **Given** the objects are loaded, **When** querying by human_friendly_id, **Then** each object is resolvable by its expected identifier

---

### User Story 2 - [Brief Title] (Priority: P2)

[Describe the next data population goal, e.g., "As an infrastructure engineer, I need device types and platforms defined so that device instances can reference them."]

**Why this priority**: [Explain the dependency chain -- this data builds on P1 or is required by P3]

**Independent Test**: [Describe how to verify this population independently]

**Acceptance Scenarios**:

1. **Given** prerequisite objects from P1 are loaded, **When** these object files are loaded, **Then** all relationship references resolve correctly
2. **Given** the objects are loaded, **When** inspecting relationship fields, **Then** each reference points to the correct target object

---

### User Story 3 - [Brief Title] (Priority: P3)

[Describe the next data population goal, e.g., "As an infrastructure engineer, I need device instances placed in racks with interfaces and group memberships so the inventory is complete."]

**Why this priority**: [Explain the value and dependency on earlier stories]

**Independent Test**: [Describe how to verify this population independently]

**Acceptance Scenarios**:

1. **Given** prerequisite objects from P1 and P2 are loaded, **When** these object files are loaded, **Then** devices are created with correct rack positions, inline interfaces, and group memberships

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: Review and customize these edge cases for your specific population.
  Remove any that do not apply and add domain-specific ones.
-->

- What happens when a referenced object (e.g., a manufacturer or device type) does not yet exist at load time? Files must be ordered so dependencies are loaded first.
- What happens when two objects in different files share the same human_friendly_id? This will cause a conflict -- ensure uniqueness.
- What happens when a device references a rack using the wrong number of elements in the human_friendly_id list (e.g., scalar instead of [room_shortname, rack_name])?
- What happens when a Dropdown attribute uses the display label instead of the choice name (e.g., "Front to Rear" instead of "front-to-rear")?
- What happens when interface range expansion syntax [N-M] is used but expand_range is not set to true in parameters?
- What happens when a component child is nested inline but the kind field is omitted and the relationship peer is a Generic?
- What happens when the same device has both occupied and empty module installations that reference overlapping bay templates?
- How does the system handle circular dependencies between object files?
- What happens when an IP prefix is specified without CIDR notation?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Customize the functional requirements for your specific data population.
-->

### Functional Requirements

**File Format & Structure**

- **FR-001**: Every object file MUST use `apiVersion: infrahub.app/v1` and `kind: Object`
- **FR-002**: Every `spec` block MUST contain a `kind` field matching a valid schema node kind and a `data` list of object instances
- **FR-003**: Multiple YAML documents in a single file MUST be separated by `---`

**Relationship References**

- **FR-004**: All cardinality-one relationships MUST reference the target object by its human_friendly_id -- scalar for single-element IDs, list for multi-element IDs
- **FR-005**: Group memberships MUST use `member_of_groups` as a simple list of group names
- **FR-006**: Component children MUST be nested inline using the `data` key, with `kind` specified when the relationship peer is a Generic or when multiple child types are possible

**File Organization & Load Order**

- **FR-007**: Object files MUST be placed in the `objects/` directory (or subdirectories thereof)
- **FR-008**: Files MUST be named with numeric prefixes (e.g., `01_`, `02_`) to enforce correct dependency load order
- **FR-009**: Objects that are referenced by other objects MUST be defined in files that sort earlier than the referencing files
- **FR-010**: [ADDITIONAL REQUIREMENT SPECIFIC TO YOUR POPULATION]

**Data Integrity**

- **FR-011**: Dropdown attribute values MUST use the choice `name`, not the display label
- **FR-012**: DateTime attributes MUST use ISO 8601 format (e.g., "2024-01-15T10:30:00Z")
- **FR-013**: IPHost attributes MUST include the prefix length (e.g., "192.168.1.1/24") and IPNetwork attributes MUST use CIDR notation
- **FR-014**: Interface range expansion MUST set `parameters.expand_range: true` on the relationship block when using [N-M] syntax in interface names

**Population-Specific Requirements**

- **FR-015**: [REQUIREMENT ABOUT SPECIFIC NODE KINDS TO POPULATE, e.g., "System MUST create OrganizationManufacturer objects for all referenced manufacturers"]
- **FR-016**: [REQUIREMENT ABOUT EXPECTED DATA VOLUME, e.g., "System MUST define at least N location objects covering the full hierarchy"]
- **FR-017**: [REQUIREMENT ABOUT SPECIFIC RELATIONSHIPS, e.g., "Every device MUST reference a valid device_type and rack"]
- **FR-018**: [REQUIREMENT ABOUT GROUPS, e.g., "System MUST define CoreStandardGroup objects and assign devices via member_of_groups"]

### Key Entities

<!--
  ACTION REQUIRED: List the Infrahub node kinds involved in this population.
  Include their key attributes and relationship dependencies.
-->

- **[NODE KIND 1, e.g., OrganizationManufacturer]**: [Description, key attributes, dependencies -- e.g., "No dependencies, loaded first"]
- **[NODE KIND 2, e.g., DcimDeviceType]**: [Description, key attributes, dependencies -- e.g., "Depends on manufacturers via manufacturer field"]
- **[NODE KIND 3, e.g., LocationRegion]**: [Description, key attributes, dependencies -- e.g., "Self-contained hierarchy, children nested inline"]
- **[NODE KIND 4, e.g., DcimDevice or subtype]**: [Description, key attributes, dependencies -- e.g., "Depends on device types, locations, and groups"]

### Key Files

- `objects/*.yml` -- All object data files, loaded in filename sort order
- `.infrahub.yml` -- Project configuration that specifies the objects directory path
- `schemas/*.yml` -- Schema definitions that these objects conform to (reference for valid node kinds, attributes, and relationships)

### Dependency Load Order

<!--
  ACTION REQUIRED: Define the specific load order for your population.
  Example ordering below -- customize to match your schema.
-->

```
[LOAD ORDER PLACEHOLDER -- customize to your schema, for example:]

01_manufacturers.yml        -- OrganizationManufacturer (no dependencies)
02_organizations.yml        -- OrganizationTenantGroup + Tenants (no dependencies)
03_groups.yml               -- CoreStandardGroup (no dependencies)
04_device_types.yml         -- DcimDeviceType (depends on manufacturers)
04a_module_types.yml        -- DcimModuleType (depends on manufacturers)
04b_module_bay_templates.yml -- DcimModuleBayTemplate (depends on device types)
05_platforms.yml            -- DcimPlatform (depends on manufacturers)
06_locations.yml            -- Location hierarchy (self-contained)
07_devices.yml              -- Device instances (depends on types, locations, groups)
08_module_installations.yml -- DcimModuleInstallation (depends on devices, bays, module types)
09_ip_prefixes.yml          -- IpamPrefix (typically independent or depends on locations)
```

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria for the data population.
  These should be verifiable by querying Infrahub after loading.
-->

### Measurable Outcomes

- **SC-001**: All object files load without errors when processed by Infrahub (no missing references, no schema validation failures)
- **SC-002**: [OBJECT COUNT METRIC, e.g., "N manufacturer objects, M device type objects, and P device instances are created"]
- **SC-003**: All relationship references resolve correctly -- no dangling human_friendly_id references
- **SC-004**: The file dependency order is correct -- files can be loaded sequentially by filename sort order without reference errors
- **SC-005**: [HIERARCHY METRIC, e.g., "Location tree spans from region to rack with N total location objects across all levels"]
- **SC-006**: [COMPLETENESS METRIC, e.g., "Every device has a device_type, a rack assignment, and at least one interface defined"]
- **SC-007**: [GROUP METRIC, e.g., "All devices are assigned to at least one CoreStandardGroup via member_of_groups"]
