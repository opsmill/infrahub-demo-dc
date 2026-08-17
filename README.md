# Infrahub demo

<!-- rumdl-disable -->
![Infrahub Logo](https://assets-global.website-files.com/657aff4a26dd8afbab24944b/657b0e0678f7fd35ce130776_Logo%20INFRAHUB.svg)
<!-- rumdl-enable -->

[Infrahub](https://github.com/opsmill/infrahub) by [OpsMill](https://opsmill.com) acts as a central hub to manage the data, templates and playbooks that powers your infrastructure. At its heart, Infrahub is built on 3 fundamental pillars:

- **A Flexible Schema**: A model of the infrastructure and the relation between the objects in the model, that's easily extensible.
- **Version Control**: Natively integrated into the graph database which opens up some new capabilities like branching, diffing, and merging data directly in the database.
- **Unified Storage**: By combining a graph database and git, Infrahub stores data and code needed to manage the infrastructure.

> **Note**
> This demo repository is partially authored by the OpsMill community member [tomek](https://www.linkedin.com/in/tomekzajac/) from this example: <https://github.com/t0m3kz/infrahub-demo>

## Infrahub demo

This repository is demoing the key Infrahub features for an example data center with VxLAN/EVPN and firewalls.

## Running the demo

Documentation for loading and using this demo is available on the Infrahub docs site [docs.infrahub.app/demo-dc/](https://docs.infrahub.app/demo-dc)

## Service Catalog

This repository includes an optional Streamlit-based Service Catalog that provides a user-friendly web interface for viewing and creating data center infrastructure in Infrahub.

### Features

- View lists of Data Centers and Colocation Centers with branch selection
- Create new Data Centers through a form-based interface
- Automatic branch creation and Proposed Change generation
- Workflow automation for infrastructure provisioning

### Quick Start

To start Infrahub with the Service Catalog enabled:

```bash
docker-compose --profile service-catalog up
```

The Service Catalog will be accessible at `http://localhost:8501`

To start Infrahub without the Service Catalog:

```bash
docker-compose up
```

### Documentation

For detailed setup instructions, configuration options, and usage guide, see the [demo-dc docs](https://docs.infrahub.app/demo-dc).

## Testing

```bash
# Unit and specification tests: seconds, no containers
uv run pytest tests/unit tests/smoke

# The integration tier every pull request runs
uv run pytest -m "not extended"

# Everything, including the extended workflows
uv run pytest
```

The integration suite starts its own throwaway Infrahub deployment with
[infrahub-testcontainers](https://pypi.org/project/infrahub-testcontainers/), bootstraps it from this
repository and drives complete workflows against it: building a data center from a design, generating
device configurations, reviewing and merging a proposed change, adding a POP and a network segment,
day-two operations, and conflict detection. It needs Docker; it does not use `invoke start`.

The whole suite shares one deployment, so modules run in file-name order and build on what earlier
ones merged. They are split into two tiers:

- **`core`** runs on every pull request.
- **`extended`** adds the second vendor data center, the POP and segment services, day-two operations
  and the conflict workflow. It runs when a pull request is labelled `full-integration`.

The extended tier is opt-in because it cannot currently pass: Infrahub intermittently fails to
resolve a member of its own internal generator group, which fails whichever generator is running at
the time. Run it deliberately with the label when changing a version or a generator, and read a
failure there against that known fault before assuming the demo is broken. To test a specific
Infrahub version, set `INFRAHUB_TESTING_IMAGE_VER`.

See the [developer guide](https://docs.infrahub.app/demo-dc/developer-guide) for details, and
[tests/AGENTS.md](tests/AGENTS.md) for the conventions the suite follows.

## License

This project is licensed under the MIT License. See [LICENSE.txt](LICENSE.txt) for details.
