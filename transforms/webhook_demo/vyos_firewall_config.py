"""Python Transformation rendering a VyOS `config.boot` fragment from a
SecurityFirewall plus its linked SecurityPolicy / SecurityPolicyRule data.

Query: vyos_firewall_config (queries/webhook_demo/vyos_firewall_config.gql)
Output: text/plain (UTF-8); `set firewall ...` statements suitable for VyOS.
"""

from __future__ import annotations

from typing import Any

from infrahub_sdk.transforms import InfrahubTransform

RULESET_NAME = "ALLOW-WEB"
DEFAULT_ACTION = "drop"


def _first_service(rule_node: dict) -> dict | None:
    """Pull the first underlying SecurityService out of a rule's services rel.

    Rule.services peers at SecurityServiceGroup. The demo's seed attaches a
    group containing exactly one SecurityService (https/http/ssh); we read
    from that first concrete service. If no group or no service is present
    the rule is skipped during rendering.
    """
    for group_edge in (rule_node.get("services") or {}).get("edges") or []:
        group = group_edge.get("node") or {}
        for svc_edge in (group.get("services") or {}).get("edges") or []:
            svc = svc_edge.get("node") or {}
            if svc.get("name", {}).get("value"):
                return svc
    return None


def _rule_to_vyos_lines(rule: dict) -> list[str]:
    """Render one rule to VyOS `set firewall ...` lines, or empty list on skip."""
    index = (rule.get("index") or {}).get("value")
    if index is None:
        return []
    service = _first_service(rule)
    if not service:
        return []
    port = (service.get("port") or {}).get("value")
    protocol = (service.get("protocol") or {}).get("value") or "tcp"
    if port is None:
        return []

    action = (rule.get("action") or {}).get("value") or "accept"
    # VyOS uses "accept"/"drop"; our schema uses "permit"/"deny".
    vyos_action = {"permit": "accept", "deny": "drop"}.get(action, action)

    src_zone = ((rule.get("source_zone") or {}).get("node") or {}).get("name", {}).get("value", "any")
    dst_zone = ((rule.get("destination_zone") or {}).get("node") or {}).get("name", {}).get("value", "any")

    base = f"set firewall ipv4 name {RULESET_NAME} rule {index}"
    return [
        f"{base} action '{vyos_action}'",
        f"{base} description 'rule-{index} {src_zone} -> {dst_zone}'",
        f"{base} protocol '{protocol}'",
        f"{base} destination port '{port}'",
    ]


def render_config(data: dict) -> str:
    edges = (data.get("SecurityFirewall") or {}).get("edges") or []
    if not edges:
        raise ValueError("VyosFirewallConfig: GraphQL response had no SecurityFirewall edges")
    device_node = edges[0]["node"]
    hostname = device_node["name"]["value"]

    # Flatten all rules from all attached policies, de-dup by index.
    rules_by_index: dict[int, dict] = {}
    for policy_edge in (device_node.get("policies") or {}).get("edges") or []:
        policy = policy_edge.get("node") or {}
        for rule_edge in (policy.get("rules") or {}).get("edges") or []:
            rule = rule_edge.get("node") or {}
            idx = (rule.get("index") or {}).get("value")
            if idx is None or idx in rules_by_index:
                continue
            rules_by_index[idx] = rule

    rule_lines: list[str] = []
    for idx in sorted(rules_by_index.keys()):
        rule_lines.extend(_rule_to_vyos_lines(rules_by_index[idx]))

    header = [
        f"set system host-name '{hostname}'",
        "set system login user admin authentication plaintext-password 'demo-vyos-password'",
        "set service ssh port '22'",
        "set service ssh listen-address '0.0.0.0'",
        "set interfaces ethernet eth0 address '172.20.20.11/24'",
        "set interfaces ethernet eth0 description 'mgmt'",
        "",
        f"set firewall ipv4 name {RULESET_NAME} default-action '{DEFAULT_ACTION}'",
    ]
    return "\n".join(header + rule_lines) + "\n"


class VyosFirewallConfig(InfrahubTransform):
    query = "vyos_firewall_config"

    async def transform(self, data: Any) -> str:
        return render_config(data)
