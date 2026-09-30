"""Bounded minimal action through the application composition path."""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any, Mapping

from app.gui_runtime import connect_instruments
from config_io import load_config_file
from hardware.read_only_smoke import SmokeExecutionError, resolve_report_path, write_report


def _number(config: Mapping[str, Any], key: str) -> float:
    value = config.get(key)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _remaining(deadline: float, timeout_s: float, clock) -> float:
    remaining = min(float(timeout_s), deadline - clock())
    if remaining <= 0:
        raise TimeoutError("new-path hardware smoke exceeded its deadline")
    return remaining


def _resource(driver: Any) -> Any:
    return getattr(driver.transport, "resource", driver.transport)


def _identity(driver: Any, timeout_s: float | None = None) -> str:
    transport = driver.transport
    if timeout_s is not None:
        transport.set_timeout_s(timeout_s)
    return transport.query("*IDN?").strip()


def _power_drivers(port: Any) -> list[Any]:
    return list(getattr(port.power_supply, "supplies", ()))


def _expected_match(response: Any, expected: Any) -> bool:
    actual = str(response).strip().upper()
    expected_text = str(expected).strip().upper()
    return actual == expected_text or (actual in {"0", "OFF"} and expected_text in {"0", "OFF"})


def _verify_queries(
    name: str, driver: Any, checks: list[Mapping[str, Any]], timeout_s: float | None = None
) -> list[dict[str, str]]:
    result = []
    transport = driver.transport
    if timeout_s is not None:
        transport.set_timeout_s(timeout_s)
    for check in checks:
        command = str(check["command"])
        response = transport.query(command).strip()
        result.append({"command": command, "response": response})
        if "expected" in check and not _expected_match(response, check["expected"]):
            raise AssertionError(f"{name} {command} returned {response!r}, expected {check['expected']!r}")
    return result


def _assert_model(name: str, identity: str, configured: Any) -> None:
    model = str(configured or "").strip()
    if model and not model.startswith("REPLACE_WITH_") and model not in identity:
        raise AssertionError(f"{name} identity does not contain configured model {model!r}: {identity!r}")


def _configured_span_hz(analyzer_config: Mapping[str, Any]) -> float:
    for command in analyzer_config.get("setup_commands", []):
        match = re.fullmatch(r"FREQ:SPAN\s+([0-9.eE+-]+)", str(command).strip(), re.IGNORECASE)
        if match and float(match.group(1)) > 0:
            return float(match.group(1))
    raise ValueError("spectrum_analyzer.setup_commands must contain a positive FREQ:SPAN")


def _assert_address_match(smoke_config: Mapping[str, Any], app_config: Mapping[str, Any]) -> None:
    smoke_devices = smoke_config["devices"]
    app_devices = app_config["instruments"]
    for name in ("signal_generator", "spectrum_analyzer"):
        smoke_address = smoke_devices[name].get("address")
        app_address = app_devices[name].get("address")
        if smoke_address != app_address:
            raise ValueError(f"{name} address differs between smoke and application configuration")


