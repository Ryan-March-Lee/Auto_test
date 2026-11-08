"""真实窗口手动验收：仪器发现页的等待、发牌与翻牌（重构计划步骤 10）。

脚本打开一个真实的 Windows 窗口（不是 ``offscreen``），按固定场景驱动设备发现页，
把关键帧截图、阶段序列与发牌诊断写成一份可复查的报告：

1. 常规：扫描较快返回 6 台设备 → 等待牌堆 → 发牌 → 翻牌 → 可交互；
2. 扫描失败 → 等完一轮 → 失败说明，不进入发牌；
3. 空结果 → 等完一轮 → 跳过发牌，显示“未发现设备”；
4. 发牌期间点“返回” → 覆盖层清空，结果卡片随结果一起清空；
5. 发牌期间调整窗口大小 → 按 ``surface-changed`` 取消，卡片恢复可见可点。

脚本只使用模拟扫描器，不打开任何真实端口。窗口尺寸默认 1366×768，与计划里的验收
环境一致；``--scale`` 是乘在系统缩放上的 Qt 缩放倍数，报告中的
``device_pixel_ratio`` 是实际生效的 DPR，可按它核对常规/高 DPI。

用法::

    # 常规 DPI（本机系统缩放 125%，乘 0.8 得到 DPR 1.0）
    & $env:AUTO_TEST_PYTHON tools/manual_acceptance.py --scale 0.8 --label normal-dpi

    # 高 DPI（本机系统默认缩放 125%，DPR 1.25）
    & $env:AUTO_TEST_PYTHON tools/manual_acceptance.py --label high-dpi

报告、截图与 JSON 默认写到 ``test_results/manual_acceptance/<时间>-<标签>/``；
该目录已被 ``.gitignore`` 忽略，验收证据只留在本机。退出码：0 全部通过，1 有检查
未通过，2 参数错误，3 运行环境不符合真实验收条件（非 Windows，或
``offscreen`` / ``minimal`` 等非桌面平台）。

验收是“证据不足即不通过”：窗口没有完整落在桌面可用区内、截图不是桌面合成画面、
结果挂起后没有等满等待牌堆的一整轮、阶段序列与预期不符，都会记为未通过检查，
而不是降级成诊断信息后继续算通过。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: 验收窗口的逻辑尺寸（计划步骤 10 指定的 1366×768）。
WINDOW_WIDTH = 1366
WINDOW_HEIGHT = 768
#: 验收用的最小配置：不接现场配置，也不读写任何运行目录。
ACCEPTANCE_CONFIG = {
    "test_frequencies": [1.0],
    "instruments": {"power_supplies": {}},
}

SMW_IDN = "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47"
DP832A_IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16"
DP832A_SECOND_IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B174400885,00.01.16"
CEYEAR_IDN = "Ceyear Technologies,4082F,QZNI000866,1.12.04"

DEVICE_ROLES = {
    SMW_IDN: "signal_generator",
    DP832A_IDN: "power_supply",
    CEYEAR_IDN: "spectrum_analyzer",
}

#: 六台设备的模拟网络：发牌时一行排满，能看出多张牌各自的落点。
DEVICE_RESPONSES = {
    ("192.168.1.201", 5025): SMW_IDN,
    ("192.168.1.202", 5025): CEYEAR_IDN,
    ("192.168.1.102", 5555): DP832A_IDN,
    ("192.168.1.103", 5555): DP832A_SECOND_IDN,
    ("192.168.1.104", 5555): "RIGOL TECHNOLOGIES,DP832A,DP8B174700950,00.01.14",
    ("192.168.1.150", 5025): "Keysight Technologies,N9020A,MY51234567,A.18.21",
}


#: 窗口必须完整落在屏幕可用区内：低于这个比例说明窗口被裁切，截图不能作为验收证据。
MIN_WINDOW_VISIBLE_RATIO = 0.999
#: 计时判定允许的调度误差：Qt 计时器、事件循环与截图都会推迟几毫秒到几十毫秒，
#: 但推迟不能多到把“等满一轮”误判为“没等”。这个误差只用于放宽下界。
WAIT_CYCLE_TOLERANCE_MS = 120.0
#: 验收环境的白名单：Windows 上的 Qt 桌面平台名是 ``windows``。其他平台名
#: （含 ``offscreen`` / ``minimal`` / ``cocoa`` 等）都没有经过这里的窗口合成验证。
ACCEPTED_QT_PLATFORMS = frozenset({"windows"})
#: 每个场景必须出现的阶段序列（重复、缺失、顺序错误都算不通过）。
EXPECTED_PHASES: dict[str, tuple[str, ...]] = {
    "deal": ("scanning", "result-pending", "dealing", "interactive", "closed"),
    "failure": ("scanning", "result-pending", "idle", "closed"),
    "empty": ("scanning", "result-pending", "idle", "closed"),
    "back": ("scanning", "result-pending", "dealing", "idle", "closed"),
    "resize": (
        "scanning",
        "result-pending",
        "dealing",
        "interactive",
        "closed",
    ),
}
#: 失败与空结果不得进入发牌：阶段记录里出现它，说明结果被提前消费。
SCENARIO_FORBIDDEN_PHASES: dict[str, tuple[str, ...]] = {
    "deal": (),
    "failure": ("dealing",),
    "empty": ("dealing",),
    "back": (),
    "resize": (),
}


# ---- 判定（纯函数，可离线测试） -------------------------------------------


def environment_problem(
    *, platform: str, sys_platform: str = sys.platform
) -> str | None:
    """返回拒绝运行的原因；环境符合真实验收时返回 ``None``。

    Windows 是唯一验收环境：其他系统上的窗口合成、DPI 与缩放行为都不同，
    结果不能代表交付环境，因此不在这里“近似通过”。
    """
    if not sys_platform.lower().startswith("win"):
        return f"当前系统是 {sys_platform!r}；真实窗口验收只接受 Windows 桌面会话。"
    if platform.lower() not in ACCEPTED_QT_PLATFORMS:
        return (
            f"当前 Qt 平台是 {platform!r}；真实窗口验收只接受 Windows 桌面平台 "
            f"（{', '.join(sorted(ACCEPTED_QT_PLATFORMS))}）。"
        )
    return None


def phase_sequence_verdict(
    observed: Sequence[str],
    expected: Sequence[str],
    forbidden: Sequence[str] = (),
) -> tuple[bool, str]:
    """判断阶段记录是否与预期完全一致：不多、不少、不重复、顺序正确。"""
    observed_list = [str(item) for item in observed]
    expected_list = [str(item) for item in expected]
    forbidden_set = {str(item) for item in forbidden}
    hit = [phase for phase in observed_list if phase in forbidden_set]
    if hit:
        return False, f"出现了不应有的阶段：{', '.join(hit)}"
    if observed_list != expected_list:
        return False, (
            f"实际序列 {' → '.join(observed_list) or '（空）'}，"
            f"预期 {' → '.join(expected_list)}"
        )
    return True, ""


def wait_cycle_verdict(
    *,
    scan_ms: float,
    pending_ms: float,
    outcome_ms: float,
    cycle_ms: float,
    tolerance_ms: float = WAIT_CYCLE_TOLERANCE_MS,
) -> tuple[bool, str]:
    """判断结果是否真的等满一整轮等待动画。

    结果必须在首轮结束前返回，否则“等满一轮”是自然发生的，不能证明策略；
    从扫描开始到消费结果又必须至少跨过一整轮（允许少量调度误差）。
    """
    pending_delay = pending_ms - scan_ms
    outcome_delay = outcome_ms - scan_ms
    if pending_delay > cycle_ms:
        return False, (
            f"结果在首轮等待结束之后才返回（{pending_delay:.0f}ms > "
            f"周期 {cycle_ms:.0f}ms），本次没有验证等待对结果的推迟"
        )
    lower_bound = cycle_ms - tolerance_ms
    if outcome_delay < lower_bound:
        return False, (
            f"扫描开始到结局只经过 {outcome_delay:.0f}ms，"
            f"短于一轮 {cycle_ms:.0f}ms（允许误差 {tolerance_ms:.0f}ms）"
        )
    return True, ""


def window_visibility_verdict(
    ratio: float, *, minimum: float = MIN_WINDOW_VISIBLE_RATIO
) -> tuple[bool, str]:
    """窗口必须在桌面可用区内完整可见，否则截图不是用户看到的完整窗口。"""
    if ratio < minimum:
        return False, f"窗口可见比例 {ratio:.3f} < {minimum:.3f}，窗口被屏幕边缘裁切"
    return True, ""


def capture_verdict(
    *, source: str, width: int, height: int, byte_count: int
) -> tuple[bool, str]:
    """截图必须是桌面合成画面，且确实写出了有内容的文件。

    ``widget`` 来源是控件自身渲染，只用于排查；它绕过了窗口合成，
    不能作为“用户看到的就是这样”的证据。
    """
    if source != "screen":
        return False, f"截图来源是 {source!r}，不是桌面合成画面"
    if width <= 0 or height <= 0:
        return False, f"截图尺寸无效：{width}x{height}"
    if byte_count <= 0:
        return False, "截图文件为空"
    return True, ""


# ---- 数据结构 ------------------------------------------------------------


@dataclass
class Check:
    """一条可复查的验收项。"""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class Screenshot:
    """一张验收截图及其来源（真实窗口合成或控件渲染）。

    ``width`` / ``height`` / ``bytes`` 用来判断文件确实写出了有内容的画面：
    截图失败或保存失败不能悄悄变成一张看似存在、实际不可用的证据。
    """

    file: str
    source: str
    width: int = 0
    height: int = 0
    bytes: int = 0


@dataclass
class ScenarioResult:
    """一个验收场景的结果。"""

    key: str
    title: str
    description: str
    checks: list[Check] = field(default_factory=list)
    screenshots: list[Screenshot] = field(default_factory=list)
    phases: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for check in self.checks if check.passed)


# ---- 模拟扫描器 ----------------------------------------------------------


def _registry() -> Any:
    """带缩写设备码的模拟设备表：与现场 ``instruments.json`` 一样按缩写码显示。"""
    from domain.instruments.labels import generate_instrument_label
    from domain.instruments.models import InstrumentIdentity
    from infrastructure.persistence.instrument_registry import InstrumentRegistry

    entries = []
    taken: list[str] = []
    for idn in (SMW_IDN, DP832A_IDN, CEYEAR_IDN):
        identity = InstrumentIdentity.from_idn(idn)
        label = generate_instrument_label(identity.full_code, taken)
        taken.append(label)
        entries.append(
            {
                "vendor": identity.vendor,
                "model": identity.model,
                "full_code": identity.full_code,
                "label": label,
                "role": DEVICE_ROLES[idn],
                "status": "online",
                "known_ips": [],
            }
        )
    return InstrumentRegistry(
        document={"schema_version": 4, "instruments": entries}
    )


def _inner_scanner() -> Any:
    from infrastructure.discovery.instrument_discovery import (
        SCAN_MODE_SIMULATED,
        build_scanner,
    )

    return build_scanner(
        registry=_registry(),
        mode=SCAN_MODE_SIMULATED,
        responses=DEVICE_RESPONSES,
        timeout_s=0.02,
    )


class _PacedScanner:
    """在模拟扫描器外面加一段固定延迟，让等待画面留出可观察的时间。"""

    def __init__(self, inner: Any, delay_s: float) -> None:
        self._inner = inner
        self._delay_s = max(0.0, float(delay_s))
        self._cancel = threading.Event()

    def set_cancel_event(self, event: threading.Event) -> None:
        self._cancel = event
        self._inner.set_cancel_event(event)

    def request_cancel(self) -> None:
        self._cancel.set()
        self._inner.request_cancel()

    def scan(self, subnet: Any, *, progress: Any = None) -> Any:
        if self._delay_s > 0.0:
            time.sleep(self._delay_s)
        return self._inner.scan(subnet, progress=progress)


class _InstantScanner:
    """立即返回一份已经解析好的模拟结果：把等待时间留给页面，而不是网段遍历。"""

    def __init__(self, result: Any) -> None:
        self._result = result
        self._cancel = threading.Event()

    def set_cancel_event(self, event: threading.Event) -> None:
        self._cancel = event

    def request_cancel(self) -> None:
        self._cancel.set()

    def scan(self, subnet: Any, *, progress: Any = None) -> Any:
        return self._result


#: 预解析好的模拟结果：模拟网段遍历要遍历整个 /24，验收里只需要“扫描很快返回”。
_DEVICE_RESULT: Any = None


def _device_result() -> Any:
    """构造一次模拟扫描结果并缓存；只在进程启动时做一次真实解析。"""
    global _DEVICE_RESULT
    if _DEVICE_RESULT is None:
        from application.ports.device_discovery import DEMO_SUBNET

        _DEVICE_RESULT = _inner_scanner().scan(DEMO_SUBNET)
    return _DEVICE_RESULT


class _FailingScanner:
    """扫描开始后立即失败的扫描器，用于验证失败分支。"""

    def __init__(self, delay_s: float, message: str) -> None:
        self._delay_s = max(0.0, float(delay_s))
        self._message = message
        self._cancel = threading.Event()

    def set_cancel_event(self, event: threading.Event) -> None:
        self._cancel = event

    def request_cancel(self) -> None:
        self._cancel.set()

    def scan(self, subnet: Any, *, progress: Any = None) -> Any:
        from infrastructure.discovery.instrument_discovery import (
            InstrumentDiscoveryError,
        )

        if self._delay_s > 0.0:
            time.sleep(self._delay_s)
        raise InstrumentDiscoveryError(self._message)


def _factory(*, delay_s: float = 0.0, empty: bool = False, failure: str | None = None):
    """返回扫描器工厂；每个场景各一份，互不共享状态。"""

    def factory(_mode: str) -> Any:
        if failure is not None:
            return _FailingScanner(delay_s, failure)
        if empty:
            from infrastructure.discovery.instrument_discovery import (
                SCAN_MODE_SIMULATED,
                build_scanner,
            )

            return _PacedScanner(
                build_scanner(
                    registry=_registry(),
                    mode=SCAN_MODE_SIMULATED,
                    responses={},
                    timeout_s=0.02,
                ),
                delay_s,
            )
        return _PacedScanner(_InstantScanner(_device_result()), delay_s)

    return factory


# ---- Qt 辅助 -------------------------------------------------------------


def _pump(ms: int) -> None:
    """处理事件并等待真实时间过去（动画计时器照常走）。"""
    from PySide6.QtTest import QTest

    QTest.qWait(max(0, int(ms)))


def _wait_until(
    predicate: Callable[[], bool], timeout_ms: int = 8000, step_ms: int = 10
) -> bool:
    """等状态出现；每次等待都处理事件，超时返回最后一次判断结果。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if predicate():
            return True
        _pump(step_ms)
    return bool(predicate())


