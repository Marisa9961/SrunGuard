"""Opt-in release smoke test. Uses a temporary profile; never contacts the network/vault."""

import json
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory

from PySide6.QtCore import QTimer


def run(app, report: Path) -> int:
    from . import __version__
    from .gui import MainWindow
    from .protocol import encode_info
    from .monitor import Update
    from .settings import CredentialStore, load_settings

    result = {"ok": False, "version": __version__}
    try:
        with TemporaryDirectory(prefix="srun-selftest-") as directory:
            window = MainWindow(Path(directory), onboarding=False)
            try:
                assert window.portal.text() == ""
                assert window.interval.value() == 1800
                assert window.retry_interval.value() == 10800
                assert window.max_backoff.value() == 10800
                window._show_setup()
                setup = window.setup_dialog
                assert setup is not None and setup.portal.text() == ""
                setup.reject()
                assert window.worker is None
                window.retry_interval.setValue(3 * 3600)
                window.max_backoff.setValue(72 * 3600)
                window.log_directory.setText(str(Path(directory) / "logs"))
                window._settings().validate(credentials=False)
                assert window.save() is not None
                assert window.retry_interval.value() == 10800
                assert window.max_backoff.value() == 259200
                assert Path(window.log_handler.baseFilename).is_file()
                assert Path(window.log_handler.baseFilename).read_text(encoding="utf-8").isascii()
                assert window.guard_button.text() == "启动守护"
                assert window.save_action in window.more_button.menu().actions()
                window._update(Update("reconnecting", "测试", 0, "network.down",
                                      "Network down, try login once.", True, 30))
                window._update(Update("online", "测试", 0, "network.restored",
                                      "Login success, network restored."))
                window._diagnostic("selftest.detail", "Diagnostic-only test event.")
                assert "[WARNING] Network down" in window.logs.toPlainText()
                assert "Diagnostic-only" not in window.logs.toPlainText()
                assert "Diagnostic-only" in Path(window.detail_handler.baseFilename).read_text(encoding="utf-8")
                legacy = Path(directory) / "legacy.json"
                legacy.write_text('{"interval":30,"retry_interval":30,"max_backoff":300}')
                migrated = load_settings(legacy)
                assert (migrated.interval, migrated.retry_interval, migrated.max_backoff) == (1800, 10800, 10800)
                assert encode_info(b"a", "0123456789abcdef0123456789abcdef") == "{SRBX1}ozirbATTtMD="
                context = ssl.create_default_context()
                result.update(qt_platform=app.platformName(),
                              credential_backend=type(CredentialStore()._backend()).__module__,
                              tls_ca_count=len(context.get_ca_certs()),
                              tabs=[window.tabs.tabText(i) for i in range(window.tabs.count())])
                window.show()
                QTimer.singleShot(400, window.request_exit)
                app.exec()
                result["ok"] = True
            finally:
                window._quit_now()
                window.hide()
    except Exception as exc:
        # No credential data/URLs or local paths included in failure diagnostics.
        result["error_type"] = type(exc).__name__
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
