"""Shared metadata and optional event logging for instrument actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ActionContext:
    instrument_id: str | None = None
    channel_id: str | None = None
    measurement_id: str | None = None


ActionLogger = Callable[[str, ActionContext, Mapping[str, object], BaseException | None], None]


class ActionBase:
    def __init__(self, context: ActionContext | None = None, logger: ActionLogger | None = None) -> None:
        self.context = context or ActionContext()
        self.logger = logger

    def _call(self, action_name: str, method: Callable[..., Any], *args, timeout_s: float,
              event_values: Mapping[str, object] | None = None, **kwargs) -> Any:
        values = {"timeout_s": timeout_s, **dict(event_values or {})}
        try:
            result = method(*args, timeout_s=timeout_s, **kwargs)
        except Exception as error:
            self._emit(action_name, values, error)
            raise
        self._emit(action_name, values, None)
        return result

    def _emit(self, action_name: str, values: Mapping[str, object], error: BaseException | None) -> None:
        """Emit best-effort telemetry without changing hardware behavior."""
        if self.logger is None:
            return
        try:
            self.logger(action_name, self.context, values, error)
        except Exception:
            # A logger must never mask a driver result or driver exception.
            return
