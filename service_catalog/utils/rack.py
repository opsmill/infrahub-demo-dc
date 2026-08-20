"""Rack visualization utilities for the Infrahub Service Catalog."""

from typing import Any, Dict, List, Optional

from .ui import get_device_color, truncate_device_name


def create_rack_unit_map(rack_height: int, devices: List[Dict[str, Any]]) -> Dict[int, Optional[Dict[str, Any]]]:
    """Create a map of rack units to devices.

    Maps each rack unit (1 to rack_height) to either a device occupying that unit
    or None if the unit is empty. For devices spanning multiple units, each unit
    is mapped with information about the device and its position within the span.

    Args:
        rack_height: Total number of rack units in the rack (e.g., 42).
        devices: List of DcimDevice objects with attributes:
            - name: Device name
            - position: Starting rack unit (1-based)
            - height: Number of rack units occupied
            - device_type: Type of device (optional)
            - id: Device ID

    Returns:
        Dictionary mapping rack unit number (1-based) to device info or None.
        Device info includes:
            - device: The full device dictionary
            - span: Total number of units this device spans
            - position: "start", "middle", or "end" indicating position within span
            - unit_offset: Offset from device start (0 for start unit)

    Example:
        For a 2U device at position 10:
        {
            10: {"device": {...}, "span": 2, "position": "start", "unit_offset": 0},
            11: {"device": {...}, "span": 2, "position": "end", "unit_offset": 1},
            12: None,  # Empty
            ...
        }
    """
    # Initialize all rack units as empty
    rack_units: Dict[int, Optional[Dict[str, Any]]] = {unit: None for unit in range(1, rack_height + 1)}

    # Sort devices by position to handle overlaps consistently
    sorted_devices = sorted(devices, key=lambda d: d.get("position", {}).get("value", 0) or 0)

    for device in sorted_devices:
        position_value = device.get("position", {}).get("value")
        height_value = device.get("height", {}).get("value", 1)

        # Skip devices with invalid or missing position
        if position_value is None or position_value <= 0:
            continue

        # Ensure height is at least 1
        if height_value is None or height_value < 1:
            height_value = 1

        # Calculate which units this device occupies
        start_unit = int(position_value)
        end_unit = start_unit + int(height_value) - 1

        # Skip if device position is outside rack bounds
        if start_unit > rack_height or end_unit < 1:
            continue

        # Clamp to rack boundaries
        start_unit = max(1, start_unit)
        end_unit = min(rack_height, end_unit)

        # Map each unit occupied by this device
        for unit in range(start_unit, end_unit + 1):
            # Check for overlap - skip if unit already occupied
            if rack_units[unit] is not None:
                # Log warning about overlap (in production, could use logging)
                continue

            # Determine position within device span
            unit_offset = unit - start_unit
            total_span = end_unit - start_unit + 1

            if total_span == 1:
                position_type = "single"
            elif unit_offset == 0:
                position_type = "start"
            elif unit_offset == total_span - 1:
                position_type = "end"
            else:
                position_type = "middle"

            rack_units[unit] = {
                "device": device,
                "span": total_span,
                "position": position_type,
                "unit_offset": unit_offset,
            }

    return rack_units


def generate_rack_html(
    rack: Dict[str, Any],
    devices: List[Dict[str, Any]],
    base_url: str = "http://localhost:8000",
    branch: str = "main",
    label_mode: str = "Hostname",
) -> str:
    """Generate HTML for rack diagram visualization.

    Creates a NetBox-style rack diagram with numbered units and positioned devices.

    Args:
        rack: LocationRack object with attributes:
            - name: Rack identifier
            - height: Total rack units (e.g., 42)
            - id: Rack ID
        devices: List of DcimDevice objects in this rack
        base_url: Base URL of Infrahub instance for generating device links
        branch: Branch name for device links
        label_mode: Display mode for device labels ("Hostname" or "Device Type")

    Returns:
        HTML string for rack diagram with embedded CSS
    """
    rack_height = rack.get("height", {}).get("value", 42)
    rack_name = rack.get("name", {}).get("value", "Unknown Rack")

    # Create rack unit map
    rack_units = create_rack_unit_map(rack_height, devices)

    # Generate CSS styles
    css = _generate_rack_css()

    # Generate rack units HTML
    units_html = generate_rack_units_html(rack_units, rack_height, base_url, branch, label_mode)

    # Combine into complete HTML
    html = f"""<style>
{css}
</style>
<div class="rack-container">
    <div class="rack-header">{rack_name}</div>
    <div class="rack-body">
{units_html}
    </div>
</div>"""

    return html


