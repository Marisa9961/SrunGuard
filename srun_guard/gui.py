"""Qt desktop UI and tray lifecycle. No network I/O runs on the GUI thread."""

from datetime import datetime
import logging
from pathlib import Path
import sys

from PySide6.QtCore import QEvent, QLockFile, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QSystemTrayIcon, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from .durations import short_duration
from .logging_utils import LogLimiter, diagnostic_path, log_path, open_log_handler
from .monitor import Update
from .settings import CredentialStore, Settings, data_directory, load_settings, save_settings
from .worker import NetworkWorker
from .setup_dialog import SetupDialog
from .widgets import DurationEdit


STATES = {
    "idle": ("已暂停", "#64748b"), "checking": ("正在检测", "#2563eb"),
    "online": ("网络在线", "#059669"), "suspect": ("等待确认", "#d97706"),
    "reconnecting": ("正在重连", "#2563eb"), "cooldown": ("等待重试", "#d97706"),
    "error": ("需要处理", "#dc2626"),
}


def make_icon(color: str = "#2563eb") -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 16, 16)
    painter.setPen(QPen(QColor("white"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(19, 36, 28, 45)
    painter.drawLine(28, 45, 46, 22)
    painter.end()
    return QIcon(pixmap)


STYLE = """
QMainWindow { background: #f1f5f9; }
QWidget { color: #0f172a; font-family: 'Microsoft YaHei UI', 'Noto Sans', sans-serif; font-size: 13px; }
QLabel#title { font-size: 26px; font-weight: 700; }
QLabel#muted { color: #64748b; }
QFrame#card { background: white; border: 1px solid #e2e8f0; border-radius: 12px; }
QLabel#state { font-size: 21px; font-weight: 700; }
QTabWidget::pane { background: white; border: 1px solid #e2e8f0; border-radius: 8px; }
QTabBar::tab { padding: 10px 22px; background: #e2e8f0; }
QTabBar::tab:selected { background: white; color: #2563eb; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; border: 1px solid #cbd5e1; border-radius: 6px; padding: 7px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #2563eb; }
QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 7px; padding: 9px 18px; }
QPushButton:hover { background: #eff6ff; border-color: #60a5fa; }
QPushButton#primary { color: white; background: #2563eb; border-color: #2563eb; font-weight: 600; }
QPushButton:disabled { color: #94a3b8; background: #e2e8f0; border-color: #e2e8f0; }
QPlainTextEdit { background: #0f172a; color: #cbd5e1; border: none; border-radius: 8px; padding: 10px; font-family: Consolas, monospace; font-size: 12px; }
QCheckBox { spacing: 7px; padding: 3px 0; }
"""


class MainWindow(QMainWindow):
    def __init__(self, directory: Path, *, onboarding: bool = True):
        super().__init__()
        self.directory = directory
        self.config_path = directory / "settings.json"
        self.vault = CredentialStore()
        self.worker: NetworkWorker | None = None
        self.quitting = False
        self.stopping = False
        self._tray_tip_shown = False
        self.setup_dialog: SetupDialog | None = None
        self.logger = logging.getLogger("srun_guard")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        warning = ""
        try:
            self.saved = load_settings(self.config_path)
        except (OSError, ValueError, TypeError):
            self.saved = Settings()
            warning = "Config load failed. Using defaults."
        try:
            self.log_handler = open_log_handler(log_path(self.saved.log_directory, directory))
        except OSError:
            self.log_handler = open_log_handler(log_path("", directory))
            warning += " Log directory unavailable. Using default directory."
        self.logger.addHandler(self.log_handler)
        self.detail_logger = logging.getLogger("srun_guard.diagnostics")
        self.detail_logger.setLevel(logging.DEBUG)
        self.detail_logger.propagate = False
        try:
            self.detail_handler = open_log_handler(diagnostic_path(directory), detailed=True)
        except OSError:
            self.logger.removeHandler(self.log_handler)
            self.log_handler.close()
            raise
        self.detail_logger.addHandler(self.detail_handler)
        self.log_limiter = LogLimiter(self.saved.log_repeat_interval)
        self.detail_limiter = LogLimiter(self.saved.log_repeat_interval)
        self.setWindowTitle("Srun Guard · 校园网守护")
        self.setWindowIcon(make_icon())
        self.resize(880, 780)
        self.setMinimumSize(680, 680)
        self._build_ui()
        self._build_tray()
        self._fill(self.saved)
        self._set_state("idle", "请填写认证网址、账号和密码，然后启动守护。")
        self._log("Application ready.", important=False)
        if warning:
            self._log(warning, logging.WARNING)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._log("System tray unavailable; window will stay accessible.", logging.WARNING)
        if self.saved.remember and self.saved.portal and self.saved.username:
            try:
                self.password.setText(self.vault.get(self.saved))
            except Exception:
                self._log("Password unavailable. Enter it manually.", logging.WARNING)
        needs_setup = not self.saved.portal or not self.saved.username
        if onboarding and needs_setup:
            QTimer.singleShot(0, self._show_setup)
        elif self.saved.auto_start:
            if not needs_setup and self.password.text():
                QTimer.singleShot(0, self.start_guard)
            else:
                self._log("Auto-start skipped. Connection details missing.", logging.WARNING)

    def _show_setup(self):
        if self.quitting or self.worker or self.setup_dialog is not None:
            return
        dialog = SetupDialog(self._settings(), self)
        self.setup_dialog = dialog
        dialog.submitted.connect(self._complete_setup)
        dialog.finished.connect(self._setup_finished)
        dialog.open()

    def _setup_finished(self, _result):
        if self.setup_dialog:
            self.setup_dialog.password.clear()
        self.setup_dialog = None

    def _complete_setup(self):
        dialog = self.setup_dialog
        if dialog is None:
            return
        self.portal.setText(dialog.portal.text().strip().rstrip("/"))
        self.username.setText(dialog.username.text().strip())
        self.password.setText(dialog.password.text())
        self.remember.setChecked(dialog.remember.isChecked())
        if self.save() is not None:
            dialog.accept()
            self.tabs.setCurrentIndex(0)
            self._set_state("idle", "连接信息已保存。点击“启动守护”开始检测。")

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(15)
        title = QLabel("Srun Guard")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("校园网自动重连  /  后台守护  /  托盘常驻")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        self.state_label = QLabel()
        self.state_label.setObjectName("state")
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setObjectName("muted")
        card_layout.addWidget(self.state_label)
        card_layout.addWidget(self.detail_label)
        layout.addWidget(card)

        self.tabs = QTabWidget()
        basic = self.basic_settings = QWidget()
        form = QFormLayout(basic)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(10)
        self.portal = QLineEdit()
        self.portal.setPlaceholderText("请输入认证网址，如 https://认证服务器")
        self.username = QLineEdit()
        self.username.setPlaceholderText("校园网账号")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("校园网密码")
        password_row = QWidget()
        password_layout = QHBoxLayout(password_row)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.addWidget(self.password)
        reveal = QCheckBox("显示")
        reveal.toggled.connect(lambda checked: self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        password_layout.addWidget(reveal)
        self.remember = QCheckBox("记住密码（仅保存到系统凭据库）")
        self.auto_start = QCheckBox("下次打开程序时自动启动守护")
        self.minimize = QCheckBox("最小化 / 关闭窗口时收起到托盘")
        form.addRow("认证网址", self.portal)
        form.addRow("账号", self.username)
        form.addRow("密码", password_row)
        form.addRow("", self.remember)
        form.addRow("", self.auto_start)
        form.addRow("", self.minimize)
        self.tabs.addTab(basic, "账号与运行")

        advanced = self.network_settings = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(20, 18, 20, 18)
        self.ac_id = QLineEdit()
        self.interval = DurationEdit(5, 86400)
        self.retry_interval = DurationEdit(5, 86400)
        self.timeout = self._spin(2, 30, " 秒")
        self.threshold = self._spin(1, 10, " 次")
        self.max_backoff = DurationEdit(30, 604800)
        self.password_hmac = QCheckBox("HMAC 使用密码（按认证服务器要求选择）")
        for label, field in (("AC ID", self.ac_id),
                             ("检测间隔", self.interval), ("初始重试间隔", self.retry_interval),
                             ("单次网络超时", self.timeout),
                             ("连续失败确认", self.threshold), ("最大重试退避", self.max_backoff)):
            advanced_form.addRow(label, field)
        advanced_form.addRow("协议兼容", self.password_hmac)
        default_hint = QLabel("默认：每 30 分钟检查，认证失败后冷却 3 小时。")
        default_hint.setObjectName("muted")
        default_hint.setWordWrap(True)
        advanced_form.addRow("", default_hint)
        reset_timing = QPushButton("恢复默认时间")
        reset_timing.clicked.connect(self._reset_timing)
        advanced_form.addRow("", reset_timing)
        self.tabs.addTab(advanced, "网络设置")
        self._build_log_tab()
        layout.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        self.guard_button = QPushButton("启动守护")
        self.guard_button.setObjectName("primary")
        self.guard_button.clicked.connect(self.toggle_guard)
        self.check_button = QPushButton("立即检测 / 重连")
        self.check_button.clicked.connect(self.check_now)
        actions.addWidget(self.guard_button)
        actions.addWidget(self.check_button)
        actions.addStretch()
        save_hint = QLabel("启动时自动保存设置")
        save_hint.setObjectName("muted")
        actions.addWidget(save_hint)
        self.more_button = QToolButton()
        self.more_button.setText("更多")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(self.more_button)
        self.save_action = more_menu.addAction("保存设置（不启动守护）")
        self.save_action.setToolTip("只保存配置；启动守护和立即检测也会自动保存，无需提前点击。")
        self.save_action.triggered.connect(self.save)
        self.more_button.setMenu(more_menu)
        actions.addWidget(self.more_button)
        layout.addLayout(actions)
        footer = QLabel("暂停后可修改配置 · 退出请使用托盘菜单 · 日志不记录账号或密码")
        footer.setObjectName("muted")
        layout.addWidget(footer)

    def _build_log_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 18)
        title = QLabel("重要事件：断网、认证结果、冷却及守护状态。详细诊断自动保存在默认目录。")
        title.setWordWrap(True)
        title.setObjectName("muted")
        layout.addWidget(title)
        self.log_options_button = QToolButton()
        self.log_options_button.setText("日志设置与排查…")
        self.log_options_button.setCheckable(True)
        layout.addWidget(self.log_options_button)
        self.log_options = QWidget()
        options_layout = QVBoxLayout(self.log_options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        self.log_options_button.toggled.connect(self.log_options.setVisible)
        self.log_options.hide()
        layout.addWidget(self.log_options)
        self.log_settings = QWidget()
        form = QFormLayout(self.log_settings)
        form.setContentsMargins(0, 0, 0, 0)
        self.log_directory = QLineEdit()
        self.log_directory.setPlaceholderText(f"留空使用默认目录：{self.directory}")
        path_row = QWidget()
        row = QHBoxLayout(path_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.log_directory, 1)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._choose_log_directory)
        row.addWidget(browse)
        reset = QPushButton("默认")
        reset.clicked.connect(self.log_directory.clear)
        row.addWidget(reset)
        self.log_repeat_interval = DurationEdit(30, 86400)
        form.addRow("重要日志目录", path_row)
        form.addRow("同类日志最小间隔", self.log_repeat_interval)
        options_layout.addWidget(self.log_settings)
        hint = QLabel("重要日志 1 MB × 4；详细诊断 2 MB × 4，固定写入默认目录下的 logs 子目录。\n"
                      "两类日志均合并重复事件；不记录账号、密码、认证网址或原始响应。")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        options_layout.addWidget(hint)
        self.active_log_path = QLabel()
        self.active_log_path.setWordWrap(True)
        self.active_log_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._refresh_log_path()
        options_layout.addWidget(self.active_log_path)
        detail_path = QLabel("详细诊断：" + self.detail_handler.baseFilename)
        detail_path.setWordWrap(True)
        detail_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        options_layout.addWidget(detail_path)
        folder_actions = QHBoxLayout()
        open_folder = QPushButton("打开重要日志目录")
        open_folder.clicked.connect(self._open_log_directory)
        folder_actions.addWidget(open_folder)
        open_detail = QPushButton("打开详细诊断目录")
        open_detail.clicked.connect(self._open_diagnostic_directory)
        folder_actions.addWidget(open_detail)
        options_layout.addLayout(folder_actions)
        actions = QHBoxLayout()
        actions.addWidget(QLabel("重要事件"))
        actions.addStretch()
        clear = QPushButton("清空显示")
        actions.addWidget(clear)
        layout.addLayout(actions)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(600)
        self.logs.setMinimumHeight(130)
        clear.clicked.connect(self.logs.clear)
        layout.addWidget(self.logs, 1)
        self.tabs.addTab(page, "日志")

    def _choose_log_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "选择日志存储目录",
                    self.log_directory.text() or str(Path(self.log_handler.baseFilename).parent))
        if folder:
            self.log_directory.setText(folder)

    def _refresh_log_path(self):
        self.active_log_path.setText("重要日志文件：" + self.log_handler.baseFilename)

    def _open_log_directory(self):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.log_handler.baseFilename).parent))):
            self._warn("无法打开日志目录，请复制上方路径手动打开。")

    def _open_diagnostic_directory(self):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.detail_handler.baseFilename).parent))):
            self._warn("无法打开详细诊断目录，请复制路径手动打开。")

    @staticmethod
    def _spin(low: int, high: int, suffix: str) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(low, high)
        widget.setSuffix(suffix)
        return widget

    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu(self)
        show_action = menu.addAction("显示主窗口")
        show_action.triggered.connect(self.show_window)
        menu.addSeparator()
        self.tray_guard = menu.addAction("启动守护")
        self.tray_guard.triggered.connect(self.toggle_guard)
        self.tray_check = menu.addAction("立即检测 / 重连")
        self.tray_check.triggered.connect(self.check_now)
        menu.addSeparator()
        exit_action = QAction("退出程序", self)
        exit_action.triggered.connect(self.request_exit)
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _reset_timing(self):
        defaults = Settings()
        for name in ("interval", "retry_interval", "max_backoff"):
            getattr(self, name).setValue(getattr(defaults, name))

    def _fill(self, settings: Settings):
        for name in ("username", "portal", "ac_id", "log_directory"):
            getattr(self, name).setText(getattr(settings, name))
        for name in ("interval", "retry_interval", "timeout", "threshold", "max_backoff", "log_repeat_interval"):
            getattr(self, name).setValue(getattr(settings, name))
        for name in ("remember", "auto_start", "password_hmac"):
            getattr(self, name).setChecked(getattr(settings, name))
        self.minimize.setChecked(settings.minimize_to_tray)

    def _settings(self) -> Settings:
        return Settings(
            username=self.username.text().strip(), portal=self.portal.text().strip().rstrip("/"),
            ac_id=self.ac_id.text().strip(), interval=self.interval.value(),
            retry_interval=self.retry_interval.value(),
            timeout=self.timeout.value(), threshold=self.threshold.value(),
            max_backoff=self.max_backoff.value(), password_hmac=self.password_hmac.isChecked(),
            remember=self.remember.isChecked(), auto_start=self.auto_start.isChecked(),
            minimize_to_tray=self.minimize.isChecked(),
            log_directory=self.log_directory.text().strip(),
            log_repeat_interval=self.log_repeat_interval.value(),
        )

    def save(self) -> Settings | None:
        try:
            settings = self._settings()
            settings.validate(credentials=settings.remember)
            if settings.remember and not self.password.text():
                raise ValueError("记住密码前，请先输入密码")
        except ValueError as exc:
            self._warn(str(exc))
            return None
        candidate_handler = None
        try:
            destination = log_path(settings.log_directory, self.directory)
            if destination != Path(self.log_handler.baseFilename):
                candidate_handler = open_log_handler(destination)
            if settings.remember:
                self.vault.set(settings, self.password.text())
            old_key = (self.saved.username, self.saved.portal.rstrip("/").lower())
            new_key = (settings.username, settings.portal.rstrip("/").lower())
            if self.saved.remember and (not settings.remember or old_key != new_key):
                self.vault.delete(self.saved)
            save_settings(self.config_path, settings)
        except Exception:
            if candidate_handler:
                candidate_handler.close()
            self._warn("保存失败，请检查日志/配置目录权限和系统凭据库。\n"
                       "原日志位置仍保持不变。没有可用凭据库时，可取消“记住密码”。")
            return None
        self._flush_log_summary()
        if candidate_handler:
            self.logger.removeHandler(self.log_handler)
            self.log_handler.close()
            self.log_handler = candidate_handler
            self.logger.addHandler(self.log_handler)
            self._refresh_log_path()
        self.log_limiter.interval = settings.log_repeat_interval
        self.detail_limiter.interval = settings.log_repeat_interval
        self.saved = settings
        self._log("Settings saved.", important=False)
        self.statusBar().showMessage("设置已保存", 3000)
        return settings

    def _launch(self, once: bool):
        if self.worker or self.quitting:
            return
        if not self.portal.text().strip():
            self._warn("请输入认证网址")
            return
        if not self.username.text().strip():
            self._warn("请输入校园网账号")
            return
        if not self.password.text():
            self._warn("请输入校园网密码")
            return
        settings = self.save()
        if settings is None:
            return
        if settings.portal.startswith("http://"):
            answer = QMessageBox.warning(self, "未加密的认证连接",
                "此认证地址使用 HTTP，认证材料可能被截获。建议使用 HTTPS。\n仍要继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.stopping = False
        self.worker = NetworkWorker(settings, self.password.text(), once, self)
        self.worker.update.connect(self._update)
        self.worker.diagnostic.connect(self._diagnostic)
        self.worker.finished.connect(self._finished)
        self._set_running(True, once)
        self._log("Manual check started." if once else
                  f"Guard started; check every {short_duration(settings.interval)}, "
                  f"retry after {short_duration(settings.retry_interval)}.", important=not once)
        self._diagnostic("guard.settings", f"Effective settings: interval={settings.interval}s, "
            f"retry={settings.retry_interval}s, max_backoff={settings.max_backoff}s, "
            f"timeout={settings.timeout}s, threshold={settings.threshold}, "
            f"password_hmac={settings.password_hmac}.")
        self.worker.start()

    def toggle_guard(self):
        if self.stopping or self.quitting:
            return
        if self.worker:
            self.stop_guard()
        else:
            self.start_guard()

    def start_guard(self):
        self._launch(False)

    def check_now(self):
        if self.worker:
            if not self.stopping:
                self.worker.check_now()
        else:
            self._launch(True)

    def stop_guard(self):
        if self.worker:
            self.stopping = True
            self.worker.stop()
            self.guard_button.setText("正在停止…")
            self.guard_button.setEnabled(False)
            self.check_button.setEnabled(False)
            self.tray_guard.setText("正在停止…")
            self.tray_guard.setEnabled(False)
            self.tray_check.setEnabled(False)
            self.detail_label.setText("正在停止，等待当前网络请求结束…")
            self._log("Stop requested; waiting for the current request to finish.", important=False)

    def _set_running(self, running: bool, once: bool = False):
        self.basic_settings.setEnabled(not running)
        self.network_settings.setEnabled(not running)
        self.log_settings.setEnabled(not running)
        self.save_action.setEnabled(not running)
        text = "取消检测" if running and once else "暂停守护" if running else "启动守护"
        self.guard_button.setText(text)
        self.tray_guard.setText(text)
        self.guard_button.setEnabled(not self.quitting)
        self.tray_guard.setEnabled(not self.quitting)
        self.check_button.setEnabled(not once)
        self.tray_check.setEnabled(not once)

    def _finished(self):
        old_worker = self.worker
        self.worker = None
        if old_worker:
            old_worker.deleteLater()
        self._set_running(False)
        if self.stopping:
            self._set_state("idle", "守护已暂停；暂停不会注销现有校园网连接。")
        was_stopping = self.stopping
        self.stopping = False
        self._flush_log_summary()
        if was_stopping and old_worker and not old_worker.once:
            self._log("Guard paused; existing network session kept.")
        else:
            self._log("Worker stopped.", important=False)
        if self.quitting:
            self._quit_now()

    def _update(self, update: Update):
        if not self.stopping:
            self._set_state(update.state, update.message)
            if update.log_key:
                event_message = update.log_message or {
                    "network.online": "Connectivity check passed.",
                    "network.restored": "Network restored.",
                    "network.down": "Network down, try login once.",
                    "network.suspect": "Connectivity check failed; waiting for confirmation.",
                    "worker.error": "Worker failed; monitoring stopped. Check diagnostic log.",
                }.get(update.log_key, "Login failed; check connection settings.")
                if not update.important:
                    self._diagnostic(update.log_key, event_message)
                    return
                if update.log_key == "network.restored":
                    self._flush_log_summary()
                message = self.log_limiter.accept(update.log_key, event_message,
                                                   force=update.log_key in ("network.restored", "worker.error"))
                if message:
                    self._log(message, update.level)

    def _set_state(self, state: str, message: str):
        label, color = STATES[state]
        self.state_label.setText("●  " + label)
        self.state_label.setStyleSheet(f"color: {color};")
        self.detail_label.setText(message)
        self.tray.setIcon(make_icon(color))
        self.tray.setToolTip("Srun Guard · " + label + "\n" + message)

    def _flush_log_summary(self):
        summary = self.log_limiter.flush()
        if summary:
            self._log(summary)
        detail_summary = self.detail_limiter.flush()
        if detail_summary:
            self.detail_logger.debug(detail_summary)

    def _diagnostic(self, key: str, message: str):
        accepted = self.detail_limiter.accept(key, message)
        if accepted:
            self.detail_logger.debug(accepted)

    def _log(self, message: str, level: int = logging.INFO, *, important: bool = True):
        if important:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logs.appendPlainText(f"{stamp} [{logging.getLevelName(level)}] {message}")
            self.logger.log(level, message)
        self.detail_logger.log(level if important else logging.DEBUG, message)

    def _warn(self, message: str):
        self.show_window()
        QMessageBox.warning(self, "Srun Guard", message)

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _can_hide(self) -> bool:
        return self.minimize.isChecked() and QSystemTrayIcon.isSystemTrayAvailable() and not self.quitting

    def _hide_to_tray(self):
        if not self._can_hide():
            return
        self.tray.show()
        self.hide()
        if not self._tray_tip_shown:
            self.tray.showMessage("Srun Guard", "程序仍在后台运行。点击托盘图标恢复，右键菜单可退出。")
            self._tray_tip_shown = True

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized() and self._can_hide():
            QTimer.singleShot(0, self._hide_to_tray)

    def closeEvent(self, event):
        if self.quitting and self.worker is None:
            event.accept()
            return
        event.ignore()
        if self._can_hide():
            self._hide_to_tray()
        else:
            self.request_exit()

    def request_exit(self):
        if self.quitting:
            return
        self.quitting = True
        if self.worker:
            self.show_window()
            self.stop_guard()
            self.detail_label.setText("正在退出，等待当前网络请求结束…")
            self.setEnabled(False)
        else:
            self._quit_now()

    def _quit_now(self):
        self.quitting = True
        if self.setup_dialog:
            self.setup_dialog.reject()
        self.tray.hide()
        self._flush_log_summary()
        self.logger.removeHandler(self.log_handler)
        self.log_handler.close()
        self.detail_logger.removeHandler(self.detail_handler)
        self.detail_handler.close()
        QApplication.instance().quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SrunGuard")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from .selftest import run
        return run(app, Path(sys.argv[2]))
    try:
        directory = data_directory()
    except OSError:
        QMessageBox.critical(None, "Srun Guard", "无法创建配置目录，请检查权限。")
        return 1
    lock = QLockFile(str(directory / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, "Srun Guard", "程序已在运行，或配置目录无法加锁。请检查系统托盘和目录权限。")
        return 1
    try:
        window = MainWindow(directory)
    except OSError:
        QMessageBox.critical(None, "Srun Guard", "无法读取配置或创建日志，请检查配置目录权限。")
        lock.unlock()
        return 1
    window.show()
    result = app.exec()
    lock.unlock()
    return result
