<!--
Sync Impact Report
===================
Version change: 1.0.0 → 1.1.0
Modified principles:
  - VI. Sibling-Repo Alignment (retitled and reworded to state the uniformity
    obligation directly)
Added sections: none
Removed sections: none
Templates requiring updates:
  - .specify/templates/plan-template.md ✅ (generic Constitution Check gate; fills per feature)
  - .specify/templates/spec-template.md ✅ (no constitution references)
  - .specify/templates/tasks-template.md ✅ (no constitution references)
Follow-up TODOs: none
-->

# infrahub-demo-dc Constitution

## Core Principles

### I. Design-Driven Composition

Every capability in this repository is expressed through the five Infrahub component
types — schemas, generators, transforms, checks, and Jinja2 templates — and every
component MUST be registered in `.infrahub.yml`. A transform or generator that imports
a helper module or selects a template from device data MUST declare those paths under
`watch.files` (`check_definitions` excepted: its model forbids unknown keys). An
unregistered or unwatched component is a defect, not a style choice: it produces stale
artifacts silently.

### II. Typed, Documented Python

All function signatures MUST carry complete type hints. All modules, classes, and
functions MUST carry Google-style docstrings. Code MUST pass `ruff` (lint and format)
and `mypy` before it is committed. Use `pathlib` over `os.path`; keep lines at 100
characters or fewer. Rationale: demo repositories are read far more than they are
written — by prospects, customers, and AI agents — so the code is the documentation.

### III. Tiered Testing Discipline

Directories define test tiers: `tests/unit/` and `tests/smoke/` MUST run without
Docker in seconds; `tests/integration/` boots its own throwaway Infrahub stack via
`infrahub-testcontainers`, exactly once per pytest session. The markers `core`,
`extended`, and `offline` select scope within the integration tier only, enforced with
`--strict-markers`. New features MUST add tests in the matching tier; both success and
failure paths MUST be covered.

### IV. Local-CI Parity

Every CI gate MUST be exactly one `uv run invoke` task, so a local pass of the named
task guarantees the CI gate passes. Raw test or lint invocations in workflow files are
forbidden. After any code change and before every commit or PR, `uv run invoke lint`
MUST pass. Rationale: parity is what makes CI failures reproducible and keeps the four
demo repositories aligned on behaviour rather than on YAML text.

### V. Reproducible Versions

Dependencies are managed by `uv` with a committed `uv.lock`. Specifiers are floors with
no upper bound, and a floor MUST name the last stable release — a temporary beta floor
MUST carry a comment naming the stable release it waits on. The only mechanism that
moves the version under test is a bump pull request opened by the update workflow; the
bump PR's own CI run is the qualification gate. Manual pin edits outside a bump PR are
a violation of this principle.

### VI. Sibling-Repo Alignment

This repository's CI/CD and test shape stays uniform with `infrahub-solution-ai-dc`,
`infrahub-demo-sp`, and `infrahub-demo-service-catalog`: the same gate names, the same
bump workflow behavior, the same tier semantics and the same locally runnable tasks.
Shape changes MUST be proven here first, then propagated to the siblings. The supported
Python range is `>=3.11,<3.15`, verified at both ends by the lint matrix. Divergence
between the repositories without a recorded decision is drift, not local preference.

## Operational Constraints

- Package management is `uv`-only; Docker is required for the integration tier.
- Never commit `.env` files or credentials. API tokens in documentation are demo
  tokens for local development only.
- Jinja2 device-config templates MUST set `autoescape=False`; interface-role lookups
  MUST use the HTML-decoding helper.
- Bump PRs MUST be opened with the `GH_UPDATE_PACKAGE_OTTO` secret — a
  `GITHUB_TOKEN`-opened PR triggers no workflows and silently voids the QA gate.
- Expensive full-tier integration runs are bounded: they run on Infrahub bump PRs and
  explicit manual dispatch only — never on ordinary pushes or schedules.

## Development Workflow & Quality Gates

- Lint gates: `rumdl`, `yamllint`, `ruff`, `mypy` — all reachable via
  `uv run invoke lint` and individually as `lint-*` tasks; these are the same tasks CI
  runs.
- Test gates: `test-unit` on every PR; `test-integration --tier=core` on every PR;
  the full tier per the bump policy above.
- Feature work follows the spec-kit flow (`.specify/`): specify → clarify → plan →
  tasks → implement, with the Constitution Check gate in every plan.
- Commits happen only on explicit request — no auto-commit. Commit messages explain
  "why", not "what".

## Governance

This constitution supersedes ad-hoc practice in this repository. Amendments are made
by pull request, require the repository owner's approval (per `CODEOWNERS`), and MUST
update the version line below according to semantic versioning: MAJOR for removed or
redefined principles, MINOR for new or materially expanded principles or sections,
PATCH for clarifications. Every PR review MUST verify compliance with the principles;
complexity that violates a principle MUST be justified in the plan's Complexity
Tracking table or rejected. Runtime development guidance lives in `AGENTS.md`
(`CLAUDE.md` routes to it); this file governs, that file instructs.

**Version**: 1.1.0 | **Ratified**: 2026-08-18 | **Last Amended**: 2026-08-19
