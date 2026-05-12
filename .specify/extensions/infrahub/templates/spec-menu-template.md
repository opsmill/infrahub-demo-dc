# Menu Specification: [MENU NAME]

> **Workflow**: This is an Infrahub menu customization spec. Use the `infrahub:menu-creator` skill to implement the menu defined below.

**Feature Branch**: `[###-menu-name]`
**Created**: [DATE]
**Status**: Draft
**Input**: User description: "$ARGUMENTS"

## Key Files

- `menus/*.yml` -- Menu definition files (YAML format: apiVersion: infrahub.app/v1, kind: Menu)
- `.infrahub.yml` -- Project config where menu files are registered under the `menus:` key
- `schemas/*.yml` -- Schema files where `include_in_menu: false` must be set for nodes included in the custom menu

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.

  EXAMPLE USER STORIES FOR MENUS:
  - "Organize device types into a Device Management navigation group"
  - "Create a Location Management section with regions, sites, and rooms"
  - "Add an IPAM navigation group for prefixes, IP addresses, and VLANs"
  - "Build a flat menu for a simple project with only 3-4 node types"
  - "Restructure the sidebar to match our team's operational workflow"
-->

### User Story 1 - [Brief Title] (Priority: P1)

[DESCRIBE THE PRIMARY MENU STRUCTURE GOAL -- e.g., "As a network engineer, I want device types organized under a 'Device Management' group so I can quickly find servers, switches, and routers in the sidebar."]

**Why this priority**: [EXPLAIN WHY THIS NAVIGATION GROUP IS THE MOST IMPORTANT]

**Independent Test**: Load the menu with `invoke load-menu` and verify the navigation group appears in the Infrahub web UI sidebar with the correct hierarchy and icons.

**Acceptance Scenarios**:

1. **Given** the menu file is loaded, **When** a user opens the Infrahub web UI, **Then** the [GROUP NAME] section appears in the left sidebar with the correct label and icon
2. **Given** the [GROUP NAME] section is visible, **When** a user clicks a leaf menu item, **Then** they are navigated to the correct node list view
3. **Given** schema nodes have `include_in_menu: false`, **When** the menu is loaded, **Then** no duplicate entries appear in the sidebar

---

### User Story 2 - [Brief Title] (Priority: P2)

[DESCRIBE A SECONDARY MENU GROUP OR NESTED HIERARCHY -- e.g., "As an infrastructure manager, I want a 'Locations' group with sub-groups for geographic hierarchy (Regions > Sites > Rooms) so the navigation mirrors our physical topology."]

**Why this priority**: [EXPLAIN THE VALUE OF THIS NAVIGATION GROUP]

**Independent Test**: Load the menu and verify nested children expand correctly in the sidebar, showing the expected parent-child hierarchy.

**Acceptance Scenarios**:

1. **Given** the menu file defines a nested hierarchy, **When** a user expands a group header, **Then** child items are displayed beneath it
2. **Given** a group header has no `kind` property, **When** a user clicks it, **Then** it expands/collapses children rather than navigating

---

### User Story 3 - [Brief Title] (Priority: P3)

[DESCRIBE AN ADDITIONAL MENU GROUP OR REFINEMENT -- e.g., "As a network planner, I want an 'IPAM' navigation group with entries for prefixes, IP addresses, and VLANs using appropriate network icons."]

**Why this priority**: [EXPLAIN THE VALUE]

**Independent Test**: [DESCRIBE HOW TO VERIFY THIS MENU GROUP INDEPENDENTLY]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[ADD MORE USER STORIES AS NEEDED FOR ADDITIONAL MENU GROUPS]

### Edge Cases

<!--
  ACTION REQUIRED: Fill these out with the menu-specific edge cases that apply to your design.
-->

- What happens when a `kind` value in a menu item does not match any loaded schema node?
- What happens when two menu items share the same `name` and `namespace` combination?
- What happens if `children` is used without the required `data` wrapper key?
- What happens if both `kind` and `path` are set on the same menu item?
- What happens if schema nodes still have `include_in_menu: true` while a custom menu is loaded (duplicate sidebar entries)?
- What happens if the menu file is missing `apiVersion: infrahub.app/v1` or `kind: Menu`?
- What happens if an MDI icon name is misspelled (e.g., `mdi:servr` instead of `mdi:server`)?
- How does the menu behave when a deeply nested hierarchy (3+ levels) is defined?

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: Fill out functional requirements specific to your menu design.
  Use the Infrahub menu terminology below as a guide.