def generate_rack_units_html(
    rack_units: Dict[int, Optional[Dict[str, Any]]],
    rack_height: int,
    base_url: str,
    branch: str,
    label_mode: str = "Hostname",
) -> str:
    """Generate HTML for all rack units.

    Creates HTML for each rack unit from bottom to top, with devices
    rendered as colored rectangles and empty units as blank spaces.

    Args:
        rack_units: Map of rack units to devices or None
        rack_height: Total rack height
        base_url: Base URL of Infrahub instance
        branch: Branch name for device links
        label_mode: Display mode for device labels ("Hostname" or "Device Type")

    Returns:
        HTML string for all rack units
    """
    units_html_parts = []

    # Iterate from top to bottom for display (but units numbered bottom to top)
    for unit_num in range(rack_height, 0, -1):
        unit_info = rack_units.get(unit_num)

        if unit_info is None:
            # Empty rack unit
            units_html_parts.append(_generate_empty_unit_html(unit_num))
        else:
            # Unit occupied by device
            position = unit_info["position"]

            if position == "start" or position == "single":
                # Start of device or single-unit device - render full device
                units_html_parts.append(_generate_device_html(unit_info, unit_num, base_url, branch, label_mode))
            # For "middle" and "end" positions, don't render anything
            # (the device span is handled in the start unit)

    return "\n".join(units_html_parts)


def _generate_empty_unit_html(unit_num: int) -> str:
    """Generate HTML for an empty rack unit.

    Args:
        unit_num: Rack unit number

    Returns:
        HTML string for empty unit
    """
    return f"""<div class="rack-unit rack-unit-empty">
    <span class="rack-unit-number">U{unit_num}</span>
</div>"""


def _generate_device_html(
    unit_info: Dict[str, Any],
    unit_num: int,
    base_url: str,
    branch: str,
    label_mode: str = "Hostname",
) -> str:
    """Generate HTML for a device in the rack.

    Args:
        unit_info: Device information from rack unit map
        unit_num: Starting rack unit number
        base_url: Base URL of Infrahub instance
        branch: Branch name for device links
        label_mode: Display mode for device labels ("Hostname" or "Device Type")

    Returns:
        HTML string for device
    """
    device = unit_info["device"]
    span = unit_info["span"]

    device_name = device.get("name", {}).get("value", "Unknown Device")
    device_type = device.get("device_type", {}).get("value")
    device_role = device.get("role", {}).get("value")
    device_id = device.get("id", "")

    # Determine what to display based on label_mode
    if label_mode == "Device Type" and device_type:
        display_text = truncate_device_name(device_type, max_length=18)
    else:
        display_text = truncate_device_name(device_name, max_length=18)

    # Get color class based on device role
    color_class = get_device_color(device_role)

    # Calculate height in pixels (each unit is approximately 20px)
    height_px = span * 20

    # Generate Infrahub URL for the device.
    #
    # The kind has to come from the data: a rack holds DcimDevice switches and
    # VirtualizationPhysicalHost hypervisors side by side, and Infrahub resolves
    # an object page by kind, so a hardcoded DcimDevice sends every host to a
    # page that cannot exist.
    device_kind = device.get("kind") or "DcimDevice"
    device_url = f"{base_url}/objects/{device_kind}/{device_id}?branch={branch}" if device_id else "#"

    # For 1U devices, only show main text. For 2U+, show main text and secondary info
    if span == 1:
        device_content = f'<div class="device-name">{display_text}</div>'
    else:
        # For multi-U devices, show the primary label and optionally secondary info
        if label_mode == "Device Type":
            # If showing device type, show hostname as secondary info
            secondary_text = truncate_device_name(device_name, 18)
            device_content = (
                f'<div class="device-name">{display_text}</div><div class="device-type-label">{secondary_text}</div>'
            )
        else:
            # If showing hostname, show device type as secondary info (if available)
            device_type_html = (
                f'<div class="device-type-label">{truncate_device_name(device_type, 18)}</div>' if device_type else ""
            )
            device_content = f'<div class="device-name">{display_text}</div>{device_type_html}'

    # Generate device HTML with clickable link
    device_html = f"""<div class="rack-unit rack-unit-device" style="height: {height_px}px;">
    <span class="rack-unit-number">U{unit_num}</span>
    <a href="{device_url}" target="_blank" class="device-link">
        <div class="{color_class}" title="{device_name} - Click to view in Infrahub">
            {device_content}
        </div>
    </a>
</div>"""

    return device_html


