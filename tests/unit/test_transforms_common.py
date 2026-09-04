"""Unit tests for the shared transform helpers in `transforms/common.py`.

`get_anycast_gateway` decides the address a leaf renders for an L3 segment, so
the cases that matter are the ones where a prefix cannot yield a host address.
Before this helper existed the templates built the gateway from the VLAN ID
(`192.168.{{ vlan_id }}.1/24`), which silently produced an invalid address for
any VLAN above 255.
"""

import pytest

from transforms.common import get_anycast_gateway, get_vlans


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("172.25.9.0/24", "172.25.9.1/24"),
        ("10.0.0.0/8", "10.0.0.1/8"),
        ("192.168.10.64/26", "192.168.10.65/26"),
        ("2001:db8::/64", "2001:db8::1/64"),
        # A host address given instead of a network: strict=False accepts it and
        # the gateway is still the first usable address of the containing subnet.
        ("172.25.9.7/24", "172.25.9.1/24"),
        # `hosts()` treats a /31 and a /32 as usable addresses rather than as
        # network addresses, so both yield their own first address.
        ("10.0.0.0/31", "10.0.0.0/31"),
        ("10.0.0.1/32", "10.0.0.1/32"),
        (None, None),
        ("", None),
        ("not-a-prefix", None),
    ],
)
def test_get_anycast_gateway(prefix: str | None, expected: str | None) -> None:
    """The gateway is the first usable address, or None when there is none."""
    assert get_anycast_gateway(prefix) == expected


def _interface(vlan_id: int, prefix: str | None) -> dict:
    """Build the interface shape `get_vlans` reads, as `clean_data` returns it.

    Args:
        vlan_id: VLAN ID of the segment.
        prefix: Prefix recorded on the segment, or None when it has none.

    Returns:
        One cleaned interface dictionary carrying a single segment.
    """
    return {
        "interface_services": [
            {
                "typename": "ServiceNetworkSegment",
                "vlan_id": vlan_id,
                "customer_name": "Hypervisor-Management",
                "segment_type": "l3_gateway",
                "external_routing": False,
                "prefix": {"prefix": prefix} if prefix else None,
            }
        ]
    }


def test_get_vlans_carries_the_gateway() -> None:
    """A segment with a prefix reaches the template with a gateway."""
    vlans = get_vlans([_interface(900, "172.25.9.0/24")])

    assert len(vlans) == 1
    assert vlans[0]["prefix"] == "172.25.9.0/24"
    assert vlans[0]["gateway"] == "172.25.9.1/24"
    assert vlans[0]["vni"] == 10900


def test_get_vlans_without_a_prefix_has_no_gateway() -> None:
    """An unset prefix relationship leaves the gateway empty, not invented.

    `clean_data` turns an unset relationship into None, so the lookup has to
    survive a null rather than a missing key.
    """
    vlans = get_vlans([_interface(900, None)])

    assert vlans[0]["prefix"] is None
    assert vlans[0]["gateway"] is None
