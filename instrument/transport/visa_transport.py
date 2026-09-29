"""SCPI transport adapter for an already opened VISA resource."""

from __future__ import annotations

from typing import Any

from pyvisa import constants

from .scpi_transport import ScpiTransportError, ScpiTransportTimeoutError


class VisaScpiTransport:
    """Translate a VISA resource into the small :class:`ScpiTransport` API.

    Resource ownership stays here: the caller supplies the resource and this
    adapter closes it exactly once.  Closing is terminal even if the underlying
    resource reports an error.  It deliberately does not create a VISA
    ResourceManager, which keeps driver and unit tests hardware-independent.
    """

    def __init__(self, resource: Any, *, timeout_s: float = 5.0) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s 必须大于 0")
        self.resource = resource
        self.timeout_s = float(timeout_s)
        self.closed = False
        self.close_count = 0
        try:
            self.resource.timeout = int(self.timeout_s * 1000)
        except Exception as exc:
            raise ScpiTransportError("无法配置 VISA 超时") from exc

    def write(self, command: str) -> None:
        self._ensure_open()
        try:
            self.resource.write(command)
        except Exception as exc:
            raise _translate_error(exc, command) from exc

    def query(self, command: str) -> str:
        self._ensure_open()
        try:
            return self.resource.query(command)
        except Exception as exc:
            raise _translate_error(exc, command) from exc

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.close_count += 1
        try:
            self.resource.close()
        except Exception as exc:
            raise _translate_error(exc, "close") from exc

    def _ensure_open(self) -> None:
        if self.closed:
            raise ScpiTransportError("SCPI transport 已关闭")


def _translate_error(error: Exception, command: str) -> ScpiTransportError:
    if getattr(error, "error_code", None) == constants.StatusCode.error_timeout:
        return ScpiTransportTimeoutError(f"SCPI 操作超时: {command}")
    text = str(error).lower()
    if isinstance(error, TimeoutError) or "timeout" in text or "timed out" in text:
        return ScpiTransportTimeoutError(f"SCPI 操作超时: {command}")
    return ScpiTransportError(f"SCPI 通信失败: {command}: {error}")