def run_new_path_minimal_action(
    smoke_config: Mapping[str, Any], application_config_path: str | Path, *, clock=time.monotonic
) -> dict[str, Any]:
    if smoke_config.get("smoke_mode") != "minimal_action":
        raise ValueError("new-path smoke requires smoke_mode=minimal_action")
    if smoke_config.get("max_action_count") != 1:
        raise ValueError("new-path smoke requires max_action_count=1")
    for key in ("max_duration_s", "action_timeout_s", "cleanup_timeout_s"):
        _number(smoke_config, key)
    if _number(smoke_config, "action_timeout_s") > _number(smoke_config, "max_duration_s"):
        raise ValueError("action_timeout_s cannot exceed max_duration_s")

    app_config = load_config_file(application_config_path)
    _assert_address_match(smoke_config, app_config)
    rf_config = smoke_config["devices"]["signal_generator"]
    analyzer_config = smoke_config["devices"]["spectrum_analyzer"]
    power_config = smoke_config["devices"]["power_supply"]
    params = rf_config["rf_parameters"]
    measurements = analyzer_config.get("measurement_queries", [])
    if len(measurements) != 1 or measurements[0].get("command", "").upper() != "CALC:MARK1:Y?":
        raise ValueError("new-path smoke requires exactly one CALC:MARK1:Y? measurement")
    measurement = measurements[0]
    report: dict[str, Any] = {
        "mode": "minimal_action_new_assembly", "action_count": 0,
        "events": [{"type": "start"}], "devices": {}, "resources_closed": False,
        "operations": [],
    }
    deadline = clock() + _number(smoke_config, "max_duration_s")
    action_timeout = _number(smoke_config, "action_timeout_s")
    cleanup_timeout = _number(smoke_config, "cleanup_timeout_s")
    port = None
    power_drivers: list[Any] = []
    failure: Exception | None = None
    cleanup_errors: list[str] = []

    def record(operation: str, command: str, response: str | None = None) -> None:
        item = {"operation": operation, "command": command}
        if response is not None:
            item["response"] = response
        report["operations"].append(item)

    try:
        port = connect_instruments(str(application_config_path), recorder=record)
        power_drivers = _power_drivers(port)
        identities = {
            "signal_generator": _identity(port.signal_generator, _remaining(deadline, action_timeout, clock)),
            "spectrum_analyzer": _identity(port.spectrum_analyzer, _remaining(deadline, action_timeout, clock)),
        }
        _assert_model("signal_generator", identities["signal_generator"], rf_config.get("model"))
        _assert_model("spectrum_analyzer", identities["spectrum_analyzer"], analyzer_config.get("model"))
        report["devices"] = {
            name: {"identity": identity, "queries": [], "cleanup": []}
            for name, identity in identities.items()
        }
        power_results = []
        for driver in power_drivers:
            identity = _identity(driver, _remaining(deadline, action_timeout, clock))
            _assert_model("power_supply", identity, power_config.get("model"))
            power_results.append({"identity": identity, "queries": [], "cleanup": [],
                                  "address": getattr(_resource(driver), "resource_name", getattr(_resource(driver), "address", ""))})
        report["devices"]["power_supply"] = power_results
        report["devices"]["signal_generator"]["queries"] = _verify_queries(
            "signal_generator", port.signal_generator, rf_config.get("state_queries", []),
            _remaining(deadline, action_timeout, clock),
        )
        for index, driver in enumerate(power_drivers):
            report["devices"]["power_supply"][index]["queries"] = _verify_queries(
                "power_supply", driver, power_config.get("state_queries", []),
                _remaining(deadline, action_timeout, clock),
            )
        analyzer_checks = analyzer_config.get("state_queries", [])
        if analyzer_checks:
            report["devices"]["spectrum_analyzer"]["queries"] = _verify_queries(
                "spectrum_analyzer", port.spectrum_analyzer, analyzer_checks,
                _remaining(deadline, action_timeout, clock),
            )
        report["events"].append({"type": "assembled", "path": "app.gui_runtime.connect_instruments"})

        operation_timeout = _remaining(deadline, action_timeout, clock)
        port.set_frequency(float(params["frequency_hz"]) / 1e9, timeout_s=operation_timeout)
        port.set_span(float(_configured_span_hz(analyzer_config)) / 1e6, timeout_s=_remaining(deadline, action_timeout, clock))
        port.set_power(float(params["power_dbm"]), timeout_s=_remaining(deadline, action_timeout, clock))
        port.start_measurement()
        port.rf_output_on(timeout_s=_remaining(deadline, action_timeout, clock))
        report["action_count"] = 1
        report["events"].append({"type": "action", "device": "signal_generator", "command": "OUTP ON"})
        try:
            value = float(port.measure_power_with_average(timeout_s=_remaining(deadline, action_timeout, clock)))
            minimum, maximum = measurement.get("min_dbm"), measurement.get("max_dbm")
            if minimum is not None and value < float(minimum) or maximum is not None and value > float(maximum):
                raise AssertionError(f"measurement outside configured range: {value}")
            report["devices"]["spectrum_analyzer"]["measurements"] = [{"command": "CALC:MARK1:Y?", "response": value}]
            report["events"].append({"type": "measurement", "device": "spectrum_analyzer"})
        finally:
            port.rf_output_off(timeout_s=cleanup_timeout)
            report["devices"]["signal_generator"]["cleanup"].append("OUTP:STAT OFF")
            report["events"].append({"type": "cleanup", "device": "signal_generator", "command": "OUTP:STAT OFF"})
    except Exception as exc:
        failure = exc
    finally:
        if port is not None:
            try:
                cleanup_errors.extend(str(error) for error in port.emergency_power_off_all(timeout_s=cleanup_timeout))
            except Exception as exc:
                cleanup_errors.append(f"power emergency shutdown: {type(exc).__name__}: {exc}")
            for index, channel in getattr(port, "_last_emergency_power_off", []):
                if index < len(report.get("devices", {}).get("power_supply", [])):
                    report["devices"]["power_supply"][index].setdefault("cleanup", []).append(
                        f"OUTP {channel},OFF"
                    )
                    report["events"].append({
                        "type": "cleanup", "device": "power_supply", "index": index,
                        "command": f"OUTP {channel},OFF",
                    })
            for index, driver in enumerate(power_drivers):
                result = report.get("devices", {}).get("power_supply", [{}] * len(power_drivers))[index]
                try:
                    result["cleanup"] = ["OUTP CH2,OFF", "OUTP CH1,OFF"]
                    result["final_state"] = _verify_queries(
                        "power_supply", driver, power_config.get("state_queries", []), cleanup_timeout
                    )
                    result["safe_after_cleanup"] = True
                except Exception as exc:
                    cleanup_errors.append(f"power_supply[{index}] final state: {type(exc).__name__}: {exc}")
                    result["safe_after_cleanup"] = False
            for name in ("signal_generator", "spectrum_analyzer"):
                if name in report["devices"]:
                    driver = getattr(port, name)
                    if name == "signal_generator":
                        try:
                            final_state = _verify_queries(
                                name, driver, [{"command": "OUTP?", "expected": "0"}], cleanup_timeout
                            )
                            report["devices"][name]["final_state"] = final_state
                            report["devices"][name]["safe_after_cleanup"] = True
                        except Exception as exc:
                            cleanup_errors.append(f"{name} final state: {type(exc).__name__}: {exc}")
                            report["devices"][name]["safe_after_cleanup"] = False
                    else:
                        report["devices"][name]["safe_after_cleanup"] = True
            try:
                cleanup_errors.extend(str(error) for error in port.close_all(close_rf=True, timeout_s=cleanup_timeout))
            except Exception as exc:
                cleanup_errors.append(f"session close: {type(exc).__name__}: {exc}")
            session = getattr(port, "session", None)
            report["resources_closed"] = bool(getattr(session, "resources_closed", False))
            for index, driver in enumerate(power_drivers):
                if index < len(report.get("devices", {}).get("power_supply", [])):
                    report["devices"]["power_supply"][index]["closed"] = bool(getattr(driver, "closed", False))
            for name in ("signal_generator", "spectrum_analyzer"):
                if name in report["devices"]:
                    report["devices"][name]["closed"] = bool(getattr(getattr(port, name), "closed", False))
            report["events"].append({"type": "end", "success": failure is None and not cleanup_errors})
        if cleanup_errors:
            report["cleanup_errors"] = cleanup_errors
    if failure is not None:
        report["error"] = f"{type(failure).__name__}: {failure}"
        raise SmokeExecutionError(str(failure), report) from failure
    if cleanup_errors or not report["resources_closed"]:
        report["error"] = "new-path smoke cleanup failed"
        raise SmokeExecutionError(report["error"], report)
    return report


def execute_and_write_new_path_report(
    smoke_config: Mapping[str, Any], *, smoke_config_path: str | Path,
    application_config_path: str | Path, hardware_root: str | Path,
) -> dict[str, Any]:
    report_path = resolve_report_path(smoke_config_path, smoke_config["result_path"], hardware_root)
    try:
        report = run_new_path_minimal_action(smoke_config, application_config_path)
    except SmokeExecutionError as exc:
        write_report(exc.report, report_path)
        raise
    write_report(report, report_path)
    return report