def _is_close(actual: Any, expected: Any, tolerance: int = 12) -> bool:
    return max(
        abs(actual.red() - expected.red()),
        abs(actual.green() - expected.green()),
        abs(actual.blue() - expected.blue()),
    ) <= tolerance


def _ink(image: Any, step: int = 6) -> int:
    """与左上角底色不同的采样点数量：判断画面里有没有画出东西。"""
    if image.isNull() or image.width() < 2 or image.height() < 2:
        return 0
    background = image.pixelColor(0, 0)
    count = 0
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            if not _is_close(image.pixelColor(x, y), background):
                count += 1
    return count


def _widget_ink(widget: Any) -> int:
    return _ink(widget.grab().toImage())


def _phase_label(phase: Any) -> str:
    return str(getattr(phase, "value", phase))


def _capture_window(window: Any, screen: Any, path: Path) -> Screenshot:
    """优先截真实窗口；窗口被屏幕边缘裁掉或合成不可用时退回控件渲染。

    退回控件渲染只用于排查画面内容；是否算作验收证据由 :func:`capture_verdict`
    判断，调用方会把非桌面合成来源记为未通过检查。
    """
    shot = screen.grabWindow(window.winId())
    expected_width = round(window.width() * window.devicePixelRatioF())
    if shot.isNull() or shot.width() < expected_width - 2:
        shot = window.grab()
        source = "widget"
    else:
        source = "screen"
    # 先删掉同名旧文件：保存失败时不能因为上次运行留下的文件而被当成有效证据。
    path.unlink(missing_ok=True)
    saved = shot.save(str(path))
    byte_count = path.stat().st_size if saved and path.exists() else 0
    return Screenshot(
        file=path.name,
        source=source,
        width=shot.width(),
        height=shot.height(),
        bytes=byte_count,
    )


