"""First-run connection setup; no preconfigured server and no network operations."""

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                               QLabel, QLineEdit, QVBoxLayout)

from .settings import Settings


class SetupDialog(QDialog):
    submitted = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("首次使用 · 连接设置")
        self.setMinimumWidth(480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        hint = QLabel("请输入你的认证网址、账号和密码。\n"
                      "程序不预设任何学校服务器；保存后点击“启动守护”才会联网。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.portal = QLineEdit(settings.portal)
        self.portal.setPlaceholderText("https://认证服务器（不含路径或参数）")
        self.username = QLineEdit(settings.username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox("记住密码（仅保存到系统凭据库）")
        self.remember.setChecked(settings.remember)
        form.addRow("认证网址", self.portal)
        form.addRow("账号", self.username)
        form.addRow("密码", self.password)
        form.addRow("", self.remember)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setStyleSheet("color: #dc2626;")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存连接设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("稍后设置")
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def submit(self):
        try:
            replace(self.settings, portal=self.portal.text().strip().rstrip("/"),
                    username=self.username.text().strip()).validate()
            if not self.password.text():
                raise ValueError("请输入密码")
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.error.clear()
        self.submitted.emit()
