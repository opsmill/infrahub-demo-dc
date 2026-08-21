"""Python Transformation for Infrahub's Custom Webhook on the
`infrahub.artifact.updated` event (filtered to the `vyos_firewall_config`
artifact definition).

Given an event data dict from Infrahub, resolves the target device's HFID via
a GraphQL query and shapes the payload described in
`specs/002-port-webhook-to-bundle-dc/contracts/webhook_payload.md`.
"""

from __future__ import annotations

import os
from typing import Any

from infrahub_sdk.transforms import InfrahubTransform

SCHEMA_VERSION = "2"
ARTIFACT_DEFINITION_NAME = "vyos_firewall_config"
ARTIFACT_NAME = "vyos-firewall-config"
TARGET_KIND_DEFAULT = "SecurityFirewall"


def _base_url(client) -> str:
    base = getattr(client, "address", None) or getattr(client, "base_url", None)
    if not base:
        base = os.environ.get("INFRAHUB_ADDRESS", "http://127.0.0.1:8000")
    return base.rstrip("/")


def _device_key(hfid: list[str]) -> str:
    """Derive a single inventory-selector string from a (possibly compound) HFID.

    Uses the double-underscore separator ("__") to join compound HFIDs; for
    single-element HFIDs like SecurityFirewall this is just the name ("fw1").
    """
    return "__".join(str(part) for part in hfid)


def build_payload(event: dict, artifact_node: dict, base_url: str) -> dict:
    """Build the outbound webhook payload from the resolved artifact node."""
    device = (artifact_node.get("object") or {}).get("node") or {}

    hfid_field = device.get("hfid")
    if isinstance(hfid_field, list):
        hfid = [str(part) for part in hfid_field if part is not None]
    elif isinstance(hfid_field, str):
        hfid = [hfid_field]
    else:
        name = (device.get("name") or {}).get("value")
        hfid = [name] if name else []
    if not hfid:
        raise ValueError("WebhookFirewallDeploy: could not resolve HFID for target device")

    artifact_id = artifact_node.get("id") or event.get("node_id", "")
    checksum = (artifact_node.get("checksum") or {}).get("value") or event.get("checksum", "")
    checksum_previous = (artifact_node.get("checksum_previous") or {}).get("value")
    storage_id = (artifact_node.get("storage_id") or {}).get("value") or event.get("storage_id", "")

    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event.get("id") or event.get("event_id", ""),
        "occurred_at": event.get("occurred_at", ""),
        "branch": event.get("branch", "main"),
        # Authoritative identifier — a list, as Infrahub emits it. Consumers
        # that need to look the node back up via GraphQL should pass the
        # whole list to the hfid filter.
        "hfid": hfid,
        # Convenience selector derived from hfid; matches the Ansible
        # inventory hostname for this repo's conventions.
        "device_key": _device_key(hfid),
        "device_kind": event.get("target_kind", TARGET_KIND_DEFAULT),
        "device_node_id": device.get("id") or event.get("target_id", ""),
        "artifact": {
            "definition_name": event.get("artifact_definition_name", ARTIFACT_DEFINITION_NAME),
            "artifact_name": ARTIFACT_NAME,
            "url": f"{base_url}/api/artifact/{artifact_id}",
            "storage_id": storage_id,
            "checksum": checksum,
            "checksum_previous": checksum_previous,
        },
    }


class WebhookFirewallDeploy(InfrahubTransform):
    # `query` is the NAME of the CoreGraphQLQuery registered by .infrahub.yml
    # (file: queries/webhook_demo/webhook_firewall_deploy.gql). The base
    # class resolves it via `client.query_gql_query(name=self.query, ...)`.
    query = "webhook_firewall_deploy"

    async def transform(self, data: Any) -> dict:
        event = data.get("data") if isinstance(data, dict) and "data" in data else data
        # Infrahub's artifact.updated event uses `node_id` for the CoreArtifact id.
        artifact_id = event.get("node_id") or event.get("target_id")
        if not artifact_id:
            raise ValueError("WebhookFirewallDeploy: event missing node_id / target_id")

        result = await self.client.query_gql_query(
            name=self.query,
            variables={"node_id": artifact_id},
            branch_name=self.branch_name,
        )
        payload = result.get("data") if isinstance(result, dict) and "data" in result else result
        edges = (payload.get("CoreArtifact") or {}).get("edges") or []
        if not edges:
            raise ValueError(f"WebhookFirewallDeploy: no CoreArtifact matched id={artifact_id}")
        artifact_node = edges[0]["node"]
        return build_payload(
            event=event,
            artifact_node=artifact_node,
            base_url=_base_url(self.client),
        )
