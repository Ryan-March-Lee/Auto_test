"""Transport-level SCPI contract and an entirely offline test double."""

from __future__ import annotations

from typing import Callable, Mapping, Protocol, Union
import math


class ScpiTransportError(RuntimeError):
    """Base error for communication failures at the SCPI transport boundary."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        command: str | None = None,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.command_summary = _command_summary(command)
        self.original_error = original_error


class ScpiTransportTimeoutError(ScpiTransportError, TimeoutError):
    """Raised when a SCPI operation exceeds its configured timeout."""


class ScpiTransport(Protocol):
    def set_timeout_s(self, timeout_s: float) -> None:
        ...

    """Minimal transport contract required by SCPI drivers."""

    def write(self, command: str) -> None:
        ...

    def query(self, command: str) -> str:
        ...

    def close(self) -> None:
        ...


FailureRule = Union[str, Callable[[str], bool], Exception, None]


class MockScpiTransport:
    """Deterministic SCPI transport for offline unit tests.

    The mock never imports or constructs a VISA ResourceManager.  Timeout rules
    are command based so tests can exercise timeout handling without sleeping.
    ``close`` is terminal and attempts the underlying close operation at most
    once, including when the configured close operation fails.
    """

    def __init__(
        self,
        responses: Mapping[str, str] | None = None,
        *,
        timeout_s: float = 5.0,
        timeout_on_write: FailureRule = None,
        timeout_on_query: FailureRule = None,
        fail_on_write: FailureRule = None,
        fail_on_query: FailureRule = None,
        fail_on_close: bool = False,
    ) -> None:
        if not math.isfinite(float(timeout_s)) or timeout_s <= 0:
            raise ValueError("timeout_s 必须是有限正数")
        self.responses = dict(responses or {})
        self.timeout_s = float(timeout_s)
        self.timeout_on_write = timeout_on_write
        self.timeout_on_query = timeout_on_query
        self.fail_on_write = fail_on_write
        self.fail_on_query = fail_on_query
        self.fail_on_close = fail_on_close
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.operations: list[tuple[str, str]] = []
        self.closed = False
        self.close_count = 0

    def write(self, command: str) -> None:
        command = _validate_command(command)
        self._ensure_open()
        self.operations.append(("write", command))
        self.writes.append(command)
        self._raise_if_timeout(self.timeout_on_write, command, "write")
        if _matches_failure_rule(self.fail_on_write, command):
            raise _failure_for(self.fail_on_write, "write", command, "SCPI 写入失败")

    def query(self, command: str) -> str:
        command = _validate_command(command)
        self._ensure_open()
        self.operations.append(("query", command))
        self.queries.append(command)
        self._raise_if_timeout(self.timeout_on_query, command, "query")
        if _matches_failure_rule(self.fail_on_query, command):
            raise _failure_for(self.fail_on_query, "query", command, "SCPI 查询失败")
        if command not in self.responses:
            raise ScpiTransportError(
                f"未配置 SCPI 查询响应: {_command_summary(command)}",
                operation="query",
                command=command,
            )
        response = self.responses[command]
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
        if self.fail_on_close:
            raise ScpiTransportError(
                "SCPI transport 关闭失败",
                operation="close",
                original_error=self.fail_on_close if isinstance(self.fail_on_close, Exception) else None,
            )

    def set_timeout_s(self, timeout_s: float) -> None:
        timeout_s = float(timeout_s)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s 必须是有限正数")
        self.timeout_s = timeout_s

    def _ensure_open(self) -> None:
        if self.closed:
            raise ScpiTransportError("SCPI transport 已关闭", operation="transport")

    def _raise_if_timeout(self, rule: FailureRule, command: str, operation: str) -> None:
        if _matches_failure_rule(rule, command):
            raise ScpiTransportTimeoutError(
                f"SCPI 操作超时 ({self.timeout_s:g}s): {_command_summary(command)}",
                operation=operation,
                command=command,
            )


def _matches_failure_rule(rule: FailureRule, value: str) -> bool:
    if rule is None:
        return False
    if isinstance(rule, str):
        return rule == value
    if isinstance(rule, Exception):
        return True
    return bool(rule(value))


def _failure_for(rule: FailureRule, operation: str, command: str, prefix: str) -> ScpiTransportError:
    if isinstance(rule, Exception):
        error_type = (
            ScpiTransportTimeoutError
            if isinstance(rule, ScpiTransportTimeoutError)
            else ScpiTransportError
        )
        return error_type(
            f"{prefix}: {_command_summary(command)}",
            operation=operation,
            command=command,
            original_error=rule,
        )
    return ScpiTransportError(
        f"{prefix}: {_command_summary(command)}",
        operation=operation,
        command=command,
    )


def _validate_command(command: str) -> str:
    if not isinstance(command, str) or not command.strip():
        raise ValueError("SCPI command 必须是非空字符串")
    return command


def _command_summary(command: str | None, limit: int = 120) -> str:
    if command is None:
        return ""
    compact = " ".join(str(command).split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."