# ---- 验收场景 ------------------------------------------------------------


class AcceptanceCase:
    """一个场景一个窗口：场景之间不共享面板、卡片或动画状态。"""

    def __init__(
        self,
        *,
        key: str,
        title: str,
        description: str,
        app: Any,
        screen: Any,
        output_dir: Path,
    ) -> None:
        self.result = ScenarioResult(key=key, title=title, description=description)
        self.app = app
        self.screen = screen
        self.output_dir = output_dir
        self.window: Any = None
        self.page: Any = None
        self.panel: Any = None
        self._started = 0.0
        self.scan_started_ms = 0.0

    # ---- 生命周期 --------------------------------------------------------

    def open(self, factory: Callable[[str], Any]) -> "AcceptanceCase":
        from presentation.qt.test_configuration_page import TestConfigurationPage
        from presentation.qt.configuration_session import ConfigurationSession
        from presentation.qt.instrument_setup_page import InstrumentSetupPage

        self.page = TestConfigurationPage(dict(ACCEPTANCE_CONFIG))
        session = ConfigurationSession(self.page)
        self.window = InstrumentSetupPage(session, scanner_factory=factory)
        self.window.setWindowTitle(f"PA 自动测试 · 真实窗口验收 · {self.result.title}")
        self.window.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.window.move(0, 0)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.window.enter()
        _pump(250)
        self.panel = self.window.discovery_panel
        self.panel.phase_changed.connect(self._record_phase)
        self._started = time.monotonic()
        self._check_window_visible()
        return self

    def _check_window_visible(self) -> None:
        """每个场景都要确认窗口完整落在桌面可用区里，截图才算有效证据。"""
        frame = self.window.frameGeometry()
        available = self.screen.availableGeometry()
        visible = frame.intersected(available)
        ratio = (visible.width() * visible.height()) / max(
            1, frame.width() * frame.height()
        )
        ok, detail = window_visibility_verdict(ratio)
        self.check(
            "窗口完整显示在桌面上",
            ok,
            detail or f"可见比例 {ratio:.3f}",
        )

    def close(self) -> None:
        panel = self.panel
        if panel is not None:
            try:
                panel.shutdown(timeout_ms=2000)
            except RuntimeError as error:  # 页面已进入不可关闭状态时仍要释放窗口
                self.result.notes.append(f"shutdown 失败：{error}")
        self.check_phase_sequence()
        for widget in (self.window, self.page):
            if widget is not None:
                widget.close()
        _pump(80)
        for widget in (self.window, self.page):
            if widget is not None:
                widget.deleteLater()
        _pump(50)

    # ---- 记录 ------------------------------------------------------------

    def _record_phase(self, phase: str) -> None:
        self.result.phases.append(
            {"phase": str(phase), "at_ms": round(self.elapsed_ms(), 1)}
        )

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._started) * 1000.0

    def check(self, name: str, condition: Any, detail: str = "") -> bool:
        passed = bool(condition)
        self.result.checks.append(
            Check(name=name, passed=passed, detail="" if passed else detail)
        )
        return passed

    def shot(self, name: str) -> None:
        path = self.output_dir / f"{self.result.key}-{name}.png"
        screenshot = _capture_window(self.window, self.screen, path)
        self.result.screenshots.append(screenshot)
        ok, detail = capture_verdict(
            source=screenshot.source,
            width=screenshot.width,
            height=screenshot.height,
            byte_count=screenshot.bytes,
        )
        self.check(f"截图 {name} 是有效的真实窗口画面", ok, detail)

    def begin_scan(self) -> None:
        """记录扫描起点再发起扫描：等待周期的判定以这里为基准。"""
        self.scan_started_ms = self.elapsed_ms()
        self.panel.request_scan()

    def check_phase_sequence(self) -> None:
        """阶段记录必须与场景预期完全一致，否则日志再完整也不算通过。

        在 :meth:`close` 里调用：``closed`` 是关窗时真实发生的最后一步，
        把它一起纳入序列，才能确认场景从扫描一直收尾到页面关闭。
        """
        observed = [str(item["phase"]) for item in self.result.phases]
        expected = EXPECTED_PHASES[self.result.key]
        forbidden = SCENARIO_FORBIDDEN_PHASES[self.result.key]
        ok, detail = phase_sequence_verdict(observed, expected, forbidden)
        self.check("阶段序列符合预期", ok, detail)

    def diag(self, label: str, snapshot: dict[str, Any]) -> None:
        self.result.diagnostics[label] = {
            key: snapshot.get(key)
            for key in (
                "event",
                "phase",
                "elapsed_ms",
                "duration_ms",
                "card_count",
                "front_source",
                "overlay_visible",
                "overlay_width",
                "overlay_height",
                "paint_frames",
                "first_paint_ms",
                "drawn_cards",
                "first_drawn_cards",
                "revealed_count",
                "cancel_reason",
                "restore_result",
            )
        }

    # ---- 共同检查 --------------------------------------------------------

    def check_no_residuals(self) -> None:
        """动画、等待牌堆与扫描线程都收干净，不留下看不见的计时器。"""
        panel = self.panel
        layer = panel.deal_animation
        # worker.finished 可能比结果消费晚到：先给它一点时间释放，再判断有没有残留。
        _wait_until(
            lambda: panel.worker is None and not panel.is_scanning(), timeout_ms=1500
        )
        self.check(
            "发牌计时器已停止",
            not layer.is_running() and not layer._timer.isActive(),
            f"is_running={layer.is_running()}",
        )
        self.check(
            "等待牌堆计时器已停止",
            not panel.wait_stack._frame_timer.isActive(),
            "等待牌堆隐藏后仍在走帧",
        )
        self.check(
            "扫描线程已释放",
            panel.worker is None and not panel.is_scanning(),
            "worker 仍被面板持有",
        )


