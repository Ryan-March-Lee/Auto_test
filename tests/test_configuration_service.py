import unittest

from application.configuration_service import load_for_run
from application.ports.config_repository import ConfigurationLoadResult
from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan
from domain.configuration.types import ConfigValidationResult


class InMemoryConfigurationRepository:
    def __init__(self, configuration):
        self.configuration = configuration
        self.calls = []

    def load(self, plan_path, mapping_path=None):
        self.calls.append((plan_path, mapping_path))
        return ConfigurationLoadResult(
            self.configuration,
            ConfigValidationResult([], []),
        )

    def load_legacy_data(self, value):
        raise AssertionError("不应从内存仓储调用 legacy 转换")

    def load_for_run(self, path, mapping_path=None):
        raise AssertionError("应用服务不应依赖仓储的运行门禁实现")


class ConfigurationServiceTests(unittest.TestCase):
    def test_load_for_run_uses_port_and_applies_wiring_gate(self):
        mapping = RunResourceMapping(wiring_confirmed=False)
        configuration = RunConfiguration(TestPlan(), mapping)
        repository = InMemoryConfigurationRepository(configuration)

        result = load_for_run(repository, "memory://config")

        self.assertFalse(result.valid)
        self.assertIn("wiring.confirmed", {issue.path for issue in result.validation.errors})
        self.assertEqual(repository.calls, [("memory://config", None)])

    def test_load_for_run_preserves_valid_confirmed_configuration(self):
        mapping = RunResourceMapping(wiring_confirmed=True)
        configuration = RunConfiguration(TestPlan(), mapping)
        repository = InMemoryConfigurationRepository(configuration)

        result = load_for_run(repository, "memory://config", "memory://mapping")

        self.assertIs(result.configuration, configuration)
        self.assertTrue(result.valid)
        self.assertEqual(repository.calls, [("memory://config", "memory://mapping")])


if __name__ == "__main__":
    unittest.main()
