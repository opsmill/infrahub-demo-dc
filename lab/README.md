# Webhook demo lab

Single-node VyOS containerlab topology used by the custom-webhook firewall demo.

## Topology

- One VyOS 1.5 Stream node: `fw1`
- Management subnet: `172.20.20.0/24` (docker network `cwdemo-webhook-demo-mgmt`)
- fw1 management IP: `172.20.20.11`
- SSH: port 22 on the VyOS container, also mapped to host port 2221

## Prerequisites

- `containerlab` ≥ 0.56 on `PATH`
- `vyos:1.5-stream` container image (see `lab/image/README.md`)
- Passwordless sudo rule scoped to the containerlab binary, e.g.:

  ```text
  # /etc/sudoers.d/containerlab
  <your-user> ALL=(root) NOPASSWD: /home/<your-user>/.local/bin/containerlab
  ```

## Bring up / tear down

Normally driven by `invoke demo-webhook-lab-up` / `-down`. Equivalent manual
commands:

```bash
sudo -E containerlab deploy  -t lab/demo-webhook.clab.yml
sudo -E containerlab destroy -t lab/demo-webhook.clab.yml --cleanup
```
