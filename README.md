# Infrahub demo

<!-- markdownlint-disable -->
![Infrahub Logo](https://assets-global.website-files.com/657aff4a26dd8afbab24944b/657b0e0678f7fd35ce130776_Logo%20INFRAHUB.svg)
<!-- markdownlint-restore -->

[Infrahub](https://github.com/opsmill/infrahub) by [OpsMill](https://opsmill.com) acts as a central hub to manage the data, templates and playbooks that powers your infrastructure. At its heart, Infrahub is built on 3 fundamental pillars:

- **A Flexible Schema**: A model of the infrastructure and the relation between the objects in the model, that's easily extensible.
- **Version Control**: Natively integrated into the graph database which opens up some new capabilities like branching, diffing, and merging data directly in the database.
- **Unified Storage**: By combining a graph database and git, Infrahub stores data and code needed to manage the infrastructure.

> **Note**
> This demo repository is partially authored by the OpsMill community member [tomek](https://www.linkedin.com/in/tomekzajac/) from this example: <https://github.com/t0m3kz/infrahub-demo>

## Infrahub demo

This repository is demoing the key Infrahub features for an example data center with VxLAN/EVPN and firewalls.

## Running the demo

Documentation for loading and using this demo is available on the Infrahub docs site [docs.infrahub.app/bundle-dc/](https://docs.infrahub.app/bundle-dc)

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

For detailed setup instructions, configuration options, and usage guide, see [docs/docs/service-catalog.mdx](docs/docs/service-catalog.mdx).

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
