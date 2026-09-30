"""Stable application actions for instrument capabilities."""

from .signal_generator_actions import SignalGeneratorActions
from .spectrum_analyzer_actions import SpectrumAnalyzerActions
from .power_supply_actions import PowerSupplyActions

__all__ = ["SignalGeneratorActions", "SpectrumAnalyzerActions", "PowerSupplyActions"]
