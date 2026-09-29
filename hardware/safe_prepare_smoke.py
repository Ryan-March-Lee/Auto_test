"""Run the real-device preparation smoke without enabling any output."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from hardware.read_only_smoke import SmokeExecutionError, resolve_report_path, write_report


_OUTPUT_TOKENS = {"OUTP", "OUTPUT", "RF", "OUT", "STATE"}
_ALLOWED_SETUP_ROOTS = {
    "signal_generator": {"FREQ", "FREQUENCY", "POW", "POWER", "OUTP", "OUTPUT"},
    "spectrum_analyzer": {"FREQ", "FREQUENCY", "BAND", "BANDWIDTH", "SPAN", "*CLS", "CLS"},
    "power_supply": {"VOLT", "VOLTAGE", "CURR", "CURRENT", "OUTP", "OUTPUT"},
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
    if allowed_roots is not None and path[0].lstrip("*") not in allowed_roots and parts[0].upper() not in allowed_roots:
        raise ValueError(f"SCPI command is outside the allowed command set: {command!r}")
    argument = parts[1].strip().upper() if len(parts) == 2 else ""
    output_control = bool(_OUTPUT_TOKENS.intersection(path))
    if output_control:
        is_query = parts[0].endswith("?") and not argument
        if not is_query and (not allow_output_off or argument not in {"OFF", "0"}):
            raise ValueError(f"Output-control command is not allowed: {command!r}")
    return command.strip()


def _response(resource: Any, command: str, clock: Any) -> dict[str, Any]:
    started = clock()
    value = resource.query(command).strip()
    return {"command": command, "response": value, "elapsed_ms": round((clock() - started) * 1000, 3)}


def _expected_matches(actual: str, expected: Any) -> bool:
    if expected is None:
        return True
    return actual.split(",", 1)[0].strip() == str(expected).strip()


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
        if not isinstance(raw, Mapping) or not raw.get("address"):
            raise ValueError(f"{name} requires a device address")
        checks = raw.get("state_queries")
        if not isinstance(checks, list) or not checks:
            raise ValueError(f"{name} requires safety state_queries")
        normalized_checks = []
        for check in checks:
            if not isinstance(check, Mapping) or "expected" not in check:
                raise ValueError(f"{name} safety queries require an explicit expected safe value")
            query = _validate_command(check.get("command"))
            if not query.endswith("?"):
                raise ValueError(f"{name} safety command must be a query: {query!r}")
            query_root = query[:-1].upper().split(":", 1)[0].lstrip("*")
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
            _OUTPUT_TOKENS.intersection(query["command"].upper().rstrip("?").replace(":", " ").split())
            for query in normalized_checks
        )
        if output_check_required and not has_output_check:
            raise ValueError(f"{name} safety queries must verify output state")
        normalized[name] = {
            **raw,
            "setup_commands": setup_commands,
            "cleanup_commands": cleanup_commands,
            "state_queries": normalized_checks,
        }
    return normalized


def run_safe_prepare_smoke(config: Mapping[str, Any], resource_manager: Any, *, clock=time.monotonic) -> dict[str, Any]:
    """Prepare instruments while requiring all configured outputs to remain off."""
    devices = _validate_config(config)

    report: dict[str, Any] = {"mode": "safe_prepare", "devices": {}, "resources_closed": False}
    resources: list[tuple[str, Any, list[str]]] = []
    failure: Exception | None = None
    try:
        for name, device in devices.items():
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
                initial_state = [_response(resource, check["command"], clock) for check in device["state_queries"]]
                result["initial_state"] = initial_state
                _check_safe_state(name, initial_state, device["state_queries"])
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
                checks = devices[name]["state_queries"]
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
