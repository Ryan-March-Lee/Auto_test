"""可选的 AI 助手聊天面板。

助手是可选能力：缺失可选依赖（例如 ``requests``）时面板退化为不可用占位实现，
窗口与测量链路仍可正常启动。面板自己承担三件事：

- 通过 :func:`create_assistant` 按需导入 ``assistant.llm``，把“缺少可选依赖”与
  “助手初始化失败”都转成用户可见的说明，而不是静默吞掉；
- 把测试上下文整理成纯文本，供 AI 侧引用；
- 只通过窗口的只读属性读取当前页面与实时数据，不写回任何测量状态。

日志统一走 ``infrastructure.logging.app_logging.get_logger``；界面提示走面板自身的
聊天显示区，不用 ``print``。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from infrastructure.logging.app_logging import get_logger

from ..theme import set_button_variant
from .chat_dialogs import ChatHistoryDialog, ChatSettingsDialog
from .chat_worker import ChatWorker, UnavailableAssistant

logger = get_logger(__name__)

_MODEL_DISPLAY_NAMES = (("qwen", "Qwen"), ("deepseek", "DeepSeek"))

# 实时数据里参与上下文摘要的扫描列：键 -> 展示名。
_SWEEP_CONTEXT_FIELDS = (
    ("input_power_dut", "输入功率(Pin)"),
    ("output_power_dut", "输出功率(Pout)"),
    ("input_power_sg", "信号源输入(Pin)"),
    ("output_power_driver", "驱动输出(Pout)"),
    ("gain", "增益(Gain)"),
    ("efficiency", "效率(PAE)"),
)
_SWEEP_EXTREMA_FIELDS = frozenset({"gain", "efficiency"})

_WELCOME_TEMPLATE = (
    "你好！我是{name} AI助手，专门为功放测试系统提供技术支持。我可以帮你：\n\n"
    "• 分析测试数据和结果\n"
    "• 诊断测试过程中的问题\n"
    "• 提供功放设计和测试建议\n"
    "• 解答技术疑问\n\n"
    "有什么需要帮助的吗？"
)


def create_assistant(
    paths: Any = None,
) -> tuple[Any | None, str | None]:
    """构造可选 LLM 助手。

    返回 ``(助手实例, 不可用原因)``：可用时原因为 ``None``。

    **可选能力边界**：这里是助手初始化的唯一入口，因此整个构造过程（导入 +
    实例化）的普通异常都在此降级为“不可用 + 原因”，只记录日志、不向上抛出——
    缺失可选依赖、配置文件损坏、可选搜索工具初始化失败都不允许影响主测量链路。
    ``BaseException``（``KeyboardInterrupt``/``SystemExit``）不捕获。
    """
    try:
        from assistant.llm import LLMChat

        return LLMChat(paths=paths), None
    except ImportError as error:  # 缺少可选依赖属于预期情况
        logger.info("AI 助手未启用（缺少可选依赖）: %s", error)
        return None, f"缺少可选依赖: {error}"
    except Exception as error:  # noqa: BLE001 - 可选能力边界：任何初始化失败都必须降级
        logger.warning("AI 助手初始化失败: %s", error)
        return None, f"助手初始化失败: {error}"


class ChatPanel(QWidget):
    """聊天面板：显示对话、发送消息并展示助手不可用的原因。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        paths: Any = None,
        assistant_factory: Callable[[Any], tuple[Any | None, str | None]] = create_assistant,
    ) -> None:
        super().__init__(parent)
        self.paths = paths
        self.main_window = parent
        self.assistant_factory = assistant_factory
        self.chat_worker: ChatWorker | None = None
        self.unavailable_reason: str | None = None
        self._welcome_shown = False
        self.llm_chat = self._build_assistant()
        self._build_ui()
        self.load_chat_history()

    # ---- 构造 ----

    def _build_assistant(self) -> Any:
        # 注入的工厂同样属于可选能力边界：它的失败不能让面板/窗口构造失败。
        try:
            assistant, reason = self.assistant_factory(self.paths)
        except Exception as error:  # noqa: BLE001 - 可选能力边界
            logger.warning("AI 助手工厂异常: %s", error)
            assistant, reason = None, f"助手初始化失败: {error}"
        if assistant is None:
            self.unavailable_reason = reason or "不可用"
            logger.warning("AI 助手未启用: %s", self.unavailable_reason)
            return UnavailableAssistant()
        logger.info("AI 助手已启用: %s", getattr(assistant, "model_name", "unknown"))
        return assistant

    def _build_ui(self) -> None:
        self.setObjectName("chatPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(6)
        header_title = QLabel("CHAT")
        header_title.setProperty("role", "section")
        header.addWidget(header_title)
        header.addStretch(1)
        self.new_chat_btn = QPushButton("+")
        set_button_variant(self.new_chat_btn, "secondary")
        self.new_chat_btn.setFixedWidth(34)
        self.new_chat_btn.setToolTip("开始新对话")
        self.history_btn = QPushButton("历史")
        set_button_variant(self.history_btn, "secondary")
        self.settings_btn = QPushButton("设置")
        set_button_variant(self.settings_btn, "secondary")
        self.close_btn = QPushButton("关闭")
        set_button_variant(self.close_btn, "ghost")
        self.new_chat_btn.clicked.connect(self.start_new_conversation)
        self.history_btn.clicked.connect(self.show_history_dialog)
        self.settings_btn.clicked.connect(self.show_settings_dialog)
        self.close_btn.clicked.connect(self.hide_chat_panel)
        for button in (self.new_chat_btn, self.history_btn, self.settings_btn, self.close_btn):
            header.addWidget(button)
        layout.addLayout(header)

        self.web_search_checkbox = QCheckBox("联网搜索")
        self.web_search_checkbox.stateChanged.connect(self.on_web_search_changed)
        layout.addWidget(self.web_search_checkbox)

        self.chat_display = QTextEdit()
        self.chat_display.setObjectName("chatView")
        self.chat_display.setReadOnly(True)
        self.chat_display.setMinimumHeight(160)
        layout.addWidget(self.chat_display, 1)

        shortcuts = QHBoxLayout()
        shortcuts.setSpacing(8)
        self.analyze_test_btn = QPushButton("分析测试数据")
        set_button_variant(self.analyze_test_btn, "secondary")
        self.troubleshoot_btn = QPushButton("故障诊断")
        set_button_variant(self.troubleshoot_btn, "secondary")
        self.clear_btn = QPushButton("清除历史")
        set_button_variant(self.clear_btn, "ghost")
        self.analyze_test_btn.clicked.connect(self.analyze_test_data)
        self.troubleshoot_btn.clicked.connect(self.troubleshoot_issues)
        self.clear_btn.clicked.connect(self.clear_chat_history)
        for button in (self.analyze_test_btn, self.troubleshoot_btn, self.clear_btn):
            shortcuts.addWidget(button)
        shortcuts.addStretch(1)
        layout.addLayout(shortcuts)

        # 思考状态用独立标签表达：不再向聊天记录里追加一条永远删不掉的占位消息。
        self.thinking_label = QLabel("🤔 AI 正在思考...")
        self.thinking_label.setProperty("role", "hint")
        self.thinking_label.hide()
        layout.addWidget(self.thinking_label)

        self.message_input = QTextEdit()
        self.message_input.setMaximumHeight(84)
        self.message_input.setPlaceholderText("输入问题，Ctrl+Enter 发送")
        self.message_input.installEventFilter(self)
        self.send_btn = QPushButton("发送 (Ctrl+Enter)")
        set_button_variant(self.send_btn, "primary")
        self.send_btn.clicked.connect(self.send_message)
        composer = QVBoxLayout()
        composer.setSpacing(8)
        composer.addWidget(self.message_input)
        composer.addWidget(self.send_btn)
        layout.addLayout(composer)

        self.web_search_checkbox.setChecked(bool(self.llm_chat.enable_web_search))

    # ---- 消息展示 ----

    def show_welcome_message(self) -> None:
        """显示一次欢迎语；重复打开面板不再重复堆叠同一段文字。"""
        if self._welcome_shown:
            return
        self._welcome_shown = True
        self.add_assistant_message(_WELCOME_TEMPLATE.format(name=self._model_display_name()))

    def _model_display_name(self) -> str:
        model_name = str(getattr(self.llm_chat, "model_name", "") or "")
        lowered = model_name.lower()
        for marker, display in _MODEL_DISPLAY_NAMES:
            if marker in lowered:
                return display
        return model_name or "AI"

    def add_system_message(self, message: str) -> None:
        self.chat_display.append(f"[{datetime.now():%H:%M:%S}] {message}")

    def add_user_message(self, message: str) -> None:
        self.chat_display.append(f"我: {message}")

    def add_assistant_message(self, message: str) -> None:
        try:
            import markdown
        except ImportError:  # markdown 是可选依赖，退化为纯文本
            logger.info("未安装 markdown，助手回复按纯文本显示")
            self.chat_display.append(f"AI: {message}")
            return
        self.chat_display.append(
            f"AI: {markdown.markdown(message, extensions=['fenced_code', 'tables', 'nl2br'])}"
        )

    def _set_thinking(self, visible: bool) -> None:
        if visible:
            self.thinking_label.show()
            return
        self.thinking_label.hide()

    # ---- 测试上下文 ----

    def get_test_context(self) -> str:
        window = self.main_window
        if window is None:
            return "功放测试系统"
        context = ["功放测试系统环境"]
        config = getattr(window, "config", None)
        if config:
            context.append(f"测试频率: {config.get('test_frequencies', 'N/A')}")
            context.append(
                "驱动模式: "
                f"{'启用' if config.get('driver_mode', {}).get('enabled', False) else '禁用'}"
            )
            context.append(f"衰减器: {config.get('attenuator', {}).get('type', 'N/A')}")
        page_key = self._current_page_key()
        context.append(f"当前页面: {page_key or '未知'}")
        data = None
        current_frequency = None
        if page_key == "visualization":
            data, current_frequency = self._loaded_history_point(window)
        if not data:
            data, current_frequency = self._realtime_point(window)
        if data:
            context.append(f"当前频率: {current_frequency} GHz")
            sweep = data.get("sweep_data", {})
            for key, label in _SWEEP_CONTEXT_FIELDS:
                values = sweep.get(key)
                if not isinstance(values, list):
                    continue
                rounded = [round(value, 2) for value in values]
                context.append(f"{label}数组: {rounded}")
                if rounded and key in _SWEEP_EXTREMA_FIELDS:
                    context.append(f"最大{label}: {max(rounded):.2f}")
        return "\n".join(context)

    def _current_page_key(self) -> str | None:
        """当前可见页面的稳定键；页面标签顺序就是 ``window.pages`` 的注册顺序。"""
        window = self.main_window
        pages = getattr(window, "pages", None)
        tab_widget = getattr(window, "tab_widget", None)
        if not isinstance(pages, Mapping) or tab_widget is None:
            return None
        keys = list(pages)
        index = tab_widget.currentIndex()
        return keys[index] if 0 <= index < len(keys) else None

    def _loaded_history_point(self, window: Any) -> tuple[Any, str | None]:
        loaded = getattr(window, "loaded_data", None)
        if not loaded:
            return None, None
        frequencies = getattr(window, "frequency_list", [])
        index = getattr(window, "current_freq_index", 0)
        if not 0 <= index < len(frequencies):
            return None, None
        frequency = str(frequencies[index])
        return loaded.get(frequency), frequency

    def _realtime_point(self, window: Any) -> tuple[Any, str | None]:
        data = getattr(window, "real_time_data", None)
        if not data:
            return None, None
        frequencies = getattr(window, "rt_frequency_list", [])
        index = getattr(window, "rt_current_freq_index", 0)
        if not 0 <= index < len(frequencies):
            return None, None
        frequency = str(frequencies[index])
        return data.get(frequency), frequency

    # ---- 发送 ----

    def send_message(self) -> None:
        message = self.message_input.toPlainText().strip()
        if not message:
            return
        if self.chat_worker is not None and self.chat_worker.isRunning():
            self.add_system_message("上一条回复还在生成，请稍候")
            return
        if self.unavailable_reason is not None:
            self.add_system_message(f"AI 助手不可用: {self.unavailable_reason}")
            return
        self.add_user_message(message)
        self.message_input.clear()
        self.get_ai_response(message, self.get_test_context())

    def get_ai_response(self, message: str, context: str) -> None:
        worker = ChatWorker(self.llm_chat, message, context)
        self.chat_worker = worker
        worker.response_ready.connect(self.on_ai_response_ready)
        worker.error_occurred.connect(self.on_ai_error)
        # 用身份绑定的收尾回调：迟到的 finished 信号只能释放它自己的 worker，
        # 不能把随后新建的回复线程当成本线程释放掉。
        worker.finished.connect(lambda: self._on_worker_finished(worker))
        self._set_thinking(True)
        worker.start()

    def on_ai_finished(self) -> None:
        """释放当前回复线程（未绑定身份的旧入口，测试与手动收尾使用）。"""
        worker = self.chat_worker
        if worker is not None:
            self._on_worker_finished(worker)

    def _on_worker_finished(self, worker: Any) -> None:
        self._set_thinking(False)
        self._release_worker(worker)

    def on_ai_response_ready(self, response: str) -> None:
        self._set_thinking(False)
        self.add_assistant_message(response)

    def on_ai_error(self, error_message: str) -> None:
        self._set_thinking(False)
        logger.warning("AI 回复失败: %s", error_message)
        self.add_system_message(f"错误: {error_message}")

    # ---- 快捷动作 ----

    def analyze_test_data(self) -> None:
        self.message_input.setPlainText(
            "请帮我分析当前的测试数据，包括增益、效率、压缩点等关键指标，并指出可能存在的问题。"
        )
        self.send_message()

    def troubleshoot_issues(self) -> None:
        self.message_input.setPlainText("我在测试过程中遇到了问题，请帮我进行故障诊断和排查。")
        self.send_message()

    def on_web_search_changed(self, state: int) -> None:
        self.llm_chat.set_web_search(state == Qt.CheckState.Checked.value)

    # ---- 历史 ----

    def load_chat_history(self) -> None:
        for message in getattr(self.llm_chat, "conversation_history", [])[-20:]:
            if not isinstance(message, Mapping):
                continue
            if message.get("role") == "user":
                self.add_user_message(message.get("content", ""))
            elif message.get("role") == "assistant":
                self.add_assistant_message(message.get("content", ""))

    def load_selected_history(self, history_data: list) -> None:
        self.llm_chat.conversation_history = history_data
        self.chat_display.clear()
        self.load_chat_history()
        self.add_system_message("已加载历史对话")

    def clear_chat_history(self) -> None:
        question = QMessageBox.question(
            self, "确认", "确定要清除所有聊天历史吗？", QMessageBox.Yes | QMessageBox.No
        )
        if question != QMessageBox.Yes:
            return
        self.llm_chat.clear_history()
        self.chat_display.clear()
        self.add_system_message("聊天历史已清除")

    def start_new_conversation(self) -> None:
        if self.llm_chat.conversation_history:
            question = QMessageBox.question(
                self, "新对话", "确定要开始新对话吗？当前对话将被保存。",
                QMessageBox.Yes | QMessageBox.No,
            )
            if question != QMessageBox.Yes:
                return
            try:
                self.llm_chat.save_history()
            except (OSError, ValueError) as error:
                # 保存失败必须让用户看到：旧实现用 ``except Exception: pass`` 静默丢弃。
                logger.warning("保存对话历史失败: %s", error)
                QMessageBox.warning(self, "保存失败", f"当前对话保存失败: {error}")
                return
        self.llm_chat.conversation_history = []
        self.chat_display.clear()

    def show_history_dialog(self) -> None:
        dialog = ChatHistoryDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        selected_history = dialog.get_selected_history()
        if selected_history:
            self.load_selected_history(selected_history)

    def show_settings_dialog(self) -> None:
        ChatSettingsDialog(self.llm_chat, self).exec()

    # ---- 生命周期 ----

    def eventFilter(self, obj: Any, event: Any) -> bool:
        if (
            obj == self.message_input
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key_Return
            and event.modifiers() == Qt.ControlModifier
        ):
            self.send_message()
            return True
        return super().eventFilter(obj, event)

    def hide_chat_panel(self) -> None:
        if self.main_window is not None:
            self.main_window.toggle_chat_panel()

    # ---- 线程收尾 ----

    def is_busy(self) -> bool:
        """助手是否仍有回复线程在跑。"""
        return self.chat_worker is not None and self.chat_worker.isRunning()

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """协作关闭助手线程，返回 ``True`` 表示可以安全销毁面板。

        只做“请求中断 + 限时等待”：同步 HTTP 请求无法被可靠中断，因此超时后
        **保持 worker 引用并返回 False**，由窗口决定是否继续等待——不强杀线程
        （``terminate()`` 会让线程带着锁/半写状态退出）。助手是可选能力，超时
        只影响助手面板，不改变测量端口与仪器安全清理的既有顺序。
        """
        worker = self.chat_worker
        if worker is None:
            return True
        worker.requestInterruption()
        if worker.isRunning() and not worker.wait(timeout_ms):
            logger.warning(
                "助手响应线程未在 %dms 内停止，保持引用以便重试关闭", timeout_ms
            )
            return False
        self._release_worker(worker)
        return True

    def _release_worker(self, worker: Any) -> None:
        """释放一个已停止的 worker；只清掉仍指向它的当前引用。"""
        if self.chat_worker is worker:
            self.chat_worker = None
        worker.deleteLater()

    def closeEvent(self, event: Any) -> None:
        """面板自身关闭时也只做协作收尾；失败就保持打开。"""
        if not self.shutdown():
            event.ignore()
            return
        super().closeEvent(event)


__all__ = ["ChatPanel", "create_assistant"]
