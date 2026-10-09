"""Connection diagram dialog with lazy optional diagram import."""

from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class ConnectionDialog(QDialog):
    def __init__(self, diagram_type: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("连接说明")
        self.setModal(True)
        self.resize(800, 600)
        try:
            from connection_diagrams import ConnectionDiagram
        except Exception as error:
            print(f"连接图功能未启用: {error}")
            ConnectionDiagram = None
        factories = {
            "cable_loss_path1": "create_cable_loss_path1",
            "cable_loss_path2": "create_cable_loss_path2",
            "driver_mapping": "create_driver_mapping",
            "amplifier_test": "create_amplifier_test",
            "amplifier_test_no_driver": "create_amplifier_test_no_driver",
        }
        method = factories.get(diagram_type)
        figure = getattr(ConnectionDiagram, method)() if ConnectionDiagram and method else Figure()
        layout = QVBoxLayout(self)
        layout.addWidget(FigureCanvas(figure))
        buttons = QHBoxLayout()
        self.cancel_btn = QPushButton("取消")
        self.ok_btn = QPushButton("我已正确连接")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)
