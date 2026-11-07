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
from .transport import (
    MockScpiTransport,
    ScpiTransport,
    ScpiTransportError,
    ScpiTransportTimeoutError,
    VisaScpiTransport,
)
from .drivers import (
    ScpiPowerSupplyDriver,
    SignalGeneratorDriver,
    ScpiSpectrumAnalyzerDriver,
)
from .action import (
    ActionContext,
    ActionLogger,
    PowerSupplyActions,
    SignalGeneratorActions,
    SpectrumAnalyzerActions,
)
from .signal_generator_factory import create_signal_generator_driver
from .flow import (
    SafetyShutdownError,
    SafetyShutdownFlow,
)
from .power_control import (
    PowerChannelStep,
    PowerControlError,
    PowerController,
    PowerGroupPlan,
    PowerOnControlError,
    cleanup_failures_of,
)
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
    "SimulatedPowerSupply",
    "SimulatedSignalGenerator",
    "SimulatedSpectrumAnalyzer",
    "MockScpiTransport",
    "ScpiTransport",
    "ScpiTransportError",
    "ScpiTransportTimeoutError",
    "VisaScpiTransport",
    "ScpiPowerSupplyDriver",
    "SignalGeneratorDriver",
    "ScpiSpectrumAnalyzerDriver",
    "SignalGeneratorActions",
    "SpectrumAnalyzerActions",
    "PowerSupplyActions",
    "ActionContext",
    "ActionLogger",
    "create_signal_generator_driver",
    "SafetyShutdownFlow",
    "SafetyShutdownError",
    "InstrumentSession",
    "ManagedInstrumentSession",
    "InstrumentSessionError",
    "PowerChannelStep",
    "PowerController",
    "PowerControlError",
    "PowerGroupPlan",
    "PowerOnControlError",
    "cleanup_failures_of",
    "InstrumentSafetyError",
    "shutdown_instruments",
]
