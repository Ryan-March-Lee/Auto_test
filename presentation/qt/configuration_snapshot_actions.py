"""命名配置快照的窗口动作：名称输入、文件选择与用户反馈。

对话框与窗口状态同步集中在本模块；配置编写、正式配置写入与快照持久化仍由协调器/
应用层完成，主窗口只继承 :class:`ConfigurationSnapshotActions`。这样主窗口不会重新
持有配置业务或文件路径判断，也保持在装配行数上限内。

与 :mod:`presentation.qt.stage_result_selection` 同构：可预期失败（用户取消、名称
为空、文件不可读）只写日志并返回 ``False``，调用方保持当前草稿不变。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QWidget

from infrastructure.logging.app_logging import get_logger

logger = get_logger(__name__)

JSON_FILTER = "JSON 配置 (*.json)"


def save_named_configuration(
    parent: QWidget,
    coordinator: Any,
    *,
    log: Callable[[str], Any],
    on_saved: Callable[[], Any],
) -> bool:
    """询问名称并保存配置快照；用户取消或保存失败时返回 ``False``。"""
    name, accepted = QInputDialog.getText(parent, "保存配置", "配置名称：")
    if not accepted:
        log("已取消保存配置")
        return False
    name = name.strip()
    if not name:
        QMessageBox.warning(parent, "保存失败", "配置名称不能为空")
        log("配置保存失败: 配置名称不能为空")
        return False
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


def load_named_configuration(
    parent: QWidget,
    coordinator: Any,
    *,
    directory: Path,
    log: Callable[[str], Any],
    on_loaded: Callable[[], Any],
) -> bool:
    """选择并加载配置快照；用户取消或加载失败时返回 ``False``。"""
    path, _selected_filter = QFileDialog.getOpenFileName(
        parent, "加载配置", str(directory), JSON_FILTER
    )
    if not path:
        log("已取消加载配置")
        return False
    if not coordinator.load_named_configuration(path):
        log(f"配置加载失败: {coordinator.last_load_error}")
        return False
    on_loaded()
    log(f"已加载配置: {path}")
    return True


class ConfigurationSnapshotActions:
    """主窗口的命名配置动作：对话框、反馈与窗口状态同步。

    本类只由 :class:`presentation.qt.main_window.MainWindow` 继承；宿主需要提供
    ``coordinator``、``paths``、``add_log_message``、``configuration_editor``、
    ``workspace_binding`` 与 ``_sync_finalize_entry``。把这段窗口动作放在本模块，
    主窗口只保留页面装配与信号连接，不重新长出配置快照业务。
    """

    def save_config(self) -> None:
        """保存命名配置快照；写入由协调器完成。"""
        save_named_configuration(
            self,
            self.coordinator,
            log=self.add_log_message,
            on_saved=self._after_configuration_saved,
        )

    def _load_configuration_snapshot(self) -> None:
        """加载用户明确选择的配置快照，不创建执行上下文。"""
        load_named_configuration(
            self,
            self.coordinator,
            directory=self.paths.configuration_snapshots_dir,
            log=self.add_log_message,
            on_loaded=self._after_configuration_loaded,
        )

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
    "load_named_configuration",
    "save_named_configuration",
]
