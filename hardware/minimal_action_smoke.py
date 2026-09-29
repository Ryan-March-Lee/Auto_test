"""Execute one tightly bounded RF smoke action on real instruments."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Mapping

from hardware.read_only_smoke import SmokeExecutionError, resolve_report_path, write_report
from hardware.safe_prepare_smoke import _expected_matches, _validate_command

_DEVICES = ("signal_generator", "spectrum_analyzer", "power_supply")
_QUERY_ROOTS = {
    "signal_generator": {"OUTP", "OUTPUT", "RF"},
    "spectrum_analyzer": {"FREQ", "FREQUENCY", "BAND", "BANDWIDTH", "SPAN", "POW", "POWER"},
    "power_supply": {"OUTP", "OUTPUT"},
}
_SAFE_SETUP_ROOTS = {
    "signal_generator": {"OUTP", "OUTPUT"},
    "spectrum_analyzer": {"FREQ", "FREQUENCY", "BAND", "BANDWIDTH", "SPAN", "CALC"},
    "power_supply": {"VOLT", "VOLTAGE", "CURR", "CURRENT", "VOLT:PROT", "CURR:PROT", "OUTP", "OUTPUT"},
}
_SAFE_CLEANUP_ROOTS = {
    **_SAFE_SETUP_ROOTS,
    "spectrum_analyzer": _SAFE_SETUP_ROOTS["spectrum_analyzer"] | {"CLS"},
}


def _query(resource: Any, command: str, clock: Any, deadline: float | None = None) -> dict[str, Any]:
    if deadline is not None and clock() >= deadline:
        raise TimeoutError("hardware smoke exceeded max_duration_s")
    started = clock()
    value = resource.query(command).strip()
    if deadline is not None and clock() > deadline:
        raise TimeoutError("hardware smoke exceeded max_duration_s")
    return {"command": command, "response": value, "elapsed_ms": round((clock() - started) * 1000, 3)}


def _validate_queries(name: str, values: Any, *, require_expected: bool) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{name} requires non-empty query configuration")
    normalized = []
    for item in values:
        if not isinstance(item, Mapping):
            raise ValueError(f"{name} query must be an object")
        command = item.get("command")
        if not isinstance(command, str) or not command.strip() or "\n" in command or "\r" in command:
            raise ValueError(f"Invalid {name} query: {command!r}")
        command = command.strip()
        upper = command.upper()
        query_path = upper.rstrip("?").lstrip(":").split(":", 1)[0]
        channel_query = name == "power_supply" and upper in {"OUTP? CH1", "OUTP? CH2", ":OUTPUT? CH1", ":OUTPUT? CH2"}
        allowed = channel_query or (query_path in _QUERY_ROOTS[name] and upper.endswith("?") and " " not in upper)
        if not allowed or (not channel_query and not upper.endswith("?")):
            raise ValueError(f"{name} query is outside the read-only command set: {command!r}")
        if not command.endswith("?") and not channel_query:
            raise ValueError(f"{name} command must be a query: {command!r}")
        if require_expected and ("expected" not in item or item["expected"] is None):
            raise ValueError(f"{name} safety queries require an explicit expected value")
        normalized.append({"command": command, "expected": item.get("expected")})
    return normalized


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _validate_config(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if config.get("environment") != "hardware_smoke" or config.get("smoke_mode") != "minimal_action":
        raise ValueError("Hardware configuration must select smoke_mode=minimal_action")
    if config.get("require_empty_setup") is not True or config.get("require_user_confirmation") is not True:
        raise ValueError("Minimal action smoke requires empty setup and user confirmation")
    if config.get("max_action_count") != 1:
        raise ValueError("Minimal action smoke must have max_action_count=1")
    for key in ("max_duration_s", "action_timeout_s", "cleanup_timeout_s"):
        value = _number(config.get(key), key)
        if value <= 0:
            raise ValueError(f"{key} must be positive")
    if _number(config["action_timeout_s"], "action_timeout_s") > _number(config["max_duration_s"], "max_duration_s"):
        raise ValueError("action_timeout_s cannot exceed max_duration_s")

    devices = config.get("devices")
    if not isinstance(devices, Mapping) or set(devices) != set(_DEVICES):
        raise ValueError(f"Minimal action requires exactly these devices: {list(_DEVICES)}")
    normalized: dict[str, dict[str, Any]] = {}
    for name in _DEVICES:
        raw = devices[name]
        if not isinstance(raw, Mapping) or not raw.get("address"):
            raise ValueError(f"{name} requires a device address")
        timeout_ms = int(raw.get("timeout_ms", 5000))
        if timeout_ms <= 0:
            raise ValueError(f"{name} timeout_ms must be positive")
        state = _validate_queries(name, raw.get("state_queries"), require_expected=True)
        measurements_raw = raw.get("measurement_queries", [])
        measurements = _validate_queries(name, measurements_raw, require_expected=False) if measurements_raw else []
        setup = raw.get("setup_commands", [])
        if not isinstance(setup, list):
            raise ValueError(f"{name} setup_commands must be a list")
        setup = [_validate_command(c, allow_output_off=True, allowed_roots=_SAFE_SETUP_ROOTS[name]) for c in setup]
        cleanup = raw.get("cleanup_commands", [])
        if not isinstance(cleanup, list) or (name not in {"signal_generator", "power_supply"} and not cleanup):
            raise ValueError(f"{name} requires cleanup_commands")
        if name == "power_supply":
            cleanup = []
        else:
            cleanup = [_validate_command(c, allow_output_off=True, allowed_roots=_SAFE_CLEANUP_ROOTS[name]) for c in cleanup]
        if name == "signal_generator":
            if cleanup:
                raise ValueError("signal_generator cleanup is managed by the smoke executor")
            if not any(q["command"].upper().rstrip("?").split(":")[-1] in {"OUTP", "OUTPUT", "STAT"} and q["expected"] in {"0", 0, False, "OFF"} for q in state):
                raise ValueError("signal_generator state_queries must verify RF/output is off")
        if name == "power_supply":
            power = raw.get("power_off_sequence")
            if not isinstance(power, list) or len(power) != 2:
                raise ValueError("power_supply requires power_off_sequence [Drain off, Gate off]")
            commands = []
            for command in power:
                if not isinstance(command, str) or "\n" in command or "\r" in command:
                    raise ValueError("power_off_sequence commands must be single-line SCPI")
                command = command.strip()
                if not command.upper().startswith(("OUTP ", ":OUTPUT ")) or not command.upper().endswith((",OFF", ",0", " OFF", " 0")):
                    raise ValueError("power_off_sequence commands must turn outputs off")
                commands.append(command)
            if not any(token in commands[0].upper() for token in ("CH2,OFF", "CH2,0", "CH2 OFF", "CH2 0")) or not any(token in commands[1].upper() for token in ("CH1,OFF", "CH1,0", "CH1 OFF", "CH1 0")):
                raise ValueError("power_off_sequence must disable Drain CH2 before Gate CH1")
            cleanup = commands
            for channel in ("CH2", "CH1"):
                if not any(channel in q["command"].upper() and q["expected"] in {"0", 0, False, "OFF"} for q in state):
                    raise ValueError(f"power_supply state_queries must verify {channel} is off")
        normalized[name] = {**raw, "timeout_ms": timeout_ms, "setup_commands": setup,
                            "cleanup_commands": cleanup, "state_queries": state, "measurement_queries": measurements}

    rf = normalized["signal_generator"]
    params = rf.get("rf_parameters")
    if not isinstance(params, Mapping):
        raise ValueError("signal_generator.rf_parameters is required")
    for field in ("frequency_hz", "min_frequency_hz", "max_frequency_hz", "power_dbm", "max_power_dbm"):
        params = dict(params)
        params[field] = _number(params.get(field), f"rf_parameters.{field}")
    if not (0 < params["min_frequency_hz"] <= params["frequency_hz"] <= params["max_frequency_hz"]):
        raise ValueError("frequency_hz must be within the approved positive frequency range")
    if not (params["power_dbm"] <= params["max_power_dbm"] <= -10):
        raise ValueError("power_dbm must not exceed max_power_dbm, and max_power_dbm must be <= -10 dBm")
    if params["power_dbm"] > -10:
        raise ValueError("minimal action RF power is capped at -10 dBm")
    rf["rf_parameters"] = params
    if not any(q["command"].upper().rstrip("?").split(":")[-1] in {"OUTP", "OUTPUT", "STAT"} and q["expected"] in {"0", 0, False, "OFF"} for q in rf["state_queries"]):
        raise ValueError("signal_generator state_queries must include an explicitly-off output check")
    sa_measurements = normalized["spectrum_analyzer"]["measurement_queries"]
    if len(sa_measurements) != 1 or sa_measurements[0]["command"].upper() not in {"POW?", "CALC:MARK1:Y?"}:
        raise ValueError("spectrum_analyzer must configure exactly one supported power measurement query")
    if normalized["power_supply"]["cleanup_commands"] != normalized["power_supply"]["power_off_sequence"]:
        raise ValueError("power_supply cleanup_commands must match power_off_sequence")
    normalized["signal_generator"]["action_commands"] = ["OUTP ON"]
    return normalized


def _bounded_operation(resource: Any, timeout_ms: int, deadline: float, clock: Any, operation: Any) -> Any:
    remaining_ms = int((deadline - clock()) * 1000)
    if remaining_ms <= 0:
        raise TimeoutError("hardware smoke exceeded its operation deadline")
    resource.timeout = max(1, min(timeout_ms, remaining_ms))
    return operation()


def run_minimal_action_smoke(config: Mapping[str, Any], resource_manager: Any, *, clock=time.monotonic) -> dict[str, Any]:
    devices = _validate_config(config)
    deadline = clock() + float(config["max_duration_s"])
    report: dict[str, Any] = {"mode": "minimal_action", "events": [{"type": "start"}],
                              "action_count": 0, "devices": {}, "resources_closed": False}
    resources: dict[str, Any] = {}
    failure: Exception | None = None
    rf_may_be_on = False
    try:
        for name in _DEVICES:
            device = devices[name]
            open_ms = min(device["timeout_ms"], max(1, int((deadline - clock()) * 1000)))
            resource = resource_manager.open_resource(device["address"], open_timeout=open_ms)
            resources[name] = resource
            resource.timeout = open_ms
            result = report["devices"][name] = {"writes": [], "queries": [], "cleanup": []}
            identity = _bounded_operation(resource, device["timeout_ms"], deadline, clock, lambda: resource.query("*IDN?").strip())
            result["identity"] = identity
            report["events"].append({"type": "identity", "device": name})
            expected_model = device.get("model", "")
            if expected_model and expected_model not in identity:
                raise AssertionError(f"{name} identity does not contain configured model {expected_model!r}")
            for check in device["state_queries"]:
                response = _bounded_operation(resource, device["timeout_ms"], deadline, clock,
                                              lambda c=check["command"]: resource.query(c).strip())
                result["queries"].append({"command": check["command"], "response": response})
                if not _expected_matches(response, check["expected"]):
                    raise AssertionError(f"{name} initial state {check['command']!r} was {response!r}, expected {check['expected']!r}")
            for command in device["setup_commands"]:
                _bounded_operation(resource, device["timeout_ms"], deadline, clock, lambda c=command: resource.write(c))
                result["writes"].append(command)
            if name == "spectrum_analyzer":
                expected_center = f"FREQ:CENT {devices['signal_generator']['rf_parameters']['frequency_hz']:.12g}"
                # Analyzer center is derived from the approved RF frequency.
                result["writes"].append(expected_center)
                _bounded_operation(resource, device["timeout_ms"], deadline, clock,
                                   lambda c=expected_center: resource.write(c))

        params = devices["signal_generator"]["rf_parameters"]
        # Values are numeric, range-checked, and rendered here rather than accepted as arbitrary SCPI.
        for command in (f"FREQ {params['frequency_hz']:.12g}", f"POW {params['power_dbm']:.12g}"):
            _bounded_operation(resources["signal_generator"], devices["signal_generator"]["timeout_ms"], deadline,
                               clock, lambda c=command: resources["signal_generator"].write(c))
            report["devices"]["signal_generator"]["writes"].append(command)
        sg = resources["signal_generator"]
        action_deadline = min(deadline, clock() + float(config["action_timeout_s"]))
        rf_may_be_on = True
        _bounded_operation(sg, devices["signal_generator"]["timeout_ms"], action_deadline, clock, lambda: sg.write("OUTP ON"))
        report["action_count"] = 1
        report["events"].append({"type": "action", "device": "signal_generator", "command": "OUTP ON"})

        try:
            # One analyzer read is the only operation while RF is enabled.
            item = devices["spectrum_analyzer"]["measurement_queries"][0]
            if item["command"].upper() == "CALC:MARK1:Y?":
                _bounded_operation(resources["spectrum_analyzer"], devices["spectrum_analyzer"]["timeout_ms"], action_deadline,
                                    clock, lambda: resources["spectrum_analyzer"].write("CALC:MARK1:MAX"))
                report["devices"]["spectrum_analyzer"]["writes"].append("CALC:MARK1:MAX")
            response = _bounded_operation(resources["spectrum_analyzer"], devices["spectrum_analyzer"]["timeout_ms"],
                                          action_deadline, clock, lambda: resources["spectrum_analyzer"].query(item["command"]).strip())
            report["devices"]["spectrum_analyzer"]["measurements"] = [{"command": item["command"], "response": response}]
            report["events"].append({"type": "measurement", "device": "spectrum_analyzer"})
            numeric = float(response)
            minimum = item.get("min_dbm")
            maximum = item.get("max_dbm")
            in_range = (minimum is None or numeric >= float(minimum)) and (maximum is None or numeric <= float(maximum))
            if item.get("expected") is not None and not _expected_matches(response, item["expected"]):
                raise AssertionError(f"spectrum analyzer measurement {item['command']!r} returned {response!r}")
            if not in_range:
                raise AssertionError(f"spectrum analyzer measurement {item['command']!r} returned {response!r}")
        finally:
            # Turn RF off immediately after the one bounded measurement, even when it fails.
            sg.timeout = max(1, min(devices["signal_generator"]["timeout_ms"], int(float(config["cleanup_timeout_s"]) * 1000)))
            sg.write("OUTP OFF")
            report["devices"]["signal_generator"]["cleanup"].append("OUTP OFF")
            report["events"].append({"type": "cleanup", "device": "signal_generator", "command": "OUTP OFF"})
            rf_may_be_on = False

    except Exception as exc:
        failure = exc
    finally:
        cleanup_ms = max(1, int(float(config["cleanup_timeout_s"]) * 1000))
        close_errors: list[str] = []
        sg = resources.get("signal_generator")
        if sg is not None and rf_may_be_on:
            try:
                sg.timeout = cleanup_ms
                sg.write("OUTP OFF")
                report["devices"]["signal_generator"].setdefault("cleanup", []).append("OUTP OFF")
                report["events"].append({"type": "cleanup", "device": "signal_generator", "command": "OUTP OFF"})
            except Exception as exc:
                close_errors.append(f"signal_generator emergency RF off: {type(exc).__name__}: {exc}")
        ps = resources.get("power_supply")
        if ps is not None:
            for command in devices["power_supply"]["cleanup_commands"]:
                try:
                    ps.timeout = cleanup_ms; ps.write(command)
                    report["devices"]["power_supply"].setdefault("cleanup", []).append(command)
                    report["events"].append({"type": "cleanup", "device": "power_supply", "command": command})
                except Exception as exc:
                    close_errors.append(f"power_supply cleanup {command!r}: {type(exc).__name__}: {exc}")
        for name in ("power_supply", "spectrum_analyzer"):
            resource = resources.get(name)
            if resource is None:
                continue
            result = report["devices"][name]
            if name == "spectrum_analyzer":
                for command in devices[name]["cleanup_commands"]:
                    try:
                        resource.timeout = cleanup_ms
                        resource.write(command)
                        result.setdefault("cleanup", []).append(command)
                        report["events"].append({"type": "cleanup", "device": name, "command": command})
                    except Exception as exc:
                        close_errors.append(f"{name} cleanup {command!r}: {type(exc).__name__}: {exc}")
            try:
                resource.timeout = cleanup_ms
                final = []
                for item in devices[name]["state_queries"]:
                    response = resource.query(item["command"]).strip()
                    final.append({"command": item["command"], "response": response})
                    if not _expected_matches(response, item["expected"]):
                        raise AssertionError(f"{item['command']} final state is {response!r}")
                result["final_state"] = final; result["safe_after_cleanup"] = True
            except Exception as exc:
                result["safe_after_cleanup"] = False
                close_errors.append(f"{name} final state: {type(exc).__name__}: {exc}")
        if sg is not None:
            try:
                sg.timeout = cleanup_ms
                response = sg.query("OUTP?").strip()
                report["devices"]["signal_generator"]["final_state"] = [{"command": "OUTP?", "response": response}]
                if response.split(",", 1)[0].strip() not in {"0", "OFF"}:
                    raise AssertionError(f"RF output remains enabled: {response!r}")
                report["devices"]["signal_generator"]["safe_after_cleanup"] = True
            except Exception as exc:
                report["devices"]["signal_generator"]["safe_after_cleanup"] = False
                close_errors.append(f"signal_generator final state: {type(exc).__name__}: {exc}")
        for name in ("signal_generator", "power_supply", "spectrum_analyzer"):
            resource = resources.get(name)
            if resource is None:
                continue
            try:
                resource.timeout = cleanup_ms; resource.close()
                report["devices"][name]["closed"] = True
            except Exception as exc:
                report["devices"][name]["closed"] = False
                close_errors.append(f"{name} close: {type(exc).__name__}: {exc}")
        try:
            resource_manager.close()
        except Exception as exc:
            close_errors.append(f"manager close: {type(exc).__name__}: {exc}")
        report["resources_closed"] = not close_errors
        if close_errors:
            report["close_errors"] = close_errors
            if failure is None:
                failure = RuntimeError("Minimal action smoke cleanup failed")
        report["events"].append({"type": "end", "success": failure is None and not close_errors})
    if failure is not None:
        report["error"] = f"{type(failure).__name__}: {failure}"
        raise SmokeExecutionError(str(failure), report) from failure
    return report


def execute_and_write_minimal_action_report(config: Mapping[str, Any], resource_manager: Any, *, config_path: str | Path, hardware_root: str | Path) -> dict[str, Any]:
    report_path = resolve_report_path(config_path, config["result_path"], hardware_root) if config.get("result_path") else None
    try:
        report = run_minimal_action_smoke(config, resource_manager)
    except SmokeExecutionError as exc:
        if report_path:
            write_report(exc.report, report_path)
        raise
    if report_path:
        write_report(report, report_path)
    return report
