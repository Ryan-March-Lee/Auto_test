"""设备发现面板：扫描网段、展示发现结果并按角色勾选本次测试所需设备。

边界：

- 面板只负责界面与扫描线程生命周期；扫描实现由 ``scanner_factory(mode)`` 注入，
  因此离线测试与现场扫描共用同一套界面逻辑。
- 面板不改配置、不连接仪器：用户确认“应用到配置”后，只把所选设备交给配置页，
  由配置页写入地址控件与身份记录。
- 发现结果**包含未知设备**：未在设备表中登记的序列号同样列在表里并标记为未登记；
  身份完全读不出来的地址也留在表里，但标记为“无法识别”，不允许被应用成测试角色。
"""

from __future__ import annotations

import ipaddress
import re
import threading
from typing import Any, Callable, Sequence

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from domain.instruments.models import make_device_key
from infrastructure.discovery.instrument_discovery import (
    SCAN_MODE_NETWORK,
    SCAN_MODE_SIMULATED,
    InstrumentDiscoveryError,
)
from infrastructure.discovery.simulated_network import DEMO_SUBNET

from .input_controls import WheelSafeComboBox

# 角色选项是“本次测试语义”，与设备身份（序列号）分开：设备可以换 IP，角色不会变。
ROLE_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "不指定"),
    ("signal_generator", "信号发生器"),
    ("spectrum_analyzer", "频谱分析仪"),
    ("PS1", "电源 PS1"),
    ("PS2", "电源 PS2"),
    ("PS3", "电源 PS3"),
    ("PS4", "电源 PS4"),
)
SCAN_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    (SCAN_MODE_SIMULATED, "模拟设备（离线演示）"),
    (SCAN_MODE_NETWORK, "真实网络扫描"),
)
_TABLE_HEADERS = ("使用", "测试角色", "设备名称", "序列号", "地址", "状态")
_IP_PATTERN = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def subnet_from_address(address: Any) -> str | None:
    """从已有 VISA 地址推导 ``/24`` 网段，作为扫描框的初值。"""
    if not isinstance(address, str):
        return None
    match = _IP_PATTERN.search(address)
    if match is None:
        return None
    try:
        return str(ipaddress.IPv4Network(f"{match.group(1)}/24", strict=False))
    except ValueError:
        return None


def binding_device_key(record: Any) -> str | None:
    """返回一条设备绑定记录的身份键；无法确定身份时返回 ``None``。

    身份是 ``厂商:序列号``。旧配置只有厂商与序列号两段时在这里重新拼出同一个键；
    只有序列号没有厂商时无法确定身份，返回 ``None``（不做“仅按序列号”的猜测匹配，
    否则两个厂商的同号设备会被混为一谈）。
    """
    if not isinstance(record, dict):
        return None
    key = str(record.get("device_key") or "").strip().casefold()
    if key:
        return key
    vendor = str(record.get("vendor") or "").strip()
    serial = str(record.get("serial") or "").strip()
    if not vendor or not serial:
        return None
    try:
        return make_device_key(vendor, serial)
    except ValueError:
        return None


class DiscoveryWorker(QThread):
    """在后台线程执行一次扫描；只上报结果与失败，不触碰界面。"""

    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        scanner_factory: Callable[[str], Any],
        subnet: str,
        mode: str,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.scanner_factory = scanner_factory
        self.subnet = subnet
        self.mode = mode
        self.scanner: Any = None
        # 取消标记在构造时创建并交给扫描器：即使取消发生在扫描开始之前也不会丢失。
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            scanner = self.scanner_factory(self.mode)
        except (InstrumentDiscoveryError, OSError, ValueError) as error:
            self.failed.emit(f"无法开始扫描: {error}")
            return
        scanner.set_cancel_event(self.cancel_event)
        if self.isInterruptionRequested():
            self.cancel_event.set()
        self.scanner = scanner
        try:
            result = scanner.scan(
                self.subnet,
                progress=lambda done, total: self.progress.emit(int(done), int(total)),
            )
        except InstrumentDiscoveryError as error:
            self.failed.emit(str(error))
            return
        except (OSError, ValueError, RuntimeError) as error:
            # 扫描失败也必须回到界面：否则按钮一直停在“正在扫描”，用户无法重试。
            self.failed.emit(f"扫描失败: {error}")
            return
        if self.cancel_event.is_set() or self.isInterruptionRequested():
            self.cancelled.emit()
            return
        self.completed.emit(result)


