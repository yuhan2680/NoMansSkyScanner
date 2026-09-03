"""Narrow pyMHF 0.2.4 attach adapter; does not alter the installed library globals."""

from __future__ import annotations

import ast
import sys
import threading
from pathlib import Path
from types import FunctionType

from nms_scanner.runtime_bootstrap import bootstrap_script

HEADLESS_IMPORT = """\
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput
with create_app_session(input=DummyInput(), output=DummyOutput()):
    import pymhf
"""


class _NoProcessTermination:
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def kill(self, pid, signal):
        # Upstream's completion callback kills the target even when start_exe is false.
        # Observation does not grant the launcher permission to terminate any process.
        raise RuntimeError("观察启动器不能结束游戏或其他进程。")


class _PinnedProcess:
    def __init__(self, delegate, pid, executable_name):
        self.delegate, self.pid, self.executable_name = delegate, pid, executable_name

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def Pymem(self, requested, **kwargs):  # noqa: N802 - third-party API spelling
        if requested not in (self.pid, self.executable_name):
            raise ValueError("拒绝连接非预期进程。")
        return self.delegate.Pymem(self.pid)


class _PersistentRunner:
    def __init__(self, delegate, library, error_log):
        self.delegate, self.library, self.error_log = delegate, library, error_log

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def run_data(self, strings):
        # pyMHF 0.2.4 starts with an escaped sys.path assignment. Replace that
        # setup with canonical paths, and execute all subsequent payloads together.
        first = ast.parse(strings[0].value)
        if (
            strings[0].is_file
            or len(first.body) != 2
            or not isinstance(first.body[0], ast.Import)
            or not isinstance(first.body[1], ast.Assign)
            or ast.unparse(first.body[1].targets[0]) != "sys.path"
        ):
            raise RuntimeError("不支持的 pyMHF 引导结构；未开始初始化。")
        payloads = [(HEADLESS_IMPORT, False), *[(item.value, item.is_file) for item in strings[1:]]]
        code = bootstrap_script(payloads, list(sys.path), self.error_log)
        return self.delegate.run_data([self.library.StringType(code, False)])


class _RuntimeLibrary:
    def __init__(self, delegate, error_log):
        self.delegate, self.error_log = delegate, error_log

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def pyRunner(self, process):  # noqa: N802 - third-party API spelling
        return _PersistentRunner(self.delegate.pyRunner(process), self.delegate, self.error_log)


def attach_entrypoint(
    original, pid: int, error_log: Path | None = None, executable_name: str = "NMS.exe"
):
    """Isolate two unsafe launcher defaults, leaving hook and injection code unchanged."""
    namespace = dict(original.__globals__)
    namespace.update(
        os=_NoProcessTermination(namespace["os"]),
        pymem=_PinnedProcess(namespace["pymem"], pid, executable_name),
        REMOVE_SELF=False,
        END_EVENT=threading.Event(),
    )
    if error_log is not None:
        namespace["dllinject"] = _RuntimeLibrary(namespace["dllinject"], error_log)
    return FunctionType(
        original.__code__, namespace, original.__name__, original.__defaults__, original.__closure__
    )
