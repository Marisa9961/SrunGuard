"""Compile to native code + a self-contained distribution using Nuitka (not PyInstaller)."""

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def command(mode: str, compiler: str, jobs: int) -> list[str]:
    output = ROOT / "build" / "nuitka"
    args = [sys.executable, "-m", "nuitka", f"--mode={mode}",
            "--enable-plugin=pyside6", "--include-qt-plugins=platforms,styles",
            "--include-distribution-metadata=keyring", "--assume-yes-for-downloads",
            f"--output-dir={output}", f"--jobs={jobs}",
            f"--report={output / 'compilation-report.xml'}",
            "--product-name=Srun Guard", "--product-version=1.3.0",
            "--file-version=1.3.0", "--file-description=Srun campus network guard"]
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
    options = parser.parse_args()
    if options.jobs < 1:
        parser.error("--jobs must be >= 1")
    if options.compiler == "msvc" and sys.platform != "win32":
        parser.error("MSVC requires Windows")
    (ROOT / "build" / "nuitka").mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(command(options.mode, options.compiler, options.jobs), cwd=ROOT, check=True)
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