def _generate_rack_css() -> str:
    """Generate CSS styles for rack visualization.

    Returns:
        CSS string with all rack diagram styles
    """
    return """
    .rack-container {
        border: 2px solid #333;
        border-radius: 4px;
        background-color: #f5f5f5;
        padding: 10px;
        margin: 10px;
        width: 220px;
        display: inline-block;
        vertical-align: top;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }

    .rack-header {
        font-weight: bold;
        text-align: center;
        padding: 8px;
        background-color: #e0e0e0;
        border-radius: 4px;
        margin-bottom: 10px;
        font-size: 14px;
        color: #333;
    }

    .rack-body {
        display: flex;
        flex-direction: column;
        gap: 1px;
    }

    .rack-unit {
        position: relative;
        border: 1px solid #ccc;
        background-color: white;
        min-height: 20px;
    }

    .rack-unit-empty {
        height: 20px;
    }

    .rack-unit-device {
        border: none;
        padding: 0;
        background-color: transparent;
    }

    .rack-unit-number {
        position: absolute;
        left: 2px;
        top: 2px;
        font-size: 9px;
        color: #666;
        font-weight: bold;
        z-index: 10;
        background-color: rgba(255, 255, 255, 0.7);
        padding: 1px 3px;
        border-radius: 2px;
    }

    .device-link {
        display: block;
        height: 100%;
        text-decoration: none;
        color: inherit;
    }

    .device {
        height: 100%;
        border: 2px solid #a8a8cc;
        color: #333;
        padding: 2px 8px;
        text-align: center;
        font-size: 12px;
        overflow: hidden;
        cursor: pointer;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        background-color: #e6e6fa;
        border-radius: 3px;
        box-sizing: border-box;
        transition: all 0.2s ease;
    }

    .device:hover {
        opacity: 0.85;
        box-shadow: 0 0 8px rgba(0,0,0,0.5);
        transform: translateY(-1px);
    }

    .device-name {
        font-weight: bold;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        line-height: 1.2;
        text-shadow: 0 1px 1px rgba(255,255,255,0.5);
        width: 100%;
    }

    .device-type-label {
        font-size: 9px;
        opacity: 0.95;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        margin-top: 1px;
        line-height: 1.1;
        text-shadow: 0 1px 1px rgba(255,255,255,0.3);
        width: 100%;
    }

    /* Role-based color classes - colors match schemas/base/dcim.yml */
    .device-role-leaf {
        background-color: #e6e6fa;
        border-color: #a8a8cc;
    }

    .device-role-spine {
        background-color: #aeeeee;
        border-color: #7cb8b8;
    }

    .device-role-border-leaf {
        background-color: #dda0dd;
        border-color: #b070b0;
    }

    .device-role-console {
        background-color: #e8e7ad;
        border-color: #b8b77d;
    }

    .device-role-oob {
        background-color: #e8e7ed;
        border-color: #b8b7bd;
    }

    .device-role-edge {
        background-color: #bf7fbf;
        border-color: #8f4f8f;
    }

    .device-role-firewall {
        background-color: #6a5acd;
        border-color: #4a3a9d;
        color: white;
    }

    .device-role-firewall .device-name {
        text-shadow: 0 1px 2px rgba(0,0,0,0.3);
    }

    .device-role-firewall .device-type-label {
        text-shadow: 0 1px 1px rgba(0,0,0,0.2);
    }

    .device-role-load-balancer {
        background-color: #38e7fb;
        border-color: #08b7cb;
    }

    .device-role-hypervisor {
        background-color: #7ec8a0;
        border-color: #4e9870;
    }

    .device-role-compute {
        background-color: #9fb8d4;
        border-color: #6f88a4;
    }
    """
