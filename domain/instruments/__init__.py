"""仪器注册表领域层。"""

from domain.instruments.labels import (
    InstrumentLabelError,
    generate_instrument_label,
    generate_instrument_labels,
    instrument_display_label,
    label_candidates,
    label_prefix,
    normalize_label_full_code,
)
from domain.instruments.models import (
    DEVICE_ROLES,
    POWER_SUPPLY_ROLE,
    DeviceStatus,
    Instrument,
    InstrumentEndpoint,
    InstrumentIdentity,
    canonical_role,
    make_device_key,
    normalize_full_code,
    normalize_model,
    normalize_role,
    normalize_text,
    normalize_vendor,
)

__all__ = [
    "DEVICE_ROLES",
    "DeviceStatus",
    "Instrument",
    "InstrumentEndpoint",
    "InstrumentIdentity",
    "InstrumentLabelError",
    "POWER_SUPPLY_ROLE",
    "canonical_role",
    "generate_instrument_label",
    "generate_instrument_labels",
    "instrument_display_label",
    "label_candidates",
    "label_prefix",
    "make_device_key",
    "normalize_full_code",
    "normalize_label_full_code",
    "normalize_model",
    "normalize_role",
    "normalize_text",
    "normalize_vendor",
]
