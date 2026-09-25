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
from .simulation import (
    CommandRecorder,
    RecordedSequence,
    SafetyInstrumentSession,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)

__all__ = [
    "InstrumentSession",
    "InstrumentState",
    "PowerSupplyPort",
    "SignalGeneratorPort",
    "SpectrumAnalyzerPort",
    "CommandRecorder",
    "RecordedSequence",
    "SafetyInstrumentSession",
    "SimulatedPowerSupply",
    "SimulatedSignalGenerator",
    "SimulatedSpectrumAnalyzer",
]
