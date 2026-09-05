"""Verify the portable ZIP without connecting to a game process."""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "NoMansSkyScanner-v1.1.0-NMS170671"


def main():
    archive = ROOT / "dist" / f"{NAME}.zip"
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    expected = archive.with_suffix(".zip.sha256").read_text(encoding="ascii").split()[0]
    assert digest == expected, "Archive checksum mismatch"
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None, "Corrupt ZIP"
        names = set(package.namelist())
        for name in names:
            path = Path(name)
            assert not path.is_absolute() and ".." not in path.parts, name
            assert path.parts[0] == NAME, name
            assert "logs" not in path.parts and "__pycache__" not in path.parts, name
            assert "biosphere" not in name.lower(), name
        for relative in (
            "NoMansSkyScanner.exe", "runtime/python.exe", "runtime/pythonw.exe",
            "runtime/python312.dll", "runtime/Lib/tkinter/__init__.py",
            "runtime/DLLs/_tkinter.pyd", "runtime/tcl/tcl8.6/init.tcl",
            "runtime/tcl/tk8.6/tk.tcl", "LICENSE", "RELEASE.json",
        ):
            assert f"{NAME}/{relative}" in names, relative
        for folder in ("nms_scanner", "native"):
            for source in (ROOT / folder).rglob("*"):
                if not source.is_file() or "__pycache__" in source.parts:
                    continue
                relative = source.relative_to(ROOT).as_posix()
                assert package.read(f"{NAME}/{relative}") == source.read_bytes(), relative
        metadata = json.loads(package.read(f"{NAME}/RELEASE.json"))
        assert metadata["program_version"] == "1.1.0"
        assert metadata["auto_upload_default"] is False
        assert metadata["interface"] == "windows_gui"
    smoke = (
        "import tkinter; import nms_scanner.gui; "
        "from prompt_toolkit.application import create_app_session; "
        "from prompt_toolkit.input import DummyInput; "
        "from prompt_toolkit.output import DummyOutput; "
        "from nms_scanner import __version__; assert __version__ == '1.1.0';\n"
        "with create_app_session(input=DummyInput(), output=DummyOutput()):\n"
        "    import pymhf\n"
        "print('Portable imports OK; no game connection or GUI opened.')\n"
    )
    subprocess.run(
        [str(archive.parent / NAME / "runtime/python.exe"), "-X", "utf8", "-c", smoke],
        cwd=archive.parent / NAME,
        check=True,
        timeout=30,
    )
    print(f"Verified {archive.name}: {digest}")


if __name__ == "__main__":
    main()
