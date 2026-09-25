"""稳定的结果读取层。

提供对测量结果 JSON 的规范化访问，兼容旧格式和新格式，处理缺失字段，
为报告和可视化模块提供稳定的数据接口。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from domain.models import (
    AmplifierMeasurementResult,
    AmplifierScanPoint,
    CableLossPoint,
    CableLossResult,
    CompressionPoint,
    DriverPowerMappingPoint,
    DriverPowerMappingResult,
    MeasurementResult,
)
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


def parse_result_model(data: Mapping[str, Any]) -> MeasurementResult:
    """将新格式或历史 JSON 转为稳定的领域结果模型。

    该函数是只读适配器：不会修改输入，也不会把历史文件迁移或覆盖。
    历史主功放结果中的列式和行式 ``sweep_data`` 都会转换为统一点模型。
    """
    if not isinstance(data, Mapping):
        raise ValueError("结果必须是映射对象")
    result_type = str(data.get("result_type", ""))
    if result_type == "cable_loss" or "cable_losses" in data:
        points = []
        losses = data.get("cable_losses", {})
        if isinstance(losses, Mapping):
            for frequency, values in losses.items():
                if not isinstance(values, Mapping):
                    continue
                frequency_value = _number(frequency)
                if frequency_value is None:
                    continue
                points.append(CableLossPoint(
                    frequency_hz=frequency_value,
                    path1_loss_db=_number(values.get("total_path1"), 0.0),
                    path2_loss_db=_number(values.get("total_path2"), 0.0),
                    cable_losses_db={str(k): float(v) for k, v in values.items()
                                     if k.startswith("cable") and _number(v) is not None},
                ))
        return CableLossResult(run_id=str(data.get("run_id", "legacy")), points=tuple(points),
                               raw_readings=dict(data),
                               schema_version=str(data.get("schema_version", "1.0")),
                               metadata=_metadata(data, "cable_loss"))

    if result_type == "driver_power_mapping" or "power_mapping" in data:
        points = []
        mapping = data.get("power_mapping", {})
        if isinstance(mapping, Mapping):
            for frequency, values in mapping.items():
                if not isinstance(values, Mapping):
                    continue
                frequency_value = _number(frequency)
                if frequency_value is None:
                    continue
                if any(key in values for key in ("input_power", "output_power", "actual_output_power")):
                    values = {str(values.get("input_power", values.get("sg_power", 0.0))):
                              values.get("actual_output_power", values.get("compensated_output_power",
                                                                          values.get("output_power")))}
                for input_power, output_power in values.items():
                    if isinstance(output_power, Mapping):
                        raw_output = output_power.get("output_power", output_power.get("raw_output_power"))
                        compensated_output = output_power.get("actual_output_power", output_power.get("compensated_output_power"))
                    else:
                        raw_output = output_power
                        compensated_output = output_power
                    points.append(DriverPowerMappingPoint(
                        frequency_hz=frequency_value,
                        input_power_dbm=_number(input_power, 0.0),
                        raw_output_power_dbm=_number(raw_output, 0.0),
                        compensated_output_power_dbm=_number(compensated_output, 0.0),
                    ))
        return DriverPowerMappingResult(run_id=str(data.get("run_id", "legacy")), points=tuple(points),
                                        raw_readings=dict(data),
                                        schema_version=str(data.get("schema_version", "1.0")),
                                        metadata=_metadata(data, "driver_power_mapping"))

    points = []
    compression = {}
    compression_points = {}
    results = data.get("results", {})
    if isinstance(results, Mapping):
        for frequency, item in results.items():
            freq = _number(frequency)
            if freq is None or not isinstance(item, Mapping):
                continue
            for row in _sweep_rows(item.get("sweep_data", [])):
                voltages = {str(key)[2:]: float(value) for key, value in row.items()
                            if str(key).startswith("V_") and _number(value) is not None}
                currents = {str(key)[2:]: float(value) for key, value in row.items()
                            if str(key).startswith("I_") and _number(value) is not None}
                points.append(AmplifierScanPoint(
                    frequency_hz=freq,
                    input_power_dbm=_number(row.get("input_power_dut", row.get("input_power")), 0.0),
                    output_power_dbm=_number(row.get("output_power_dut", row.get("output_power")), 0.0),
                    gain_db=_number(row.get("gain"), 0.0),
                    dc_voltage_v=_number(row.get("dc_voltage")),
                    dc_current_a=_number(row.get("dc_current")),
                    dc_power_w=_number(row.get("dc_power")),
                    efficiency_percent=_number(row.get("efficiency")),
                    voltages_v=voltages,
                    currents_a=currents,
                ))
            compression_value = item.get("compression_point")
            if isinstance(compression_value, Mapping) and compression_value:
                output = _number(compression_value.get("output_power"))
                point = CompressionPoint(
                    output_power_dbm=output,
                    input_power_dbm=_number(compression_value.get("input_power")),
                    gain_db=_number(compression_value.get("gain")),
                    efficiency_percent=_number(compression_value.get("efficiency")),
                    signal_generator_power_dbm=_number(compression_value.get("sg_power")),
                    compression_db=_number(compression_value.get("compression")),
                    achieved=item.get("compression_achieved"),
                )
                compression_points[str(freq)] = point
                if output is not None:
                    compression[str(freq)] = output
    return AmplifierMeasurementResult(
        run_id=str(data.get("run_id", "legacy")), points=tuple(points),
        compression_points_dbm=compression, raw_readings=dict(data),
        schema_version=str(data.get("schema_version", "1.0")),
        compression_points=compression_points,
        metadata=_metadata(data, "amplifier_measurement"),
        plan_snapshot=data.get("config", data.get("plan_snapshot", {})),
        resource_snapshot=data.get("resource_snapshot", {}),
    )


def _metadata(data: Mapping[str, Any], default_type: str) -> Any:
    from domain.models import ResultMetadata

    run_id = str(data.get("run_id", "legacy"))
    return ResultMetadata(
        run_id=run_id,
        result_type=str(data.get("result_type", default_type)),
        saved_at=data.get("saved_at"),
        measurement_time=data.get("measurement_time"),
        schema_version=str(data.get("schema_version", "1.0")),
        method_version=str(data.get("method_version", "1.0")),
    )


def load_result_model(path: PathLike) -> MeasurementResult:
    """读取结果并返回领域模型，历史 JSON 由只读适配器转换。"""
    return parse_result_model(load_json_result(path))


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sweep_rows(sweep_data: Any) -> list[dict[str, Any]]:
    if isinstance(sweep_data, list):
        return [dict(row) for row in sweep_data if isinstance(row, Mapping)]
    if not isinstance(sweep_data, Mapping):
        return []
    columns = {key: value for key, value in sweep_data.items() if isinstance(value, list)}
    rows = []
    for index in range(max((len(values) for values in columns.values()), default=0)):
        row = {}
        for key, values in columns.items():
            value = values[index] if index < len(values) else None
            if key in ("voltages", "currents") and isinstance(value, Mapping):
                prefix = "V_" if key == "voltages" else "I_"
                row.update({f"{prefix}{name}": item for name, item in value.items()})
            else:
                row[key] = value
        rows.append(row)
    return rows


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
    if isinstance(data, MeasurementResult):
        compression_points = getattr(data, "compression_points", {})
        return [
            {
                "frequency": frequency,
                "output_power": point.output_power_dbm,
                "input_power": point.input_power_dbm,
                "gain": point.gain_db,
                "efficiency": point.efficiency_percent,
                "sg_power": point.signal_generator_power_dbm,
                "compression": point.compression_db,
                "compression_achieved": point.achieved,
            }
            for frequency, point in compression_points.items()
        ]
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


def _legacy_saturation_points(results: Any) -> List[Dict[str, Any]]:
    points = []
    if not isinstance(results, Mapping):
        return points
    for frequency, result in results.items():
        if not isinstance(result, Mapping):
            continue
        compression = result.get("compression_point")
        normalized = normalize_frequency_key(frequency)
        if normalized is not None and isinstance(compression, Mapping) and compression:
            points.append({**compression, "frequency": normalized})
    return points


def get_sweep_dataframe_data(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """提取用于构建 DataFrame 的扫描数据。

    返回以频率为键的字典，值为包含所有扫描点的字典列表。
    每个字典包含：input_power_dut, output_power_dut, gain, efficiency 等字段。
    """
    if isinstance(data, MeasurementResult):
        model_data: Dict[str, List[Dict[str, Any]]] = {}
        for point in data.points:
            frequency = str(float(point.frequency_hz))
            if hasattr(point, "input_power_dbm") and hasattr(point, "gain_db"):
                row = {
                    "input_power_dut": point.input_power_dbm,
                    "output_power_dut": point.output_power_dbm,
                    "gain": point.gain_db,
                    "efficiency": point.efficiency_percent,
                    "dc_power": point.dc_power_w,
                }
                row.update({f"V_{key}": value for key, value in point.voltages_v.items()})
                row.update({f"I_{key}": value for key, value in point.currents_a.items()})
            else:
                row = point.to_dict()
            model_data.setdefault(frequency, []).append(row)
        return model_data
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
