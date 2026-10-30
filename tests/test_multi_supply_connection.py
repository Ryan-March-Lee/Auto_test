"""步骤 12：连接与安全关闭使用动态电源集合。

固定的行为：

- ``instruments.power_supplies`` 以 ``supply_id`` 为键时，多台电源各按自己的
  地址连接、各自做 ``*IDN?`` 身份检查和“初始输出已关闭”检查；
- 供电分配可以写 ``supply_id``（新配置），也可以写该电源自己的 ``label`` 或
  旧文件残留的 ``visual`` 槽位；三者都唯一解析到同一台电源，含混时在连接前失败；
- 物理路由统一是 ``supply_id/CHx``，急停只关闭实际声明的通道，且先漏后栅；
- 任意异常路径（身份不符 / 初始输出未关闭 / 第二台连接失败 / 准备失败）都关闭
  所有已打开的资源，并把 ResourceManager 一起释放；
- 日志中出现 ``DP8B1519/CH1`` 形式的短标签与通道；集合键与 ``label`` 不同时，通道动作
  日志写 ``现场短标签/CHx``，路由仍发给 token；
- 每台电源连接前打印一行身份核对，含完整身份、``*IDN?`` 原文与核对结论
  （一致 / 不一致 / 无法比较）。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_multi_supply_connection -v
```
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyvisa import constants, errors

from application.errors import CleanupError, DeviceCapabilityError, DeviceConnectionError
from instrument.assembly_plan import hardware_assembly_plan
from instrument.measurement_adapter import PortMeasurementAdapter
from instrument.measurement_factory import (
    _ConfiguredPowerSupply,
    _VisaSession,
    create_measurement_port,
)
from instrument.simulation import CommandRecorder, SimulatedPowerSupply
from infrastructure.logging import app_logging

SIGNAL = "SG"
SPECTRUM = "SA"
SUPPLY_A = "TCPIP0::192.168.1.102::inst0::INSTR"
SUPPLY_B = "TCPIP0::192.168.1.103::inst0::INSTR"
SUPPLY_C = "TCPIP0::192.168.1.104::inst0::INSTR"


def _supply(
    *,
    address: str,
    label: str | None = None,
    visual: str | None = None,
    gate: str = "CH1",
    drain: str = "CH2",
    enabled: bool = True,
) -> dict:
    """一台 DP832A：gate/drain 通道角色显式写在通道上。"""
    channels = {
        gate: {"role": "gate", "voltage": {"value": 2.8}, "current": {"value": 0.1}},
        drain: {"role": "drain", "voltage": {"value": 28.0}, "current": {"value": 2.0}},
    }
    item = {"address": address, "enabled": enabled, "channels": channels}
    if label is not None:
        item["label"] = label
    if visual is not None:
        item["visual"] = visual
    return item


def _assignment(supply: str, channels: list[str]) -> dict:
    return {"name": supply, "supply_id": supply, "channel": channels}


def _two_supply_config() -> dict:
    """DUT 用 DP8B1519 的 CH1/CH2，驱动功放用 DP8B0950 的 CH1/CH2。

    ``key`` 是集合键（就是 ``supply_id``），``label`` 是它的现场短标签，
    ``visual`` 是旧文件残留的槽位名——三者写在不同的电源上，覆盖三种引用写法。
    """
    return {
        "instruments": {
            "signal_generator": {"address": SIGNAL, "enabled": True},
            "spectrum_analyzer": {"address": SPECTRUM, "enabled": True},
            "power_supplies": {
                "DP8B1519": _supply(address=SUPPLY_A),
                "DP8B0950": _supply(address=SUPPLY_B, label="DP8B0950"),
                "DP8B0885": _supply(address=SUPPLY_C, label="DP8B0885", visual="PS3"),
            },
        },
        "power_supply_assignment": {
            "dut_amplifier": {
                "supplies": {"carrier": _assignment("DP8B1519", ["CH1", "CH2"])}
            },
            "driver_amplifier": {
                "supplies": {"main": _assignment("DP8B0950", ["CH1", "CH2"])}
            },
        },
    }


class _FakeResource:
    """一台假的 DP832A；``outputs`` 返回初始输出状态。"""

    def __init__(self, address, *, outputs="OFF", identity=None):
        self.address = address
        self.timeout = None
        self.closed = False
        self.closed_at = None
        self.commands = []
        self._outputs = outputs
        self._identity = identity or "RIGOL TECHNOLOGIES,DP832A,TEST,1.0"

    def write(self, command):
        self.commands.append(("write", command))

    def query(self, command):
        self.commands.append(("query", command))
        if command == "*IDN?":
            return self._identity
        if command.startswith("OUTP?"):
            return self._outputs
        if command.startswith(":MEASure:VOLTage?"):
            return "2.8"
        if command.startswith(":MEASure:CURRent?"):
            return "0.25"
        return "-30.0"

    def close(self):
        self.closed = True


class _FakeResourceManager:
    """按地址打开假的 VISA 资源；记录打开顺序与释放情况。

    ``outputs`` 按地址给出该电源初始输出查询结果（默认 ``OFF``）；测试需要时
    可以给单台电源设成 ``"ON"`` / ``1`` 来覆盖“初始输出未关闭”的分支。
    """

    def __init__(self, *, fail_open=(), resource_factory=None, outputs=None):
        self.resources = []
        self.opened_addresses = []
        self.closed = False
        self._fail_open = set(fail_open)
        self._outputs = {str(key): value for key, value in dict(outputs or {}).items()}
        self._resource_factory = resource_factory or (
            lambda address: _FakeResource(
                address, outputs=str(self._outputs.get(address, "OFF"))
            )
        )

    def open_resource(self, address):
        if address in self._fail_open:
            raise errors.VisaIOError(constants.StatusCode.error_resource_not_found)
        self.opened_addresses.append(address)
        resource = self._resource_factory(address)
        self.resources.append(resource)
        return resource

    def close(self):
        self.closed = True

    def resource(self, address):
        for resource in self.resources:
            if resource.address == address:
                return resource
        raise AssertionError(f"没有打开过 {address}")


class _LogCapture:
    """临时给工厂/适配器的 logger 挂一个记录 handler。"""

    def __init__(self, *loggers):
        import logging

        self.messages = []
        self._records = []
        for logger in loggers:
            handler = logging.Handler()
            handler.emit = self._records.append
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            self.messages.append((logger, handler))

    def text(self):
        return "\n".join(record.getMessage() for record in self._records)

    def close(self):
        for logger, handler in self.messages:
            logger.removeHandler(handler)


class _PatchedManager:
    def __init__(self, manager):
        self.manager = manager
        self._patch = None

    def __enter__(self):
        self._patch = patch(
            "instrument.measurement_factory.pyvisa.ResourceManager",
            return_value=self.manager,
        )
        self._patch.start()
        return self.manager

    def __exit__(self, *exc):
        self._patch.stop()
        return False


class _ConfigFile:
    def __init__(self, config):
        self.config = config
        self._directory = None

    def __enter__(self):
        self._directory = tempfile.TemporaryDirectory()
        path = Path(self._directory.name) / "config.json"
        path.write_text(json.dumps(self.config), encoding="utf-8")
        return str(path)

    def __exit__(self, *exc):
        self._directory.cleanup()
        return False


class MultiSupplyConnectionTests(unittest.TestCase):
    def test_two_supplies_are_connected_and_checked_individually(self):
        config = _two_supply_config()
        manager = _FakeResourceManager(outputs={SUPPLY_B: "0"})
        with _ConfigFile(config) as path, _PatchedManager(manager):
            port = create_measurement_port(path, mode="hardware")

        # 连接的电源是“被分配引用的那两台”，按 supply_id 排序。
        self.assertEqual(manager.opened_addresses, [SIGNAL, SPECTRUM, SUPPLY_B, SUPPLY_A])
        for address in (SUPPLY_A, SUPPLY_B):
            resource = manager.resource(address)
            # 每台都做过身份检查和初始输出检查（只查自己分配的通道）。
            self.assertIn(("query", "*IDN?"), resource.commands)
            self.assertIn(("query", "OUTP? CH1"), resource.commands)
            self.assertIn(("query", "OUTP? CH2"), resource.commands)
        # 路由前缀是 supply_id：DUT 与驱动功放各自落在自己的电源上。
        self.assertEqual(
            port.dut_power_channels, {"gate": "DP8B1519/CH1", "drain": "DP8B1519/CH2"}
        )
        self.assertEqual(
            port.driver_power_channels,
            {"gate": "DP8B0950/CH1", "drain": "DP8B0950/CH2"},
        )
        self.assertEqual(port.close_all(close_rf=True), [])
        self.assertTrue(manager.closed)

    def test_identity_is_checked_on_every_supply_before_any_output_is_touched(self):
        manager = _FakeResourceManager(
            resource_factory=lambda address: _FakeResource(
                address,
                identity=(
                    "KEYSIGHT TECHNOLOGIES,E3631A,0,1.0"
                    if address == SUPPLY_B
                    else None
                ),
            )
        )
        with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
            with self.assertRaises(DeviceCapabilityError) as raised:
                create_measurement_port(path, mode="hardware")

        self.assertIn("DP8B0950", str(raised.exception))
        self.assertTrue(all(resource.closed for resource in manager.resources))
        self.assertTrue(manager.closed)

    def test_initial_output_check_is_per_supply(self):
        # "0" 与 "OFF" 都是已关闭；只有一台报告非关闭时整体失败。
        manager = _FakeResourceManager(outputs={SUPPLY_B: 1})
        with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
            with self.assertRaisesRegex(DeviceCapabilityError, "初始输出未关闭"):
                create_measurement_port(path, mode="hardware")

        self.assertTrue(all(resource.closed for resource in manager.resources))
        self.assertTrue(manager.closed)

    def test_second_supply_connection_failure_closes_the_first_supply(self):
        # DP8B0950 排序在前、先被打开并完成检查；DP8B1519 打不开时它必须被释放。
        manager = _FakeResourceManager(fail_open={SUPPLY_A})
        with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
            with self.assertRaises(DeviceConnectionError):
                create_measurement_port(path, mode="hardware")

        # 先打开并检查过的 DP8B0950 必须释放；信号源/频谱仪同样是本函数打开的。
        self.assertEqual(manager.opened_addresses, [SIGNAL, SPECTRUM, SUPPLY_B])
        self.assertTrue(manager.resource(SUPPLY_B).closed)
        self.assertTrue(manager.resource(SIGNAL).closed)
        self.assertTrue(manager.resource(SPECTRUM).closed)
        self.assertTrue(manager.closed)

    def test_prepare_failure_closes_every_supply(self):
        def failing_session_factory(*args, **kwargs):
            session = _VisaSession(*args, **kwargs)

            def fail_prepare(*_args, **_kwargs):
                raise RuntimeError("prepare failed")

            session.prepare = fail_prepare
            return session

        manager = _FakeResourceManager()
        with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
            with patch(
                "instrument.measurement_factory._VisaSession",
                side_effect=failing_session_factory,
            ):
                with self.assertRaisesRegex(RuntimeError, "prepare failed"):
                    create_measurement_port(path, mode="hardware")

        self.assertTrue(manager.resource(SUPPLY_A).closed)
        self.assertEqual(manager.opened_addresses, [SIGNAL, SPECTRUM, SUPPLY_B, SUPPLY_A])
        self.assertTrue(manager.resource(SUPPLY_B).closed)
        self.assertTrue(manager.resource(SUPPLY_B).closed)
        self.assertTrue(manager.resource(SIGNAL).closed)
        self.assertTrue(manager.resource(SPECTRUM).closed)
        self.assertTrue(manager.closed)

    def test_emergency_shutdown_covers_both_supplies_drain_before_gate(self):
        manager = _FakeResourceManager()
        with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
            port = create_measurement_port(path, mode="hardware")
            self.assertEqual(port.emergency_power_off_all(), [])
            self.assertEqual(port.close_all(close_rf=True), [])

        self.assertEqual(
            port._last_emergency_power_off,
            [
                (0, "DP8B0950/CH2"),
                (1, "DP8B1519/CH2"),
                (0, "DP8B0950/CH1"),
                (1, "DP8B1519/CH1"),
            ],
        )
        self.assertIn(("write", ":OUTPut CH2,OFF"), manager.resource(SUPPLY_B).commands)
        self.assertIn(("write", ":OUTPut CH1,OFF"), manager.resource(SUPPLY_A).commands)

    def test_emergency_shutdown_only_touches_declared_channels(self):
        manager = _FakeResourceManager()
        with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
            port = create_measurement_port(path, mode="hardware")
            # 第三台电源已启用、有地址，但没有被任何分配引用：不连接、不关闭。
            self.assertEqual(port.emergency_power_off_all(), [])
            self.assertEqual(port.close_all(close_rf=True), [])

        self.assertNotIn(SUPPLY_C, manager.opened_addresses)
        for channel in port._last_emergency_power_off:
            self.assertIn(channel[1].split("/")[0], {"DP8B1519", "DP8B0950"})

    def test_measurement_path_uses_each_supply_channel(self):
        """上电、读取与测量分别落在各自电源的通道上，不串到另一台设备。"""
        manager = _FakeResourceManager()
        with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
            port = create_measurement_port(path, mode="hardware")
            port.setup_driver_amplifier_power()
            port.setup_dut_power()
            port.power_on_driver()
            port.power_on_sequence()
            port.start_measurement()
            port.set_frequency(2.3)
            port.set_span(10)
            driver_voltage = port.read_voltage("DP8B0950", "CH1")
            dut_current = port.read_current("DP8B1519", "CH2")
            measured = port.measure_power_with_average()
            self.assertEqual(port.close_all(close_rf=True), [])

        self.assertEqual(driver_voltage, 2.8)
        self.assertEqual(dut_current, 0.25)
        self.assertEqual(measured, -30.0)
        driver = manager.resource(SUPPLY_B)
        dut = manager.resource(SUPPLY_A)
        self.assertIn(("write", ":SOURce1:VOLTage 2.8"), driver.commands)
        self.assertIn(("write", ":SOURce2:VOLTage 28"), dut.commands)
        self.assertIn(("write", ":OUTPut CH1,ON"), driver.commands)
        self.assertIn(("write", ":OUTPut CH2,ON"), dut.commands)
        self.assertIn(("query", ":MEASure:CURRent? CH2"), dut.commands)
        self.assertNotIn(("query", ":MEASure:CURRent? CH2"), driver.commands)
        # 收尾：两台电源都掉电并释放。
        for resource in (driver, dut):
            self.assertIn(("write", ":OUTPut CH1,OFF"), resource.commands)
            self.assertIn(("write", ":OUTPut CH2,OFF"), resource.commands)
            self.assertTrue(resource.closed)

    def test_initial_output_check_covers_every_configured_channel(self):
        """安全前提：整台电源的输出都必须已关闭，未分配的通道也不例外。"""
        manager = _FakeResourceManager()
        with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
            create_measurement_port(path, mode="hardware")

        # DP8B0950 的通道全部被分配，检查的就是这些通道。
        self.assertEqual(
            [
                command
                for kind, command in manager.resource(SUPPLY_B).commands
                if kind == "query" and command.startswith("OUTP?")
            ],
            ["OUTP? CH1", "OUTP? CH2"],
        )
        # 错误消息点名检查过的 ``短标签/CHx``，操作者能核对现场标签。
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B0950"]["label"] = "现场 B 电源"
        manager = _FakeResourceManager(outputs={SUPPLY_B: "ON"})
        with _ConfigFile(config) as path, _PatchedManager(manager):
            with self.assertRaises(DeviceCapabilityError) as raised:
                create_measurement_port(path, mode="hardware")
        self.assertIn("现场 B 电源/CH1", str(raised.exception))
        self.assertIn("现场 B 电源/CH2", str(raised.exception))

    def test_logs_name_the_short_label_and_channel(self):
        capture = _LogCapture(app_logging.get_logger("instrument.measurement_factory"))
        manager = _FakeResourceManager()
        try:
            with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
                port = create_measurement_port(path, mode="hardware")
                self.assertEqual(port.emergency_power_off_all(), [])
                self.assertEqual(port.close_all(close_rf=True), [])
        finally:
            capture.close()

        text = capture.text()
        self.assertIn("DP8B1519/CH1", text)
        self.assertIn("DP8B0950/CH2", text)
        self.assertIn("DP8B1519", text)

    def test_connection_writes_one_identity_line_per_supply(self):
        """审计：每台电源一行身份核对，含短标签、型号、完整序列号与核对结论。"""
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B1519"].update(
            {
                "model": "DP832A",
                "serial": "DP8B214901519",
                "device_key": "rigol technologies:dp8b214901519",
            }
        )
        # 设备自己应答的序列号与配置一致：结论必须是“一致”。
        manager = _FakeResourceManager(
            resource_factory=lambda address: _FakeResource(
                address,
                identity=(
                    "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16"
                    if address == SUPPLY_A
                    else "RIGOL TECHNOLOGIES,DP832A,DP8B174700950,00.01.14"
                ),
            )
        )
        capture = _LogCapture(
            app_logging.get_logger("instrument.measurement_factory")
        )
        try:
            with _ConfigFile(config) as path, _PatchedManager(manager):
                create_measurement_port(path, mode="hardware")
        finally:
            capture.close()

        text = capture.text()
        # 现场短标签在前，完整序列号与设备键在后：日志能直接对应实物，也能追溯到设备。
        self.assertIn(
            "生产电源身份核对: DP8B1519（DP832A） · 序列号 DP8B214901519"
            " · 设备键 rigol technologies:dp8b214901519",
            text,
        )
        # 设备自己应答的 ``*IDN?`` 原文同样在日志里，不只看配置里写的那份身份。
        self.assertIn(
            "设备应答 *IDN? RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16", text
        )
        self.assertIn("核对结果 一致", text)
        # 没有型号/序列号的电源只显示短标签，也没有可比对的序列号。
        self.assertIn("生产电源身份核对: DP8B0950；", text)
        self.assertIn("核对结果 无法比较（配置没有序列号，设备序列号 DP8B174700950）", text)

    def test_identity_mismatch_is_reported_with_both_serials(self):
        """配置与实物不是同一台时必须能在日志里看出来，而不是两份身份并排放着。"""
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B1519"]["serial"] = "DP8B214901519"
        # 地址上应答的是另一台 DP832A（序列号不同）。
        manager = _FakeResourceManager(
            resource_factory=lambda address: _FakeResource(
                address,
                identity=(
                    "RIGOL TECHNOLOGIES,DP832A,DP8B174400885,00.01.14"
                    if address == SUPPLY_A
                    else None
                ),
            )
        )
        capture = _LogCapture(app_logging.get_logger("instrument.measurement_factory"))
        try:
            with _ConfigFile(config) as path, _PatchedManager(manager):
                create_measurement_port(path, mode="hardware")
        finally:
            capture.close()

        text = capture.text()
        self.assertIn(
            "核对结果 不一致（配置 DP8B214901519，设备序列号 DP8B174400885）", text
        )

    def test_channel_actions_log_the_short_label_not_the_routing_key(self):
        """通道动作（上电/掉电/急停）日志写现场短标签；路由仍发给 ``supply_id/CHx``。"""
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B1519"]["label"] = "现场 A 电源"
        manager = _FakeResourceManager()
        capture = _LogCapture(
            app_logging.get_logger("instrument.power_control"),
            app_logging.get_logger("instrument.measurement_adapter"),
        )
        try:
            with _ConfigFile(config) as path, _PatchedManager(manager):
                port = create_measurement_port(path, mode="hardware")
                port.power_on_sequence()
                self.assertEqual(port.emergency_power_off_all(), [])
                self.assertEqual(port.close_all(close_rf=True), [])
        finally:
            capture.close()

        text = capture.text()
        self.assertIn("打开电源通道 现场 A 电源/CH1（gate）", text)
        self.assertIn("紧急关闭电源通道 现场 A 电源/CH2", text)
        # 日志不能把集合键当成设备名。
        self.assertNotIn("打开电源通道 DP8B1519/CH1", text)
        # 设备实际收到的仍是路由 token。
        resource = manager.resource(SUPPLY_A)
        self.assertIn(("write", ":OUTPut CH1,ON"), resource.commands)
        self.assertIn(("write", ":OUTPut CH2,OFF"), resource.commands)

    def test_route_uses_the_supply_id_when_the_label_differs(self):
        """短标签只用于显示：标签与集合键不同时，物理路由仍按 ``supply_id/CHx``。"""
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B1519"]["label"] = "现场 A 电源"
        manager = _FakeResourceManager()
        capture = _LogCapture(app_logging.get_logger("instrument.measurement_factory"))
        try:
            with _ConfigFile(config) as path, _PatchedManager(manager):
                port = create_measurement_port(path, mode="hardware")
                self.assertEqual(
                    port.dut_power_channels,
                    {"gate": "DP8B1519/CH1", "drain": "DP8B1519/CH2"},
                )
                # 配置 + 上电 + 读取都落在被分配的那台电源上，且不会被标签挡住。
                port.setup_dut_power()
                port.power_on_sequence()
                self.assertEqual(port.read_voltage("DP8B1519", "CH1"), 2.8)
                self.assertEqual(port.emergency_power_off_all(), [])
                self.assertEqual(port.close_all(close_rf=True), [])
        finally:
            capture.close()

        resource = manager.resource(SUPPLY_A)
        self.assertIn(("write", ":SOURce1:VOLTage 2.8"), resource.commands)
        self.assertIn(("write", ":OUTPut CH1,ON"), resource.commands)
        self.assertIn(("query", ":MEASure:VOLTage? CH1"), resource.commands)
        self.assertEqual(
            port._last_emergency_power_off,
            [
                (0, "DP8B0950/CH2"),
                (1, "DP8B1519/CH2"),
                (0, "DP8B0950/CH1"),
                (1, "DP8B1519/CH1"),
            ],
        )
        # 日志说人话：短标签 + 通道。
        text = capture.text()
        self.assertIn("现场 A 电源/CH1", text)



class AssignmentReferenceTests(unittest.TestCase):
    def _plan(self, config, reference):
        assignment = dict(
            config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]
        )
        assignment["name"] = reference
        assignment["supply_id"] = reference
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"] = assignment
        config["power_supply_assignment"]["driver_amplifier"]["supplies"] = {}
        return hardware_assembly_plan(config)

    def test_supply_id_and_label_resolve_to_the_same_supply(self):
        for reference, supply_id in (("DP8B1519", "DP8B1519"), ("DP8B0950", "DP8B0950")):
            with self.subTest(reference=reference):
                plan = self._plan(_two_supply_config(), reference)
                self.assertEqual(plan["supply_names"], [supply_id])
                self.assertEqual(plan["all_channels"]["gate"], f"{supply_id}/CH1")

    def test_visual_slot_resolves_to_the_supply_actually_named_by_the_config(self):
        # 旧文件里 ``visual: PS3`` 指向 DP8B0885；这里按身份解析，不按槽位序号。
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B0885"]["enabled"] = True
        plan = self._plan(config, "PS3")
        self.assertEqual(plan["supply_names"], ["DP8B0885"])
        self.assertEqual(plan["supply_labels"]["DP8B0885"], "DP8B0885")
        self.assertEqual(plan["all_channels"]["gate"], "DP8B0885/CH1")

    def test_supply_without_a_label_uses_its_supply_id(self):
        plan = self._plan(_two_supply_config(), "DP8B1519")
        self.assertEqual(plan["supply_labels"]["DP8B1519"], "DP8B1519")

    def test_unknown_reference_fails_before_opening_anything(self):
        manager = _FakeResourceManager()
        config = _two_supply_config()
        for field in ("supply_id", "name"):
            config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"][
                field
            ] = "PS1"
        with _ConfigFile(config) as path, _PatchedManager(manager):
            with self.assertRaisesRegex(ValueError, "不存在的电源"):
                create_measurement_port(path, mode="hardware")
        self.assertEqual(manager.opened_addresses, [])

    def test_ambiguous_supply_id_alias_fails_instead_of_picking_one(self):
        # 另一台电源挂着这个集合键当 label：同一个名字指向两台设备，必须失败。
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B0885"]["label"] = "DP8B0950"
        config["instruments"]["power_supplies"]["DP8B0885"]["enabled"] = True
        with self.assertRaisesRegex(ValueError, "同时是另一台电源的 label/visual"):
            self._plan(config, "DP8B0950")

    def test_two_labels_with_the_same_name_fail_instead_of_picking_one(self):
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B0885"]["label"] = "DP8B0950"
        config["instruments"]["power_supplies"]["DP8B0885"]["enabled"] = True
        config["instruments"]["power_supplies"]["DP8B0950"]["visual"] = "DUP"
        config["instruments"]["power_supplies"]["DP8B0885"]["visual"] = "DUP"
        with self.assertRaisesRegex(ValueError, "对应多台电源"):
            self._plan(config, "DUP")

    def test_disabled_supply_is_not_a_resolution_target(self):
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B1519"]["enabled"] = False
        with self.assertRaisesRegex(ValueError, "未启用的电源"):
            self._plan(config, "DP8B1519")

    def test_duplicate_short_labels_fail_instead_of_lying_in_the_log(self):
        """两台电源显示名相同：日志无法指向唯一设备，装配前就拒绝。"""
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B0950"]["label"] = "DP8B1519"
        config["instruments"]["power_supplies"]["DP8B0885"]["label"] = "DP8B1519"
        config["instruments"]["power_supplies"]["DP8B0885"]["enabled"] = True
        # 引用写另一台已启用但没被引用的电源，避免先撞上“同名别名”这条更早的
        # 引用歧义规则；这里专门覆盖显示名重复。
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"] = (
            _assignment("DP8B0885", ["CH1", "CH2"])
        )
        with self.assertRaisesRegex(ValueError, "短标签重复"):
            hardware_assembly_plan(config)


class ConfiguredPowerSupplyRoutingTests(unittest.TestCase):
    def test_routing_uses_the_supply_id_and_rejects_unknown_tokens(self):
        first = SimulatedPowerSupply(CommandRecorder())
        second = SimulatedPowerSupply(CommandRecorder())
        power = _ConfiguredPowerSupply(
            {"DP8B1519": first, "DP8B0950": second},
            supply_labels={"DP8B1519": "DP8B1519", "DP8B0950": "DP8B0950"},
        )
        power.connect()
        power.set_voltage_v("DP8B1519/CH1", 2.8)
        power.set_current_limit_a("DP8B0950/CH2", 1.5)

        self.assertEqual(first.voltages, {"CH1": 2.8})
        self.assertEqual(second.currents, {"CH2": 1.5})
        with self.assertRaisesRegex(ValueError, "未配置的生产电源通道"):
            power.set_voltage_v("DP8B0885/CH1", 1.0)
        with self.assertRaisesRegex(ValueError, "未配置的生产电源通道"):
            power.set_voltage_v("CH1", 1.0)
        power.close()

    def test_close_failure_names_the_supply_label_and_still_closes_the_others(self):
        recorder = CommandRecorder()

        def fail_close(*_args, **_kwargs):
            raise OSError("visa close failed")

        failing = SimulatedPowerSupply(recorder)
        failing.connect()
        failing.close = fail_close
        healthy = SimulatedPowerSupply(recorder)
        healthy.connect()
        power = _ConfiguredPowerSupply({"DP8B1519": failing, "DP8B0950": healthy})

        with self.assertRaises(CleanupError) as raised:
            power.close()

        # 失败记录点名出问题的那台电源，而不是编号；其余电源照常释放。
        self.assertEqual(
            [failure.action for failure in raised.exception.failures], ["DP8B1519"]
        )
        self.assertFalse(healthy.connected)

    def test_port_adapter_is_the_formal_hardware_port(self):
        manager = _FakeResourceManager()
        with _ConfigFile(_two_supply_config()) as path, _PatchedManager(manager):
            port = create_measurement_port(path, mode="hardware")
            self.assertIsInstance(port, PortMeasurementAdapter)
            self.assertEqual(port.close_all(close_rf=True), [])


if __name__ == "__main__":
    unittest.main()
