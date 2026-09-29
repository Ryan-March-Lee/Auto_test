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
    FailureInjector,
    RecordedSequence,
    SafetyInstrumentSession,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)
from .transport import (
    MockScpiTransport,
    ScpiTransport,
    ScpiTransportError,
    ScpiTransportTimeoutError,
    VisaScpiTransport,
)
from .drivers import (
    ScpiPowerSupplyDriver,
    ScpiSignalGeneratorDriver,
    SignalGeneratorDriver,
    ScpiSpectrumAnalyzerDriver,
)
from .action import SignalGeneratorActions
from .signal_generator_factory import create_signal_generator_driver

__all__ = [
    "InstrumentSession",
    "InstrumentState",
    "PowerSupplyPort",
    "SignalGeneratorPort",
    "SpectrumAnalyzerPort",
    "CommandRecorder",
    "FailureInjector",
    "RecordedSequence",
    "SafetyInstrumentSession",
    "SimulatedPowerSupply",
    "SimulatedSignalGenerator",
    "SimulatedSpectrumAnalyzer",
    "MockScpiTransport",
    "ScpiTransport",
    "ScpiTransportError",
    "ScpiTransportTimeoutError",
    "VisaScpiTransport",
    "ScpiPowerSupplyDriver",
    "ScpiSignalGeneratorDriver",
    "SignalGeneratorDriver",
    "ScpiSpectrumAnalyzerDriver",
    "SignalGeneratorActions",
    "create_signal_generator_driver",
]
