"""正式测量结果的只读解析层。

本模块只接受正式结果 schema：

- 线损结果包含 ``cable_losses``；
- 驱动映射结果包含 ``power_mapping``；
- 主功放结果包含 ``results``，每个频点的 ``sweep_data`` 是列式结构；
- 结果文档必须带非空 ``run_id``。

历史 JSON 形状（``canonical_model`` 与 ``legacy_payload`` 混排、``config`` /
``derived_metrics`` / ``resource_snapshot`` 等旧字段别名、行式 ``sweep_data``）不再
被解析：缺少正式字段时直接抛出 ``ValueError``。版本化模型工件
（``*_model.json`` 中的 ``canonical_model``）仍可读取，因为它是运行目录中明确登记的
正式工件，不含服务层原始载荷。
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Dict, List, Mapping, Optional

from domain.models import (
    AmplifierMeasurementResult,
    AmplifierScanPoint,
    CableLossPoint,
    CableLossResult,
    CompressionPoint,
    DriverPowerMappingPoint,
    DriverPowerMappingResult,
    MeasurementResult,
    METHOD_VERSION,
    ResultMetadata,
    ResultStatus,
    SCHEMA_VERSION,
)


__all__ = [
    "get_config_snapshot",
    "get_result_metadata",
    "get_saturation_points",
    "get_sweep_dataframe_data",
    "get_sweep_results",
    "normalize_frequency_key",
    "parse_result_model",
]

_RESULT_TYPE_CABLE_LOSS = "cable_loss"
_RESULT_TYPE_DRIVER_POWER_MAPPING = "driver_power_mapping"
_RESULT_TYPE_AMPLIFIER = frozenset({"amplifier", "amplifier_measurement"})


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


def parse_result_model(data: Mapping[str, Any]) -> MeasurementResult:
    """把正式结果文档转换为稳定的领域结果模型。

    该函数是只读适配器：不会修改输入，也不会把结果文件迁移或覆盖。版本化模型
    工件（``{"canonical_model": ...}``）按正式工件读取；其余文档必须带 ``run_id``，
    并按 ``cable_losses`` / ``power_mapping`` / ``results`` 之一识别测量类型。
    """
    if not isinstance(data, Mapping):
        raise ValueError("结果必须是映射对象")
    canonical = data.get("canonical_model")
    if canonical is not None:
        return _model_from_canonical(canonical)
    run_id = _require_run_id(data)
    if _is_cable_loss(data):
        return _cable_loss_result(data, run_id)
    if _is_driver_power_mapping(data):
        return _driver_power_mapping_result(data, run_id)
    if _is_amplifier(data):
        return _amplifier_result(data, run_id)
    raise ValueError(
        "不是正式测量结果 schema：缺少 cable_losses / power_mapping / results 之一"
    )


# -- 正式结果文档 -----------------------------------------------------------


def _cable_loss_result(data: Mapping[str, Any], run_id: str) -> CableLossResult:
    losses = data.get("cable_losses")
    if not isinstance(losses, Mapping):
        raise ValueError("线损结果缺少 cable_losses 子对象")
    points = []
    for frequency, values in losses.items():
        frequency_value = _number(frequency)
        if frequency_value is None or frequency_value <= 0:
            raise ValueError(f"线损结果的频率键非法: {frequency!r}")
        if not isinstance(values, Mapping):
            raise ValueError(f"线损结果 {frequency!r} 必须是映射")
        points.append(
            CableLossPoint(
                frequency_hz=frequency_value,
                path1_loss_db=_number(values.get("total_path1"), 0.0),
                path2_loss_db=_number(values.get("total_path2"), 0.0),
                cable_losses_db={
                    str(key): float(value)
                    for key, value in values.items()
                    if str(key).startswith("cable") and _number(value) is not None
                },
            )
        )
    return CableLossResult(
        run_id=run_id,
        points=tuple(points),
        raw_readings=dict(data),
        schema_version=_schema_version(data),
        metadata=_metadata(data, _RESULT_TYPE_CABLE_LOSS),
        **_common_result_fields(data),
    )


def _driver_power_mapping_result(
    data: Mapping[str, Any], run_id: str
) -> DriverPowerMappingResult:
    mapping = data.get("power_mapping")
    if not isinstance(mapping, Mapping):
        raise ValueError("驱动映射结果缺少 power_mapping 子对象")
    points = []
    for frequency, values in mapping.items():
        frequency_value = _number(frequency)
        if frequency_value is None or frequency_value <= 0:
            raise ValueError(f"驱动映射结果的频率键非法: {frequency!r}")
        if not isinstance(values, Mapping):
            raise ValueError(f"驱动映射结果 {frequency!r} 必须是映射")
        for input_power, output_power in values.items():
            if isinstance(output_power, Mapping) or _number(output_power) is None:
                raise ValueError(
                    f"驱动映射结果 {frequency!r} 必须把输入功率映射为输出功率数值"
                )
            if _number(input_power) is None:
                raise ValueError(f"驱动映射结果的输入功率键非法: {input_power!r}")
            points.append(
                DriverPowerMappingPoint(
                    frequency_hz=frequency_value,
                    input_power_dbm=_number(input_power, 0.0),
                    raw_output_power_dbm=_number(output_power, 0.0),
                    compensated_output_power_dbm=_number(output_power, 0.0),
                )
            )
    return DriverPowerMappingResult(
        run_id=run_id,
        points=tuple(points),
        raw_readings=dict(data),
        schema_version=_schema_version(data),
        metadata=_metadata(data, _RESULT_TYPE_DRIVER_POWER_MAPPING),
        **_common_result_fields(data),
    )


def _amplifier_result(data: Mapping[str, Any], run_id: str) -> AmplifierMeasurementResult:
    results = data.get("results")
    if not isinstance(results, Mapping):
        raise ValueError("主功放结果缺少 results 子对象")
    points = []
    compression: dict[str, float | None] = {}
    compression_points: dict[str, CompressionPoint] = {}
    for frequency, item in results.items():
        frequency_value = _number(frequency)
        if frequency_value is None or frequency_value <= 0:
            raise ValueError(f"主功放结果的频率键非法: {frequency!r}")
        if not isinstance(item, Mapping):
            raise ValueError(f"主功放结果 {frequency!r} 必须是映射")
        for row in _sweep_rows(item.get("sweep_data", {}), frequency):
            points.append(
                AmplifierScanPoint(
                    frequency_hz=frequency_value,
                    input_power_dbm=_number(row.get("input_power_dut"), 0.0),
                    output_power_dbm=_number(row.get("output_power_dut"), 0.0),
                    gain_db=_number(row.get("gain"), 0.0),
                    dc_voltage_v=_number(row.get("dc_voltage")),
                    dc_current_a=_number(row.get("dc_current")),
                    dc_power_w=_number(row.get("dc_power")),
                    efficiency_percent=_number(row.get("efficiency")),
                    voltages_v={
                        str(key)[2:]: float(value)
                        for key, value in row.items()
                        if str(key).startswith("V_") and _number(value) is not None
                    },
                    currents_a={
                        str(key)[2:]: float(value)
                        for key, value in row.items()
                        if str(key).startswith("I_") and _number(value) is not None
                    },
                )
            )
        raw_point = item.get("compression_point")
        if isinstance(raw_point, Mapping) and raw_point:
            point = CompressionPoint(
                output_power_dbm=_number(raw_point.get("output_power")),
                input_power_dbm=_number(raw_point.get("input_power")),
                gain_db=_number(raw_point.get("gain")),
                efficiency_percent=_number(raw_point.get("efficiency")),
                signal_generator_power_dbm=_number(raw_point.get("sg_power_at_compression")),
                compression_db=_number(raw_point.get("compression_dB")),
                achieved=item.get("compression_achieved"),
            )
            compression_points[str(frequency)] = point
            compression[str(frequency)] = point.output_power_dbm
    return AmplifierMeasurementResult(
        run_id=run_id,
        points=tuple(points),
        compression_points_dbm=compression,
        raw_readings=dict(data),
        schema_version=_schema_version(data),
        compression_points=compression_points,
        metadata=_metadata(data, "amplifier_measurement"),
        plan_snapshot=_mapping_field(data, "configuration_snapshot"),
        resource_snapshot=_mapping_field(data, "device_summary"),
        **_common_result_fields(data),
    )


# -- 版本化模型工件 ---------------------------------------------------------


def _model_from_canonical(value: Any) -> MeasurementResult:
    """读取运行目录登记的版本化模型工件。"""
    if not isinstance(value, Mapping):
        raise ValueError("canonical_model 必须是映射对象")
    run_id = _require_run_id(value)
    measurement_type = str(value.get("measurement_type") or "")
    common = _common_result_fields(value)
    shared = dict(
        run_id=run_id,
        raw_readings=_mapping_field(value, "raw_readings"),
        derived_metrics=_mapping_field(value, "derived_metrics"),
        schema_version=_schema_version(value),
        method_version=str(value.get("method_version") or METHOD_VERSION),
        plan_snapshot=_mapping_field(value, "plan_snapshot"),
        resource_snapshot=_mapping_field(value, "resource_snapshot"),
        metadata=_canonical_metadata(value),
        status=_result_status(value),
        **common,
    )
    if measurement_type == _RESULT_TYPE_CABLE_LOSS:
        return CableLossResult(
            points=tuple(
                CableLossPoint(**_dataclass_kwargs(CableLossPoint, item))
                for item in _sequence(value, "points")
            ),
            **shared,
        )
    if measurement_type == _RESULT_TYPE_DRIVER_POWER_MAPPING:
        return DriverPowerMappingResult(
            points=tuple(
                DriverPowerMappingPoint(**_dataclass_kwargs(DriverPowerMappingPoint, item))
                for item in _sequence(value, "points")
            ),
            **shared,
        )
    if measurement_type in _RESULT_TYPE_AMPLIFIER:
        return AmplifierMeasurementResult(
            points=tuple(
                AmplifierScanPoint(**_dataclass_kwargs(AmplifierScanPoint, item))
                for item in _sequence(value, "points")
            ),
            compression_points_dbm=_mapping_field(value, "compression_points_dbm"),
            compression_points={
                str(key): CompressionPoint(**_dataclass_kwargs(CompressionPoint, item))
                for key, item in _mapping_field(value, "compression_points").items()
                if isinstance(item, Mapping)
            },
            **shared,
        )
    raise ValueError(f"无法识别的正式结果模型: measurement_type={measurement_type!r}")


# -- 契约字段 ---------------------------------------------------------------


def _common_result_fields(data: Mapping[str, Any]) -> Dict[str, Any]:
    """读取正式结果契约字段；不再接受旧字段别名。"""
    return {
        "configuration_snapshot": _mapping_field(data, "configuration_snapshot"),
        "device_summary": _mapping_field(data, "device_summary"),
        "raw_data_reference": _mapping_field(data, "raw_data_reference"),
        "calculation_summary": _mapping_field(data, "calculation_summary"),
        "errors": _errors_field(data),
        "cleanup_records": _records_field(data),
        "created_at": _created_at(data),
        "termination": data.get("termination"),
        "result_reference": _mapping_field(data, "result_reference"),
    }


def _metadata(data: Mapping[str, Any], default_type: str) -> ResultMetadata:
    return ResultMetadata(
        run_id=_require_run_id(data),
        result_type=str(data.get("result_type") or default_type),
        saved_at=_optional_text(data.get("saved_at")),
        measurement_time=_optional_text(data.get("measurement_time")),
        schema_version=_schema_version(data),
        method_version=str(data.get("method_version") or METHOD_VERSION),
    )


def _canonical_metadata(data: Mapping[str, Any]) -> ResultMetadata:
    value = data.get("metadata")
    if not isinstance(value, Mapping):
        raise ValueError("版本化模型工件缺少 metadata")
    return ResultMetadata(**_dataclass_kwargs(ResultMetadata, value))


def _mapping_field(data: Mapping[str, Any], name: str) -> Dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是映射")
    return dict(value)


def _errors_field(data: Mapping[str, Any]) -> tuple[str, ...]:
    value = data.get("errors", ())
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError("errors 必须是字符串或字符串序列")
    return tuple(str(item) for item in value)


def _records_field(data: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    value = data.get("cleanup_records", ())
    if isinstance(value, Mapping):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError("cleanup_records 必须是映射序列")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError("cleanup_records 中的每项必须是映射")
    return tuple(value)


def _sequence(data: Mapping[str, Any], name: str) -> tuple[Mapping[str, Any], ...]:
    value = data.get(name, ())
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} 必须是序列")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"{name} 中的每项必须是映射")
    return tuple(value)


def _dataclass_kwargs(cls: type, value: Mapping[str, Any]) -> Dict[str, Any]:
    if not is_dataclass(cls):
        raise TypeError(f"{cls!r} 不是数据类")
    names = {item.name for item in fields(cls)}
    return {name: value[name] for name in names if name in value}


def _require_run_id(data: Mapping[str, Any]) -> str:
    value = data.get("run_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("正式测量结果必须包含非空 run_id")
    return value


def _result_status(data: Mapping[str, Any]) -> ResultStatus:
    value = data.get("status", ResultStatus.COMPLETED.value)
    try:
        return ResultStatus(str(value))
    except ValueError as error:
        raise ValueError(f"结果状态无效: {value!r}") from error


def _schema_version(data: Mapping[str, Any]) -> str:
    return str(data.get("schema_version") or SCHEMA_VERSION)


def _created_at(data: Mapping[str, Any]) -> str | None:
    return _optional_text(data.get("created_at"))


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _is_cable_loss(data: Mapping[str, Any]) -> bool:
    return _declared_type(data) == _RESULT_TYPE_CABLE_LOSS or "cable_losses" in data


def _is_driver_power_mapping(data: Mapping[str, Any]) -> bool:
    return (
        _declared_type(data) == _RESULT_TYPE_DRIVER_POWER_MAPPING
        or "power_mapping" in data
    )


def _is_amplifier(data: Mapping[str, Any]) -> bool:
    return _declared_type(data) in _RESULT_TYPE_AMPLIFIER or "results" in data


def _declared_type(data: Mapping[str, Any]) -> str:
    for key in ("result_type", "measurement_type"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sweep_rows(sweep_data: Any, frequency: Any) -> List[Dict[str, Any]]:
    """把正式列式 ``sweep_data`` 展开成逐点字典；其它形状直接报错。"""
    if not isinstance(sweep_data, Mapping):
        raise ValueError(f"主功放结果 {frequency!r} 的 sweep_data 必须是列式映射")
    columns = {}
    for key, values in sweep_data.items():
        if not isinstance(values, (list, tuple)):
            raise ValueError(
                f"主功放结果 {frequency!r} 的 sweep_data.{key} 必须是扫描点序列"
            )
        columns[str(key)] = values
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


# -- 只读查询辅助 -----------------------------------------------------------


def get_sweep_results(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从结果数据中提取扫描数据。

    返回以频率字符串为键的字典，值为该频率点的扫描数据。
    如果数据缺失或为空，返回空字典。
    """
    results = data.get("results", {})
    if not isinstance(results, dict):
        return {}

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
            point_data = dict(compression_point)
            point_data["frequency"] = freq_str
            saturation_points.append(point_data)

    return saturation_points


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
        sweep_data = result.get("sweep_data", {})
        normalized_sweep = []
        if isinstance(sweep_data, dict):
            # 正式结果使用列式 JSON：每个字段对应一组扫描点。
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
        else:
            raise ValueError(f"频率 {freq_str} 的 sweep_data 必须是列式映射")

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
    """提取结果中的正式配置快照（``configuration_snapshot``）。"""
    config = data.get("configuration_snapshot", {})
    if not isinstance(config, dict):
        return {}
    return config