def _scenario_deal(case: AcceptanceCase) -> None:
    """正常路径：等待 → 发牌 → 翻牌 → 可交互。"""
    from presentation.qt.instrument_discovery_panel import DiscoveryPhase
    from presentation.qt.scan_card import WAIT_CYCLE_MS

    panel = case.panel
    layer = panel.deal_animation

    case.begin_scan()
    case.check(
        "点击“查找设备”后进入 SCANNING",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.SCANNING, 2000),
        _phase_label(panel.phase()),
    )
    _pump(180)
    case.shot("01-waiting")
    case.check("扫描中显示等待牌堆", panel.wait_stack.isVisible())
    case.check("扫描中收起“查找设备”", not panel.scan_btn.isVisible())
    case.check("扫描设置暂时禁用", not panel.subnet_edit.isEnabled())
    case.check(
        "等待牌堆有可见画面", _widget_ink(panel.wait_stack) > 0, "等待画面是空的"
    )

    case.check(
        "扫描结果先挂起，不立即发牌",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.RESULT_PENDING, 5000),
        _phase_label(panel.phase()),
    )
    pending_ms = case.elapsed_ms()
    case.check("挂起期间等待牌堆仍在", panel.wait_stack.isVisible())
    case.check(
        "一轮等待动画结束后进入 DEALING",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.DEALING, 6000),
        _phase_label(panel.phase()),
    )
    dealing_ms = case.elapsed_ms()
    ok, detail = wait_cycle_verdict(
        scan_ms=case.scan_started_ms,
        pending_ms=pending_ms,
        outcome_ms=dealing_ms,
        cycle_ms=WAIT_CYCLE_MS,
    )
    case.check("扫描很快返回，但发牌等满了这一轮等待动画", ok, detail)

    cards = panel.cards()
    overlay = panel.animation_overlay()
    case.check(
        "发牌层挂在页面级覆盖层上",
        overlay is not None and layer.parentWidget() is overlay,
        f"parent={type(layer.parentWidget()).__name__}",
    )
    case.check(
        "覆盖层铺满页面根容器",
        overlay is not None and overlay.geometry() == overlay.parentWidget().rect(),
        "覆盖层几何与根容器不一致",
    )
    case.check(
        "每张卡各有一个动画项",
        layer.item_count() == len(cards) == 6,
        f"items={layer.item_count()} cards={len(cards)}",
    )
    case.check(
        "发牌期间真实卡片先藏起来",
        all(card.graphicsEffect() is not None for card in cards),
        "有卡片没有透明度效果，可能提前露出",
    )
    case.check("发牌期间卡片区不可点", not panel.cards_area.isEnabled())

    _pump(120)
    case.shot("02-dealing")
    live = layer.diagnostics()
    case.diag("dealing", live)
    case.check("发牌阶段画面非空", _widget_ink(layer) > 0, "覆盖层是空白的")
    case.check(
        "发牌阶段覆盖层可见",
        live.get("overlay_visible") is True,
        f"overlay_visible={live.get('overlay_visible')}",
    )
    case.check(
        "首帧真的画出了牌面",
        int(live.get("first_drawn_cards") or 0) > 0,
        f"first_drawn_cards={live.get('first_drawn_cards')}",
    )
    case.check(
        "正面来自卡片资产（不是控件截图）",
        live.get("front_source") == "asset",
        f"front_source={live.get('front_source')}",
    )

    case.check(
        "最后一张落位后进入翻牌",
        _wait_until(lambda: layer.phase_at() == "flipping", 5000),
        f"phase={layer.phase_at()}",
    )
    _pump(70)
    case.shot("03-flipping")
    case.diag("flipping", layer.diagnostics())
    case.check("翻牌阶段画面非空", _widget_ink(layer) > 0, "覆盖层是空白的")

    case.check(
        "最后一张翻完进入 INTERACTIVE",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.INTERACTIVE, 8000),
        _phase_label(panel.phase()),
    )
    _pump(150)
    case.shot("04-result")
    case.check(
        "结果卡片可见且数量正确",
        len(cards) == 6 and all(card.isVisible() for card in cards),
        f"cards={len(cards)}",
    )
    case.check(
        "卡片不再带透明度效果",
        all(card.graphicsEffect() is None for card in cards),
        "有卡片仍被藏起",
    )
    case.check("卡片区恢复可点", panel.cards_area.isEnabled())
    case.check(
        "不再补写统计行",
        not panel.status_label.isVisible(),
        f"status={panel.status_label.text()!r}",
    )
    finished = layer.diagnostics()
    case.diag("finish", finished)
    case.check(
        "收尾诊断为 finish",
        finished.get("event") == "finish",
        f"event={finished.get('event')}",
    )
    case.check(
        "六张牌全部翻开",
        int(finished.get("revealed_count") or 0) == 6,
        f"revealed={finished.get('revealed_count')}",
    )
    case.check(
        "整段动画确实绘制过",
        int(finished.get("paint_frames") or 0) > 0
        and int(finished.get("first_drawn_cards") or 0) > 0,
        f"paint_frames={finished.get('paint_frames')}",
    )
    case.check_no_residuals()


