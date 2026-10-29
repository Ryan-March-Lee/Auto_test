"""设备发现面板：扫描网段、展示发现结果并勾选本次测试所需设备。

边界：

- 面板只负责界面与扫描线程生命周期；扫描实现由 ``scanner_factory(mode)`` 注入，
  因此离线测试与现场扫描共用同一套界面逻辑。
- 面板不改配置、不连接仪器：用户确认“应用到配置”后，只把所选设备交给配置页，
  由配置页写成仪器连接配置（地址来自本次发现的设备）与设备身份记录。
- **测试角色由设备表登记，界面不让用户选**：现场每台仪器只承担一种角色。信号源与
  频谱仪显示“设备名（角色）”，电源显示“短标签（型号，电源）”（如
  ``DP8B1519（DP832A，电源）``）：电源是同一个类别下的多台设备，靠短标签区分，不再
  用“电源 PS3”这类槽位编号。没登记角色的设备不能勾选应用。
- **多台电源各自独立**：电源不再按角色键互相覆盖，勾选几台就传递几台；只有信号源与
  频谱仪保持“一个角色一台设备”的唯一性。
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

from application.ports.device_discovery import (
    DEMO_SUBNET,
    SCAN_MODE_NETWORK,
    SCAN_MODE_SIMULATED,
    DeviceDiscoveryError,
)
from domain.instruments.models import (
    POWER_SUPPLY_ROLE,
    canonical_role,
    make_device_key,
)

from .config_form_state import LEGACY_BINDING_SOURCE
from .input_controls import WheelSafeComboBox
from .theme import set_button_variant, set_tone

# 角色标签是“本次测试语义”，与设备身份（序列号）分开：设备可以换 IP，角色不会变。
# 角色值来自设备表 ``instruments.json`` 的 ``role`` 字段，这里只负责显示。电源的类别是
# ``power_supply``（多台共用），``PS1``..``PS4`` 只用于显示旧绑定记录，不再作为新角色。
ROLE_LABELS: dict[str, str] = {
    "signal_generator": "信号发生器",
    "spectrum_analyzer": "频谱分析仪",
    POWER_SUPPLY_ROLE: "电源",
    "PS1": "电源 PS1",
    "PS2": "电源 PS2",
    "PS3": "电源 PS3",
    "PS4": "电源 PS4",
}
SCAN_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    (SCAN_MODE_SIMULATED, "模拟设备（离线演示）"),
    (SCAN_MODE_NETWORK, "真实网络扫描"),
)
# 各列宽度按内容计算；表格本身也只占内容宽度，不再被横向拉成长条。
_TABLE_HEADERS = ("使用", "设备（型号，角色）", "序列号", "状态")
_IP_PATTERN = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def role_label(role: Any) -> str:
    """返回角色的界面标签；未登记或未知角色返回空串。"""
    return ROLE_LABELS.get(str(role or ""), "")


def device_heading(device: Any) -> str:
    """结果表里的一行设备描述。

    电源按“短标签（型号，电源）”显示：现场核对实物靠短标签，而不是“电源 PS3”这种槽位
    编号。短标签只来自设备表（发现过程不生成标签），还没有标签的电源退回资产名。
    其他角色沿用“设备名（角色）”，未登记设备显示“未知设备（型号）”。
    """
    role = canonical_role(getattr(device, "known_role", None))
    heading = str(device.display_name)
    label = role_label(role)
    if role == POWER_SUPPLY_ROLE:
        identity = getattr(device, "identity", None)
        model = getattr(identity, "model", "") or ""
        return f"{heading}（{model}，{label}）" if model else f"{heading}（{label}）"
    return f"{heading}（{label}）" if label else heading


def device_applicable(device: Any) -> bool:
    """这台设备能否被应用：身份可解析，且设备表登记了固定测试角色。"""
    return bool(device.has_identity and canonical_role(device.known_role))


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
        except (DeviceDiscoveryError, OSError, ValueError) as error:
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
        except DeviceDiscoveryError as error:
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
        title: str = "设备发现",
        parent: Any = None,
    ) -> None:
        super().__init__(title, parent)
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
        self._refresh_bindings_summary()

    def _refresh_bindings_summary(self) -> None:
        """在面板上显示当前配置了哪些设备：连接信息不靠一张空白结果表来猜。

        只显示角色与设备名，不显示地址：IP 只是本次可达的地址，不是用户需要
        关心的连接信息。电源优先显示短标签（``supply_id``），与结果表一致。
        """
        parts = []
        ordered = [role for role in ROLE_LABELS if role in self.device_bindings]
        ordered += [role for role in self.device_bindings if role not in ROLE_LABELS]
        for role in ordered:
            record = self.device_bindings.get(role)
            if not isinstance(record, dict):
                continue
            name = str(
                record.get("supply_id")
                or record.get("label")
                or record.get("name")
                or record.get("model")
                or record.get("serial")
                or "未命名设备"
            )
            detail = name
            if record.get("source") == "simulated":
                detail += "（模拟数据）"
            elif record.get("source") == LEGACY_BINDING_SOURCE:
                detail += "（沿用已保存的配置，未扫描确认）"
            parts.append(f"{role_label(role) or role} ← {detail}")
        self.bindings_label.setText(
            "当前配置："
            + ("；".join(parts) if parts else "尚未绑定设备；请扫描后勾选本次测试所用的设备")
        )

    def clear_results(self) -> None:
        """清空上一次扫描结果；切换/重新加载配置时调用，避免旧结果看起来像当前配置。"""
        self.devices = []
        self._clear_table()
        self.apply_btn.setEnabled(False)
        self.status_label.setText("尚未扫描")

    # ---------- 界面 ----------

    def _build_ui(self, default_subnet: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(8)
        self.subnet_edit = QLineEdit(str(default_subnet))
        self.subnet_edit.setMaximumWidth(200)
        self.subnet_edit.setToolTip("扫描范围，例如 192.168.1.0/24；单个地址按 /24 处理")
        self.mode_combo = WheelSafeComboBox()
        self.mode_combo.addItems([label for _value, label in SCAN_MODE_CHOICES])
        self.scan_btn = QPushButton("获取设备")
        self.scan_btn.setToolTip("扫描网段并读取每台设备的 *IDN?；不会改变任何仪器状态")
        set_button_variant(self.scan_btn, "secondary")
        self.status_label = QLabel("尚未扫描")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "hint")
        self.mode_hint = QLabel("")
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setProperty("role", "hint")
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
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setTextElideMode(Qt.ElideRight)
        header = self.table.horizontalHeader()
        # 各列按内容取宽：设备名长短不一，拉伸某一列只会留下大片空白。
        header.setMinimumSectionSize(44)
        for column in range(len(_TABLE_HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self._fit_table_height(0)
        self._fit_table_width()
        layout.addWidget(self.table)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.apply_btn = QPushButton("应用所选设备到配置")
        self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip(
            "把勾选设备的当前地址与身份（序列号）写入仪器连接配置；"
            "测试角色取自设备表登记的 role"
        )
        set_button_variant(self.apply_btn, "primary")
        self.clear_btn = QPushButton("清除设备绑定")
        self.clear_btn.setToolTip("删除配置中记录的角色与设备身份对应关系，不改动地址")
        set_button_variant(self.clear_btn, "ghost")
        actions.addWidget(self.apply_btn)
        actions.addWidget(self.clear_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.bindings_label = QLabel("")
        self.bindings_label.setWordWrap(True)
        self.bindings_label.setProperty("role", "hint")
        layout.addWidget(self.bindings_label)
        layout.addWidget(self.status_label)

        self.scan_btn.clicked.connect(self.request_scan)
        self.apply_btn.clicked.connect(self.request_apply)
        self.clear_btn.clicked.connect(self.request_clear)
        self._update_mode_hint()
        self._refresh_bindings_summary()

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
        set_tone(self.mode_hint, "warning" if simulated else "muted")

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
        usable = sum(1 for device in devices if device_applicable(device))
        no_role = sum(1 for device in devices if device.is_known and not device.has_role)
        unregistered = sum(1 for device in devices if device.is_unknown_identity)
        unidentified = sum(1 for device in devices if device.is_unidentified)
        text = f"发现 {len(devices)} 台设备：已登记 {known} 台，其中可应用 {usable} 台"
        if no_role:
            text += f"，未分配角色 {no_role} 台"
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
        self.apply_btn.setEnabled(any(device_applicable(device) for device in devices))
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
        self._fit_table_width()
        self._rows = []

    def _fit_table_height(self, rows: int) -> None:
        """表格高度贴着行数：结果少时不占满整屏，结果多时最多显示约 8 行再滚动。"""
        header = self.table.horizontalHeader().height()
        row_height = self.table.verticalHeader().defaultSectionSize()
        visible_rows = max(1, min(rows, 8))
        self.table.setFixedHeight(header + row_height * visible_rows + 4)

    def _fit_table_width(self) -> None:
        """表格宽度贴着列内容，不再横向撑满卡片。

        设备名长短差别很大，让某一列吃掉剩余宽度只会留下大片空白（设备列尤其明显）。
        这里按内容取列宽，再把表格本身收窄到这些列的总宽；行多到出现纵向滚动条时把
        滚动条宽度也算进去，避免最后一列被挤掉。
        """
        header = self.table.horizontalHeader()
        total = 0
        for column in range(self.table.columnCount()):
            self.table.resizeColumnToContents(column)
            total += header.sectionSize(column)
        total += 2 * self.table.frameWidth()
        if self.table.rowCount() > 8:
            total += self.table.verticalScrollBar().sizeHint().width()
        self.table.setMaximumWidth(total)

    @staticmethod
    def _status_text(device: Any) -> str:
        if device.is_unidentified:
            return "无法识别"
        if not device.is_known:
            return "未登记"
        return "已登记" if device.has_role else "未分配角色"

    def _fill_table(self, devices: Sequence[Any]) -> None:
        self._clear_table()
        self.table.setRowCount(len(devices))
        self._fit_table_height(len(devices))
        for row, device in enumerate(devices):
            identity = device.identity
            applicable = device_applicable(device)
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Unchecked)
            if not applicable:
                # 身份读不出来或设备表没登记角色：勾选框禁用，避免“应用了一半”。
                check.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, check)
            # 角色和显示名写在同一个单元格：每台设备只有一种角色，不需要用户再选。
            # 电源显示“短标签（型号，电源）”，信号源与频谱仪沿用“设备名（角色）”。
            name_item = QTableWidgetItem(device_heading(device))
            if identity is not None:
                details = [f"{identity.model} / {identity.vendor}"]
                if device.label:
                    details.append(f"短标签 {device.label}")
                details.append(str(device.idn or ""))
                name_item.setToolTip("\n".join(item for item in details if item))
            else:
                name_item.setToolTip(device.error or "")
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, QTableWidgetItem(identity.serial if identity else "-"))
            status_item = QTableWidgetItem(self._status_text(device))
            if device.is_unidentified:
                status_item.setToolTip(device.error or "端口有应答但 *IDN? 无法解析")
            elif device.is_unknown_identity:
                status_item.setToolTip(
                    "未在设备表 instruments.json 中登记，无法确定测试角色；请先登记设备"
                )
            elif not device.has_role:
                status_item.setToolTip(
                    "设备表没有登记这台设备的测试角色（role）；补上后才能应用"
                )
            elif device.is_power_supply and not device.config_slot:
                # 过渡期提示：电源集合仍按旧槽位写入，见 config_page.apply_discovered_devices。
                status_item.setToolTip(
                    "这台电源在设备表里没有旧槽位（PS1..PS4）：当前配置只能把地址写回旧槽位，"
                    "多电源/动态电源绑定属于后续步骤"
                )
            self.table.setItem(row, 3, status_item)
            self._rows.append({"device": device, "check": check})
        self._fit_table_width()

    def _preselect_bound_devices(self) -> None:
        """按配置里保存的身份预选：IP 变了也能找回同一台设备。

        只按 ``厂商:序列号`` 匹配，不做“仅按序列号”或“按短标签”的猜测。角色以设备表
        登记为准；旧绑定里的槽位（``PS3``）先折叠成 ``power_supply`` 再比较，因此旧配置
        不会因为“PS3 → 电源”被误报成角色变化。
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
        moved: list[str] = []
        for row in self._rows:
            device = row["device"]
            key = device.device_key
            if key is None or key not in bound:
                continue
            if not device_applicable(device):
                # 设备表没登记角色时不能预选：应用后只会写进一个猜出来的角色。
                continue
            row["check"].setCheckState(Qt.Checked)
            matched += 1
            bound_key = bound[key]
            # 绑定键可能是类别（``power_supply``）、旧槽位（``PS3``）或短标签
            # （``DP8B1519``）：前两者折叠成类别比较，短标签按设备的标签比对。
            bound_role = canonical_role(bound_key) or bound_key
            same_category = bound_role == device.role
            same_supply = device.is_power_supply and bound_key in {
                device.label,
                device.supply_id,
            }
            if not (same_category or same_supply):
                moved.append(
                    f"{device.display_name} 配置里是 {role_label(bound_role) or bound_role}，"
                    f"设备表登记为 {role_label(device.role)}"
                )
        if matched:
            text = f"{self.status_label.text()}；已按配置中的设备身份预选 {matched} 台"
            if moved:
                text += "；角色以设备表为准（" + "；".join(moved) + "）"
            self.status_label.setText(text)

    def selected_assignments(self) -> tuple[list[dict[str, Any]], list[str]]:
        """返回 ``(要应用的设备, 问题说明)``；身份/角色缺失、唯一角色重复都算问题。

        电源是同一个类别下的多台设备：勾选几台就返回几条，不再按角色键互相判定
        “重复”；只有信号源与频谱仪保持“一个角色一台设备”。每条记录都带短标签
        （``label`` / ``supply_id``）与身份（``serial`` / ``device_key``），配置页不需要
        从显示文本反解析。
        """
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
            role_value = device.role
            if not role_value:
                problems.append(
                    f"{device.display_name} 在设备表中没有登记测试角色，不能应用"
                )
                continue
            if role_value != POWER_SUPPLY_ROLE:
                # 信号源与频谱仪是“一个角色一台设备”：同一角色被勾选两次就整批拒绝。
                # 电源不参与这里——多台电源各自独立传递，不按角色键互相覆盖。
                if role_value in used_roles:
                    problems.append(
                        f"角色 {role_label(role_value)} 被重复选择："
                        f"{used_roles[role_value]} 与 {device.display_name}"
                    )
                    continue
                used_roles[role_value] = device.display_name
            identity = device.identity
            assignments.append(
                {
                    "role": role_value,
                    "label": device.label,
                    "supply_id": device.supply_id,
                    # 过渡期：配置里的电源集合仍按旧槽位（PS1..PS4）存放地址。
                    "config_slot": device.config_slot,
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
            self.status_label.setText(
                "请先勾选本次测试所需的设备（测试角色来自设备表登记，不需要选择）"
            )
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
    "ROLE_LABELS",
    "SCAN_MODE_CHOICES",
    "binding_device_key",
    "device_applicable",
    "device_heading",
    "role_label",
    "subnet_from_address",
]