class DeviceDiscoveryPanel(QGroupBox):
    """扫描 + 结果选择控件；通过 :attr:`apply_requested` 交付用户勾选的设备。"""

    apply_requested = Signal(object)
    clear_requested = Signal()
    log_message = Signal(str)

    def __init__(
        self,
        *,
        scanner_factory: Callable[[str], Any],
        default_subnet: str | None = None,
        parent: Any = None,
    ) -> None:
        super().__init__("设备发现", parent)
        # 结果表高度固定，面板不参与纵向拉伸：否则两栏布局会被挤到页面很下方。
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.scanner_factory = scanner_factory
        self.worker: DiscoveryWorker | None = None
        self.devices: list[Any] = []
        self.last_mode: str = SCAN_MODE_SIMULATED
        self._rows: list[dict[str, Any]] = []
        self._closed = False
        # 配置里保存的设备身份（角色 -> 序列号/device_key），用于扫描后按身份预选。
        self.device_bindings: dict[str, dict[str, Any]] = {}
        self._build_ui(default_subnet or DEMO_SUBNET)

    def set_device_bindings(self, bindings: Any) -> None:
        """登记配置里的角色绑定；下一次扫描结果会按身份预选同一台设备。"""
        self.device_bindings = {
            str(role): dict(record)
            for role, record in (bindings or {}).items()
            if isinstance(record, dict)
        }

    def clear_results(self) -> None:
        """清空上一次扫描结果；切换/重新加载配置时调用，避免旧结果看起来像当前配置。"""
        self.devices = []
        self._clear_table()
        self.apply_btn.setEnabled(False)
        self.status_label.setText("尚未扫描")

    # ---------- 界面 ----------

    def _build_ui(self, default_subnet: str) -> None:
        layout = QVBoxLayout(self)
        controls = QGridLayout()
        self.subnet_edit = QLineEdit(str(default_subnet))
        self.subnet_edit.setMaximumWidth(200)
        self.subnet_edit.setToolTip("扫描范围，例如 192.168.1.0/24；单个地址按 /24 处理")
        self.mode_combo = WheelSafeComboBox()
        self.mode_combo.addItems([label for _value, label in SCAN_MODE_CHOICES])
        self.scan_btn = QPushButton("获取设备")
        self.scan_btn.setToolTip("扫描网段并读取每台设备的 *IDN?；不会改变任何仪器状态")
        self.status_label = QLabel("尚未扫描")
        self.status_label.setWordWrap(True)
        self.mode_hint = QLabel("")
        self.mode_hint.setWordWrap(True)
        controls.addWidget(QLabel("扫描网段:"), 0, 0)
        controls.addWidget(self.subnet_edit, 0, 1)
        controls.addWidget(QLabel("扫描方式:"), 0, 2)
        controls.addWidget(self.mode_combo, 0, 3)
        controls.addWidget(self.scan_btn, 0, 4)
        controls.addWidget(self.mode_hint, 1, 0, 1, 5)
        controls.setColumnStretch(1, 1)
        layout.addLayout(controls)
        self.mode_combo.currentIndexChanged.connect(self._update_mode_hint)

        self.table = QTableWidget(0, len(_TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(list(_TABLE_HEADERS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setTextElideMode(Qt.ElideRight)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        # 序列号/地址/状态按内容定宽会挤掉设备名：固定列宽，让名称列拿到剩余宽度。
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(3, 125)
        self.table.setColumnWidth(4, 125)
        self.table.setColumnWidth(5, 105)
        self._fit_table_height(0)
        layout.addWidget(self.table)

        actions = QHBoxLayout()
        self.apply_btn = QPushButton("应用所选设备到配置")
        self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip("把勾选设备的当前地址与身份（序列号）写入仪器连接配置")
        self.clear_btn = QPushButton("清除设备绑定")
        self.clear_btn.setToolTip("删除配置中记录的角色与设备身份对应关系，不改动地址")
        actions.addWidget(self.apply_btn, 2)
        actions.addWidget(self.clear_btn, 1)
        layout.addLayout(actions)
        layout.addWidget(self.status_label)

        self.scan_btn.clicked.connect(self.request_scan)
        self.apply_btn.clicked.connect(self.request_apply)
        self.clear_btn.clicked.connect(self.request_clear)
        self._update_mode_hint()

    # ---------- 扫描 ----------

    def current_mode(self) -> str:
        index = self.mode_combo.currentIndex()
        return SCAN_MODE_CHOICES[index][0] if 0 <= index < len(SCAN_MODE_CHOICES) else SCAN_MODE_SIMULATED

    def _update_mode_hint(self, *_args) -> None:
        """模拟模式给出显著提示：演示结果不能被误当成现场设备。"""
        simulated = self.current_mode() == SCAN_MODE_SIMULATED
        self.mode_hint.setText(
            "模拟数据仅用于离线演示：结果不会写入设备表，也不能用于真实测量。"
            if simulated
            else "将向网段内每个地址发送一条 *IDN? 只读查询；不会改变仪器状态。"
        )

    def request_scan(self) -> None:
        """启动后台扫描；同一时刻只允许一个扫描线程。"""
        if self.is_scanning():
            self.status_label.setText("扫描正在进行，请等待完成")
            return
        subnet = self.subnet_edit.text().strip()
        self.devices = []
        self._clear_table()
        self.apply_btn.setEnabled(False)
        self._set_controls_enabled(False)
        self.status_label.setText(f"正在扫描 {subnet or '(空)'} …")
        worker = DiscoveryWorker(self.scanner_factory, subnet, self.current_mode(), self)
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(self._on_worker_finished)
        self.worker = worker
        worker.start()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.scan_btn.setEnabled(enabled)
        self.subnet_edit.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        self.clear_btn.setEnabled(enabled)

    def _on_progress(self, done: int, total: int) -> None:
        if self._closed:
            return
        self.status_label.setText(f"正在扫描 … {done}/{total}")

    def _on_completed(self, result: Any) -> None:
        if self._closed:
            return
        devices = list(result.devices)
        self.devices = devices
        self.last_mode = getattr(result, "mode", self.current_mode())
        self._fill_table(devices)
        known = sum(1 for device in devices if device.is_known)
        unregistered = sum(1 for device in devices if device.is_unknown_identity)
        unidentified = sum(1 for device in devices if device.is_unidentified)
        text = f"发现 {len(devices)} 台设备：已登记 {known} 台"
        if unregistered:
            text += f"，未登记 {unregistered} 台"
        if unidentified:
            text += f"，无法识别 {unidentified} 个地址"
        failures = tuple(result.failures)
        notices = tuple(getattr(result, "notices", ()) or ())
        if failures:
            text += "；" + "；".join(failures)
        if notices:
            text += "；提示：" + "；".join(notices)
        if self.last_mode == SCAN_MODE_SIMULATED:
            text += "（模拟数据，仅用于离线演示；不能用于真实测量）"
        self.status_label.setText(text)
        self._preselect_bound_devices()
        self.apply_btn.setEnabled(bool(devices))
        self.log_message.emit(f"设备扫描完成: {self.status_label.text()}")

    def _on_failed(self, message: str) -> None:
        if self._closed:
            return
        self.status_label.setText(f"扫描失败: {message}")
        self.log_message.emit(f"设备扫描失败: {message}")

    def _on_cancelled(self) -> None:
        if self._closed:
            return
        self.status_label.setText("扫描已取消，结果未更新")
        self.apply_btn.setEnabled(False)
        self.log_message.emit("设备扫描已取消")

    def _on_worker_finished(self) -> None:
        worker = self.worker
        self.worker = None
        self._set_controls_enabled(True)
        if self._closed:
            return
        if worker is not None:
            worker.deleteLater()

    def is_scanning(self) -> bool:
        worker = self.worker
        return worker is not None and worker.isRunning()

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """页面关闭时协作停止扫描；不强杀线程，失败时保留引用由调用方处理。"""
        self._closed = True
        worker = self.worker
        if worker is None:
            return True
        worker.cancel_event.set()
        worker.requestInterruption()
        scanner = worker.scanner
        if scanner is not None:
            scanner.request_cancel()
        if worker.isRunning() and not worker.wait(timeout_ms):
            return False
        self.worker = None
        return True

    # ---------- 结果表 ----------

    def _clear_table(self) -> None:
        self.table.setRowCount(0)
        self._fit_table_height(0)
        self._rows = []

    def _fit_table_height(self, rows: int) -> None:
        """表格高度贴着行数：结果少时不占满整屏，结果多时最多显示约 8 行再滚动。"""
        header = self.table.horizontalHeader().height()
        row_height = self.table.verticalHeader().defaultSectionSize()
        visible_rows = max(1, min(rows, 8))
        self.table.setFixedHeight(header + row_height * visible_rows + 4)

    @staticmethod
    def _status_text(device: Any) -> str:
        if device.is_known:
            return "已登记"
        return "未登记" if device.is_unknown_identity else "无法识别"

    def _fill_table(self, devices: Sequence[Any]) -> None:
        self._clear_table()
        self.table.setRowCount(len(devices))
        self._fit_table_height(len(devices))
        for row, device in enumerate(devices):
            identity = device.identity
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Unchecked)
            if device.is_unidentified:
                # 身份读不出来就不能绑定角色：勾选框直接禁用，避免“应用了一半”。
                check.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, check)
            role_combo = WheelSafeComboBox()
            role_combo.addItems([label for _value, label in ROLE_CHOICES])
            role_combo.setEnabled(not device.is_unidentified)
            self.table.setCellWidget(row, 1, role_combo)
            name_item = QTableWidgetItem(str(device.display_name))
            if identity is not None:
                name_item.setToolTip(f"{identity.model} / {identity.vendor}\n{device.idn}")
            else:
                name_item.setToolTip(device.error or "")
            self.table.setItem(row, 2, name_item)
            self.table.setItem(row, 3, QTableWidgetItem(identity.serial if identity else "-"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{device.ip}:{device.port}"))
            status_item = QTableWidgetItem(self._status_text(device))
            if device.is_unidentified:
                status_item.setToolTip(device.error or "端口有应答但 *IDN? 无法解析")
            elif device.is_unknown_identity:
                status_item.setToolTip("未在设备表 instruments.json 中登记；仍可勾选使用")
            self.table.setItem(row, 5, status_item)
            self._rows.append({"device": device, "check": check, "role": role_combo})

    def _preselect_bound_devices(self) -> None:
        """按配置里保存的身份预选：IP 变了也能找回同一台设备，用户仍可改。

        只按 ``厂商:序列号`` 匹配，不做“仅按序列号”的猜测。
        """
        if not self.device_bindings:
            return
        bound = {
            binding_device_key(record): role
            for role, record in self.device_bindings.items()
            if binding_device_key(record) is not None
        }
        if not bound:
            return
        matched = 0
        role_index = {value: index for index, (value, _label) in enumerate(ROLE_CHOICES)}
        for row in self._rows:
            device = row["device"]
            key = device.device_key
            if key is None or key not in bound:
                continue
            role = bound[key]
            row["check"].setCheckState(Qt.Checked)
            index = role_index.get(role)
            if index is not None:
                row["role"].setCurrentIndex(index)
            matched += 1
        if matched:
            self.status_label.setText(
                f"{self.status_label.text()}；已按配置中的设备身份预选 {matched} 台"
            )

    def selected_assignments(self) -> tuple[list[dict[str, Any]], list[str]]:
        """返回 ``(要应用的设备, 问题说明)``；角色未选、重复或身份缺失都算问题。"""
        assignments: list[dict[str, Any]] = []
        problems: list[str] = []
        used_roles: dict[str, str] = {}
        for row in self._rows:
            if row["check"].checkState() != Qt.Checked:
                continue
            device = row["device"]
            if not device.has_identity:
                problems.append(
                    f"{device.ip}:{device.port} 无法识别身份，不能应用；请检查设备响应后重试"
                )
                continue
            role_value = ROLE_CHOICES[row["role"].currentIndex()][0]
            if not role_value:
                problems.append(f"{device.display_name} @ {device.ip} 未选择测试角色")
                continue
            if role_value in used_roles:
                problems.append(f"角色 {role_value} 被重复选择：{used_roles[role_value]} 与 {device.ip}")
                continue
            used_roles[role_value] = device.ip
            identity = device.identity
            assignments.append(
                {
                    "role": role_value,
                    "address": device.visa_address,
                    "name": device.known_name,
                    "vendor": identity.vendor,
                    "model": identity.model,
                    "serial": identity.serial,
                    "device_key": identity.device_key,
                    "ip": device.ip,
                    "port": device.port,
                    "known": device.is_known,
                    "simulated": self.last_mode == SCAN_MODE_SIMULATED,
                }
            )
        return assignments, problems

    def request_apply(self) -> None:
        assignments, problems = self.selected_assignments()
        if problems:
            self.status_label.setText("；".join(problems))
            return
        if not assignments:
            self.status_label.setText("请先勾选本次测试所需的设备，并为每台设备选择测试角色")
            return
        self.apply_requested.emit(assignments)

    def request_clear(self) -> None:
        if not self.device_bindings:
            self.status_label.setText("配置中没有设备绑定记录")
            return
        self.clear_requested.emit()

    def show_message(self, message: str) -> None:
        """显示一次应用/清除操作的结果（由配置页回写，避免面板自己猜结果）。"""
        if not self._closed:
            self.status_label.setText(message)


__all__ = [
    "DeviceDiscoveryPanel",
    "DiscoveryWorker",
    "ROLE_CHOICES",
    "SCAN_MODE_CHOICES",
    "binding_device_key",
    "subnet_from_address",
]
