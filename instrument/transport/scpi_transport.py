"""Transport-level SCPI contract and an entirely offline test double."""

from __future__ import annotations

from typing import Callable, Mapping, Protocol, Union


class ScpiTransportError(RuntimeError):
    """Base error for communication failures at the SCPI transport boundary."""


class ScpiTransportTimeoutError(ScpiTransportError, TimeoutError):
    """Raised when a SCPI operation exceeds its configured timeout."""


class ScpiTransport(Protocol):
    """Minimal transport contract required by SCPI drivers."""

    def write(self, command: str) -> None:
        ...

    def query(self, command: str) -> str:
        ...

    def close(self) -> None:
        ...


FailureRule = Union[str, Callable[[str], bool], None]


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
        if timeout_s <= 0:
            raise ValueError("timeout_s 必须大于 0")
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
        self._ensure_open()
        self.operations.append(("write", command))
        self.writes.append(command)
        self._raise_if_timeout(self.timeout_on_write, command)
        if _matches_failure_rule(self.fail_on_write, command):
            raise ScpiTransportError(f"SCPI 写入失败: {command}")

    def query(self, command: str) -> str:
        self._ensure_open()
        self.operations.append(("query", command))
        self.queries.append(command)
        self._raise_if_timeout(self.timeout_on_query, command)
        if _matches_failure_rule(self.fail_on_query, command):
            raise ScpiTransportError(f"SCPI 查询失败: {command}")
        if command not in self.responses:
            raise ScpiTransportError(f"未配置 SCPI 查询响应: {command}")
        return self.responses[command]

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.close_count += 1
        if self.fail_on_close:
            raise ScpiTransportError("SCPI transport 关闭失败")

    def _ensure_open(self) -> None:
        if self.closed:
            raise ScpiTransportError("SCPI transport 已关闭")

    def _raise_if_timeout(self, rule: FailureRule, command: str) -> None:
        if _matches_failure_rule(rule, command):
            raise ScpiTransportTimeoutError(
                f"SCPI 操作超时 ({self.timeout_s:g}s): {command}"
            )


def _matches_failure_rule(rule: FailureRule, value: str) -> bool:
    if rule is None:
        return False
    if isinstance(rule, str):
        return rule == value
    return bool(rule(value))
