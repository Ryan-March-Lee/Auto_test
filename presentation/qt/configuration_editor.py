"""两个配置页共享一份编辑草稿的装配器（步骤 13）。

``ConfigurationPage`` 拥有测试参数、设备连接与运行入口，``PowerConfigurationPage``
拥有电源参数、供电分配、上电计划与漏极。窗口把两个页面实例交给本模块，由它统一：

- 连接“PA 单元数量”和“设备绑定写回”这两个跨页派生事件；
- 把同一份文档回填到两页、同期切换测试模式、汇总 dirty、统一标记保存成功；
- 在最终动作期间同时禁用两页。

回填由模块级 :func:`apply_config_to_pages` 完成：两页要么都接受新草稿，要么都回到
调用前的读数，不会停在“参数页已是新配置、电源页还是旧配置”的中间状态；运行快照的
回填也复用同一入口。

编辑器只依赖页面的窄接口（``apply_config`` / ``is_dirty`` / ``mark_clean`` /
``set_test_mode`` / ``set_finalizing``），不读控件也不合并配置文档——整份文档的
组装与校验仍由 :class:`presentation.qt.application_coordinator.ApplicationCoordinator`
完成，避免出现第二份保存入口。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from .config_projection import ConfigBackfillError

__all__ = ["ConfigurationEditor", "apply_config_to_pages"]


def apply_config_to_pages(
    pages: Iterable[Any],
    config: Any,
    *,
    log: Callable[[str], Any] | None = None,
) -> None:
    """把同一份编辑草稿确定性回填到多个配置页：全部成功或全部回滚。

    两个配置页共享一份草稿，回填不能停在“前一页已是新配置、后一页还是旧配置”的
    中间状态。调用前先拒绝非对象输入（否则会先回填第一页才失败），再为每页保存
    当前控件读数；任一页回填失败时，把已经回填的页恢复到调用前的读数，然后把失败
    继续抛给调用方。回滚失败不吞掉原异常，只额外记录，避免掩盖真正原因。
    """
    targets = [page for page in pages if page is not None]
    if not targets:
        return
    if not isinstance(config, dict):
        raise ConfigBackfillError(
            "冻结配置必须是 JSON 对象（dict），无法确定性地回填配置页"
        )
    # 快照失败时还没有任何页面被改写，直接抛出即可，界面仍是调用前的一致状态。
    snapshots = [page.build_config() for page in targets]
    try:
        for page in targets:
            page.apply_config(config)
    except ConfigBackfillError:
        # 只把可识别的回填失败当成“应回滚”的事务结果；其它异常是程序缺陷，照常
        # 抛出，不能用回滚逻辑掩盖真正的失败原因。
        rollback_failures = []
        for page, snapshot in zip(targets, snapshots):
            try:
                page.apply_config(snapshot)
            except ConfigBackfillError as failure:
                rollback_failures.append(f"{type(page).__name__}: {failure}")
        if rollback_failures and log is not None:
            log("配置页回填回滚失败: " + "；".join(rollback_failures))
        raise


class ConfigurationEditor:
    """把参数页与独立电源页装配成一份共享编辑状态。"""

    def __init__(
        self,
        config_page: Any,
        power_page: Any | None = None,
        *,
        log: Callable[[str], Any] | None = None,
        driver_mode_changed: Callable[[bool], Any] | None = None,
    ) -> None:
        self.config_page = config_page
        self.power_page = power_page
        self._log = log
        self._driver_mode_changed = driver_mode_changed
        self._connect()

    def pages(self) -> list[Any]:
        """当前存在的配置页（参数页在前）。"""
        return [
            page
            for page in (self.config_page, self.power_page)
            if page is not None
        ]

    def contains(self, widget: Any) -> bool:
        return widget is not None and widget in self.pages()

    def _connect(self) -> None:
        page, power = self.config_page, self.power_page
        if page is None or power is None:
            return
        page.pa_unit_count_changed.connect(power.set_pa_unit_count)
        page.device_selection_finished.connect(self._refresh_power_bindings)
        power.validation_failed.connect(self._report_power_error)
        if self._driver_mode_changed is not None:
            power.driver_mode_changed.connect(self._driver_mode_changed)
        self._sync_pa_unit_count()

    def _report_power_error(self, message: str) -> None:
        if self._log is not None:
            self._log(f"电源配置校验失败: {message}")

    def _refresh_power_bindings(self, _result: Any = None, _message: str = "") -> None:
        """绑定整批写入后刷新电源页；写入失败时绑定集合不变。"""
        power = self.power_page
        if power is None:
            return
        power.apply_device_bindings(getattr(self.config_page, "device_bindings", {}))

    def _sync_pa_unit_count(self) -> None:
        power = self.power_page
        combo = getattr(self.config_page, "pa_unit_count_combo", None)
        if power is None or combo is None:
            return
        try:
            power.set_pa_unit_count(int(combo.currentText()))
        except (TypeError, ValueError):
            return

    # ---- 页面状态 -----------------------------------------------------------

    def apply_config(self, config: Any) -> None:
        apply_config_to_pages(self.pages(), config, log=self._log)

    def set_test_mode(self, enabled: bool) -> None:
        for page in self.pages():
            page.set_test_mode(enabled)

    def is_dirty(self) -> bool:
        return any(page.is_dirty() for page in self.pages())

    def mark_clean(self) -> None:
        for page in self.pages():
            mark_clean = getattr(page, "mark_clean", None)
            if callable(mark_clean):
                mark_clean()

    def set_finalizing(self, active: bool) -> None:
        for page in self.pages():
            set_finalizing = getattr(page, "set_finalizing", None)
            if callable(set_finalizing):
                set_finalizing(active)
