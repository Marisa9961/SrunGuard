from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from srun_guard.settings import CredentialStore, Settings, load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_roundtrip_without_secrets(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertEqual(load_settings(path), Settings())
            settings = Settings(username="test", remember=True)
            save_settings(path, settings)
            self.assertEqual(load_settings(path), settings)
            self.assertNotIn('"password"', path.read_text())
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_new_defaults_and_unconfigured_profile(self):
        settings = Settings()
        self.assertEqual((settings.portal, settings.username), ("", ""))
        self.assertEqual(settings.interval, 1800)
        self.assertEqual(settings.retry_interval, 10800)
        self.assertEqual(settings.max_backoff, 10800)
        settings.validate(credentials=False)
        with self.assertRaises(ValueError):
            settings.validate()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{}")
            self.assertEqual(load_settings(path), settings)

    def test_existing_custom_settings_are_not_overwritten(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = Settings(portal="https://portal.example.org", username="test",
                                interval=60, retry_interval=120, max_backoff=3600)
            save_settings(path, settings)
            self.assertEqual(load_settings(path), settings)

    def test_bad_config(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for content in ("{", "[]", '{"interval":true}', '{"remember":"yes"}',
                            '{"interval":1}', '{"portal":"file:///tmp"}'):
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(ValueError):
                    load_settings(path)

    def test_validation(self):
        for update in ({"portal": "https://a/b"}, {"portal": "ftp://a"},
                       {"portal": "https://user:password@a"}, {"portal": "https://a:bad"},
                       {"ac_id": "-1"}, {"ac_id": "０"}, {"interval": 2},
                       {"timeout": 40}, {"threshold": 0},
                       {"retry_interval": 100, "max_backoff": 30},
                       {"retry_interval": 86401}, {"max_backoff": 604801},
                       {"log_directory": "relative/path"}, {"log_repeat_interval": 0}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                replace(Settings(username="test", portal="https://portal.example.org"), **update).validate()
        Settings(username="test", portal="http://127.0.0.1:8080").validate()

    def test_known_legacy_defaults_are_migrated_once(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"interval":30,"retry_interval":30,"max_backoff":300}')
            migrated = load_settings(path)
            self.assertEqual((migrated.interval, migrated.retry_interval, migrated.max_backoff),
                             (1800, 10800, 10800))
            self.assertEqual(migrated.schema_version, 2)
            save_settings(path, replace(migrated, interval=30, retry_interval=30, max_backoff=300))
            # A current-version deliberate 30s choice is not mistaken for an old default.
            current = load_settings(path)
            self.assertEqual((current.interval, current.retry_interval, current.max_backoff), (30, 30, 300))

    def test_legacy_retry_interval_is_preserved(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"interval":120,"max_backoff":3600}')
            settings = load_settings(path)
            self.assertEqual(settings.retry_interval, 120)
            self.assertEqual(settings.log_directory, "")
            self.assertEqual(settings.log_repeat_interval, 300)

    def test_hour_intervals_and_week_backoff_roundtrip(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = Settings(interval=3600, retry_interval=10800, max_backoff=604800,
                                log_directory=str(Path(directory).resolve()))
            save_settings(path, settings)
            self.assertEqual(load_settings(path), settings)
        # Detection interval is now independent of retry/backoff durations.
        Settings(interval=86400, retry_interval=30, max_backoff=300).validate(credentials=False)

    def test_unknown_fields_ignored(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"future": 123}))
            self.assertEqual(load_settings(path), Settings())

    def test_vault_separates_portal_accounts(self):
        vault = CredentialStore()
        backend = Mock()
        with patch.object(vault, "_backend", return_value=backend):
            settings = Settings(username="test", portal="https://portal.example.org")
            vault.set(settings, "secret")
            backend.set_password.assert_called_once_with(
                "SrunGuard:https://portal.example.org", "test", "secret")
            vault.delete(settings)
            backend.delete_password.assert_called_once()

    def test_insecure_vault_backend_refused(self):
        # No actual system credential is created by this test suite.
        with patch("srun_guard.settings.sys.platform", "linux"), \
                patch("keyring.get_keyring", return_value=Mock()):
            with self.assertRaises(RuntimeError):
                CredentialStore().get(Settings(username="test"))


if __name__ == "__main__":
    unittest.main()
