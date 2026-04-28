# AGENTS.md - Tests

> Guidance for AI coding assistants working in the `tests/` directory.
> Parent: [../AGENTS.md](../AGENTS.md)

## Overview

This directory contains the test suite for demo-dc, using pytest as the test framework. Tests are organized into unit tests (fast, isolated) and integration tests (require running Infrahub instance).

## Test Commands

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -vv

# Run specific test categories
uv run pytest tests/unit/
uv run pytest tests/integration/

# Run specific test file
uv run pytest tests/unit/test_cloud_security_mock.py

# Run with coverage
uv run pytest --cov=.
```

## Directory Structure

```text
tests/
├── conftest.py       # Root pytest fixtures (session-scoped)
├── unit/             # Fast, isolated unit tests
│   ├── test_*.py     # Unit test files
│   └── simulators/   # Mock data and simulators
├── integration/      # Tests requiring running Infrahub
│   ├── conftest.py   # Integration-specific fixtures
│   ├── data/         # Test data files
│   └── test_*.py     # Integration test files
└── smoke/            # Quick smoke tests
```

## Writing Tests

### Unit Tests

Unit tests should be fast and isolated. Mock all external dependencies:

```python
from unittest.mock import MagicMock, patch

def test_my_function():
    """Test description explaining what is being tested."""
    with patch("module.external_dependency") as mock_dep:
        mock_dep.return_value = {"key": "value"}
        result = my_function()
        assert result == expected_value
```

### Integration Tests

Integration tests run against a live Infrahub instance:

```python
import pytest

@pytest.mark.integration
async def test_create_device(client):
    """Test device creation in Infrahub."""
    # Use fixtures from conftest.py
    result = await client.create_device(...)
    assert result.id is not None
```

### Test Requirements

1. **Type hints required** - All test functions need parameter types
2. **Docstrings required** - Explain what each test validates
3. **Both paths** - Test success AND failure scenarios
4. **Descriptive names** - `test_create_device_with_invalid_name_raises_error`

## Fixtures

### Root Fixtures (conftest.py)

| Fixture        | Scope   | Description                  |
| -------------- | ------- | ---------------------------- |
| `root_dir`     | session | Project root directory path  |
| `fixtures_dir` | session | Test fixtures directory      |
| `schema_dir`   | session | Schema files directory       |
| `data_dir`     | session | Data files directory         |

### Integration Fixtures (integration/conftest.py)

| Fixture         | Scope   | Description            |
| --------------- | ------- | ---------------------- |
| `client`        | class   | Infrahub SDK client    |
| `infrahub_port` | session | Running Infrahub port  |

## Mocking Guidelines

### Infrahub SDK

```python
from unittest.mock import AsyncMock, MagicMock

# Mock the SDK client
mock_client = MagicMock()
mock_client.execute_graphql = AsyncMock(return_value={"data": {...}})
```

### GraphQL Responses

Store mock responses in `tests/unit/simulators/`:

```python
from pathlib import Path

def load_mock_response(filename: str) -> dict:
    """Load mock GraphQL response from file."""
    with open(Path(__file__).parent / "simulators" / filename) as f:
        return json.load(f)
```

## Test Data

- Store test fixtures in `tests/fixtures/` or `tests/integration/data/`
- Use YAML files for test data that mirrors the `objects/` structure
- Keep test data minimal - only what's needed for the test

## Common Patterns

### Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_operation()
    assert result is not None
```

### Parametrized Tests

```python
import pytest

@pytest.mark.parametrize("input,expected", [
    ("arista", "eos"),
    ("cisco", "nxos"),
    ("juniper", "junos"),
])
def test_platform_mapping(input, expected):
    assert get_platform(input) == expected
```

### Testing Transforms

```python
def test_leaf_transform(root_dir):
    """Test leaf device configuration transform."""
    # Load sample data
    # Run transform
    # Assert output matches expected
```

## Common Pitfalls

1. **Missing async markers** - Add `@pytest.mark.asyncio` for async tests
2. **Hardcoded paths** - Use fixtures like `root_dir` instead
3. **Leaked state** - Each test should be independent
4. **Missing mocks** - Unit tests should not call external services
5. **Slow tests in unit/** - Move to integration/ if they need real Infrahub

## Running Integration Tests

Integration tests require a running Infrahub instance:

```bash
# Start Infrahub
uv run invoke start

# Wait for it to be ready
# Then run integration tests
uv run pytest tests/integration/

# Or run the full workflow test
uv run pytest tests/integration/test_workflow.py -vv
```
