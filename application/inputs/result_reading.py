"""Application-facing pure result parsing API."""

from domain.result_reading import (
    get_config_snapshot,
    get_result_metadata,
    get_saturation_points,
    get_sweep_dataframe_data,
    get_sweep_results,
    normalize_frequency_key,
    parse_result_model,
)

__all__ = [
    "get_config_snapshot",
    "get_result_metadata",
    "get_saturation_points",
    "get_sweep_dataframe_data",
    "get_sweep_results",
    "normalize_frequency_key",
    "parse_result_model",
]
