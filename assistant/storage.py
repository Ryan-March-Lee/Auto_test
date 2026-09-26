"""Persistence helpers for optional assistant state.

The GUI uses these functions instead of knowing assistant file locations or
the JSON shape of search configuration and chat history.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from project_paths import (
    CHAT_HISTORY_FILE,
    SEARCH_API_CONFIG_FILE,
    PROJECT_ROOT,
)


def list_history_files() -> List[Path]:
    """Return saved assistant history files, newest first."""
    files = list(PROJECT_ROOT.glob("chat_history_*.json"))
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def has_current_history() -> bool:
    return CHAT_HISTORY_FILE.exists()


def load_history_file(path: Optional[Path] = None) -> list:
    """Load a history file, returning an empty history for invalid state."""
    target = CHAT_HISTORY_FILE if path is None else Path(path)
    try:
        with target.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, list) else []
    except (OSError, ValueError, TypeError):
        return []


def load_search_api_config() -> Dict[str, Any]:
    """Load optional search settings without exposing their file path."""
    try:
        with SEARCH_API_CONFIG_FILE.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_search_api_config(config: Dict[str, Any]) -> None:
    """Persist optional search settings."""
    SEARCH_API_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SEARCH_API_CONFIG_FILE.open("w", encoding="utf-8") as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)


__all__ = [
    "has_current_history",
    "list_history_files",
    "load_history_file",
    "load_search_api_config",
    "save_search_api_config",
]
