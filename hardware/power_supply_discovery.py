"""Read-only discovery of the power supply used by a hardware smoke run."""

from __future__ import annotations

from typing import Any, Mapping


def discover_power_supplies(resource_manager: Any, device: Mapping[str, Any], report: dict[str, Any], *, expected_match: Any) -> list[str]:
    """Return every discovered power supply that matches the safe-state checks."""
    list_resources = getattr(resource_manager, "list_resources", None)
    if not callable(list_resources):
        raise ValueError("power_supply discovery requires a VISA resource manager with list_resources()")
    resources = list_resources()
    candidates = report.setdefault("power_supply_discovery", [])
    matches: list[str] = []
    state_queries = device.get("state_queries", [])
    expected_model = str(device.get("model", "")).strip()
    for address in resources:
        item: dict[str, Any] = {"address": address}
        resource = None
        try:
            resource = resource_manager.open_resource(address, open_timeout=int(device["timeout_ms"]))
            resource.timeout = int(device["timeout_ms"])
            identity = resource.query("*IDN?").strip()
            item["identity"] = identity
            if expected_model and expected_model not in identity:
                item["matched"] = False
                item["reason"] = "identity does not match configured model"
            else:
                states = []
                for check in state_queries:
                    response = resource.query(check["command"]).strip()
                    states.append({"command": check["command"], "response": response})
                item["state"] = states
                safe = all(expected_match(entry["response"], check["expected"])
                           for entry, check in zip(states, state_queries))
                item["matched"] = safe
                if safe:
                    matches.append(address)
                else:
                    item["reason"] = "configured power channels were not all off"
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if resource is not None:
                try:
                    resource.close()
                    item["closed"] = True
                except Exception as exc:
                    item["closed"] = False
                    item["close_error"] = f"{type(exc).__name__}: {exc}"
        candidates.append(item)
    if not matches:
        raise RuntimeError("Expected at least one usable power supply, found 0")
    report["power_supply_discovery_count"] = len(matches)
    return matches


def discover_power_supply(resource_manager: Any, device: Mapping[str, Any], report: dict[str, Any], *, expected_match: Any) -> list[str]:
    """Compatibility alias for callers migrating from single-supply discovery."""
    return discover_power_supplies(resource_manager, device, report, expected_match=expected_match)
