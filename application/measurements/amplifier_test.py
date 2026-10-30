"""Application use case for the main amplifier measurement."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

import domain.measurement.calculations as measurement_calculations
from app.cancellation import CancellationToken, MeasurementCancelled
from application.archival_service import ArchivalOutcome, archive_completed_stage
from application.dto import AmplifierMeasurementRequest, AmplifierMeasurementResult, MeasurementResult
from application.errors import ConfigurationError
from application.measurements.services import AmplifierMeasurementService
from application.ports.measurement_session import CleanupRecord
from domain.measurement.values import (
    CableLossTable,
    DriverOutputTable,
    Frequency,
    MeasurementValueError,
    PowerDbm,
)
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
)


logger = logging.getLogger(__name__)

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
# 拆分结果文件的稳定格式版本；脱离 GUI 单独读取时据此判断字段语义。
AMPLIFIER_SPLIT_SCHEMA_VERSION = 1


class AmplifierCalibrationError(ConfigurationError):
    """主功放校准输入（线损 / 驱动映射）结构无效。"""


class AmplifierMeasurementUseCase:
    """Coordinate the amplifier sweep and its calibration inputs.

    运行契约运行（注入 ``stage_recorder``）只消费**当前运行**显式登记的输入：
    线损来自 ``inputs/cable_loss.json``，驱动映射启用时来自
    ``inputs/driver_mapping.json``；主功放所有结果写入当前运行的 ``results/``，
    完成后把主功放阶段和运行级状态更新为 ``completed``。驱动模式关闭时不要求
    映射文件存在。旧兼容流程不注入 ``stage_recorder``，必须显式提供
    ``loss_data`` / ``driver_mapping`` 或其路径；缺少显式输入时在硬件动作前失败，
    不再回退到按时间扫描的“最新结果”读取。
    """

    def __init__(
        self,
        request: AmplifierMeasurementRequest,
        *,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.request = request
        if request.measurement_port is None:
            raise ValueError("AmplifierMeasurementUseCase 必须由应用组装层注入 measurement_port")
        self.config = _service_config(request.configuration)
        self.inst_ctrl = request.measurement_port
        self.owns_measurement_port = bool(
            request.safety_options.get("owns_measurement_port", False)
        )
        self.run_id = request.run_id
        self.run_directory = request.run_directory
        self.result_repository = request.result_repository
        if self.result_repository is None:
            raise ValueError("AmplifierMeasurementUseCase 必须注入 result_repository")
        self.safety_cleanup = request.safety_cleanup
        if self.safety_cleanup is None:
            raise ValueError("AmplifierMeasurementUseCase 必须由应用组装层注入 safety_cleanup")
        self.input_reader = request.input_reader
        # 运行契约运行会注入阶段记录器：输入只从当前运行目录读取，结果写入当前
        # 运行的 results/ 并更新 manifest；旧兼容流程不注入，保持全局路径行为。
        self.stage_recorder = request.stage_recorder
        self._stage_open = False
        # 阶段成功提交后的自动归档入口；未注入时不生成任何交付产物。
        self.archival_service = request.archival_service
        self.last_archival_outcome: ArchivalOutcome | None = None
        self.loss_data = self._load_loss_data(request)
        self.driver_mapping = self._load_driver_mapping(request)
        if self.stage_recorder is not None:
            # 运行契约运行在构造阶段就拒绝结构无效的校准输入，避免先连接仪器或
            # 执行 RF 动作后才在扫描计算中暴露缺字段问题。
            validate_amplifier_calibration_inputs(
                self.loss_data,
                self.driver_mapping,
                frequencies=self.config.get("test_frequencies", ()),
                driver_enabled=self._driver_mode_enabled(),
            )
        self.sleep_fn = sleep_fn or time.sleep
        self._token = request.cancellation_token or CancellationToken()
        self._service = AmplifierMeasurementService(
            self.config,
            self.inst_ctrl,
            self.loss_data,
            self.driver_mapping,
            run_id=self.run_id,
            safety_cleanup=self.safety_cleanup,
            event_sink=request.event_sink,
            cancellation_token=self._token,
            sleep_fn=self.sleep_fn,
            settle_delay_s=3.0,
            owns_measurement_port=self.owns_measurement_port,
        )
        self.measurement_results: dict[str, Any] = {}
        self.last_result: MeasurementResult | None = None

    @property
    def last_cleanup_record(self) -> CleanupRecord | None:
        """返回最近一次安全清理的结构化记录。"""
        return self._service.last_cleanup_record

    def stop_measurement(self) -> None:
        self._token.request_stop(reason="用户停止")

    def cancel_measurement(self) -> None:
        self._token.request_cancel(reason="任务已取消")

    def emergency_stop(self) -> None:
        self._token.request_emergency_stop(reason="紧急停止")

    def calculate_actual_power(self, frequency: float, measured_power: float) -> float:
        """按强类型值对象补偿 DUT 输出功率，对外仍返回 dBm 浮点数。

        线损表和衰减器在用例构造时已解析为值对象，与测量扫描使用同一份；
        这里不再按调用重复解析原始文档。
        """
        actual = measurement_calculations.compensate_amplifier_output_power(
            measured_power=PowerDbm(measured_power),
            frequency=Frequency.from_ghz(frequency),
            loss_table=self._service.loss_table,
            attenuator_value=self._service.attenuator_value,
        )
        return actual.dbm

    def perform_power_sweep(self, frequency: float) -> dict[str, Any]:
        return self._service.perform_power_sweep(frequency)

    def measure_all_frequencies(self) -> MeasurementResult:
        try:
            self._begin_stage()
            payload = self._service.run()
            result = AmplifierMeasurementResult.from_service_payload(
                payload,
                run_id=self.run_id,
            )
            self.measurement_results = {
                item.frequency_ghz: item for item in result.frequencies
            }
            result = self._save(result)
            self.last_result = result
            return result
        except MeasurementCancelled as error:
            self._record_cancel(getattr(error, "reason", None) or "测量已取消")
            raise
        except Exception as error:  # 任何测量失败都要登记阶段失败后原样上抛
            self._record_failure(error)
            raise

    # -- 运行契约：校准输入 ------------------------------------------------

    def _driver_mode_enabled(self) -> bool:
        mode = self.config.get("driver_mode", {})
        return bool(mode.get("enabled")) if isinstance(mode, Mapping) else False

    def _load_loss_data(self, request: AmplifierMeasurementRequest) -> Mapping[str, Any]:
        """解析本次运行实际消费的线损输入。

        运行契约运行以当前运行目录为唯一输入来源：强制校验
        ``inputs/cable_loss.json`` 已完成且校验值一致，显式 ``loss_data`` /
        ``loss_data_path`` 只记录告警、不参与解析；旧兼容流程必须显式提供
        ``loss_data`` 或 ``loss_data_path``，缺少显式输入时在硬件动作前立即失败，
        绝不回退到按时间扫描的“最新结果”读取。
        """
        if self.stage_recorder is not None:
            if request.loss_data is not None or request.loss_data_path is not None:
                logger.warning(
                    "运行契约运行 %s 忽略显式 loss_data/loss_data_path，只消费当前运行输入",
                    self.run_id,
                )
            return self._read_run_cable_loss()
        if request.loss_data is not None:
            return request.loss_data
        if self.input_reader is None:
            raise ValueError("AmplifierMeasurementUseCase 必须注入 input_reader 或 loss_data")
        if request.loss_data_path is not None:
            return self.input_reader.read_cable_loss(request.loss_data_path)
        raise ValueError(
            "旧兼容流程必须显式提供 loss_data_path（或改用当前运行 manifest 的显式输入）；"
            "不再自动查找最新线损结果"
        )

    def _load_driver_mapping(
        self, request: AmplifierMeasurementRequest
    ) -> Mapping[str, Any] | None:
        """解析本次运行实际消费的驱动映射输入。

        驱动模式关闭时不要求映射文件存在，直接返回 ``None``。运行契约运行在驱动
        模式启用时强制校验 ``inputs/driver_mapping.json`` 已完成且校验值一致，显式
        ``driver_mapping`` / ``driver_mapping_path`` 只记录告警；旧兼容流程在驱动
        模式下必须显式提供 ``driver_mapping`` 或 ``driver_mapping_path``，缺少显式
        输入时在硬件动作前立即失败。
        """
        if self.stage_recorder is not None:
            if not self._driver_mode_enabled():
                if request.driver_mapping is not None or request.driver_mapping_path is not None:
                    logger.warning(
                        "运行契约运行 %s 未启用驱动模式，忽略显式驱动映射输入",
                        self.run_id,
                    )
                return None
            if request.driver_mapping is not None or request.driver_mapping_path is not None:
                logger.warning(
                    "运行契约运行 %s 忽略显式 driver_mapping/driver_mapping_path，"
                    "只消费当前运行输入",
                    self.run_id,
                )
            return self._read_run_driver_mapping()
        driver_mapping = request.driver_mapping
        if self._driver_mode_enabled() and driver_mapping is None:
            if self.input_reader is None:
                raise ValueError(
                    "AmplifierMeasurementUseCase 必须注入 input_reader 或 driver_mapping"
                )
            if request.driver_mapping_path is None:
                raise ValueError(
                    "旧兼容流程在驱动模式下必须显式提供 driver_mapping_path"
                    "（或改用当前运行 manifest 的显式输入）；"
                    "不再自动查找最新驱动映射结果"
                )
            driver_mapping = self.input_reader.read_driver_mapping(
                request.driver_mapping_path
            )
        return driver_mapping

    def _read_run_cable_loss(self) -> Mapping[str, Any]:
        """只从当前运行目录读取已校验的线损输入，缺失时在硬件动作前失败。"""
        return self._read_run_input(STAGE_CABLE_LOSS)

    def _read_run_driver_mapping(self) -> Mapping[str, Any]:
        """只从当前运行目录读取已校验的驱动映射输入，缺失时在硬件动作前失败。"""
        return self._read_run_input(STAGE_DRIVER_MAPPING)

    def _read_run_input(self, stage: str) -> Mapping[str, Any]:
        if self.input_reader is None:
            raise ValueError("主功放读取当前运行输入时必须注入 input_reader")
        path = self.stage_recorder.verified_result_path(stage)
        if stage == STAGE_CABLE_LOSS:
            return self.input_reader.read_cable_loss(path)
        return self.input_reader.read_driver_mapping(path)

    # -- 运行契约：阶段生命周期 --------------------------------------------

    def _begin_stage(self) -> None:
        """开始当前运行的主功放阶段；未注入记录器时保持旧流程行为。"""
        if self.stage_recorder is None:
            return
        if not self._driver_mode_enabled():
            # 未启用驱动模式时，映射阶段不属于本次运行，标记为 skipped，
            # 主功放阶段因此不要求映射文件存在。
            self.stage_recorder.skip(STAGE_DRIVER_MAPPING, note="未启用驱动模式")
        self.stage_recorder.start(STAGE_AMPLIFIER)
        self._stage_open = True

    def _record_complete(self, result: MeasurementResult) -> Any:
        """把完成的主功放结果写入当前运行，登记阶段完成并收敛运行状态。"""
        if self.stage_recorder is None:
            return None
        payload = result.to_dict()
        reference = self.stage_recorder.complete(
            STAGE_AMPLIFIER,
            payload,
            result_path=AMPLIFIER_RESULT_PATH,
            extra_files=_split_result_files(payload),
        )
        self._stage_open = False
        # 运行级完成是权威提交的一部分：更新失败必须向调用方暴露，不能把
        # “阶段已完成但运行仍在 running”的状态伪装成完整成功。
        self.stage_recorder.complete_run()
        return reference

    def _record_failure(self, error: Exception) -> None:
        if self.stage_recorder is None or not self._stage_open:
            return
        self._stage_open = False
        self._try_record(
            lambda: self.stage_recorder.fail(
                STAGE_AMPLIFIER, str(error) or error.__class__.__name__
            )
        )

    def _record_cancel(self, reason: str) -> None:
        if self.stage_recorder is None or not self._stage_open:
            return
        self._stage_open = False
        self._try_record(lambda: self.stage_recorder.cancel(STAGE_AMPLIFIER, reason))

    @staticmethod
    def _try_record(action: Callable[[], Any]) -> None:
        """阶段状态写入失败不应掩盖原始测量结果或异常。"""
        try:
            action()
        except Exception:
            logger.exception("主功放阶段状态记录失败")

    # -- 运行契约：结果持久化 ----------------------------------------------

    def _save(self, result: MeasurementResult) -> MeasurementResult:
        if self.stage_recorder is not None:
            return self._save_to_run_directory(result)
        saved = self.result_repository.save(
            result,
            result_type="amplifier_measurement",
            run_id=self.run_id,
            run_directory=self.run_directory,
        )
        self.run_directory = saved.run_directory
        return result.with_saved_result(archive_path=_saved_path(saved, "archive_path"))

    def _save_to_run_directory(self, result: MeasurementResult) -> MeasurementResult:
        """运行契约运行：权威结果写入当前运行的 ``results/``。

        主功放结果及拆分文件先原子提交并登记 manifest，运行目录结果是本次运行的权威
        来源；返回值只暴露该权威路径，不再同时写全局兼容副本。正式结果提交后立即触发
        自动归档（计划步骤 4）：CSV 生成失败只把该产物登记为 ``failed``，不回滚正式
        结果、不改变阶段或运行状态。
        """
        reference = self._record_complete(result)
        self.last_archival_outcome = archive_completed_stage(
            self.archival_service,
            run_id=self.run_id,
            stage=STAGE_AMPLIFIER,
        )
        archive_path = Path(self.run_directory) / reference.path
        return result.with_saved_result(archive_path=str(archive_path))


def validate_amplifier_calibration_inputs(
    loss_data: Any,
    driver_mapping: Any,
    *,
    frequencies: Any,
    driver_enabled: bool,
) -> None:
    """校验主功放校准输入结构，确保后续计算所需字段都存在。

    只做结构与必需字段校验，不做取值范围判断：

    - ``cable_losses`` 必须是对象，且包含每个测试频率对应对象；
    - 无驱动时每个频率必须包含 ``cable1``（DUT 输入功率 = sg_power - cable1）；
    - 补偿 DUT 输出功率始终需要 ``cable2`` 与 ``cable4``；
    - 驱动模式启用时，每个测试频率必须有非空的驱动映射子对象。

    文档在边界处收敛为 :class:`CableLossTable` / :class:`DriverOutputTable`；
    “某个计算路径需要哪几段线缆”由 :class:`CableLossProfile` 的
    ``require_*_path()`` 方法表达，本函数不再解释 ``cable1`` 等字符串字段名。

    任一项缺失都抛出 :class:`AmplifierCalibrationError`，供组装层在连接仪器前和
    用例构造时统一拒绝无效输入。
    """
    if not isinstance(loss_data, Mapping):
        raise AmplifierCalibrationError("线损输入必须是 JSON 对象")
    cable_losses = loss_data.get("cable_losses")
    if not isinstance(cable_losses, Mapping):
        raise AmplifierCalibrationError("线损输入缺少 cable_losses 子对象")
    try:
        loss_table = CableLossTable.from_mapping(cable_losses, source="线损输入")
    except (TypeError, ValueError) as error:
        raise AmplifierCalibrationError(str(error)) from error

    frequency_values = [Frequency.from_ghz(value) for value in frequencies]
    for frequency in frequency_values:
        try:
            profile = loss_table.profile_at(frequency)
            # DUT 输出补偿始终需要线④ + 线②；无驱动时 DUT 输入还需要线①。
            profile.require_dut_output_path()
            if not driver_enabled:
                profile.require_dut_input_path()
        except MeasurementValueError as error:
            raise AmplifierCalibrationError(str(error)) from error
    if not driver_enabled:
        return
    if not isinstance(driver_mapping, Mapping):
        raise AmplifierCalibrationError("驱动模式启用时缺少有效的驱动映射输入")
    try:
        driver_table = DriverOutputTable.from_mapping(
            driver_mapping, source="驱动映射输入"
        )
    except (TypeError, ValueError) as error:
        raise AmplifierCalibrationError(str(error)) from error
    for frequency in frequency_values:
        if not driver_table.has_points(frequency):
            raise AmplifierCalibrationError(
                f"驱动映射缺少测试频率 {frequency.key} 的有效映射数据"
            )


def _split_result_files(payload: Mapping[str, Any]) -> dict[str, Any]:
    """把每个频率的扫描结果拆成独立的 ``results/`` 文件。

    每个拆分文件带有稳定的 ``schema_version``、``stage``、``run_id``、
    ``measurement_type``、``frequency`` 和 ``result`` 字段，脱离 GUI 后仍可单独
    识别来源与语义。返回值供 :meth:`RunStageRecorder.complete` 作为 ``extra_files``
    一并登记，因此所有拆分文件都能从 manifest 找到。
    """
    results = payload.get("results")
    if not isinstance(results, Mapping):
        return {}
    files: dict[str, Any] = {}
    for frequency, sweep in results.items():
        name = f"results/amplifier_measurement_{_safe_token(frequency)}.json"
        if name in files:
            raise ValueError(f"主功放拆分结果文件名冲突，拒绝覆盖: {name}")
        files[name] = {
            "schema_version": AMPLIFIER_SPLIT_SCHEMA_VERSION,
            "stage": STAGE_AMPLIFIER,
            "run_id": payload.get("run_id"),
            "measurement_type": payload.get("measurement_type"),
            "frequency": frequency,
            "result": sweep,
        }
    return files


def _safe_token(value: Any) -> str:
    """把频率等值转成文件名安全片段，避免路径穿越或非法字符。"""
    token = _UNSAFE_FILENAME.sub("_", str(value)).strip("._")
    return token or "unknown"


def _service_config(configuration: Any) -> Mapping[str, Any]:
    if isinstance(configuration, Mapping):
        return configuration
    if hasattr(configuration, "test_plan") and hasattr(configuration, "run_mapping"):
        return _run_configuration_to_service_config(configuration)
    raise TypeError("无法将运行配置转换为主功放测量配置")


def _saved_path(saved: Any, name: str) -> str | None:
    value = getattr(saved, name, None)
    return str(value) if value is not None else None


def _run_configuration_to_service_config(configuration: Any) -> dict[str, Any]:
    """Convert the split configuration model to the legacy service contract.

    ``AmplifierMeasurementService`` still consumes the historical measurement
    shape.  Legacy runs retain that exact payload in ``TestPlan.raw``; modern
    split runs are rebuilt from the plan and resource mapping at this boundary.
    """
    plan = configuration.test_plan
    raw = getattr(plan, "raw", {})
    required = {
        "test_frequencies",
        "signal_source",
        "compression_point",
        "driver_mode",
        "attenuator",
        "dut_config",
        "power_supply_assignment",
    }
    if isinstance(raw, Mapping) and required.issubset(raw):
        return dict(raw)

    mapping = configuration.run_mapping
    supplies = _assignment_supplies(mapping, "dut_power_channels")
    driver_supplies = _assignment_supplies(mapping, "driver_power_channels")
    supply_assignment = {
        "dut_amplifier": {"supplies": supplies},
        "driver_amplifier": {"supplies": driver_supplies},
    }
    return {
        "test_frequencies": list(plan.frequencies),
        "signal_source": {
            "start_power": plan.start_power,
            "stop_power": plan.stop_power,
            "step": plan.power_step,
        },
        "compression_point": {"type": plan.compression_point},
        "driver_mode": {"enabled": bool(plan.driver_enabled)},
        "attenuator": {"type": f"{plan.attenuator_value}dB"},
        "dut_config": {"max_input_power": plan.max_input_power},
        "power_supply_assignment": supply_assignment,
    }


def _assignment_supplies(mapping: Any, field: str) -> dict[str, dict[str, Any]]:
    """把通道映射还原成 ``supply_id -> {name, channel}``。

    新快照的通道值是显式路由 ``supply_id/CHx``，按前缀分组，因此 DUT 的 gate 与
    drain 来自不同电源时各自成一条；旧快照的裸通道名（``CH1``）没有前缀，归到
    唯一那台电源，由 ``legacy_power_supply()`` 给出。
    """
    channels = getattr(mapping, field, ())
    legacy_name = _legacy_supply_name(mapping)
    grouped: dict[str, list[str]] = {}
    for item in channels:
        token = getattr(item, "channel", None)
        if not token:
            continue
        supply_name, channel = _split_channel_token(token, legacy_name)
        grouped.setdefault(supply_name, []).append(channel)
    return {
        name: {"name": name, "channel": channel_names}
        for name, channel_names in grouped.items()
    }


def _split_channel_token(token: str, legacy_name: str | None) -> tuple[str, str]:
    supply_name, separator, channel = str(token).partition("/")
    if separator and supply_name:
        return supply_name, channel
    # 裸通道名是旧快照的写法：它属于唯一那台电源；没有唯一电源时保留原样，
    # 让装配阶段按“引用了不存在的电源”明确失败，而不是猜一台。
    return legacy_name or "power_supply", str(token)


def _legacy_supply_name(mapping: Any) -> str | None:
    reader = getattr(mapping, "legacy_power_supply", None)
    legacy = reader() if callable(reader) else None
    return getattr(legacy, "model", None) or None
