# syntax=docker/dockerfile:1.7
#
# Webhook listener image for the custom-webhook firewall demo.
#
# Purpose: host a FastAPI endpoint that Infrahub posts webhook events to,
# and run the ansible playbook that applies the rendered VyOS config to fw1.
# Everything the playbook needs (ansible-core, paramiko, scp, the
# ansible.netcommon + vyos.vyos collections) is pinned into this image so
# the demo does not depend on host-side interpreter state.
#
# Build: driven by docker compose when the webhook-demo profile activates
# the webhook-listener service. No direct docker build is required.

FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /opt/webhook-listener

# Install deps first (better layer caching). README.md is referenced by
# pyproject.toml's `readme = "README.md"` so hatchling needs it present
# even at the deps-only sync step.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

# Install the app itself.
COPY src/ ./src/
RUN uv sync --frozen

# Ansible assets used by the listener at runtime.
COPY ansible/ ./ansible/

# Ansible collections needed by the deploy playbook. Install into the root
# user's ansible dir so they are discoverable by the listener's ansible.
RUN uv run ansible-galaxy collection install ansible.netcommon vyos.vyos

# Runtime config: absolute paths into this image; can be overridden via env.
ENV WEBHOOK_LISTENER_PLAYBOOK_PATH=/opt/webhook-listener/ansible/deploy.yml \
    WEBHOOK_LISTENER_INVENTORY_PATH=/opt/webhook-listener/ansible/inventory.yml \
    WEBHOOK_LISTENER_HOST=0.0.0.0 \
    WEBHOOK_LISTENER_PORT=8001

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=2)" || exit 1

ENTRYPOINT ["uv", "run", "webhook-listener"]
