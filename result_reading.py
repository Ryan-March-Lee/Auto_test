"""兼容入口：结果解析已迁移到 domain，文件读取保留在基础设施。"""

from domain.result_reading import *
from domain.result_reading import __all__
from infrastructure.persistence.json_result_repository import load_json_result


def load_measurement_result(path):
    return load_json_result(path)


def load_result_model(path):
    return parse_result_model(load_json_result(path))


__all__ = [*__all__, "load_measurement_result", "load_result_model"]