def _scenario_failure(case: AcceptanceCase) -> None:
    """扫描失败：等完一轮 → 失败说明，不进入发牌。"""
    from presentation.qt.instrument_discovery_panel import DiscoveryPhase
    from presentation.qt.scan_card import WAIT_CYCLE_MS

    panel = case.panel

    case.begin_scan()
    case.check(
        "失败前先进入 SCANNING",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.SCANNING, 2000),
        _phase_label(panel.phase()),
    )
    _pump(120)
    case.shot("01-waiting")
    case.check(
        "失败结论先挂起",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.RESULT_PENDING, 4000),
        _phase_label(panel.phase()),
    )
    pending_ms = case.elapsed_ms()
    case.check(
        "失败回到 IDLE 布局",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.IDLE, 6000),
        _phase_label(panel.phase()),
    )
    idle_ms = case.elapsed_ms()
    _pump(150)
    case.shot("02-failure")
    ok, detail = wait_cycle_verdict(
        scan_ms=case.scan_started_ms,
        pending_ms=pending_ms,
        outcome_ms=idle_ms,
        cycle_ms=WAIT_CYCLE_MS,
    )
    case.check("失败同样等完这一轮等待动画", ok, detail)
    case.check(
        "显示失败说明",
        panel.status_label.isVisible() and "扫描失败" in panel.status_label.text(),
        f"status={panel.status_label.text()!r}",
    )
    case.check("重新显示“查找设备”", panel.scan_btn.isVisible())
    case.check("不显示结果“返回”", not panel.back_btn.isVisible())
    case.check("没有留下结果卡片", panel.card_count() == 0)
    skipped = panel.deal_animation.diagnostics()
    case.diag("no-deal", skipped)
    case.check(
        "没有启动发牌动画",
        skipped.get("event") == "idle" and int(skipped.get("paint_frames") or 0) == 0,
        f"event={skipped.get('event')}",
    )
    case.check("失败不留下发牌画面", _widget_ink(panel.deal_animation) == 0)
    case.check_no_residuals()


