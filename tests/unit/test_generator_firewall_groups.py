"""Group membership for generated firewalls.

A firewall's vendor group decides which artifact definition renders its configuration, so getting
it wrong does not fail loudly -- it produces a plausible-looking configuration in the wrong syntax.
These tests read the real design data, so a new vendor added to a design without a matching group
fails here rather than at the next demo.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generators.common import (  # noqa: E402
    FIREWALL_ROLES,
    FIREWALL_VENDOR_GROUPS,
    _manufacturer_key,
)

BOOTSTRAP = ROOT / "objects" / "bootstrap"


def load(filename: str, kind: str) -> list[dict[str, Any]]:
    """Read every object of one kind from a bootstrap file.

    Args:
        filename: File under ``objects/bootstrap``.
        kind: Object kind to collect.

    Returns:
        The objects, in file order.
    """
    out: list[dict[str, Any]] = []
    for document in yaml.safe_load_all((BOOTSTRAP / filename).read_text()):
        if document and document.get("kind") == "Object" and document["spec"]["kind"] == kind:
            out += document["spec"].get("data") or []
    return out


def element(manufacturer: str, role: str = "dc_firewall") -> dict[str, Any]:
    """Build the shape the generator reads a design element as.

    Args:
        manufacturer: Manufacturer name as written in the object files.
        role: Design role.

    Returns:
        A minimal design element.
    """
    return {"device_type": {"manufacturer": {"name": manufacturer}}, "role": role}


@pytest.fixture(scope="module")
def design_elements() -> dict[str, dict[str, Any]]:
    """Every ``DesignElement``, keyed by name.

    Returns:
        ``{name: element}``.
    """
    return {e["name"]: e for e in load("14_design_elements.yml", "DesignElement")}


@pytest.fixture(scope="module")
def group_names() -> set[str]:
    """Every ``CoreStandardGroup`` the bootstrap creates.

    Returns:
        The group names.
    """
    return {g["name"] for g in load("00_groups.yml", "CoreStandardGroup")}


class TestVendorLookup:
    """Manufacturer to vendor-group resolution."""

    @pytest.mark.parametrize(
        ("manufacturer", "expected"),
        [
            ("Juniper", "juniper_firewall"),
            ("Palo Alto Networks", "palo_alto_firewall"),
        ],
    )
    def test_a_known_vendor_resolves_to_its_group(self, manufacturer: str, expected: str) -> None:
        """The group whose artifact definition renders that vendor's syntax."""
        assert FIREWALL_VENDOR_GROUPS[_manufacturer_key(element(manufacturer))] == expected

    def test_an_unmapped_vendor_gets_no_vendor_group(self) -> None:
        """Better no configuration artifact than one in another vendor's syntax.

        Check Point firewalls appear in ``VIRTUAL POP S``. Before the lookup existed every firewall
        was placed in ``juniper_firewall`` regardless of make, so these were targeted by the JunOS
        artifact definition.
        """
        assert FIREWALL_VENDOR_GROUPS.get(_manufacturer_key(element("Check Point"))) is None

    def test_the_key_matches_how_group_names_are_written(self) -> None:
        """Spaces become underscores, so the multi-word manufacturers line up."""
        assert _manufacturer_key(element("Palo Alto Networks")) == "palo_alto_networks"

    def test_a_missing_manufacturer_does_not_raise(self) -> None:
        """A malformed element must not take the whole generator run down."""
        assert _manufacturer_key({"role": "dc_firewall"}) == ""


class TestShippedDesigns:
    """The lookup against the design data actually shipped."""

    def test_every_mapped_group_exists(self, group_names: set[str]) -> None:
        """A vendor mapped to a group the bootstrap never creates fails at generation time."""
        missing = sorted(set(FIREWALL_VENDOR_GROUPS.values()) - group_names)
        assert not missing, f"Vendor groups referenced but never created: {missing}"

    def test_every_firewall_element_resolves_or_is_deliberately_unmapped(
        self, design_elements: dict[str, dict[str, Any]]
    ) -> None:
        """Each firewall element either has a vendor group, or has no transform to render it.

        This is the guard that matters when a design gains a vendor: it fails until the mapping and
        the group exist, instead of silently rendering the wrong syntax.
        """
        unmapped = []
        for name, entry in design_elements.items():
            if entry["role"] not in FIREWALL_ROLES:
                continue
            manufacturer = entry["device_type"][0]
            if FIREWALL_VENDOR_GROUPS.get(_manufacturer_key(element(manufacturer))) is None:
                unmapped.append(f"{name} ({manufacturer})")

        # Check Point is the known, accepted gap: the repository ships no Check Point transform.
        assert unmapped == ["2 CloudGuard Security Edges (Check Point)"], (
            f"Firewall design elements with no vendor group: {unmapped}"
        )

    def test_the_arista_demo_design_uses_palo_alto(self) -> None:
        """`invoke demo-dc-arista` is the design the runbook drives, so it carries the Palo Alto FWs."""
        designs = {d["name"]: d for d in load("15_designs.yml", "DesignTopology")}
        elements = [e[0] if isinstance(e, list) else e for e in designs["PHYSICAL DC ARISTA S"]["elements"]]
        assert "2 PALO ALTO DC FIREWALLS PA-5220" in elements

    def test_the_palo_template_matches_pan_os_interface_naming(self) -> None:
        """PAN-OS calls the out-of-band port `management`, not a numbered ethernet interface."""
        templates = {t["template_name"]: t for t in load("10_physical_device_templates.yml", "TemplateDcimDevice")}
        interfaces = templates["PA-5220_DC_FIREWALL"]["interfaces"]["data"]
        names = {i["name"] for i in interfaces}
        assert names == {"management", "ethernet1/[1-4]"}
