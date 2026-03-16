# AGENTS.md

> Universal guidance for AI coding assistants working in this repository.
> See also: [CLAUDE.md](./CLAUDE.md) for Claude-specific detailed instructions.

## Project Overview

**bundle-dc** is a comprehensive demonstration of design-driven network automation using [Infrahub](https://docs.infrahub.app). It showcases:

- Composable data center and POP topology generation
- Configuration management with Jinja2 templates
- Validation checks for network devices
- Infrastructure-as-code patterns

## Agent Operating Principles

1. **Plan → Ask → Act → Verify → Record**
   Plan briefly, ask for missing context, act with the smallest change, verify locally, then record with a concise commit or PR note.

2. **Be specific and reversible**
   Use small, scoped commits. Do not mix large refactors with behavior changes in the same PR.

3. **Match existing patterns**
   Keep CLI, adapters, examples, and directory structure consistent with the codebase.

4. **Idempotency and safety**
   Favor operations that are safe to re-run. Use dry runs. Never print or guess secrets. Handle timeouts, auth, and network errors explicitly.

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
# Run all tests
uv run pytest

# Run tests with verbose output
uv run pytest -vv

# Run specific test categories
uv run pytest tests/unit/
uv run pytest tests/integration/

# Lint and type check (ALWAYS run after code changes)
uv run invoke lint         # Full suite: ruff, mypy, markdown, yaml
uv run ruff check . --fix  # Format and lint
uv run mypy .              # Type checking only

# Full validation suite
uv run invoke validate
```

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
- `transforms/common.py` - Shared utilities (get_interface_roles, get_loopbacks, HTML decoding)

## Common Commands

### Schema and Data Loading

```bash
# Load schemas
uv run infrahubctl schema load schemas --branch main

# Load menu (full menu with all options)
uv run infrahubctl menu load menus/menu-full.yml --branch main

# Load bootstrap data
uv run infrahubctl object load objects/bootstrap --branch main

# Add repository
uv run infrahubctl object load objects/git-repo/github.yml --branch main
```

### Branch Management

```bash
# Create a new branch
uv run infrahubctl branch create <branch-name>

# Load data to specific branch
uv run infrahubctl object load objects/dc/dc-arista-s.yml --branch <branch-name>

# Create a proposed change for a branch
uv run invoke create-pc --branch <branch-name>
```

### Demo Workflows

```bash
# Demo DC Arista topology (creates branch, loads data, creates proposed change)
uv run invoke demo-dc-arista

# Extract generated configurations from Infrahub
uv run python scripts/get_configs.py --branch <branch-name>
```

## Development Environment

- **Package Manager**: `uv` (required for all dependency operations)
- **Python Version**: 3.10, 3.11, or 3.12
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

When `INFRAHUB_GIT_LOCAL=true`:

- Infrahub uses the current directory mounted at `/upstream` as the git repository
- Useful for testing generator, transform, and check changes without pushing to GitHub
- Bootstrap script automatically loads `objects/git-repo/local-dev.yml`

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

This ensures:

- Markdown files have proper formatting (blank lines around code blocks, language specifiers)
- YAML files are valid
- Python code passes ruff linting
- Type hints are correct (mypy)

## Infrahub SDK Patterns

### Generator Pattern

```python
from infrahub_sdk.generators import InfrahubGenerator

class MyTopologyGenerator(InfrahubGenerator):
    async def generate(self, data: dict) -> None:
        """Generate topology based on design data."""
        pass
```

### Transform Pattern

```python
from infrahub_sdk.transforms import InfrahubTransform
from jinja2 import Environment, FileSystemLoader

class MyTransform(InfrahubTransform):
    query = "my_config_query"

    async def transform(self, data: Any) -> Any:
        template_path = f"{self.root_directory}/templates/configs"
        env = Environment(
            loader=FileSystemLoader(template_path),
            autoescape=False,  # IMPORTANT: Disable for device configs
        )
        template = env.get_template("device_config.j2")
        return template.render(data=data)
```

### Check Pattern

```python
from infrahub_sdk.checks import InfrahubCheck

class MyCheck(InfrahubCheck):
    query = "my_validation_query"

    async def check(self, data: Any) -> None:
        if not self.is_valid(data):
            self.log_error("Validation failed", data)
```

## Common Pitfalls

1. **Missing `uv sync`** - Always run after pulling changes
2. **Missing type hints** - All functions require complete annotations
3. **Jinja2 autoescape** - Set `autoescape=False` for device configs (HTML entities like `&gt;` will appear otherwise)
4. **HTML entities** - Use `get_interface_roles()` which handles HTML decoding automatically
5. **Missing `.infrahub.yml` entries** - Register all generators/transforms/checks
6. **Wrong box style in Rich** - Use `box.SIMPLE` for terminal compatibility (avoid `box.ROUNDED`)
7. **Template data structure mismatch** - Spine templates expect `interface_roles`, leaf templates expect `interfaces.all_physical`
8. **Missing type stubs** - Install with `uv pip install types-<package>` when mypy reports import errors
9. **Wrong menu path** - Use `menus/menu-full.yml` not `menu/menu.yml`

## Debugging Transforms

```bash
# Test transforms locally before they run in Infrahub
uv run infrahubctl transform spine --branch <branch-name> --debug device=<device-name>
uv run infrahubctl transform leaf --branch <branch-name> --debug device=<device-name>

# Extract all artifacts from a branch
uv run python scripts/get_configs.py --branch <branch-name>
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

## Project Structure

```text
checks/           - Validation checks (spine, leaf, edge, loadbalancer)
generators/       - Topology generators (DC, POP, segment)
menus/            - Infrahub menu definitions
objects/          - Data files (bootstrap, dc, pop, security, events)
queries/          - GraphQL queries (config, topology, validation)
schemas/          - Schema definitions (base, extensions)
scripts/          - Automation scripts (bootstrap, get_configs, etc.)
service_catalog/  - Streamlit-based service catalog application
templates/        - Jinja2 configuration templates
transforms/       - Python transform implementations
tests/            - Unit and integration tests
```

## Sub-Project Guidelines

- [docs/AGENTS.md](./docs/AGENTS.md) - Documentation site (Docusaurus)
- [service_catalog/AGENTS.md](./service_catalog/AGENTS.md) - Streamlit application
- [tests/AGENTS.md](./tests/AGENTS.md) - Testing conventions

## Resources

- [Infrahub Documentation](https://docs.infrahub.app)
- [Infrahub SDK Documentation](https://docs.infrahub.app/python-sdk/)
- [CLAUDE.md](./CLAUDE.md) - Detailed Claude Code instructions
