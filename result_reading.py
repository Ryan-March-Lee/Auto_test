"""稳定的结果读取层。

提供对测量结果 JSON 的规范化访问，兼容旧格式和新格式，处理缺失字段，
为报告和可视化模块提供稳定的数据接口。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from result_storage import load_json_result


PathLike = Union[str, Path]


def normalize_frequency_key(value: Any) -> Optional[str]:
    """将频率键规范化为字符串格式。

    支持：
    - 浮点数：1.0 -> "1.0"
    - 整数：1 -> "1.0"
    - 字符串："1.0" -> "1.0"
    - None 或无效值：返回 None
    """
    if value is None:
        return None
    try:
        return str(float(value))
    except (ValueError, TypeError):
        return None


def load_measurement_result(path: PathLike) -> Dict[str, Any]:
    """加载测量结果 JSON，兼容旧格式和新格式。

    返回原始数据字典，不做结构验证。
    """
    return load_json_result(path)


def get_sweep_results(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从结果数据中提取扫描数据。

    返回以频率字符串为键的字典，值为该频率点的扫描数据。
    如果数据缺失或为空，返回空字典。
    """
    results = data.get("results", {})
    if not isinstance(results, dict):
        return {}

    # 规范化频率键
    normalized = {}
    for freq_key, result_data in results.items():
        normalized_key = normalize_frequency_key(freq_key)
        if normalized_key and isinstance(result_data, dict):
            normalized[normalized_key] = result_data

    return normalized


def get_saturation_points(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从结果数据中提取饱和点/压缩点数据。

    返回包含所有频率点饱和信息的列表。
    如果数据缺失，返回空列表。
    """
    results = get_sweep_results(data)
    saturation_points = []

    for freq_str, result in results.items():
        compression_point = result.get("compression_point", {})
        if isinstance(compression_point, dict) and compression_point:
            # 添加频率信息
            point_data = dict(compression_point)
            point_data["frequency"] = freq_str
            saturation_points.append(point_data)

    return saturation_points


def get_sweep_dataframe_data(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """提取用于构建 DataFrame 的扫描数据。

    返回以频率为键的字典，值为包含所有扫描点的字典列表。
    每个字典包含：input_power_dut, output_power_dut, gain, efficiency 等字段。
    """
    results = get_sweep_results(data)
    dataframe_data = {}

    for freq_str, result in results.items():
        sweep_data = result.get("sweep_data", [])
        normalized_sweep = []
        if isinstance(sweep_data, dict):
            # 生产结果使用列式 JSON：每个字段对应一组扫描点。
            columns = {key: value for key, value in sweep_data.items() if isinstance(value, list)}
            point_count = max((len(value) for value in columns.values()), default=0)
            for index in range(point_count):
                point = {}
                for key, values in columns.items():
                    value = values[index] if index < len(values) else None
                    if key == "voltages" and isinstance(value, dict):
                        point.update({f"V_{name}": item for name, item in value.items()})
                    elif key == "currents" and isinstance(value, dict):
                        point.update({f"I_{name}": item for name, item in value.items()})
                    else:
                        point[key] = value
                normalized_sweep.append(point)
        elif isinstance(sweep_data, list):
            for point in sweep_data:
                if not isinstance(point, dict):
                    continue
                normalized_point = dict(point)
                voltages = normalized_point.pop("voltages", {})
                currents = normalized_point.pop("currents", {})
                if isinstance(voltages, dict):
                    normalized_point.update({f"V_{key}": value for key, value in voltages.items()})
                if isinstance(currents, dict):
                    normalized_point.update({f"I_{key}": value for key, value in currents.items()})
                normalized_sweep.append(normalized_point)

        if normalized_sweep:
            dataframe_data[freq_str] = normalized_sweep

    return dataframe_data


def get_result_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """提取结果元数据。

    返回包含 schema_version, result_type, saved_at, measurement_time 等字段的字典。
    """
    return {
        "schema_version": data.get("schema_version"),
        "result_type": data.get("result_type"),
        "saved_at": data.get("saved_at"),
        "measurement_time": data.get("measurement_time"),
        "original_filename": data.get("original_filename"),
    }


def get_config_snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
    """提取结果中的配置快照。

    兼容旧格式（config 字段）和新格式。
    """
    config = data.get("config", {})
    if not isinstance(config, dict):
        return {}
    return config
