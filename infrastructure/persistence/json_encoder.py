"""JSON encoders used by the file-backed persistence adapters."""

from __future__ import annotations

import json

import numpy as np


class NumpyJSONEncoder(json.JSONEncoder):
    """Encode NumPy values while keeping the legacy JSON shape unchanged."""

    def default(self, value):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        return super().default(value)
