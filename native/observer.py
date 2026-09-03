# /// script
# dependencies = ["pymhf==0.2.4", "pefile==2023.2.7"]
# [tool.pymhf]
# exe = "NMS.exe"
# start_exe = false
# start_paused = false
# interactive_console = false
# [tool.pymhf.logging]
# shown = false
# log_dir = "{CURR_DIR}/../logs"
# [tool.pymhf.gui]
# shown = false
# ///
"""Observation by default; explicit single mode delegates native actions to game callbacks."""

import ctypes as C
import json
import logging
import os
import sys
import threading
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pymhf import FUNCDEF, Mod
from pymhf.core import _internal
from pymhf.core.hooking import manual_hook

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nms_scanner import __version__  # noqa: E402
from nms_scanner.compatibility import apply_run_limits, load_profile, validate  # noqa: E402
from nms_scanner.controls import SessionControls  # noqa: E402
from nms_scanner.native_windows import (  # noqa: E402
    Hotkeys,
    K,
    current_exe,
    foreground_pid,
    game_callstack,
    read_own,
)
from nms_scanner.observation import Observation  # noqa: E402

if not _internal.IS_INJECTED:
    raise RuntimeError("请使用 start_probe.bat；直接导入不会安装游戏钩子。")
if _internal.CONFIG.get("expected_pid") != os.getpid():
    raise RuntimeError("当前进程不是启动器确认的游戏进程，拒绝安装观察钩子。")

RUN_MODE = _internal.CONFIG.get("run_mode", "observe")
if RUN_MODE not in {"observe", "single", "loop"}:
    raise RuntimeError("未知的运行模式，拒绝加载。")
AUTOMATION_MODE = RUN_MODE in {"single", "loop"}
PROFILE = load_profile(ROOT, single=AUTOMATION_MODE, exploration=RUN_MODE == "loop")
if RUN_MODE == "loop":
    PROFILE["exploration"] = apply_run_limits(
        PROFILE["exploration"], _internal.CONFIG.get("run_limits", {})
    )
OFFSETS = validate(current_exe(), PROFILE)
BASE = K.GetModuleHandleW(None)
# Check live bytes as well: another mod may already have patched a candidate.
from tools.audit_native import compile_signature  # noqa: E402

for name, offset in OFFSETS.items():
    signature = {**PROFILE["hooks"], **PROFILE.get("calls", {})}[name]["signature"]
    if not compile_signature(signature).match(read_own(BASE + offset, len(signature.split()))):
        raise RuntimeError(f"{name} 内存签名与磁盘不一致，拒绝安装观察钩子。")


def hook(key, result, arguments, when="before"):
    if key not in PROFILE["hooks"]:
        return lambda function: function  # Additional callbacks are inert in observation mode.
    return manual_hook(
        f"NMSScannerObserve_{key}",
        offset=OFFSETS[key],
        func_def=FUNCDEF(result, arguments),
        detour_time=when,
    )


