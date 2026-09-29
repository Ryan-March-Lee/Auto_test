"""Execute the deliberately read-only part of the hardware smoke test."""

from __future__ import annotations

import time
import json
from pathlib import Path
from typing import Any, Mapping


READ_ONLY_COMMANDS = frozenset({"*IDN?", "SYST:ERR?", "OUTP?"})


class SmokeExecutionError(RuntimeError):
    def __init__(self, message: str, report: Mapping[str, Any]):
        super().__init__(message)
        self.report = dict(report)


def _query(resource: Any, command: str, clock) -> dict[str, Any]:
    if command not in READ_ONLY_COMMANDS or not command.endswith("?"):
        raise ValueError(f"Unsupported non-read-only smoke command: {command!r}")
    started = clock()
    response = resource.query(command).strip()
    return {
        "command": command,
        "response": response,
        "elapsed_ms": round((clock() - started) * 1000, 3),
    }


def _assert_expected_responses(name: str, device: Mapping[str, Any], queries: list[dict[str, Any]]) -> None:
    expected = device.get("expected_responses", {})
    for command, expected_text in expected.items():
        actual = next((item["response"] for item in queries if item["command"] == command), None)
        if actual is None:
            raise AssertionError(f"{name} did not execute expected query {command!r}")
        if command == "SYST:ERR?":
            actual_value = actual.split(",", 1)[0].strip()
            matches = actual_value == str(expected_text).strip()
        else:
            matches = actual == str(expected_text).strip()
        if not matches:
            raise AssertionError(
                f"{name} response for {command!r} does not match {expected_text!r}: {actual!r}"
            )


def run_read_only_smoke(
    config: Mapping[str, Any],
    resource_manager: Any,
    *,
    clock=time.monotonic,
) -> dict[str, Any]:
    """Query configured devices without sending any state-changing command.

    ``resource_manager`` is injectable so the protocol can be tested without
    opening VISA. Every opened resource is closed even when a query fails.
    """
    report: dict[str, Any] = {"devices": {}, "resources_closed": False}
    resources: list[tuple[str, Any]] = []
    failure: Exception | None = None
    try:
        for name, device in config["devices"].items():
            address = device["address"]
            resource = resource_manager.open_resource(
                address, open_timeout=int(device.get("timeout_ms", 5000))
            )
            resources.append((name, resource))
            resource.timeout = int(device.get("timeout_ms", 5000))
            result: dict[str, Any] = {"address": address, "queries": []}
            try:
                for command in device.get("queries", ["*IDN?"]):
                    result["queries"].append(_query(resource, command, clock))
                _assert_expected_responses(name, device, result["queries"])
                identity = next(
                    (item["response"] for item in result["queries"] if item["command"] == "*IDN?"),
                    "",
                )
                expected_model = device.get("model", "")
                if expected_model and not expected_model.startswith("REPLACE_WITH_"):
                    if expected_model not in identity:
                        raise AssertionError(
                            f"{name} identity does not contain configured model {expected_model!r}: {identity!r}"
                        )
                result["identity"] = identity
                result["closed"] = False
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"
                report["devices"][name] = result
                failure = exc
                break
            report["devices"][name] = result
    except Exception as exc:
        failure = exc
    finally:
        close_errors = []
        for name, resource in reversed(resources):
            try:
                resource.close()
                if name in report["devices"]:
                    report["devices"][name]["closed"] = True
            except Exception as exc:  # preserve the original query failure
                close_errors.append(f"{type(exc).__name__}: {exc}")
                if name in report["devices"]:
                    report["devices"][name]["closed"] = False
        try:
            resource_manager.close()
        except Exception as exc:
            close_errors.append(f"{type(exc).__name__}: {exc}")
        report["resources_closed"] = not close_errors
        if close_errors:
            report["close_errors"] = close_errors
            if failure is None:
                failure = RuntimeError("Failed to close one or more VISA resources")
    if failure is not None:
        report["error"] = f"{type(failure).__name__}: {failure}"
        raise SmokeExecutionError(str(failure), report) from failure
    return report


def write_report(report: Mapping[str, Any], path: str | Path) -> None:
    """Persist a local-only report without exposing VISA addresses in logs."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def resolve_report_path(config_path: str | Path, report_path: str | Path, hardware_root: str | Path) -> Path:
    configured = Path(report_path)
    if configured.is_absolute():
        raise ValueError("Hardware smoke report_path must be relative")
    root = Path(hardware_root).resolve()
    destination = (Path(config_path).resolve().parent / configured).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("Hardware smoke report_path must resolve inside hardware/")
    return destination


def execute_and_write_report(
    config: Mapping[str, Any],
    resource_manager: Any,
    *,
    config_path: str | Path,
    hardware_root: str | Path,
) -> dict[str, Any]:
    report_path = None
    if config.get("result_path"):
        report_path = resolve_report_path(config_path, config["result_path"], hardware_root)
    try:
        report = run_read_only_smoke(config, resource_manager)
    except SmokeExecutionError as exc:
        if report_path:
            write_report(exc.report, report_path)
        raise
    if report_path:
        write_report(report, report_path)
    return report
