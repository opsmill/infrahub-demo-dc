## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before specification)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_specify` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Pre-Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Pre-Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}

    Wait for the result of the hook command before proceeding to the Outline.
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Infrahub Routing

**This preset overrides the default `/speckit.specify` command with Infrahub-aware artifact routing.**

### Step 1: Detect Infrahub project

Check if `.infrahub.yml` exists in the repository root.

- **If `.infrahub.yml` does NOT exist**: Skip all Infrahub routing. Use the default `spec-template` (resolved via `resolve_template`). Proceed directly to the **Outline** section below.
- **If `.infrahub.yml` exists**: Continue to Step 2.

### Step 2: Verify Infrahub connectivity

Run `infrahubctl info` to verify that the Infrahub instance is reachable.

- **If the command fails or Infrahub is not reachable**: Stop and warn the user:
  ```
  Infrahub is not reachable. Please start your Infrahub instance first:
    invoke start
  Then re-run /speckit.specify
  ```
  Do NOT proceed further.
- **If the command succeeds**: Continue to Step 3.

### Step 3: Analyze prompt for artifact types

Read the user's input (the text after `/speckit.specify`) and classify which Infrahub artifact types are involved. Use this detection table:

| Artifact Type | Keywords / Signals | Extension Template |
|---------------|-------------------|-------------------|
| **Schema** | schema, data model, nodes, attributes, relationships, hierarchy, generics, namespace, kind, store information, model | `spec-schema-template` |
| **Transform** | transform, render, config, artifact, output, template, jinja, generate config, device config, configuration | `spec-transform-template` |
| **Check** | check, validate, validation, enforce, rule, constraint, verify | `spec-check-template` |
| **Generator** | generator, auto-create, design-driven, topology, provision, generate objects | `spec-generator-template` |
| **Menu** | menu, navigation, sidebar, UI, organize | `spec-menu-template` |

**Detection rules**:
- Match keywords case-insensitively against the user's input
- A prompt can match **multiple** artifact types
- If NO artifact type is detected, default to `spec-schema-template` (schema-first principle)

### Step 4: Handle single vs multiple artifact types

**If exactly ONE artifact type is detected**:
- Load the corresponding extension template from `.specify/extensions/infrahub/templates/`
- Proceed to the **Outline** section using that template

**If MULTIPLE artifact types are detected**:
- Present the detected types and their dependency order to the user:

  ```markdown
  ## Infrahub Artifact Routing

  Your feature involves multiple Infrahub artifact types:

  1. **Schema** - Define the data model (must be done first)
  2. **Transform** - Render device configurations (depends on schema)

  The spec-driven workflow handles one artifact type per cycle:
  `/speckit.specify` → `/speckit.plan` → `/speckit.tasks` → `/speckit.implement`

  **Starting with: Schema**
  After completing the schema cycle, run `/speckit.specify` again for the next artifact.
  ```

- Use this **dependency order** (earlier items must be completed first):
  1. Schema (always first -- everything depends on the data model)
  2. Check, Generator, Transform, Menu (all depend on schema; independent of each other)

- Load the template for the **first** artifact type in the dependency chain
- Proceed to the **Outline** section using that template

### Step 5: Load the extension template

Read the selected template from `.specify/extensions/infrahub/templates/{template-name}.md`.

- If the template file does not exist, fall back to `.specify/templates/spec-template.md` and warn the user.

## Outline

The text the user typed after `/speckit.specify` in the triggering message **is** the feature description. Assume you always have it available in this conversation even if `$ARGUMENTS` appears literally below. Do not ask the user to repeat it unless they provided an empty command.

Given that feature description, do this:

1. **Generate a concise short name** (2-4 words) for the branch:
   - Analyze the feature description and extract the most meaningful keywords
   - Create a 2-4 word short name that captures the essence of the feature
   - Use action-noun format when possible (e.g., "add-user-auth", "fix-payment-bug")
   - Preserve technical terms and acronyms (OAuth2, API, JWT, etc.)
   - Keep it concise but descriptive enough to understand the feature at a glance

2. **Create the feature branch** by running the script with `--short-name` (and `--json`). In sequential mode, do NOT pass `--number` — the script auto-detects the next available number. In timestamp mode, the script generates a `YYYYMMDD-HHMMSS` prefix automatically:

   **Branch numbering mode**: Before running the script, check if `.specify/init-options.json` exists and read the `branch_numbering` value.
   - If `"timestamp"`, add `--timestamp` (Bash) or `-Timestamp` (PowerShell) to the script invocation
   - If `"sequential"` or absent, do not add any extra flag (default behavior)

   - Bash example: `.specify/scripts/bash/create-new-feature.sh "$ARGUMENTS" --json --short-name "user-auth" "Add user authentication"`

   **IMPORTANT**:
   - Do NOT pass `--number` — the script determines the correct next number automatically
   - Always include the JSON flag (`--json` for Bash, `-Json` for PowerShell) so the output can be parsed reliably
   - You must only ever run this script once per feature
   - The JSON is provided in the terminal as output - always refer to it to get the actual content you're looking for
   - The JSON output will contain BRANCH_NAME and SPEC_FILE paths

