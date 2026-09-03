"""Windows observation primitives. Deliberately contains no input emission API."""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from pathlib import Path

K = C.WinDLL("kernel32", use_last_error=True)
U = C.WinDLL("user32", use_last_error=True)
K.GetModuleHandleW.argtypes, K.GetModuleHandleW.restype = [W.LPCWSTR], W.HMODULE
K.GetModuleFileNameW.argtypes = [W.HMODULE, W.LPWSTR, W.DWORD]
K.GetModuleFileNameW.restype = W.DWORD
K.GetCurrentProcess.argtypes, K.GetCurrentProcess.restype = [], W.HANDLE
K.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
K.ReadProcessMemory.restype = W.BOOL
K.RtlCaptureStackBackTrace.argtypes = [W.DWORD, W.DWORD, C.POINTER(C.c_void_p), C.POINTER(W.DWORD)]
K.RtlCaptureStackBackTrace.restype = W.WORD
U.RegisterHotKey.argtypes, U.RegisterHotKey.restype = [W.HWND, C.c_int, W.UINT, W.UINT], W.BOOL
U.UnregisterHotKey.argtypes, U.UnregisterHotKey.restype = [W.HWND, C.c_int], W.BOOL
U.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
U.PeekMessageW.restype = W.BOOL
U.GetForegroundWindow.argtypes, U.GetForegroundWindow.restype = [], W.HWND
U.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
U.GetWindowThreadProcessId.restype = W.DWORD


def current_exe() -> Path:
    buffer = C.create_unicode_buffer(32768)
    count = K.GetModuleFileNameW(None, buffer, len(buffer))
    if not count or count >= len(buffer):
        raise OSError("无法读取当前进程路径。")
    return Path(buffer.value)


def read_own(address: int, size: int) -> bytes:
    if not isinstance(address, int) or address < 0x10000 or not 1 <= size <= 4096:
        raise ValueError("无效的观察地址或读取长度。")
    buffer, actual = C.create_string_buffer(size), C.c_size_t()
    if not K.ReadProcessMemory(K.GetCurrentProcess(), address, buffer, size, C.byref(actual)):
        raise OSError("观察字段不可读取。")
    if actual.value != size:
        raise OSError("观察字段读取不完整。")
    return buffer.raw


def foreground_pid() -> int:
    pid = W.DWORD()
    U.GetWindowThreadProcessId(U.GetForegroundWindow(), C.byref(pid))
    return pid.value


def game_callstack(base: int, size: int) -> list[str]:
    """Only game-code RVAs, never stack contents, object addresses or destinations."""
    frames = (C.c_void_p * 64)()
    count = K.RtlCaptureStackBackTrace(0, len(frames), frames, None)
    return [
        hex(address - base)
        for address in frames[:count]
        if address and base <= address < base + size
    ]


class Hotkeys:
    """Owned and polled by the same worker thread; no low-level keyboard hook."""

    def __init__(self, config: dict):
        self.names = list(config)
        self.registered = []
        try:
            for index, name in enumerate(self.names, 1):
                if not U.RegisterHotKey(None, index, 0x4000, 111 + int(config[name][1:])):
                    raise OSError(f"无法注册 {config[name]}，探针保持停用。")
                self.registered.append(index)
        except Exception:
            self.close()
            raise

    def poll(self):
        message = W.MSG()
        while U.PeekMessageW(C.byref(message), None, 0x312, 0x312, 1):
            if 1 <= message.wParam <= len(self.names):
                yield self.names[message.wParam - 1]

    def close(self):
        for index in self.registered:
            U.UnregisterHotKey(None, index)
        self.registered.clear()
