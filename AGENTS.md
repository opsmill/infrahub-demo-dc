# AGENTS.md

> Universal guidance for AI coding assistants working in this repository.
> See also: [CLAUDE.md](./CLAUDE.md) for Claude-specific detailed instructions.

## Project Overview

**bundle-dc** is a comprehensive demonstration of design-driven network automation using [Infrahub](https://docs.infrahub.app). It showcases:

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
# Run all tests
uv run pytest

# Run tests with verbose output
uv run pytest -vv

# Run specific test categories
uv run pytest tests/unit/
uv run pytest tests/integration/

# Lint and type check
uv run invoke lint         # Full suite: ruff, mypy, markdown, yaml
uv run ruff check . --fix  # Format and lint
uv run mypy .              # Type checking only
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

## Common Pitfalls

1. **Missing `uv sync`** - Always run after pulling changes
2. **Missing type hints** - All functions require complete annotations
3. **Jinja2 autoescape** - Set `autoescape=False` for device configs
4. **HTML entities** - Use `get_interface_roles()` which handles HTML decoding
5. **Missing `.infrahub.yml` entries** - Register all generators/transforms/checks
6. **Wrong box style in Rich** - Use `box.SIMPLE` for terminal compatibility

## Sub-Project Guidelines

- [docs/AGENTS.md](./docs/AGENTS.md) - Documentation site (Docusaurus)
- [service_catalog/AGENTS.md](./service_catalog/AGENTS.md) - Streamlit application
- [tests/AGENTS.md](./tests/AGENTS.md) - Testing conventions

## Resources

- [Infrahub Documentation](https://docs.infrahub.app)
- [Infrahub SDK Documentation](https://docs.infrahub.app/python-sdk/)
- [CLAUDE.md](./CLAUDE.md) - Detailed Claude Code instructions
