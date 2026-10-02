"""GitHub release helpers: tag validation, offline binary check and ZIP packaging."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

from build import ROOT, normalize_version


def prepare(tag: str) -> dict[str, str]:
    version = normalize_version(tag)
    values = {"version": version, "prerelease": str("-" in version).lower()}
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    return values


def smoke(tag: str, root: Path = ROOT) -> dict:
    version = normalize_version(tag)
    executable = root / "dist" / "SrunGuard.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    report = root / "build" / "release-selftest.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    subprocess.run([str(executable.resolve()), "--self-test", str(report.resolve())],
                   cwd=root, check=True, timeout=180)
    result = json.loads(report.read_text(encoding="utf-8"))
    if result.get("ok") is not True or result.get("version") != version:
        raise ValueError("Binary self-test failed or embedded version does not match the tag")
    return result


def package(tag: str, root: Path = ROOT) -> Path:
    version = normalize_version(tag)
    dist = root / "dist"
    # Whitelist release contents. Never archive the whole build/config directory.
    files = [dist / "SrunGuard.exe", dist / "SrunGuard.exe.sha256",
             root / "README.md", root / "LICENSE"]
    for path in files:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing or empty release file: {path.name}")
    checksum_fields = files[1].read_text(encoding="ascii").split()
    if len(checksum_fields) != 2 or checksum_fields[1] != "SrunGuard.exe":
        raise ValueError("Invalid EXE checksum file")
    if hashlib.sha256(files[0].read_bytes()).hexdigest() != checksum_fields[0]:
        raise ValueError("EXE checksum mismatch")
    archive = dist / f"SrunGuard-v{version}-windows-x64.zip"
    temporary = archive.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as output:
            for path in files:
                output.write(path, f"SrunGuard/{path.name}")
            output.writestr("SrunGuard/VERSION", version + "\n")
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(
        f"{checksum}  {archive.name}\n", encoding="ascii")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "smoke", "package"))
    parser.add_argument("tag", help="Version tag, e.g. v1.3.1 or v1.3.1-rc.1")
    args = parser.parse_args()
    try:
        result = {"prepare": prepare, "smoke": smoke, "package": package}[args.action](args.tag)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Release {args.action} failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=True) if isinstance(result, dict) else str(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
