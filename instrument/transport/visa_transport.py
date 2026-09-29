"""SCPI transport adapter for an already opened VISA resource."""

from __future__ import annotations

from typing import Any
import math

from pyvisa import constants

from .scpi_transport import (
    ScpiTransportError,
    ScpiTransportTimeoutError,
    _command_summary,
    _validate_command,
)


class VisaScpiTransport:
    """Translate a VISA resource into the small :class:`ScpiTransport` API.

    Resource ownership stays here: the caller supplies the resource and this
    adapter closes it exactly once.  Closing is terminal even if the underlying
    resource reports an error.  It deliberately does not create a VISA
    ResourceManager, which keeps driver and unit tests hardware-independent.
    """

    def __init__(self, resource: Any, *, timeout_s: float = 5.0) -> None:
        if not math.isfinite(float(timeout_s)) or timeout_s <= 0:
            raise ValueError("timeout_s 必须是有限正数")
        self.resource = resource
        self.timeout_s = float(timeout_s)
        self.closed = False
        self.close_count = 0
        try:
            self.resource.timeout = _timeout_ms(self.timeout_s)
        except Exception as exc:
            raise ScpiTransportError(
                "无法配置 VISA 超时",
                operation="configure_timeout",
                original_error=exc,
            ) from exc

    def write(self, command: str) -> None:
        command = _validate_command(command)
        self._ensure_open()
        try:
            self.resource.write(command)
        except Exception as exc:
            raise _translate_error(exc, command, "write") from exc

    def set_timeout_s(self, timeout_s: float) -> None:
        timeout_s = float(timeout_s)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s 必须是有限正数")
        self._ensure_open()
        try:
            self.resource.timeout = _timeout_ms(timeout_s)
        except Exception as exc:
            raise ScpiTransportError(
                "无法配置 VISA 超时",
                operation="configure_timeout",
                original_error=exc,
            ) from exc
        self.timeout_s = timeout_s

    def query(self, command: str) -> str:
        command = _validate_command(command)
        self._ensure_open()
        try:
            response = self.resource.query(command)
        except Exception as exc:
            raise _translate_error(exc, command, "query") from exc
        if not isinstance(response, str):
            raise ScpiTransportError(
                "SCPI 查询响应必须是字符串",
                operation="query",
                command=command,
            )
        return response

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.close_count += 1
        try:
            self.resource.close()
        except Exception as exc:
            raise _translate_error(exc, "close", "close") from exc

    def _ensure_open(self) -> None:
        if self.closed:
            raise ScpiTransportError("SCPI transport 已关闭", operation="transport")


def _translate_error(error: Exception, command: str, operation: str) -> ScpiTransportError:
    if getattr(error, "error_code", None) == constants.StatusCode.error_timeout:
        return ScpiTransportTimeoutError(
            f"SCPI 操作超时: {_command_summary(command)}",
            operation=operation,
            command=command,
            original_error=error,
        )
    text = str(error).lower()
    if isinstance(error, TimeoutError) or "timeout" in text or "timed out" in text:
        return ScpiTransportTimeoutError(
            f"SCPI 操作超时: {_command_summary(command)}",
            operation=operation,
            command=command,
            original_error=error,
        )
    return ScpiTransportError(
        f"SCPI 通信失败: {_command_summary(command)}",
        operation=operation,
        command=command,
        original_error=error,
    )


def _timeout_ms(timeout_s: float) -> int:
    return max(1, int(timeout_s * 1000))
