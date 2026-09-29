"""Shared state and validation helpers for SCPI drivers."""

from __future__ import annotations

from typing import Protocol
import math


class DriverTransport(Protocol):
    def set_timeout_s(self, timeout_s: float) -> None: ...
    def write(self, command: str) -> None: ...
    def query(self, command: str) -> str: ...
    def close(self) -> None: ...


class ScpiDriverBase:
    def __init__(self, transport: DriverTransport) -> None:
        self.transport = transport
        self.connected = False
        self.closed = False

    def connect(self, *, timeout_s: float = 10.0) -> None:
        self._set_timeout(timeout_s)
        if self.closed:
            raise RuntimeError("driver 已关闭")
        self.connected = True

    def close(self, *, timeout_s: float = 5.0) -> None:
        if self.closed:
            return
        self._set_timeout(timeout_s)
        self.closed = True
        self.connected = False
        self.transport.close()

    def _set_timeout(self, timeout_s: float) -> None:
        try:
            timeout_s = float(timeout_s)
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout_s 必须是数字") from exc
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s 必须是有限正数")
        self.transport.set_timeout_s(timeout_s)

    def _require_connected(self) -> None:
        if not self.connected or self.closed:
            raise RuntimeError("driver 未连接")

    @staticmethod
    def _positive(value: float, name: str) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是数字") from exc
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} 必须大于 0")
        return value

    @staticmethod
    def _non_negative(value: float, name: str) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是数字") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} 必须大于等于 0")
        return value

    def _read_float(self, command: str) -> float:
        self._require_connected()
        response = self.transport.query(command).strip()
        try:
            value = float(response)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"SCPI 查询返回非数字: {command}: {response}") from exc
        if not math.isfinite(value):
            raise ValueError(f"SCPI 查询返回非有限数字: {command}: {response}")
        return value