-->

### Functional Requirements

- **FR-001**: Menu file MUST use `apiVersion: infrahub.app/v1` and `kind: Menu` with items under `spec.data`
- **FR-002**: Each menu item MUST have a unique `name` and `namespace` combination
- **FR-003**: Menu items linking to schema nodes MUST use the `kind` property (e.g., `kind: DcimDevice`) rather than hardcoded `path` values
- **FR-004**: Group headers (non-clickable parent sections) MUST omit both `kind` and `path`, and MUST contain `children.data` with at least one child item
- **FR-005**: All `children` blocks MUST use the `data` wrapper key (e.g., `children: data: [...]`)
- **FR-006**: Icons MUST use MDI format with `mdi:` prefix (e.g., `mdi:server`, `mdi:map-marker`, `mdi:ip-network`)
- **FR-007**: Schema nodes represented in the custom menu MUST have `include_in_menu: false` set in their schema definition to prevent duplicate sidebar entries
- **FR-008**: Menu file MUST be registered in `.infrahub.yml` under the `menus:` key
- **FR-009**: [MENU-SPECIFIC REQUIREMENT -- e.g., "Menu MUST organize all DCIM node types under a single 'Device Management' group"]
- **FR-010**: [MENU-SPECIFIC REQUIREMENT -- e.g., "Menu MUST provide a flat entry for LocationGeneric to show all location types in one view"]

*Mark unclear requirements:*

- **FR-011**: [NEEDS CLARIFICATION: Which schema node types should be included in the menu vs. left out?]
- **FR-012**: [NEEDS CLARIFICATION: Should the menu use a flat structure or nested hierarchy?]

### Menu Hierarchy Plan

<!--
  ACTION REQUIRED: Define the planned navigation tree structure.
  Items without `kind` are group headers (non-clickable).
  Leaf items with `kind` link to schema node list views.
-->

```
[TOP-LEVEL GROUP LABEL] (icon: mdi:[ICON])
  +-- [CHILD ITEM LABEL] -> [NamespaceKind] (icon: mdi:[ICON])
  +-- [CHILD ITEM LABEL] -> [NamespaceKind] (icon: mdi:[ICON])
  +-- [SUB-GROUP LABEL] (icon: mdi:[ICON])
      +-- [LEAF ITEM LABEL] -> [NamespaceKind] (icon: mdi:[ICON])
      +-- [LEAF ITEM LABEL] -> [NamespaceKind] (icon: mdi:[ICON])

[ANOTHER TOP-LEVEL GROUP LABEL] (icon: mdi:[ICON])
  +-- [CHILD ITEM LABEL] -> [NamespaceKind] (icon: mdi:[ICON])
```

### Common MDI Icons Reference

| Icon | Use For |
|------|---------|
| `mdi:server` | Servers, generic devices |
| `mdi:switch` | Network switches |
| `mdi:router-network` | Routers |
| `mdi:factory` | Manufacturers |
| `mdi:earth` | Regions |
| `mdi:office-building` | Sites |
| `mdi:door` | Rooms |
| `mdi:map-marker` | Locations (generic) |
| `mdi:ip-network` | IP/Network, IPAM |
| `mdi:cog` | Settings, types, config |
| `mdi:package-variant` | Device types |
| `mdi:ethernet` | Interfaces |
| `mdi:expansion-card` | Modules, cards |
| `mdi:shield-lock` | Security |

Full icon library: https://pictogrammers.com/library/mdi/

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria for the menu.
-->

### Measurable Outcomes

- **SC-001**: Menu loads without errors via `invoke load-menu` and appears in the Infrahub web UI sidebar
- **SC-002**: All leaf menu items navigate to the correct schema node list views when clicked
- **SC-003**: Group headers expand and collapse correctly, and are not clickable as navigation links
- **SC-004**: No duplicate entries appear in the sidebar (all relevant schema nodes have `include_in_menu: false`)
- **SC-005**: All icons render correctly in the UI using valid MDI icon names
- **SC-006**: [MENU-SPECIFIC OUTCOME -- e.g., "Navigation structure matches the planned hierarchy with no more than 3 levels of nesting"]
- **SC-007**: [MENU-SPECIFIC OUTCOME -- e.g., "Users can reach any node type within 2 clicks from the sidebar"]
