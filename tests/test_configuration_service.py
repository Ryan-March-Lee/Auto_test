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

    def load_for_run(self, path, mapping_path=None):
        raise AssertionError("应用服务不应依赖仓储的运行门禁实现")


class ConfigurationServiceTests(unittest.TestCase):
    def test_load_for_run_delegates_to_the_repository(self):
        """创建运行只做参数校验：加载入口不再附加接线门禁。"""
        configuration = RunConfiguration(TestPlan(), RunResourceMapping())
        repository = InMemoryConfigurationRepository(configuration)

        result = load_for_run(repository, "memory://config")

        self.assertIs(result.configuration, configuration)
        self.assertTrue(result.valid)
        self.assertEqual(repository.calls, [("memory://config", None)])

    def test_load_for_run_passes_the_mapping_path_through(self):
        configuration = RunConfiguration(TestPlan(), RunResourceMapping())
        repository = InMemoryConfigurationRepository(configuration)

        result = load_for_run(repository, "memory://config", "memory://mapping")

        self.assertIs(result.configuration, configuration)
        self.assertEqual(repository.calls, [("memory://config", "memory://mapping")])


if __name__ == "__main__":
    unittest.main()
