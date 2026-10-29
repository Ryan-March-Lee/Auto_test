"""仪器注册表领域层。"""

from domain.instruments.labels import (
    PowerSupplyLabelError,
    generate_supply_label,
    generate_supply_labels,
    normalize_supply_serial,
    supply_label_candidates,
    supply_label_prefix,
)
from domain.instruments.models import (
    DeviceStatus,
    Instrument,
    InstrumentEndpoint,
    InstrumentIdentity,
    build_instrument_name,
    make_device_key,
    normalize_model,
    normalize_serial,
    normalize_text,
    normalize_vendor,
)

__all__ = [
    "DeviceStatus",
    "Instrument",
    "InstrumentEndpoint",
    "InstrumentIdentity",
    "PowerSupplyLabelError",
    "build_instrument_name",
    "generate_supply_label",
    "generate_supply_labels",
    "make_device_key",
    "normalize_model",
    "normalize_serial",
    "normalize_supply_serial",
    "normalize_text",
    "normalize_vendor",
    "supply_label_candidates",
    "supply_label_prefix",
]
