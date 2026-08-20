# AGENTS.md

> Universal guidance for AI coding assistants working in this repository.
> See also: [CLAUDE.md](./CLAUDE.md) for Claude-specific detailed instructions.

## Project Overview

**demo-dc** is a comprehensive demonstration of design-driven network automation using [Infrahub](https://docs.infrahub.app). It showcases:

- Composable data center and POP topology generation
- Configuration management with Jinja2 templates
- Validation checks for network devices
- Infrastructure-as-code patterns

## Quick Start

```bash
# Install dependencies
uv sync

# Start Infrahub containers
uv run invoke start

# Bootstrap schemas, menu, and data
uv run invoke bootstrap

# Run full initialization (destroy + start + bootstrap + demo)
uv run invoke init
```

## Build and Test Commands

```bash
# Everything, including the integration suite (starts a real Infrahub deployment via Docker)
uv run invoke test

# No Docker needed - seconds, not minutes
uv run invoke test-unit

# The tier every pull request runs
uv run invoke test-integration

# Lint and type check
uv run invoke lint         # Full suite: rumdl, yamllint, ruff, mypy
uv run ruff check . --fix  # Format and lint
uv run mypy .              # Type checking only
```

The integration suite starts its own throwaway Infrahub deployment with `infrahub-testcontainers`;
it does not use `invoke start`. It is split into two tiers by marker: `core` runs on every pull
request, and `extended` adds the second vendor data center, the POP and segment services, day-two
operations and the conflict workflow. `ci.yml` selects it for a pull request whose branch bumps the
Infrahub version (`update-infrahub-*`, but not `update-infrahub-sdk-*`) and for a manual run of the
CI workflow with `tier=full`. That tier does not block a merge, because an upstream Infrahub fault
stops it passing reliably — see
[tests/AGENTS.md](./tests/AGENTS.md), which records the signature and what to check before blaming
the demo.

## Code Style Guidelines

### Python

- **Type hints required** on all function signatures
- **Docstrings required** for all modules, classes, and functions (Google-style)
- Format with `ruff`, pass `mypy` type checking
- PascalCase for classes, snake_case for functions/variables
- Max line length: 100 characters
- Use `pathlib` over `os.path`

### Naming Conventions

- **Schema Nodes**: PascalCase (`LocationBuilding`, `DcimDevice`)
- **Attributes/Relationships**: snake_case (`device_type`, `parent_location`)
- **Namespaces**: PascalCase (`Dcim`, `Ipam`, `Service`, `Design`)

## Architecture Overview

This project follows Infrahub's SDK pattern with five core component types:

```text
schemas/      → Data models, relationships, constraints
generators/   → Create infrastructure topology programmatically
transforms/   → Convert Infrahub data to device configurations
checks/       → Validate configurations and connectivity
templates/    → Jinja2 templates for device configurations
```

### Data Flow

```text
Schema Definition → Data Loading → Generator Execution → Transform Processing → Configuration Generation
                                         ↓
                                   Validation Checks
```

### Key Files

- `.infrahub.yml` - Central registry for all components (transforms, generators, checks, queries)
- `tasks.py` - Invoke task definitions for automation
- `pyproject.toml` - Project dependencies and tool configuration

## Testing Instructions

1. **Before committing**: Run `uv run pytest` to ensure all tests pass
2. **For new features**: Add tests in `tests/unit/` or `tests/integration/`
3. **Use mocks**: Mock external dependencies with `unittest.mock`
4. **Test both paths**: Cover success and failure scenarios
5. **Integration tests**: Require running Infrahub instance

See [tests/AGENTS.md](./tests/AGENTS.md) for detailed testing conventions.

## Post-Change Validation

**IMPORTANT**: After making code changes, always run the full lint suite:

```bash
uv run invoke lint  # Runs: rumdl, yamllint, ruff, mypy
```

Each tool is also callable on its own — these are the same tasks CI runs, so a local pass means CI
passes too:

```bash
uv run invoke lint-markdown  # rumdl check .
uv run invoke lint-yaml      # yamllint -s .
uv run invoke lint-ruff      # ruff check . && ruff format --check --diff
uv run invoke lint-mypy      # mypy --show-error-codes .
```

This ensures:

- Markdown files have proper formatting (blank lines around code blocks, language specifiers)
- YAML files are valid
- Python code passes ruff linting
- Type hints are correct (mypy)

The same parity holds for the test tasks CI runs — `test-unit` and `test-integration --tier=core`
run on every pull request, `test-integration --tier=full` when the pull request bumps the Infrahub
version or the CI workflow is dispatched with `tier=full`:

```bash
uv run invoke test-unit         # pytest tests/unit tests/smoke; pytest tests/integration -m offline
uv run invoke test-integration  # pytest tests/integration -m "not extended" (--tier=core, default)
                                 # or the whole suite (--tier=full)
uv run invoke test              # pytest tests -- unit and integration together
```

## Security Considerations

- Never commit `.env` files or credentials
- API tokens in documentation are demo tokens for local development only
- Avoid introducing OWASP top 10 vulnerabilities (XSS, SQL injection, command injection)
- Validate external inputs at system boundaries

## PR and Commit Guidelines

- Use descriptive commit messages focusing on "why" not "what"
- Reference issue numbers where applicable
- Do not auto-commit - only commit when explicitly requested
- **Always run `uv run invoke lint` after code changes and before commits/PRs**

## Development Environment

- **Package Manager**: `uv` (required)
- **Python Version**: 3.11, 3.12, 3.13, or 3.14
- **Container Runtime**: Docker (for Infrahub)

### Environment Variables

Required in `.env`:

```bash
INFRAHUB_ADDRESS="http://localhost:8000"
INFRAHUB_API_TOKEN="<your-token>"
```

Optional:

```bash
INFRAHUB_GIT_LOCAL="true"  # Use local repo instead of GitHub
```

## Common Pitfalls

1. **Missing `uv sync`** - Always run after pulling changes
2. **Missing type hints** - All functions require complete annotations
3. **Jinja2 autoescape** - Set `autoescape=False` for device configs
4. **HTML entities** - Use `get_interface_roles()` which handles HTML decoding
5. **Missing `.infrahub.yml` entries** - Register all generators/transforms/checks
6. **Missing `watch` entries** - A transform or generator that imports a helper module
   (`transforms/common.py`) or picks a template from device data (`f"{platform}.j2"`) must declare
   those paths under `watch.files`, or its artifacts go stale when they change. Not supported on
   `check_definitions`, whose model forbids unknown keys
7. **Wrong box style in Rich** - Use `box.SIMPLE` for terminal compatibility

## Sub-Project Guidelines

- [docs/AGENTS.md](./docs/AGENTS.md) - Documentation site (Docusaurus)
- [service_catalog/AGENTS.md](./service_catalog/AGENTS.md) - Streamlit application
- [tests/AGENTS.md](./tests/AGENTS.md) - Testing conventions

## Resources

- [Infrahub Documentation](https://docs.infrahub.app)
- [Infrahub SDK Documentation](https://docs.infrahub.app/python-sdk/)
- [CLAUDE.md](./CLAUDE.md) - Detailed Claude Code instructions
