"""Verify the portable ZIP without connecting to a game process."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nms_scanner import __version__  # noqa: E402
from nms_scanner.compatibility import load_profile  # noqa: E402

PROFILE = json.loads((ROOT / "native/profile.json").read_text(encoding="utf-8"))
NAME = f"NoMansSkyScanner-v{__version__}-NMS{PROFILE['game_internal_version']}"


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
        assert metadata["program_version"] == __version__
        assert metadata["star_filter_default"] is False
        assert metadata["auto_upload_default"] is False
        assert metadata["interface"] == "windows_gui"
        assert metadata["release_channel"] == "prerelease"
        assert metadata["runtime_verified"] is False
        assert metadata["star_filter_runtime_verified"] is False
        assert metadata["background_runtime_verified"] is False
        assert metadata["compatible_game_internal_version"] == PROFILE["game_internal_version"]
        assert metadata["compatible_exe_sha256"] == PROFILE["exe_sha256"]
        report = json.loads(package.read(f"{NAME}/{metadata['compatibility_audit']}"))
        assert report["program_version"] == __version__
        assert report["exe_sha256"] == PROFILE["exe_sha256"]
        assert report["runtime_verified"] is False
        selected = load_profile(ROOT, exploration=True)
        assert report["matched_entry_count"] == len(selected["hooks"] | selected["calls"])
        for relative, profile_digest in report["profile_sha256"].items():
            assert hashlib.sha256(package.read(f"{NAME}/{relative}")).hexdigest() == profile_digest
    smoke = (
        "import tkinter; import nms_scanner.gui; "
        "from prompt_toolkit.application import create_app_session; "
        "from prompt_toolkit.input import DummyInput; "
        "from prompt_toolkit.output import DummyOutput; "
        f"from nms_scanner import __version__; assert __version__ == {__version__!r};\n"
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
