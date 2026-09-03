"""Explicit integration check against our disposable host, never NMS.exe."""

import argparse
import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pymem
from pyrun_injected.dllinject import StringType, pyRunner

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from nms_scanner.runtime_bootstrap import bootstrap_script  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["shared", "failure", "imports"], required=True)
    args = parser.parse_args()
    host = ROOT / "build/RuntimeTestHost.exe"
    directory = ROOT / "logs/runtime-tests" / (args.mode + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    stop = directory / "stop"
    result = directory / "result.json"
    error = directory / "bootstrap-error.txt"
    process = subprocess.Popen(
        [str(host), str(stop)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        if process.stdout.readline().strip() != "HOST_READY":
            raise RuntimeError("Disposable host did not start")
        # The PID comes exclusively from the child handle, not name lookup.
        pm = pymem.Pymem(process.pid)
        payloads = [("shared_value = 42", False)]
        if args.mode == "failure":
            payloads += [("raise RuntimeError('expected isolated failure')", False)]
        elif args.mode == "imports":
            payloads += [
                (
                    "from prompt_toolkit.application import create_app_session\n"
                    "from prompt_toolkit.input import DummyInput\n"
                    "from prompt_toolkit.output import DummyOutput\n"
                    "with create_app_session(input=DummyInput(), output=DummyOutput()):\n"
                    "    import pymhf\n"
                    "import pefile\n"
                    "from nms_scanner.native_windows import current_exe\n"
                    "assert current_exe().name == 'RuntimeTestHost.exe'\n",
                    False,
                )
            ]
        payloads += [
            (
                f"import json\nwith open({str(result)!r}, 'w') as f:\n"
                "    json.dump({'shared_value': shared_value}, f)\n",
                False,
            )
        ]
        runner = pyRunner(pm)
        script = bootstrap_script(payloads, [str(ROOT), *sys.path], error)
        thread = threading.Thread(
            target=lambda: runner.run_data([StringType(script, False)]), daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 20
        expected = error if args.mode == "failure" else result
        while time.monotonic() < deadline and not expected.exists() and process.poll() is None:
            time.sleep(0.1)
        if not expected.exists():
            detail = error.read_text() if error.exists() else "No bootstrap result"
            raise RuntimeError(detail)
        if args.mode == "failure":
            assert "expected isolated failure" in error.read_text()
        else:
            assert json.loads(result.read_text()) == {"shared_value": 42}
        time.sleep(1)
        if process.poll() is not None:
            raise RuntimeError("Bootstrap exited the disposable host")
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "passed": True,
                    "host_alive_after_bootstrap": True,
                    "report_directory": str(directory),
                },
                ensure_ascii=False,
            )
        )
    finally:
        stop.touch()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()  # Only the host created above, never a game.
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
