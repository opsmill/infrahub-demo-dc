from typing import Any


def clean_data(data: Any) -> Any:
    """
    Recursively normalize Infrahub API data by extracting values from nested dictionaries and lists.
    """
    # Handle dictionaries
    if isinstance(data, dict):
        dict_result = {}
        for key, value in data.items():
            if isinstance(value, dict):
                # Handle special cases with single keys
                keys = set(value.keys())
                if keys == {"value"}:
                    dict_result[key] = value["value"]  # This handles None values too
                elif keys == {"edges"} and not value["edges"]:
                    dict_result[key] = []
                # Handle nested structures
                elif "node" in value:
                    dict_result[key] = clean_data(value["node"])
                elif "edges" in value:
                    dict_result[key] = clean_data(value["edges"])
                # Process any other dictionaries
                else:
                    dict_result[key] = clean_data(value)
            elif "__" in key:
                dict_result[key.replace("__", "")] = value
            else:
                dict_result[key] = clean_data(value)
        return dict_result

    # Handle lists
    if isinstance(data, list):
        return [clean_data(item.get("node", item)) for item in data]

    # Return primitives unchanged
    return data


def get_data(data: Any) -> Any:
    """
    Extracts the relevant data from the input.
    Returns the first value from the cleaned data dictionary.
    """
    cleaned_data = clean_data(data)
    if isinstance(cleaned_data, dict) and cleaned_data:
        first_key = next(iter(cleaned_data))
        first_value = cleaned_data[first_key]
        if isinstance(first_value, list):
            if not first_value:
                raise ValueError(
                    f"The query returned no {first_key}, so there is nothing to validate. This "
                    f"usually means the check targets a device of a kind the query does not match."
                )
            return first_value[0]
        # Return empty dict if first_value is None to avoid NoneType errors
        return first_value if first_value is not None else {}
    else:
        raise ValueError("clean_data() did not return a non-empty dictionary")


def validate_interfaces(data: dict[str, Any]) -> list[str]:
    """
    Validates that the device has interfaces and that loopback interfaces have IP addresses.
    """
    errors: list[str] = []
    if len(data.get("interfaces", [])) == 0:
        errors.append("Device has no interfaces configured")

    for interface in data.get("interfaces", []):
        if interface.get("role") == "loopback" and not interface.get("ip_addresses"):
            errors.append(f"Loopback interface {interface.get('name', 'unknown')} is missing IP address")

    return errors
