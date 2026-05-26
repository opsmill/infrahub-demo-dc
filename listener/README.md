# Webhook listener

FastAPI service that receives `infrahub.artifact.updated` webhook events from
Infrahub and runs the ansible deploy playbook to apply the rendered VyOS
config to fw1.

Lifecycle is owned by docker-compose profile `webhook-demo`; build/run is
driven by `invoke demo-webhook-listener-up` / `-down` from the repo root.