def _scenario_empty(case: AcceptanceCase) -> None:
    """空结果：等完一轮 → 跳过发牌，显示“未发现设备”。"""
    from presentation.qt.instrument_discovery_panel import DiscoveryPhase
    from presentation.qt.scan_card import WAIT_CYCLE_MS

    panel = case.panel

    case.begin_scan()
    case.check(
        "空结果前先进入 SCANNING",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.SCANNING, 2000),
        _phase_label(panel.phase()),
    )
    _pump(120)
    case.shot("01-waiting")
    case.check(
        "空结果先挂起",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.RESULT_PENDING, 4000),
        _phase_label(panel.phase()),
    )
    pending_ms = case.elapsed_ms()
    case.check(
        "空结果回到 IDLE 布局",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.IDLE, 6000),
        _phase_label(panel.phase()),
    )
    idle_ms = case.elapsed_ms()
    _pump(150)
    case.shot("02-empty")
    ok, detail = wait_cycle_verdict(
        scan_ms=case.scan_started_ms,
        pending_ms=pending_ms,
        outcome_ms=idle_ms,
        cycle_ms=WAIT_CYCLE_MS,
    )
    case.check("空结果同样等完这一轮等待动画", ok, detail)
    case.check(
        "显示“未发现设备”",
        panel.status_label.isVisible() and "未发现设备" in panel.status_label.text(),
        f"status={panel.status_label.text()!r}",
    )
    case.check("重新显示“查找设备”", panel.scan_btn.isVisible())
    case.check("没有结果卡片", panel.card_count() == 0)
    skipped = panel.deal_animation.diagnostics()
    case.diag("skip", skipped)
    case.check(
        "空结果走 skip，不绘制发牌画面",
        skipped.get("event") == "skip"
        and int(skipped.get("paint_frames") or 0) == 0,
        f"event={skipped.get('event')} paint_frames={skipped.get('paint_frames')}",
    )
    case.check("覆盖层保持空白", _widget_ink(panel.deal_animation) == 0)
    case.check_no_residuals()


def _scenario_back(case: AcceptanceCase) -> None:
    """发牌期间点“返回”：覆盖层清空，卡片随结果一起清空。"""
    from presentation.qt.instrument_discovery_panel import DiscoveryPhase

    panel = case.panel
    layer = panel.deal_animation

    case.begin_scan()
    case.check(
        "先进入发牌阶段",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.DEALING, 8000),
        _phase_label(panel.phase()),
    )
    _pump(200)
    case.shot("01-dealing")
    case.check("发牌正在播放", layer.is_running())
    case.check("点击前覆盖层有画面", _widget_ink(layer) > 0)

    case.check("“返回”按钮在发牌期间可见可点", panel.back_btn.isVisible())
    panel.back_btn.click()
    case.check(
        "点“返回”后回到 IDLE",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.IDLE, 2000),
        _phase_label(panel.phase()),
    )
    _pump(150)
    case.shot("02-after-back")
    snapshot = layer.diagnostics()
    case.diag("cancel", snapshot)
    case.check("发牌已经停止", not layer.is_running())
    case.check(
        "取消原因记录为 results-cleared",
        snapshot.get("cancel_reason") == "results-cleared",
        f"cancel_reason={snapshot.get('cancel_reason')}",
    )
    case.check(
        "卡片随结果清空（不恢复显示）",
        snapshot.get("restore_result") == "hidden",
        f"restore_result={snapshot.get('restore_result')}",
    )
    case.check("结果卡片已清空", panel.card_count() == 0)
    case.check("覆盖层回到空白", _widget_ink(layer) == 0)
    case.check(
        "重新显示“查找设备”",
        panel.scan_btn.isVisible() and not panel.back_btn.isVisible(),
    )
    case.check(
        "状态行随结果清空",
        panel.status_label.text() == "" and not panel.status_label.isVisible(),
        f"status={panel.status_label.text()!r}",
    )
    case.check_no_residuals()


