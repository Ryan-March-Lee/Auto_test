"""Power-supply channel role resolution shared by legacy and new assembly."""

from __future__ import annotations

from typing import Optional


def resolve_power_channel_role(channel: str, channel_config: Optional[dict] = None) -> Optional[str]:
    """Resolve gate/drain roles while retaining CH1/CH2 compatibility."""
    settings = channel_config if isinstance(channel_config, dict) else {}
    has_explicit_role = "role" in settings or "connection" in settings
    explicit_role = settings.get("role") or settings.get("connection")
    if isinstance(explicit_role, str):
        role = explicit_role.strip().lower()
        if role in {"gate", "栅", "栅极", "gate_voltage"}:
            return "gate"
        if role in {"drain", "漏", "漏极", "drain_voltage"}:
            return "drain"
    if has_explicit_role:
        return None
    return {"CH1": "gate", "CH2": "drain"}.get(str(channel).upper())
