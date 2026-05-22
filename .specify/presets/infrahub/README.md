# Infrahub Workflow Routing Preset

Routes `/speckit.specify` to Infrahub-specific spec templates when `.infrahub.yml` is detected in the repository.

## What it does

1. Checks for `.infrahub.yml` in the repo root
2. Gates on Infrahub connectivity (`infrahubctl info`)
3. Analyzes the user's prompt to detect artifact types (schema, transform, check, generator, menu)
4. If multiple types detected, presents the dependency chain and starts with the first
5. Loads the correct template from the `infrahub` extension

## Dependency chain

When a feature spans multiple artifact types:

```
Schema → [Check / Generator / Transform / Menu]
```

Schema is always first -- everything depends on the data model being loaded.

## Requires

- `infrahub` extension (provides the spec templates)
- `infrahubctl` CLI (for connectivity check)
