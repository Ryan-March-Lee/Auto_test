"""Run the real-device preparation smoke without enabling any output."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from hardware.read_only_smoke import SmokeExecutionError, resolve_report_path, write_report
from hardware.power_supply_discovery import discover_power_supplies


_OUTPUT_TOKENS = {"OUTP", "OUTPUT", "RF", "OUT", "STATE"}
_ALLOWED_SETUP_ROOTS = {
    "signal_generator": {"FREQ", "FREQUENCY", "POW", "POWER", "OUTP", "OUTPUT"},
    "spectrum_analyzer": {"FREQ", "FREQUENCY", "BAND", "BANDWIDTH", "SPAN", "*CLS", "CLS"},
    "power_supply": {"SOURCE", "VOLT", "VOLTAGE", "CURR", "CURRENT", "OUTP", "OUTPUT"},
}
_ALLOWED_QUERY_ROOTS = {
    "signal_generator": {"IDN", "SYST", "OUTP", "OUTPUT", "RF"},
    "spectrum_analyzer": {"FREQ", "FREQUENCY", "BAND", "BANDWIDTH", "SPAN", "SYST"},
    "power_supply": {"IDN", "SYST", "OUTP", "OUTPUT"},
}


def _validate_command(command: Any, *, allow_output_off: bool = False, allowed_roots: set[str] | None = None) -> str:
    if not isinstance(command, str) or not command.strip() or "\n" in command or "\r" in command:
        raise ValueError(f"Invalid SCPI command: {command!r}")
    parts = command.strip().upper().split(None, 1)
    command_path = parts[0].rstrip("?")
    path = command_path.replace(":", " ").split()
    root = path[0].lstrip("*")
    root_allowed = root in allowed_roots if allowed_roots is not None else True
    if not root_allowed and root and root[-1].isdigit():
        root_allowed = root.rstrip("0123456789") in allowed_roots
    if allowed_roots is not None and not root_allowed and parts[0].upper() not in allowed_roots:
        raise ValueError(f"SCPI command is outside the allowed command set: {command!r}")
    argument = parts[1].strip().upper() if len(parts) == 2 else ""
    output_control = bool(_OUTPUT_TOKENS.intersection(path))
    if output_control:
        is_query = parts[0].endswith("?")
        output_argument = argument.rsplit(",", 1)[-1].strip()
        if not is_query and (not allow_output_off or output_argument not in {"OFF", "0"}):
            raise ValueError(f"Output-control command is not allowed: {command!r}")
    return command.strip()


def _response(resource: Any, command: str, clock: Any) -> dict[str, Any]:
    started = clock()
    value = resource.query(command).strip()
    return {"command": command, "response": value, "elapsed_ms": round((clock() - started) * 1000, 3)}


def _expected_matches(actual: str, expected: Any) -> bool:
    if expected is None:
        return True
    actual_value = actual.split(",", 1)[0].strip().upper()
    expected_value = str(expected).strip().upper()
    if expected_value in {"0", "OFF"} and actual_value in {"0", "OFF"}:
        return True
    return actual_value == expected_value


def _check_safe_state(name: str, state: list[dict[str, Any]], checks: list[dict[str, Any]]) -> None:
    for check in checks:
        actual = next((item["response"] for item in state if item["command"] == check["command"]), None)
        if actual is None or not _expected_matches(actual, check.get("expected")):
            raise AssertionError(
                f"{name} safety query {check['command']!r} returned {actual!r}; "
                f"expected {check.get('expected')!r}"
            )


def _validate_config(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if config.get("environment") != "hardware_smoke" or config.get("smoke_mode") != "safe_prepare":
        raise ValueError("Hardware configuration must select smoke_mode=safe_prepare")
    if config.get("max_action_count") != 0:
        raise ValueError("Safe preparation smoke must have max_action_count=0")
    devices = config.get("devices")
    required = {"signal_generator", "spectrum_analyzer", "power_supply"}
    if not isinstance(devices, Mapping) or set(devices) != required:
        raise ValueError(f"Safe preparation requires exactly these devices: {sorted(required)}")
    normalized: dict[str, dict[str, Any]] = {}
    for name, raw in devices.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"{name} requires a device configuration")
        if name != "power_supply" or not raw.get("discover"):
            if not raw.get("address"):
                raise ValueError(f"{name} requires a device address")
        elif raw.get("address"):
            raise ValueError("Discovered power_supply must not also specify address")
        checks = raw.get("state_queries")
        if not isinstance(checks, list) or not checks:
            raise ValueError(f"{name} requires safety state_queries")
        normalized_checks = []
        for check in checks:
            if not isinstance(check, Mapping) or "expected" not in check:
                raise ValueError(f"{name} safety queries require an explicit expected safe value")
            query = _validate_command(check.get("command"))
            query_head = query.split(None, 1)[0]
            if not query_head.endswith("?"):
                raise ValueError(f"{name} safety command must be a query: {query!r}")
            query_root = query_head[:-1].upper().split(":", 1)[0].lstrip("*")
            if query_root not in _ALLOWED_QUERY_ROOTS[name]:
                raise ValueError(f"{name} query is outside the read-only safety query set: {query!r}")
            normalized_checks.append({"command": query, "expected": check["expected"]})
        setup_commands = [
            _validate_command(command, allow_output_off=True, allowed_roots=_ALLOWED_SETUP_ROOTS[name])
            for command in raw.get("setup_commands", [])
        ]
        cleanup_commands = [
            _validate_command(command, allow_output_off=True, allowed_roots=_ALLOWED_SETUP_ROOTS[name] | {"CLS"})
            for command in raw.get("cleanup_commands", [])
        ]
        if not cleanup_commands:
            raise ValueError(f"{name} must define safe cleanup_commands")
        # Every device's safety queries must include its output state.
        output_check_required = name in {"signal_generator", "power_supply"}
        has_output_check = any(
            _OUTPUT_TOKENS.intersection(
                query["command"].upper().split(None, 1)[0].rstrip("?").replace(":", " ").split()
            )
            for query in normalized_checks
        )
        if output_check_required and not has_output_check:
            raise ValueError(f"{name} safety queries must verify output state")
        normalized[name] = {
            **raw,
            "setup_commands": setup_commands,
            "cleanup_commands": cleanup_commands,
            "state_queries": normalized_checks,
            "initial_state_queries": (
                [
                    {"command": _validate_command(check["command"]), "expected": check["expected"]}
                    for check in raw["initial_state_queries"]
                ]
                if "initial_state_queries" in raw
                else None
            ),
        }
    return normalized


def run_safe_prepare_smoke(config: Mapping[str, Any], resource_manager: Any, *, clock=time.monotonic) -> dict[str, Any]:
    """Prepare instruments while requiring all configured outputs to remain off."""
    devices = _validate_config(config)

    report: dict[str, Any] = {"mode": "safe_prepare", "devices": {}, "resources_closed": False}
    resources: list[tuple[str, Any, list[str]]] = []
    failure: Exception | None = None
    try:
        if devices["power_supply"].get("discover"):
            devices["power_supply"]["addresses"] = discover_power_supplies(
                resource_manager, devices["power_supply"], report,
                expected_match=_expected_matches,
            )
        power_addresses = devices["power_supply"].get("addresses") or [devices["power_supply"].get("address")]
        expanded_devices = [
            ("signal_generator", devices["signal_generator"]),
            ("spectrum_analyzer", devices["spectrum_analyzer"]),
        ]
        expanded_devices.extend(
            (("power_supply" if len(power_addresses) == 1 else f"power_supply[{index}]"),
             {**devices["power_supply"], "address": address})
            for index, address in enumerate(power_addresses)
        )
        for name, device in expanded_devices:
            resource = resource_manager.open_resource(device["address"], open_timeout=int(device.get("timeout_ms", 5000)))
            cleanup = list(device.get("cleanup_commands", []))
            resources.append((name, resource, cleanup))
            result: dict[str, Any] = {"queries": [], "writes": [], "cleanup": []}
            report["devices"][name] = result
            try:
                resource.timeout = int(device.get("timeout_ms", 5000))
                result["identity"] = _response(resource, "*IDN?", clock)["response"]
                expected_model = device.get("model", "")
                if expected_model and not expected_model.startswith("REPLACE_WITH_") and expected_model not in result["identity"]:
                    raise AssertionError(f"{name} identity does not contain configured model {expected_model!r}")
                initial_checks = device.get("initial_state_queries")
                if initial_checks is None:
                    initial_checks = device["state_queries"] if name in {"signal_generator", "power_supply"} else []
                initial_state = [_response(resource, check["command"], clock) for check in initial_checks]
                result["initial_state"] = initial_state
                _check_safe_state(name, initial_state, initial_checks)
                for command in device.get("setup_commands", []):
                    resource.write(command)
                    result["writes"].append(command)
                for check in device.get("state_queries", []):
                    result["queries"].append(_response(resource, check["command"], clock))
                _check_safe_state(name, result["queries"], list(device.get("state_queries", [])))
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"
                failure = exc
                break
    except Exception as exc:
        failure = exc
    finally:
        close_errors: list[str] = []
        for name, resource, cleanup in reversed(resources):
            result = report["devices"].setdefault(name, {"queries": [], "writes": [], "cleanup": []})
            for command in cleanup:
                try:
                    resource.write(command)
                    result.setdefault("cleanup", []).append(command)
                except Exception as exc:
                    close_errors.append(f"{name} cleanup {command!r}: {type(exc).__name__}: {exc}")
            try:
                base_name = "power_supply" if name.startswith("power_supply[") else name
                checks = devices[base_name]["state_queries"]
                final_state = [_response(resource, check["command"], clock) for check in checks]
                result["final_state"] = final_state
                _check_safe_state(name, final_state, checks)
                result["safe_after_cleanup"] = True
            except Exception as exc:
                result["safe_after_cleanup"] = False
                result["final_state_error"] = f"{type(exc).__name__}: {exc}"
                close_errors.append(f"{name} final safety verification: {type(exc).__name__}: {exc}")
            try:
                resource.close()
                report["devices"].setdefault(name, {})["closed"] = True
            except Exception as exc:
                close_errors.append(f"{name} close: {type(exc).__name__}: {exc}")
                report["devices"].setdefault(name, {})["closed"] = False
        try:
            resource_manager.close()
        except Exception as exc:
            close_errors.append(f"manager close: {type(exc).__name__}: {exc}")
        report["resources_closed"] = not close_errors
        if close_errors:
            report["close_errors"] = close_errors
            if failure is None:
                failure = RuntimeError("Safe preparation cleanup failed")
    if failure is not None:
        report["error"] = f"{type(failure).__name__}: {failure}"
        raise SmokeExecutionError(str(failure), report) from failure
    return report


def execute_and_write_safe_prepare_report(config: Mapping[str, Any], resource_manager: Any, *, config_path: str | Path, hardware_root: str | Path) -> dict[str, Any]:
    report_path = resolve_report_path(config_path, config["result_path"], hardware_root) if config.get("result_path") else None
    try:
        report = run_safe_prepare_smoke(config, resource_manager)
    except SmokeExecutionError as exc:
        if report_path:
            write_report(exc.report, report_path)
        raise
    if report_path:
        write_report(report, report_path)
    return report
