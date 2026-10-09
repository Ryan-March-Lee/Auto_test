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
from .session import ManagedInstrumentSession, InstrumentSessionError
from .simulation import (
    CommandRecorder,
    FailureInjector,
    RecordedSequence,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)
SafetyInstrumentSession = ManagedInstrumentSession
from .transport import (
    MockScpiTransport,
    ScpiTransport,
    ScpiTransportError,
    ScpiTransportTimeoutError,
    VisaScpiTransport,
)
from .drivers import (
    ScpiPowerSupplyDriver,
    PowerSupplyDriver,
    ScpiSignalGeneratorDriver,
    SignalGeneratorDriver,
    ScpiSpectrumAnalyzerDriver,
)
from .action import (
    ActionContext,
    ActionLogger,
    PowerSupplyActions,
    SignalGeneratorActions,
    SpectrumAnalyzerActions,
    SpectrumAnalyzerPortAdapter,
)
from .signal_generator_factory import create_signal_generator_driver
from .flow import (
    PowerChannelSetup,
    PowerOffFlow,
    PowerOffFlowError,
    PowerOnFlow,
    PowerOnFlowError,
    SafetyShutdownError,
    SafetyShutdownFlow,
)
from .power_control import PowerController, PowerControlError
from .safety import InstrumentSafetyError, shutdown_instruments

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
    "PowerSupplyDriver",
    "ScpiSignalGeneratorDriver",
    "SignalGeneratorDriver",
    "ScpiSpectrumAnalyzerDriver",
    "SignalGeneratorActions",
    "SpectrumAnalyzerActions",
    "SpectrumAnalyzerPortAdapter",
    "PowerSupplyActions",
    "ActionContext",
    "ActionLogger",
    "create_signal_generator_driver",
    "PowerChannelSetup",
    "PowerOnFlow",
    "PowerOnFlowError",
    "PowerOffFlow",
    "PowerOffFlowError",
    "SafetyShutdownFlow",
    "SafetyShutdownError",
    "InstrumentSession",
    "ManagedInstrumentSession",
    "InstrumentSessionError",
    "PowerController",
    "PowerControlError",
    "InstrumentSafetyError",
    "shutdown_instruments",
]
