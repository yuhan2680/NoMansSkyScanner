"""Full pyMHF attach smoke test; accepts only our disposable RuntimeTestHost.exe."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def client(pid: int, directory: Path):
    import psutil
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    from nms_scanner.attach_framework import attach_entrypoint

    expected = (ROOT / "build/RuntimeTestHost.exe").resolve()
    if Path(psutil.Process(pid).exe()).resolve() != expected:
        raise ValueError("This test refuses to attach to a game or any other executable")
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        from pymhf import run_module
    attach_entrypoint(run_module, pid, directory / "error.txt", expected.name)(
        str(directory / "host_mod.py"),
        {
            "pid": pid,
            "exe": str(expected),
            "start_exe": False,
            "start_paused": False,
            "interactive_console": False,
            "internal_mod_dir": None,
            "mod_dir": None,
            "mod_save_dir": str(directory),
            "logging": {"shown": False, "log_dir": str(directory), "log_level": "info"},
            "gui": {"shown": False},
        },
    )


def parent():
    directory = ROOT / "logs/runtime-tests" / ("framework-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    stop = directory / "stop"
    (directory / "host_mod.py").write_text(
        "from pathlib import Path\nimport ctypes as C\nfrom pymhf import Mod, FUNCDEF\n"
        "from pymhf.core import _internal\nfrom pymhf.core.hooking import manual_hook\n"
        "tick_address = C.cast(C.WinDLL('kernel32').GetTickCount64, C.c_void_p).value\n"
        "class HostObserver(Mod):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n"
        "        self.seen = False\n"
        "        Path(__file__).with_suffix('.ready').write_text('ready')\n"
        "    @manual_hook('DisposableTicks', offset=tick_address - _internal.BASE_ADDRESS, "
        "func_def=FUNCDEF(C.c_uint64, []), detour_time='after')\n"
        "    def tick(self, _result_):\n"
        "        if not self.seen:\n"
        "            self.seen = True\n"
        "            Path(__file__).with_suffix('.tick').write_text(str(_result_))\n",
        encoding="utf-8",
    )
    host = subprocess.Popen(
        [str(ROOT / "build/RuntimeTestHost.exe"), str(stop)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    worker = None
    with (directory / "client.log").open("w", encoding="utf-8") as output:
        try:
            if host.stdout.readline().strip() != "HOST_READY":
                raise RuntimeError("Disposable host did not start")
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-X",
                    "utf8",
                    str(Path(__file__)),
                    "--pid",
                    str(host.pid),
                    "--directory",
                    str(directory),
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and host.poll() is None:
                logs = list(directory.glob("pymhf-*.log"))
                if logs and "Serving on executor" in logs[0].read_text(encoding="utf-8"):
                    break
                if (directory / "error.txt").exists():
                    raise RuntimeError((directory / "error.txt").read_text())
                time.sleep(0.1)
            else:
                raise RuntimeError("Full framework did not become ready; inspect " + str(directory))
            if not (directory / "host_mod.ready").exists():
                raise RuntimeError("Host mod was not loaded")
            time.sleep(1)
            if host.poll() is not None:
                raise RuntimeError("Framework exited the host")
            tick_file = directory / "host_mod.tick"
            if not tick_file.exists() or int(tick_file.read_text()) <= 0:
                raise RuntimeError("Native observation hook did not run")
            if stop.with_suffix(".bad").exists():
                raise RuntimeError("Native return value was not preserved")
            report = {
                "passed": True,
                "host_alive": True,
                "mod_loaded": True,
                "framework_ready": True,
                "native_hook_called": True,
                "native_result_preserved": True,
                "report_directory": str(directory),
            }
            (directory / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
        finally:
            stop.touch()
            for process in [host, worker]:
                if process is None:
                    continue
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()  # Only the disposable child handles created above.
                    process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.pid:
        client(args.pid, args.directory)
    else:
        parent()