3. **Load the template** determined by the Infrahub Routing section above (or the default `spec-template` if not an Infrahub project).

4. Follow this execution flow:

    1. Parse user description from Input
       If empty: ERROR "No feature description provided"
    2. Extract key concepts from description
       Identify: actors, actions, data, constraints
    3. For unclear aspects:
       - Make informed guesses based on context and industry standards
       - Only mark with [NEEDS CLARIFICATION: specific question] if:
         - The choice significantly impacts feature scope or user experience
         - Multiple reasonable interpretations exist with different implications
         - No reasonable default exists
       - **LIMIT: Maximum 3 [NEEDS CLARIFICATION] markers total**
       - Prioritize clarifications by impact: scope > security/privacy > user experience > technical details
    4. Fill User Scenarios & Testing section
       If no clear user flow: ERROR "Cannot determine user scenarios"
    5. Generate Functional Requirements
       Each requirement must be testable
       Use reasonable defaults for unspecified details (document assumptions in Assumptions section)
    6. Define Success Criteria
       Create measurable, technology-agnostic outcomes
       Include both quantitative metrics and qualitative measures
       Each criterion must be verifiable without implementation details
    7. Identify Key Entities (if data involved)
    8. Return: SUCCESS (spec ready for planning)

5. Write the specification to SPEC_FILE using the template structure, replacing placeholders with concrete details derived from the feature description while preserving section order and headings.

6. **Specification Quality Validation**: After writing the initial spec, validate it against quality criteria:

   a. **Create Spec Quality Checklist**: Generate a checklist file at `FEATURE_DIR/checklists/requirements.md` with validation items covering:
      - Content Quality (no implementation details, focused on user value, written for stakeholders, all sections completed)
      - Requirement Completeness (no NEEDS CLARIFICATION markers, testable requirements, measurable success criteria, edge cases identified)
      - Feature Readiness (acceptance criteria defined, user scenarios cover primary flows)

   b. **Run Validation Check**: Review the spec against each checklist item

   c. **Handle Validation Results**:
      - **If all items pass**: Mark checklist complete and proceed to step 7
      - **If items fail**: Fix and re-validate (max 3 iterations)
      - **If [NEEDS CLARIFICATION] markers remain** (max 3): Present options to user in table format, wait for responses, update spec

   d. **Update Checklist**: After each validation iteration, update the checklist file

7. Report completion with branch name, spec file path, checklist results, and readiness for the next phase (`/speckit.clarify` or `/speckit.plan`).

   **If multiple artifact types were detected in Infrahub Routing**, also remind the user:
   ```
   This feature involves additional artifact types after this one.
   Once you complete the full cycle for this spec, run `/speckit.specify` again for the next artifact.
   ```

8. **Check for extension hooks**: After reporting completion, check if `.specify/extensions.yml` exists in the project root.
   - If it exists, read it and look for entries under the `hooks.after_specify` key
   - Process hooks using the same logic as the pre-execution hooks above.
   - If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Quick Guidelines

- Focus on **WHAT** users need and **WHY**.
- Avoid HOW to implement (no tech stack, APIs, code structure).
- Written for business stakeholders, not developers.
- DO NOT create any checklists that are embedded in the spec. That will be a separate command.

### Section Requirements

- **Mandatory sections**: Must be completed for every feature
- **Optional sections**: Include only when relevant to the feature
- When a section doesn't apply, remove it entirely (don't leave as "N/A")

### For AI Generation

When creating this spec from a user prompt:

1. **Make informed guesses**: Use context, industry standards, and common patterns to fill gaps
2. **Document assumptions**: Record reasonable defaults in the Assumptions section
3. **Limit clarifications**: Maximum 3 [NEEDS CLARIFICATION] markers
4. **Prioritize clarifications**: scope > security/privacy > user experience > technical details
5. **Think like a tester**: Every vague requirement should fail the "testable and unambiguous" checklist item

### Success Criteria Guidelines

Success criteria must be:

1. **Measurable**: Include specific metrics (time, percentage, count, rate)
2. **Technology-agnostic**: No mention of frameworks, languages, databases, or tools
3. **User-focused**: Describe outcomes from user/business perspective
4. **Verifiable**: Can be tested/validated without knowing implementation details