class NativeObserver(Mod):
    def __init__(self):
        super().__init__()
        self.telemetry = Observation(focus_reader=lambda: foreground_pid() == os.getpid())
        self.single = None
        if AUTOMATION_MODE:
            from nms_scanner.native_single import NativeSingleEngine
            from nms_scanner.single_run import SingleRun

            engine = NativeSingleEngine(
                BASE,
                OFFSETS,
                PROFILE,
                read_own,
                lambda: game_callstack(BASE, PROFILE["image_size"]),
            )
            if RUN_MODE == "loop":
                from nms_scanner.exploration_run import ExplorationRun

                self.single = ExplorationRun(
                    engine, PROFILE["single_trial"], PROFILE["exploration"], self.telemetry.notice
                )
            else:
                self.single = SingleRun(engine, PROFILE["single_trial"], self.telemetry.notice)
        self.controls = SessionControls(self.telemetry, self.single)
        self.worker = threading.Thread(target=self._worker, daemon=True, name="nms-observer")
        self.worker.start()

    def _worker(self):
        keys, handler = None, None
        logger = logging.getLogger("nms-scanner-observer")
        logger.propagate = False
        logger.setLevel(logging.INFO)
        try:
            directory = ROOT / "logs"
            directory.mkdir(exist_ok=True)
            handler = RotatingFileHandler(
                directory / "native-observer.jsonl",
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            keys = Hotkeys(PROFILE["hotkeys"])
            next_summary = time.monotonic()
            while True:
                for action in keys.poll():
                    self.controls.handle(action)
                if self.single:
                    self.single.poll()
                for item in self.telemetry.drain():
                    item["time"] = datetime.now(UTC).isoformat()
                    item["pid"] = os.getpid()
                    item["run_mode"] = RUN_MODE
                    item["program_version"] = __version__
                    logger.info(json.dumps(item, ensure_ascii=False))
                state = self.telemetry.snapshot()
                if state["stopped"]:
                    break
                if time.monotonic() >= next_summary:
                    logger.info(
                        json.dumps(
                            {
                                "time": datetime.now(UTC).isoformat(),
                                "event": "heartbeat",
                                "pid": os.getpid(),
                                "run_mode": RUN_MODE,
                                "program_version": __version__,
                                "automation_stage": self.single.stage if self.single else None,
                                "automated_warps": self.single.warps if self.single else 0,
                                "automated_scans": self.single.scans if self.single else 0,
                                "auto_upload_enabled": self.single.auto_upload.is_set()
                                if self.single else False,
                                "upload_batches": self.single.upload_batches if self.single else 0,
                                "upload_records": self.single.upload_records if self.single else 0,
                                "game_foreground": foreground_pid() == os.getpid(),
                                **state,
                            },
                            ensure_ascii=False,
                        )
                    )
                    next_summary = time.monotonic() + PROFILE["summary_interval_seconds"]
                time.sleep(PROFILE["worker_interval_seconds"])
        except Exception:
            if self.single:
                self.single.command("stop")
            self.telemetry.command("stop")
            logging.exception("观察线程失败；记录已停止。")
            logger.error(json.dumps({"event": "observer_error", "pid": os.getpid()}))
        finally:
            if keys:
                keys.close()
            if handler:
                handler.close()
                logger.removeHandler(handler)

    @hook("application_update", None, [C.c_void_p])
    def application_update(self, this):
        self.telemetry.record("application_update", sampled=True)
        # The pinned EXE never reads incoming RCX here. The engine instead resolves
        # the singleton used by this function's two LEA instructions.
        if self.single and hasattr(self.single, "poll"):
            self.single.poll("application")

    @hook("map_update", None, [C.c_void_p, C.c_float], when="after")
    def map_update(self, this, step):
        if self.single:
            self.single.poll("map", this)

    @hook("world_update", None, [C.c_void_p, C.c_uint32, C.c_float], when="after")
    def world_update(self, this, mode, step):
        if self.single and mode == 0:
            self.single.poll("world", this)

    @hook("state_change", None, [C.c_void_p, C.c_void_p, C.c_void_p, C.c_bool], when="after")
    def state_change_after(self, this, new_state, user_data, force):
        if self.single and this == self.single.engine.application:
            try:
                state = read_own(new_state, 16).split(b"\0", 1)[0].decode("ascii")
                self.single.state_changed(state)
            except (OSError, ValueError, TypeError, UnicodeError):
                self.single.command("stop")

    @hook("simulation_update", None, [C.c_void_p, C.c_float])
    def simulation_update(self, this, step):
        self.telemetry.record("simulation_update", sampled=True)

    @hook("state_change", None, [C.c_void_p, C.c_void_p, C.c_void_p, C.c_bool])
    def state_change(self, this, new_state, user_data, force):
        # Do not read arbitrary objects or persist user_data (which may contain a destination).
        if not self.telemetry.active or self.telemetry.paused or self.telemetry.stopped:
            return
        try:
            raw = read_own(new_state, 16).split(b"\0", 1)[0]
            name = raw.decode("ascii")
            if not name or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in name):
                name = "unknown"
            self.telemetry.record(
                "state_change", state=name, caller_rvas=game_callstack(BASE, PROFILE["image_size"])
            )
        except (OSError, ValueError, UnicodeError, TypeError):
            self.telemetry.record("state_unreadable")

    @hook("interaction", None, [C.c_void_p])
    def interaction(self, this):
        if not self.telemetry.active or self.telemetry.paused or self.telemetry.stopped:
            return
        try:
            data = int.from_bytes(read_own(this + PROFILE["interaction_data_offset"], 8), "little")
            kind = int.from_bytes(read_own(data + PROFILE["interaction_type_offset"], 4), "little")
            self.telemetry.record(
                "scanner_action" if kind == PROFILE["scanner_type"] else "other_interaction"
            )
        except (OSError, ValueError, TypeError):
            self.telemetry.record("interaction_unreadable")

    # The upstream types.py omits the bool return type here; data.json's C++ symbol
    # declares bool. Preserve it in the trampoline even though this is a before hook.
    @hook("discovery", C.c_bool, [C.c_void_p, C.c_void_p, C.c_void_p])
    def discovery(self, this, discovery_data, locally_new):
        if not self.telemetry.active or self.telemetry.paused or self.telemetry.stopped:
            return
        self.telemetry.record(
            "discovery_submit", caller_rvas=game_callstack(BASE, PROFILE["image_size"])
        )

    # The pinned EXE writes a uint32 status at result+8. Only read the game's own
    # output after it returns. Do not allocate this structure or call the function.
    @hook(
        "warp_check",
        C.c_void_p,
        [C.c_void_p, C.c_void_p, C.c_float, C.c_bool, C.c_uint64, C.c_void_p],
        when="after",
    )
    def warp_check(self, this, result, distance, center, target, attributes):
        if not self.telemetry.active or self.telemetry.paused or self.telemetry.stopped:
            return
        self.telemetry.record("warp_capability_check", sampled=True)
        try:
            status = int.from_bytes(
                read_own(result + PROFILE["warp_result_status_offset"], 4), "little"
            )
            # Keep bounded counter keys; don't invent names for unverified status meanings.
            label = str(status) if 0 <= status <= 0x13 else "unknown"
            self.telemetry.record(f"warp_capability_status_{label}", sampled=True)
        except (OSError, ValueError, TypeError):
            self.telemetry.record("warp_result_unreadable", sampled=True)

    @hook("warp_candidate", C.c_bool, [C.c_void_p, C.c_bool])
    def warp_candidate_before(self, this, check_only):
        if not self.telemetry.active or self.telemetry.paused or self.telemetry.stopped:
            return
        if not check_only:
            self.telemetry.record(
                "warp_dispatch_candidate_enter",
                caller_rvas=game_callstack(BASE, PROFILE["image_size"]),
            )

    @hook("warp_candidate", C.c_bool, [C.c_void_p, C.c_bool], when="after")
    def warp_candidate_after(self, this, check_only, _result_):
        # bool true here does not prove that a warp completed. All detours implicitly
        # return None, leaving the original arguments and return value untouched.
        self.telemetry.record(
            "warp_candidate_check" if check_only else "warp_dispatch_candidate_return",
            sampled=check_only,
            returned_true=bool(_result_),
        )
