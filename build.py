"""Compile to native code + a self-contained distribution using Nuitka (not PyInstaller)."""

import argparse
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys

from srun_guard import __version__


ROOT = Path(__file__).resolve().parent


def normalize_version(value: str) -> str:
    """Accept 1.2.3 / v1.2.3 and optional SemVer prerelease identifiers."""
    match = re.fullmatch(
        r"v?(?P<core>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
        r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?", value,
    )
    if not match:
        raise ValueError("Version must be [v]MAJOR.MINOR.PATCH[-PRERELEASE], e.g. v1.3.1 or v1.3.1-rc.1")
    if any(int(part) > 65535 for part in match["core"].split(".")):
        raise ValueError("Windows version components must be <= 65535")
    prerelease = match["pre"]
    if prerelease and any(part.isdigit() and len(part) > 1 and part.startswith("0")
                          for part in prerelease.split(".")):
        raise ValueError("Numeric prerelease identifiers must not contain leading zeros")
    return match["core"] + ("-" + prerelease if prerelease else "")


@contextmanager
def compiled_version(version: str):
    """Embed the release tag in Python metadata without permanently editing the source."""
    version_file = ROOT / "srun_guard" / "__init__.py"
    original = version_file.read_bytes()
    updated, count = re.subn(rb'^__version__ = "[^"\r\n]+"',
                             f'__version__ = "{normalize_version(version)}"'.encode("ascii"),
                             original, flags=re.M)
    if count != 1:
        raise ValueError("Expected exactly one __version__ assignment")
    try:
        version_file.write_bytes(updated)
        yield
    finally:
        version_file.write_bytes(original)


def command(mode: str, compiler: str, jobs: int, version: str = __version__) -> list[str]:
    output = ROOT / "build" / "nuitka"
    windows_version = normalize_version(version).split("-", 1)[0]
    args = [sys.executable, "-m", "nuitka", f"--mode={mode}",
            "--enable-plugin=pyside6", "--include-qt-plugins=platforms,styles",
            "--include-distribution-metadata=keyring", "--assume-yes-for-downloads",
            f"--output-dir={output}", f"--jobs={jobs}",
            f"--report={output / 'compilation-report.xml'}",
            "--product-name=Srun Guard", f"--product-version={windows_version}",
            f"--file-version={windows_version}", "--file-description=Srun campus network guard"]
    if sys.platform == "win32":
        args += ["--windows-console-mode=disable", "--output-filename=SrunGuard.exe",
                 "--include-module=keyring.backends.Windows", "--include-package=win32ctypes",
                 "--nofollow-import-to=win32ctypes.tests"]
    else:
        args += ["--output-filename=SrunGuard", "--include-package=keyring.backends"]
    if compiler == "zig":
        args.append("--zig")
    elif compiler == "msvc":
        args.append("--msvc=latest")
    args.append(str(ROOT / "main.py"))
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("onefile", "standalone"), default="onefile")
    parser.add_argument("--compiler", choices=("auto", "zig", "msvc"),
                        default="zig" if sys.platform == "win32" else "auto")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--version", type=normalize_version, default=__version__,
                        help="Release version/tag; defaults to srun_guard.__version__")
    options = parser.parse_args()
    if options.jobs < 1:
        parser.error("--jobs must be >= 1")
    if options.compiler == "msvc" and sys.platform != "win32":
        parser.error("MSVC requires Windows")
    (ROOT / "build" / "nuitka").mkdir(parents=True, exist_ok=True)
    try:
        with compiled_version(options.version):
            subprocess.run(command(options.mode, options.compiler, options.jobs, options.version),
                           cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        print("Compilation failed. See compiler output; no new release was copied.", file=sys.stderr)
        return exc.returncode
    release = ROOT / "dist"
    release.mkdir(exist_ok=True)
    filename = "SrunGuard.exe" if sys.platform == "win32" else "SrunGuard"
    if options.mode == "onefile":
        source = ROOT / "build" / "nuitka" / filename
        target = release / filename
        shutil.copy2(source, target)
        checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        target.with_suffix(target.suffix + ".sha256").write_text(
            f"{checksum}  {filename}\n", encoding="ascii")
    else:
        source = ROOT / "build" / "nuitka" / "main.dist"
        target = release / "SrunGuard"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    print(f"Release: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
