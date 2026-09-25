"""仪器端口契约。

该包只包含与具体 VISA/SCPI 实现无关的接口定义。
"""

from .ports import (
    InstrumentSession,
    InstrumentState,
    PowerSupplyPort,
    SignalGeneratorPort,
    SpectrumAnalyzerPort,
)

__all__ = [
    "InstrumentSession",
    "InstrumentState",
    "PowerSupplyPort",
    "SignalGeneratorPort",
    "SpectrumAnalyzerPort",
]
