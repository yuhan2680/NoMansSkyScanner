"""Build a relocatable, self-contained Windows folder from the pinned environment."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nms_scanner import __version__  # noqa: E402

DEST = ROOT / f"dist/NoMansSkyScanner-v{__version__}-NMS170671"
RUNTIME = DEST / "runtime"


def copytree(source: Path, destination: Path, ignore=None):
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)


def main():
    if DEST.exists():
        raise RuntimeError(f"发行目录已存在，请先人工核对后移走：{DEST}")
    base = Path(sys.base_prefix)
    site = Path(next(p for p in sys.path if p.lower().endswith("site-packages")))
    DEST.mkdir(parents=True)
    RUNTIME.mkdir()
    for name in (
        "python.exe",
        "pythonw.exe",
        "python3.dll",
        "python312.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "LICENSE.txt",
    ):
        shutil.copy2(base / name, RUNTIME / name)
    copytree(base / "DLLs", RUNTIME / "DLLs", shutil.ignore_patterns("*.pyc", "__pycache__"))
    copytree(base / "tcl", RUNTIME / "tcl", shutil.ignore_patterns("*.pyc", "__pycache__"))
    excluded = {
        "site-packages",
        "idlelib",
        "turtledemo",
        "test",
        "tests",
        "ensurepip",
        "venv",
        "__pycache__",
    }
    library = RUNTIME / "Lib"
    library.mkdir(parents=True)
    for item in (base / "Lib").iterdir():
        if item.name in excluded:
            continue
        target = library / item.name
        if item.is_dir():
            copytree(item, target, shutil.ignore_patterns("*.pyc", "__pycache__"))
        elif item.suffix != ".pyc":
            shutil.copy2(item, target)

    destination_site = library / "site-packages"
    destination_site.mkdir()
    requirements = []
    for line in (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            requirements.append(Requirement(line))
    for requirement in requirements:
        distribution = importlib.metadata.distribution(requirement.name)
        if requirement.specifier and distribution.version not in requirement.specifier:
            raise RuntimeError(f"依赖版本不匹配：{requirement.name} {distribution.version}")
        for relative in distribution.files or ():
            source = Path(distribution.locate_file(relative)).resolve()
            try:
                destination_relative = source.relative_to(site.resolve())
            except ValueError:
                continue  # Console entry points outside site-packages are not needed at runtime.
            if source.is_file() and source.suffix != ".pyc" and "__pycache__" not in source.parts:
                target = destination_site / destination_relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    copytree(
        ROOT / "nms_scanner", DEST / "nms_scanner", shutil.ignore_patterns("*.pyc", "__pycache__")
    )
    copytree(ROOT / "native", DEST / "native", shutil.ignore_patterns("*.pyc", "__pycache__"))
    (DEST / "tools").mkdir()
    for name in ("audit_native.py",):
        shutil.copy2(ROOT / "tools" / name, DEST / "tools" / name)
    for name in (
        "README.md",
        "LICENSE",
        "requirements.lock.txt",
        "AGENTS.md",
        "PROJECT_CONTEXT.md",
    ):
        shutil.copy2(ROOT / name, DEST / name)
    (DEST / "research").mkdir()
    for name in (
        "UPLOAD_FINDINGS.md",
        "automatic-trial-20260903T1049.json",
        "two-cycle-trial-20260903T1145.json",
        "two-cycle-controls-trial-20260903T1153.json",
        "upload-trial-20260904T0100.json",
        "acceptance-20260904.json",
    ):
        shutil.copy2(ROOT / "research" / name, DEST / "research" / name)
    (DEST / "RELEASE.json").write_text(
        json.dumps(
            {
                "program_version": __version__,
                "compatible_game_internal_version": "170671",
                "compatible_exe_sha256": (
                    "ea7e5a29bbf931f96ae8dda81553353aab3431dc38e4ed7dfb66bdce100770e3"
                ),
                "hotkeys": {"F1": "start_pause_resume", "F2": "toggle_auto_upload", "F3": "stop"},
                "auto_upload_default": False,
                "interface": "windows_gui",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    launcher_source = ROOT / "build/release_launcher.cs"
    launcher_source.parent.mkdir(parents=True, exist_ok=True)
    launcher_source.write_text(
        "using System; using System.Diagnostics; using System.IO;\n"
        "class Launcher { [STAThread] static int Main(string[] args) {\n"
        " string root=AppDomain.CurrentDomain.BaseDirectory;\n"
        ' string py=Path.Combine(root,"runtime","pythonw.exe");\n'
        " if(!File.Exists(py)){\n"
        "  return 2;}\n"
        ' string q="-X utf8 -m nms_scanner.gui";\n'
        " foreach(string a in args) {\n"
        '  string e=a.Replace("\\\\","\\\\\\\\").Replace("\\"","\\\\\\"");\n'
        '  q+=" \\""+e+"\\"";}\n'
        " var info=new ProcessStartInfo(py,q); info.WorkingDirectory=root;\n"
        " info.UseShellExecute=false; var p=Process.Start(info);\n"
        " p.WaitForExit(); return p.ExitCode; }}\n",
        encoding="utf-8",
    )
    compiler = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/optimize+",
            "/target:winexe",
            "/out:" + str(DEST / "NoMansSkyScanner.exe"),
            str(launcher_source),
        ],
        check=True,
    )
    (DEST / "启动自动探索.bat").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\nstart "" NoMansSkyScanner.exe\r\n', encoding="utf-8-sig"
    )
    archive = DEST.parent / (DEST.name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in DEST.rglob("*"):
            if path.is_file():
                output.write(path, Path(DEST.name) / path.relative_to(DEST))
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii"
    )
    print(f"发行目录：{DEST}")
    print(f"ZIP：{archive}")


if __name__ == "__main__":
    main()