def _scenario_resize(case: AcceptanceCase) -> None:
    """发牌期间调整窗口：按 surface-changed 取消，卡片恢复可见可点。"""
    from presentation.qt.instrument_discovery_panel import DiscoveryPhase

    panel = case.panel
    layer = panel.deal_animation

    case.begin_scan()
    case.check(
        "先进入发牌阶段",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.DEALING, 8000),
        _phase_label(panel.phase()),
    )
    _pump(180)
    case.shot("01-dealing")
    case.check("发牌正在播放", layer.is_running())

    case.window.resize(1180, 700)
    case.check(
        "窗口缩放后本次发牌收尾并进入 INTERACTIVE",
        _wait_until(lambda: panel.phase() is DiscoveryPhase.INTERACTIVE, 3000),
        _phase_label(panel.phase()),
    )
    _pump(180)
    case.shot("02-after-resize")
    snapshot = layer.diagnostics()
    case.diag("cancel", snapshot)
    case.check("发牌已经停止", not layer.is_running())
    case.check(
        "取消原因记录为 surface-changed",
        snapshot.get("cancel_reason") == "surface-changed",
        f"cancel_reason={snapshot.get('cancel_reason')}",
    )
    case.check(
        "卡片恢复显示",
        snapshot.get("restore_result") == "revealed",
        f"restore_result={snapshot.get('restore_result')}",
    )
    case.check(
        "真实卡片可见、无残留效果",
        all(
            card.isVisible() and card.graphicsEffect() is None
            for card in panel.cards()
        ),
    )
    case.check("卡片区恢复可点", panel.cards_area.isEnabled())
    case.check("取消后不补写统计行", not panel.status_label.isVisible())
    case.check("覆盖层回到空白", _widget_ink(layer) == 0)
    case.check_no_residuals()


SCENARIOS: tuple[
    tuple[str, str, str, Callable[[AcceptanceCase], None], Callable[[], Any]], ...
] = (
    (
        "deal",
        "常规：等待 → 发牌 → 翻牌",
        "扫描较快返回 6 台设备；结果挂起到等待牌堆走完一轮，再发牌、逐张翻开。",
        _scenario_deal,
        lambda: _factory(delay_s=0.45),
    ),
    (
        "failure",
        "扫描失败",
        "扫描器报错；等完一轮等待动画后显示失败说明，不进入发牌。",
        _scenario_failure,
        lambda: _factory(delay_s=0.2, failure="验收模拟：目标端口不可达"),
    ),
    (
        "empty",
        "空结果",
        "模拟网络里没有任何设备；等完一轮后跳过发牌并显示空结果说明。",
        _scenario_empty,
        lambda: _factory(delay_s=0.2, empty=True),
    ),
    (
        "back",
        "发牌期间点“返回”",
        "发牌进行中点结果区的“返回”；覆盖层清空，卡片随结果一起清空。",
        _scenario_back,
        lambda: _factory(delay_s=0.0),
    ),
    (
        "resize",
        "发牌期间调整窗口",
        "发牌进行中缩放窗口；按 surface-changed 取消本次发牌，卡片恢复可见可点。",
        _scenario_resize,
        lambda: _factory(delay_s=0.0),
    ),
)


SCENARIO_KEYS = tuple(item[0] for item in SCENARIOS)


# ---- 报告 ----------------------------------------------------------------


def _environment(app: Any, window: Any) -> dict[str, Any]:
    from PySide6.QtCore import qVersion

    screen = app.primaryScreen()
    frame = window.frameGeometry()
    available = screen.availableGeometry()
    visible = frame.intersected(available)
    frame_area = max(1, frame.width() * frame.height())
    return {
        "platform": app.platformName(),
        "qt_version": qVersion(),
        "interpreter": sys.executable,
        "screen": screen.name(),
        "screen_geometry": f"{screen.geometry().width()}x{screen.geometry().height()}",
        "device_pixel_ratio": screen.devicePixelRatio(),
        "window_size": f"{window.width()}x{window.height()}",
        "window_device_pixel_ratio": window.devicePixelRatioF(),
        "window_visible_ratio": round(
            (visible.width() * visible.height()) / frame_area, 3
        ),
        "qt_scale_factor": os.environ.get("QT_SCALE_FACTOR", ""),
        "window_title": window.windowTitle(),
    }


