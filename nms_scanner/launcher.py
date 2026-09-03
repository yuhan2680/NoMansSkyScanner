"""Validate offline, or explicitly attach observation, single, or exploration mode."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import UTC, datetime
from importlib.metadata import entry_points, version
from pathlib import Path

from nms_scanner import __version__
from nms_scanner.compatibility import apply_run_limits, load_profile, validate
from nms_scanner.console_status import ConsoleStatus


def report_failure(root, phase, mode, pid, error):
    """Keep failures that occur before pyMHF creates its own log."""
    path = root / "logs/launcher-errors.jsonl"
    event = {
        "time": datetime.now(UTC).isoformat(),
        "event": "launcher_failed",
        "phase": phase,
        "run_mode": mode,
        "pid": pid,
        "exception_type": type(error).__name__,
        "message": str(error),
    }
    print(f"已停止：{error}", file=sys.stderr)
    try:
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        print(f"启动错误已保存：{path}", file=sys.stderr)
    except OSError as log_error:
        print(f"启动错误日志未能写入：{log_error}。请保留此窗口的报错。", file=sys.stderr)


def running_game():
    import psutil

    games = []
    for process in psutil.process_iter(["name", "exe", "create_time"]):
        if (process.info["name"] or "").lower() == "nms.exe":
            games.append(process)
    if len(games) != 1 or not games[0].info["exe"]:
        raise ValueError("请先打开唯一一个 NMS.exe 测试存档，保持游戏未最小化。")
    return games[0]


def executor_port_available():
    """Avoid a half-loaded runtime when pyMHF's fixed local executor is occupied."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", 6770))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(
        description=f"无人深空原生自动探索器 {__version__}"
    )
    parser.add_argument("--exe", type=Path, help="离线核查用的 NMS.exe 路径")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--observe", action="store_true", help="加载实验性观察探针到已运行的游戏")
    mode.add_argument(
        "--single", action="store_true", help="从已打开的货船地图测试一次自动跃迁和扫描"
    )
    mode.add_argument("--loop", action="store_true", help="从货船正常画面自动开图并循环跃迁扫描")
    parser.add_argument("--max-warps", type=int, help="循环模式最大跃迁次数，0 表示不限")
    parser.add_argument(
        "--max-runtime-seconds", type=float, help="循环模式最长时间，含暂停；0 为不限"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    phase, pid = "locate_game", None
    if args.loop:
        run_mode = "loop"
    elif args.single:
        run_mode = "single"
    else:
        run_mode = "observe" if args.observe else "validate"
    run_limits = {
        key: value
        for key, value in (
            ("max_warps", args.max_warps),
            ("max_runtime_seconds", args.max_runtime_seconds),
        )
        if value is not None
    }
    try:
        if args.observe and args.exe:
            raise ValueError("观察模式通过进程身份确定路径，不接受 --exe。")
        if run_limits and not args.loop:
            raise ValueError("运行上限参数只适用于 --loop 模式。")
        automatic = args.single or args.loop
        attach = args.observe or (automatic and not args.exe)
        process = running_game() if attach or not args.exe else None
        pid = process.pid if process else None
        exe = Path(process.info["exe"]) if process else args.exe
        phase = "validate_binary"
        profile = load_profile(root, single=automatic, exploration=args.loop)
        if args.loop:
            profile["exploration"] = apply_run_limits(profile["exploration"], run_limits)
        offsets = validate(exe, profile)
        print(f"无人深空原生自动探索器 {__version__}")
        print(f"{len(offsets)} 个选用函数签名唯一匹配。")
        if automatic:
            print("主游戏对象的两处代码引用已核查。")
        if not attach:
            print("离线检查结束；没有启动、连接或注入游戏。")
            return 0
        phase = "runtime_preflight"
        if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
            raise ValueError("此探针环境要求 Python 3.12 x64。")
        if version("pymhf") != "0.2.4":
            raise ValueError("需要已核查的 pyMHF 0.2.4。")
        if list(entry_points(group="pymhflib")):
            raise ValueError("当前环境存在额外 pyMHF 库，请使用项目独立环境。")
        # Prevent accidental second injection into a session with a Python-based mod.
        if any(
            Path(m.path).name.lower().startswith("python3") and m.path.lower().endswith(".dll")
            for m in process.memory_maps()
        ):
            raise ValueError(
                "本次游戏进程已加载 Python 运行时，已阻止重复加载。"
                "关闭命令行窗口或按 F3 不会卸载它；请完全退出游戏到桌面，"
                "重新进入测试存档后，只运行一次启动器。"
            )
        if not executor_port_available():
            raise ValueError(
                "本机 127.0.0.1:6770 已被其他 pyMHF 会话占用，已在注入前停止。"
                "请完全退出先前加载过探针的游戏或测试程序后重试。"
            )
        if not process.is_running() or process.exe() != str(exe):
            raise ValueError("游戏进程已改变。")
        if args.loop:
            config = profile["exploration"]
            print("自动探索器：先备份，进入存档，站在自己的货船内部，关闭游戏内菜单。")
            print("F1 启动、暂停或继续；F2 开关自动上传（默认关闭）；F3 停止。保持游戏未最小化。")
            print(
                f"最大跃迁次数：{config['max_warps'] or '不限'}；"
                f"最长运行秒数：{config['max_runtime_seconds'] or '不限'}（含暂停）。"
            )
            print("自动开图、跃迁、整系扫描和可选的“上传全部”均已有游戏成功记录。")
        elif args.single:
            print("实验性单次自动测试：请先备份，在测试存档中打开货船银河地图的自由探索。")
            print("F1 启动、暂停或继续；F2 开关自动上传（默认关闭）；F3 阻止后续动作。")
            print("此模式已有一次前台成功记录；已经发起的跃迁不能由 F3 撤销。")
        else:
            print(
                "观察探针将加载到测试存档，请确保已备份。F1 开始、暂停或继续记录，F3 停止记录。"
            )
            print("本探针不自动执行游戏操作。")
        print("退出游戏才能完全卸载钩子和运行时。")
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input import DummyInput
        from prompt_toolkit.output import DummyOutput

        with create_app_session(input=DummyInput(), output=DummyOutput()):
            from pymhf import run_module

        from nms_scanner.attach_framework import attach_entrypoint

        (root / "logs").mkdir(exist_ok=True)
        phase = "attach_framework"
        # pyMHF's injected bootstrap needs the executable name. The adapter pins the
        # external process selection to this PID and prevents launcher-driven termination.
        with ConsoleStatus(root / "logs/native-observer.jsonl", process.pid):
            attach_entrypoint(run_module, process.pid, root / "logs/bootstrap-error.txt")(
                str(root / "native/observer.py"),
                {
                    "pid": process.pid,
                    "exe": str(exe),
                    "expected_pid": process.pid,
                    "run_mode": run_mode,
                    "run_limits": run_limits,
                    "start_exe": False,
                    "start_paused": False,
                    "interactive_console": False,
                    "internal_mod_dir": None,
                    "mod_dir": None,
                    "mod_save_dir": str(root / "logs"),
                    "logging": {"shown": False, "log_dir": str(root / "logs"), "log_level": "info"},
                    "gui": {"shown": False},
                },
            )
        return 0
    except Exception as exc:
        report_failure(root, phase, run_mode, pid, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
