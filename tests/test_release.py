import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

import build
from scripts import release


class VersionTests(unittest.TestCase):
    def test_valid_release_tags(self):
        for tag, expected in (("v1.3.1", "1.3.1"), ("1.3.1", "1.3.1"),
                              ("v0.0.0", "0.0.0"), ("v2.0.0-rc.1", "2.0.0-rc.1"),
                              ("1.0.0-beta-test.2", "1.0.0-beta-test.2")):
            with self.subTest(tag=tag):
                self.assertEqual(build.normalize_version(tag), expected)

    def test_invalid_tags_rejected(self):
        for tag in ("main", "v1", "v1.2", "v01.2.3", "1.2.3.4", "v65536.0.0",
                    "1.2.3-rc..1", "1.2.3-01", "1.2.3+build", "v1.2.3\n",
                    "v1.2.3; echo secret", "../../1.2.3", "v1.2.3-$(id)"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                build.normalize_version(tag)

    def test_windows_metadata_uses_numeric_tag_version(self):
        with patch.object(build.sys, "platform", "win32"):
            args = build.command("onefile", "msvc", 4, "v2.4.6-rc.1")
        self.assertIn("--product-version=2.4.6", args)
        self.assertIn("--file-version=2.4.6", args)
        self.assertIn("--msvc=latest", args)
        self.assertIn("--windows-console-mode=disable", args)

    def test_embedded_version_is_restored_even_when_compilation_fails(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "srun_guard" / "__init__.py"
            source.parent.mkdir()
            original = b'"""Metadata."""\r\n__version__ = "1.3.0"\r\n'
            source.write_bytes(original)
            with patch.object(build, "ROOT", root):
                with build.compiled_version("v2.0.0-rc.1"):
                    self.assertIn(b'__version__ = "2.0.0-rc.1"', source.read_bytes())
                self.assertEqual(source.read_bytes(), original)
                with self.assertRaises(RuntimeError):
                    with build.compiled_version("v3.0.0"):
                        raise RuntimeError("compiler failed")
                self.assertEqual(source.read_bytes(), original)

    def test_prepare_outputs_for_github_actions(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                self.assertEqual(release.prepare("v1.4.0-rc.2"),
                                 {"version": "1.4.0-rc.2", "prerelease": "true"})
            self.assertEqual(output.read_text(encoding="utf-8"),
                             "version=1.4.0-rc.2\nprerelease=true\n")
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                self.assertEqual(release.prepare("v1.4.0")["prerelease"], "false")
                previous = output.read_bytes()
                with self.assertRaises(ValueError):
                    release.prepare("v1.4.0\nprerelease=true")
                self.assertEqual(output.read_bytes(), previous)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "dist").mkdir()
        self.exe = self.root / "dist" / "SrunGuard.exe"
        self.exe.write_bytes(b"MZ-fake-binary-for-packaging-test")
        self.checksum = self.exe.with_suffix(".exe.sha256")
        self.checksum.write_text(hashlib.sha256(self.exe.read_bytes()).hexdigest() + "  SrunGuard.exe\n")
        (self.root / "README.md").write_text("# Test README\n")
        (self.root / "LICENSE").write_text("MIT License\n")

    def test_archive_whitelist_and_checksum(self):
        (self.root / "dist" / "settings.json").write_text('{"password":"do-not-publish"}')
        (self.root / "dist" / "guard.log").write_text("do-not-publish")
        archive = release.package("v1.4.0-rc.1", self.root)
        self.assertEqual(archive.name, "SrunGuard-v1.4.0-rc.1-windows-x64.zip")
        with zipfile.ZipFile(archive) as contents:
            self.assertEqual(set(contents.namelist()), {
                "SrunGuard/SrunGuard.exe", "SrunGuard/SrunGuard.exe.sha256",
                "SrunGuard/README.md", "SrunGuard/LICENSE", "SrunGuard/VERSION",
            })
            self.assertEqual(contents.read("SrunGuard/SrunGuard.exe"), self.exe.read_bytes())
            self.assertEqual(contents.read("SrunGuard/VERSION"), b"1.4.0-rc.1\n")
            self.assertIsNone(contents.testzip())
        self.assertEqual(archive.with_suffix(".zip.sha256").read_text(),
                         hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
        self.assertFalse(archive.with_suffix(".zip.tmp").exists())

    def test_bad_exe_checksum_blocks_packaging(self):
        self.exe.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            release.package("v1.4.0", self.root)
        self.assertEqual(list((self.root / "dist").glob("*.zip")), [])

    def test_empty_or_malformed_manifest_blocks_packaging(self):
        for content in ("", " \n", "abc", "abc  wrong.exe"):
            self.checksum.write_text(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                release.package("v1.4.0", self.root)

    def test_license_required(self):
        (self.root / "LICENSE").unlink()
        with self.assertRaisesRegex(ValueError, "LICENSE"):
            release.package("v1.4.0", self.root)

    def test_smoke_checks_version_and_success(self):
        report = self.root / "build" / "release-selftest.json"
        for content, passes in (({"ok": True, "version": "1.4.0"}, True),
                                ({"ok": True, "version": "1.3.0"}, False),
                                ({"ok": False, "version": "1.4.0"}, False)):
            with self.subTest(content=content):
                def run(*args, **kwargs):
                    report.write_text(json.dumps(content), encoding="utf-8")
                with patch.object(release.subprocess, "run", side_effect=run) as process:
                    if passes:
                        self.assertEqual(release.smoke("v1.4.0", self.root), content)
                    else:
                        with self.assertRaises(ValueError):
                            release.smoke("v1.4.0", self.root)
                    self.assertTrue(process.call_args.kwargs["check"])
                    self.assertEqual(process.call_args.kwargs["timeout"], 180)

    def test_stale_smoke_report_cannot_pass(self):
        report = self.root / "build" / "release-selftest.json"
        report.parent.mkdir()
        report.write_text('{"ok":true,"version":"1.4.0"}')
        with patch.object(release.subprocess, "run"):
            with self.assertRaises(FileNotFoundError):
                release.smoke("v1.4.0", self.root)
        self.assertFalse(report.exists())

    def test_failed_executable_blocks_release(self):
        with patch.object(release.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "exe")):
            with self.assertRaises(subprocess.CalledProcessError):
                release.smoke("v1.4.0", self.root)


if __name__ == "__main__":
    unittest.main()