def _write_report(
    run_dir: Path,
    environment: dict[str, Any],
    results: Sequence[ScenarioResult],
    *,
    label: str,
    started_at: datetime,
) -> None:
    total = sum(len(result.checks) for result in results)
    passed = sum(result.passed_count for result in results)
    screen_geometry = environment.get("screen_geometry", "未知")
    device_pixel_ratio = environment.get("device_pixel_ratio", "未知")
    window_dpr = environment.get("window_device_pixel_ratio", "未知")
    visible_ratio = environment.get("window_visible_ratio", "未知")
    scale_factor = environment.get("qt_scale_factor") or "默认"
    lines = [
        "# 仪器发现页真实窗口验收",
        "",
        f"- 开始时间：{started_at.isoformat(timespec='seconds')}",
        f"- 本次标签：{label or 'default'}",
        f"- 平台：{environment.get('platform', '未知')} / "
        f"Qt {environment.get('qt_version', '未知')}",
        f"- 屏幕：{environment.get('screen', '未知')} "
        f"{screen_geometry} @DPR {device_pixel_ratio}",
        f"- 窗口：{environment.get('window_size', '未知')} "
        f"@DPR {window_dpr}"
        f"（可见比例 {visible_ratio}）",
        f"- Qt 缩放倍数：{scale_factor}",
        f"- 解释器：`{environment.get('interpreter', sys.executable)}`",
        "",
        f"## 结果：{passed}/{total} 项通过",
        "",
        "| 场景 | 检查 | 结果 |",
        "| --- | --- | --- |",
    ]
    for result in results:
        state = "通过" if result.passed else "未通过"
        lines.append(
            f"| {result.title} | {result.passed_count}/{len(result.checks)} | {state} |"
        )

    for result in results:
        lines.extend(["", f"## {result.title}", "", result.description, ""])
        if result.phases:
            sequence = " → ".join(
                f"`{item['phase']}`@{item['at_ms']:.0f}ms" for item in result.phases
            )
            lines.extend([f"阶段序列：{sequence}", ""])
        lines.append("| 检查 | 结果 | 说明 |")
        lines.append("| --- | --- | --- |")
        for check in result.checks:
            mark = "通过" if check.passed else "未通过"
            lines.append(f"| {check.name} | {mark} | {check.detail or ''} |")
        if result.diagnostics:
            lines.extend(["", "诊断快照：", ""])
            lines.append("| 时刻 | event | phase | 卡片 | 正面来源 | 绘制帧 | 首帧牌面 | 翻开 | cancel | restore |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for label_name, snapshot in result.diagnostics.items():
                lines.append(
                    "| {label} | {event} | {phase} | {cards} | {front} | {frames} | "
                    "{first} | {revealed} | {cancel} | {restore} |".format(
                        label=label_name,
                        event=snapshot.get("event"),
                        phase=snapshot.get("phase"),
                        cards=snapshot.get("card_count"),
                        front=snapshot.get("front_source"),
                        frames=snapshot.get("paint_frames"),
                        first=snapshot.get("first_drawn_cards"),
                        revealed=snapshot.get("revealed_count"),
                        cancel=snapshot.get("cancel_reason"),
                        restore=snapshot.get("restore_result"),
                    )
                )
        if result.screenshots:
            lines.extend(["", "截图："])
            for shot in result.screenshots:
                origin = "真实窗口" if shot.source == "screen" else "控件渲染（仅排查）"
                size_kb = shot.bytes / 1024.0
                lines.append(
                    f"- `{shot.file}`（{origin}，{shot.width}×{shot.height}，"
                    f"{size_kb:.1f} KB）"
                )
        for note in result.notes:
            lines.append(f"- 备注：{note}")

    lines.extend(
        [
            "",
            "## 人工确认",
            "",
            "逐张打开截图确认：等待牌堆不是空白、发牌/翻牌画面连续、结果卡片没有重影、",
            "没有提前露出真实卡片；并确认窗口在实际桌面上可见（`window_visible_ratio`）。",
            "",
        ]
    )
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    payload = {
        "label": label,
        "started_at": started_at.isoformat(timespec="seconds"),
        "environment": environment,
        "results": [asdict(result) for result in results],
        "passed": passed,
        "total": total,
    }
    (run_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---- 入口 ----------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在真实 Windows 窗口里验收仪器发现页的等待/发牌/翻牌",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=None,
        metavar="倍数",
        help="乘在系统缩放上的 Qt 缩放倍数（例如 0.8）；省略时用系统默认",
    )
    parser.add_argument(
        "--label",
        default="",
        help="本次运行标签，写进输出目录名与报告（例如 normal-dpi / high-dpi）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="报告目录；省略时写到 test_results/manual_acceptance/<时间>-<标签>/",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=SCENARIO_KEYS,
        default=None,
        metavar="场景",
        help=f"只跑指定场景（可多个）：{', '.join(SCENARIO_KEYS)}",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.scale is not None:
        if args.scale <= 0:
            print("--scale 必须是正数", file=sys.stderr)
            return 2
        os.environ["QT_SCALE_FACTOR"] = str(args.scale)

    from PySide6.QtWidgets import QApplication

    from presentation.qt.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("PA 自动测试 · 真实窗口验收")
    apply_theme(app)
    platform = app.platformName()
    problem = environment_problem(platform=platform)
    if problem is not None:
        print(f"拒绝运行：{problem}", file=sys.stderr)
        return 3

    started_at = datetime.now()
    slug = started_at.strftime("%Y%m%d-%H%M%S")
    suffix = f"-{args.label}" if args.label else ""
    run_dir = args.output or (
        PROJECT_ROOT / "test_results" / "manual_acceptance" / f"{slug}{suffix}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    screen = app.primaryScreen()

    results: list[ScenarioResult] = []
    environment: dict[str, Any] = {}
    scenarios = [
        item for item in SCENARIOS if args.only is None or item[0] in args.only
    ]
    if any(item[0] in ("deal", "back", "resize") for item in scenarios):
        print("准备模拟扫描结果（一次 /24 解析，不打开真实端口）…")
        _device_result()
    for key, title, description, runner, factory in scenarios:
        case = AcceptanceCase(
            key=key,
            title=title,
            description=description,
            app=app,
            screen=screen,
            output_dir=run_dir,
        )
        print(f"场景：{title}")
        try:
            case.open(factory())
            if not environment:
                environment = _environment(app, case.window)
            runner(case)
        except Exception as error:  # 单个场景失败仍要继续验收其余场景
            case.result.notes.append(f"运行异常：{type(error).__name__}: {error}")
            case.result.checks.append(
                Check(name="场景运行没有异常", passed=False, detail=str(error))
            )
        finally:
            case.close()
        results.append(case.result)
        state = "通过" if case.result.passed else "未通过"
        print(f"  {state} {case.result.passed_count}/{len(case.result.checks)}")

    if not environment:
        environment = {
            "platform": platform,
            "qt_version": "",
            "interpreter": sys.executable,
        }
    _write_report(
        run_dir, environment, results, label=args.label, started_at=started_at
    )

    total = sum(len(result.checks) for result in results)
    passed = sum(result.passed_count for result in results)
    print(
        f"\n真实窗口验收：{passed}/{total} 项通过，"
        f"窗口 {WINDOW_WIDTH}x{WINDOW_HEIGHT} @DPR "
        f"{environment.get('window_device_pixel_ratio')}"
    )
    print(f"报告：{run_dir / 'report.md'}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
