"""命名配置快照的窗口动作：名称输入、快照选择与用户反馈。

对话框与窗口状态同步集中在本模块；配置编写、正式配置写入与快照持久化仍由协调器/
应用层完成，主窗口只继承 :class:`ConfigurationSnapshotActions`。这样主窗口不会重新
持有配置业务或文件路径判断，也保持在装配行数上限内。

与 :mod:`presentation.qt.stage_result_selection` 同构：可预期失败（用户取消、名称
为空、快照不可读）只写日志并返回 ``False``，调用方保持当前草稿不变。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox, QWidget

from application.ports.configuration_snapshots import ConfigurationSnapshot
from infrastructure.logging.app_logging import get_logger

from .configuration_snapshot_dialog import ConfigurationSnapshotDialog
from .rack_layout import configured_power_supply_ids, normalize_rack_slots

logger = get_logger(__name__)


def save_named_configuration(
    parent: QWidget,
    coordinator: Any,
    *,
    log: Callable[[str], Any],
    on_saved: Callable[[], Any],
    rack_slots: Mapping[str, int] | None = None,
) -> bool:
    """询问名称并保存配置快照；用户取消或保存失败时返回 ``False``。

    ``rack_slots`` 随快照元数据保存，只用于下次加载时恢复仪器排布页的九宫格，
    不进入正式配置文档。
    """
    name, accepted = QInputDialog.getText(parent, "保存配置", "配置名称：")
    if not accepted:
        log("已取消保存配置")
        return False
    name = name.strip()
    if not name:
        QMessageBox.warning(parent, "保存失败", "配置名称不能为空")
        log("配置保存失败: 配置名称不能为空")
        return False
    if rack_slots:
        outcome = coordinator.save_named_configuration(name, rack_slots=rack_slots)
    else:
        outcome = coordinator.save_named_configuration(name)
    snapshot = outcome.snapshot
    if snapshot is None:
        error = outcome.error or coordinator.last_save_error
        if outcome.configuration_saved:
            title = "部分保存"
            message = "当前工作配置已保存，但命名配置快照未保存"
        else:
            title = "保存失败"
            message = "配置保存失败"
        detail = f"{message}: {error}" if error else message
        log(detail)
        QMessageBox.warning(parent, title, detail)
        return False
    on_saved()
    log(f"配置已保存: {snapshot.configuration_path}")
    QMessageBox.information(
        parent, "保存成功", f"配置已保存到：\n{snapshot.configuration_path}"
    )
    return True


def choose_configuration_snapshot(
    parent: QWidget,
    repository: Any,
    *,
    log: Callable[[str], Any],
) -> ConfigurationSnapshot | None:
    """弹出已保存配置列表；用户取消或仓储未装配时返回 ``None``。"""
    if repository is None:
        log("配置加载失败: 配置快照仓储未装配")
        QMessageBox.warning(parent, "加载配置", "配置快照仓储未装配")
        return None
    dialog = ConfigurationSnapshotDialog(repository, log_callback=log, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        log("已取消加载配置")
        return None
    snapshot = dialog.selected_snapshot
    if snapshot is None:
        log("已取消加载配置")
        return None
    return snapshot


class ConfigurationSnapshotActions:
    """主窗口的命名配置动作：对话框、反馈与窗口状态同步。

    本类只由 :class:`presentation.qt.main_window.MainWindow` 继承；宿主需要提供
    ``coordinator``、``add_log_message``、``configuration_editor``、
    ``workspace_binding``、``test_config_page``、``power_page``、``rack_page``、
    ``tab_widget``、``view_stack``、``workspace_page``、
    ``_sync_status_panel_visibility`` 与 ``_sync_finalize_entry``。把这段窗口动作
    放在本模块，主窗口只保留页面装配与信号连接，不重新长出配置快照业务。
    """

    def save_config(self) -> None:
        """保存命名配置快照；写入由协调器完成。"""
        save_named_configuration(
            self,
            self.coordinator,
            log=self.add_log_message,
            on_saved=self._after_configuration_saved,
            rack_slots=self._current_rack_slots(),
        )

    def load_configuration_from_welcome(self) -> None:
        """欢迎页的“加载配置”：选择已保存快照，回填后直接进入测试配置页。

        加载仍只更新编辑草稿，不创建执行上下文；自动化入口只在用户从欢迎页显式
        选择一份配置时走这里，配置页内不再提供第二条加载路径。快照里的仪器排布
        格位只恢复界面，不写回正式配置。
        """
        snapshot = choose_configuration_snapshot(
            self,
            getattr(self.coordinator, "configuration_snapshots", None),
            log=self.add_log_message,
        )
        if snapshot is None:
            return
        if not self.coordinator.load_named_configuration(snapshot.configuration_path):
            self.add_log_message(f"配置加载失败: {self.coordinator.last_load_error}")
            return
        self._after_configuration_loaded()
        self._enter_workspace_after_configuration_loaded(
            rack_slots=getattr(snapshot, "rack_slots", None)
        )
        self.add_log_message(f"已加载配置并进入测试配置: {snapshot.display_name}")

    def _enter_workspace_after_configuration_loaded(
        self,
        *,
        rack_slots: Mapping[str, int] | None = None,
    ) -> None:
        """进入工作区的测试配置页，并把跳过的上游页面准备成可回退的状态。

        从欢迎页加载配置时没有经过仪表配置/排布/电源配置。这里按加载的配置重建
        仪器排布页的卡片与九宫格格位，并把同一份格位交给电源配置页，因此测试配置
        页的“上一步”仍然可用，电源配置页也能继续回排布页。仪表配置页仍要求重新
        查找设备后才能继续；已保存的设备绑定不会冒充本次扫描结果。
        """
        driver_enabled = self.coordinator.amplifier_driver_mode()
        self.workspace_binding.start_session(driver_enabled)
        self.coordinator.begin_configuration()
        self.configuration_editor.set_read_only(
            self.coordinator.editing_read_only_reason()
        )
        supply_ids = configured_power_supply_ids(self.coordinator.config)
        layout = normalize_rack_slots(rack_slots, supply_ids)
        if supply_ids and not rack_slots:
            self.add_log_message(
                "该快照未保存仪器排布，已按电源顺序补位；重新保存后会记住当前排布"
            )
        self._loaded_rack_layout = layout
        power_page = getattr(self, "power_page", None)
        if power_page is not None:
            power_page.set_rack_slots(layout)
            power_page.back_btn.setVisible(True)
        self._prepare_rack_page_from_loaded_config(layout)
        self.tab_widget.setCurrentIndex(self.tab_widget.indexOf(self.test_config_page))
        self.view_stack.setCurrentWidget(self.workspace_page)
        self._sync_status_panel_visibility()
        self._sync_finalize_entry()
        self.workspace_binding.sync()

    def _prepare_rack_page_from_loaded_config(
        self, layout: Mapping[str, int]
    ) -> None:
        """用配置里的设备与格位重建排布页，供“上一步”直接展示。"""
        rack_page = getattr(self, "rack_page", None)
        if rack_page is None:
            return
        entries = rack_page.entries_from_configuration(self.coordinator.config)
        rack_page.enter(
            entries,
            driver_enabled=self.coordinator.amplifier_driver_mode(),
            animate=False,
        )
        rack_page.set_slot_assignments(layout)

    def _current_rack_slots(self) -> dict[str, int] | None:
        """保存时取当前可见的九宫格排布；跳步加载后由电源页持有同一份。"""
        power_page = getattr(self, "power_page", None)
        getter = getattr(power_page, "rack_slots", None)
        if callable(getter):
            slots = dict(getter())
            if slots:
                return slots
        rack_page = getattr(self, "rack_page", None)
        if rack_page is not None:
            slots = dict(rack_page.slot_assignments())
            if slots:
                return slots
        return None

    def _restore_loaded_rack_layout(self) -> None:
        """从仪表配置页重新进入排布页时，恢复本次会话记住的格位。"""
        layout = getattr(self, "_loaded_rack_layout", None)
        rack_page = getattr(self, "rack_page", None)
        if layout and rack_page is not None:
            rack_page.set_slot_assignments(layout)

    def _remember_rack_layout(self) -> None:
        """排布页点“下一步”时记下最新格位，供返回设备选择后再进来恢复。"""
        rack_page = getattr(self, "rack_page", None)
        if rack_page is None:
            return
        self._loaded_rack_layout = dict(rack_page.slot_assignments()) or None

    def _clear_loaded_rack_layout(self) -> None:
        """开始新的正常配置会话或回到欢迎页时，丢掉上一份加载配置的格位。"""
        self._loaded_rack_layout = None

    def _after_configuration_saved(self) -> None:
        self._mark_configuration_clean()

    def _after_configuration_loaded(self) -> None:
        """加载后同步基线、按钮语义与本次会话模式。

        还没有已提交执行会话时，配置里的驱放模式就是下一次连接要用的模式，徽标、
        驱动映射标签页与阶段条必须一起更新；已经提交会话时不能改写它的冻结模式。
        """
        self._mark_configuration_clean()
        if self.coordinator.prepared_run is not None:
            self.add_log_message(
                "已加载配置快照；当前执行会话仍使用已冻结的模式与硬件快照"
            )
            return
        binding = getattr(self, "workspace_binding", None)
        if binding is not None:
            binding.set_session_mode(self.coordinator.amplifier_driver_mode())

    def _mark_configuration_clean(self) -> None:
        editor = getattr(self, "configuration_editor", None)
        if editor is not None:
            editor.mark_clean()
        self._sync_finalize_entry()


__all__ = [
    "ConfigurationSnapshotActions",
    "choose_configuration_snapshot",
    "save_named_configuration",
]
