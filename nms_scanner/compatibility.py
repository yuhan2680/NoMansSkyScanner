"""Validate the exact binary and every selected signature before loading a probe."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pefile

from tools.audit_native import locate


def validate(exe: Path, profile: dict) -> dict:
    if exe.name.lower() != "nms.exe":
        raise ValueError("只允许核查 NMS.exe。")
    raw = exe.read_bytes()
    if hashlib.sha256(raw).hexdigest() != profile["exe_sha256"]:
        raise ValueError("游戏版本或文件发生变化，需要重新研究签名；禁止加载探针。")
    pe = pefile.PE(data=raw, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Magic != 0x20B:
            raise ValueError("需要 Windows x64 游戏。")
        sections = [
            (s.VirtualAddress, s.get_data()[: min(s.SizeOfRawData, s.Misc_VirtualSize)])
            for s in pe.sections
            if s.Characteristics & 0x20000000
        ]
        result = {}
        for key, definition in {**profile["hooks"], **profile.get("calls", {})}.items():
            matches = locate(definition["signature"], sections)
            if matches != [int(definition["rva"], 16)]:
                raise ValueError(f"{key} 签名不唯一或位置改变；禁止加载探针。")
            result[key] = matches[0]
        if "single_trial" in profile:
            from nms_scanner.native_single import resolve_application

            resolve_application(
                0,
                pe.OPTIONAL_HEADER.SizeOfImage,
                profile["single_trial"]["application"],
                pe.get_data,
            )
        return result
    finally:
        pe.close()


def validate_exploration(config: dict) -> dict:
    if config.get("schema_version") != 1 or config.get("mode") != "experimental_exploration":
        raise ValueError("不支持的循环配置。")
    limit = config["max_warps"]
    if type(limit) is not int or not 0 <= limit <= 1_000_000:
        raise ValueError("最大跃迁次数必须为 0～1000000 的整数，0 表示不限。")
    for key, low, high in [
        ("max_runtime_seconds", 0, 31_536_000),
        ("cycle_interval_seconds", 1, 60),
    ]:
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} 必须在 {low}～{high} 秒之间。")
    return config


def apply_run_limits(config: dict, limits: dict) -> dict:
    if set(limits) - {"max_warps", "max_runtime_seconds"}:
        raise ValueError("不支持的运行上限选项。")
    return validate_exploration({**config, **limits})


def load_profile(root: Path, *, single: bool = False, exploration: bool = False) -> dict:
    profile = json.loads((root / "native/profile.json").read_text(encoding="utf-8"))
    if profile.get("schema_version") != 1 or profile.get("mode") != "observation_only":
        raise ValueError("不支持的探针配置。")
    keys = profile["hotkeys"]
    if set(keys) != {"primary", "upload", "stop"} or len(set(keys.values())) != 3:
        raise ValueError("需要三个不同的控制热键。")
    if any(value not in {f"F{i}" for i in range(1, 25)} for value in keys.values()):
        raise ValueError("仅支持 F1～F24。")
    for key, low, high in [
        ("worker_interval_seconds", 0.01, 0.1),
        ("summary_interval_seconds", 0.2, 10),
    ]:
        value = profile[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} 必须位于 {low}～{high} 秒。")
    if single or exploration:
        trial = json.loads((root / "native/single_trial.json").read_text(encoding="utf-8"))
        if (
            trial.get("schema_version") != 1
            or trial.get("mode") != "experimental_single_from_open_map"
        ):
            raise ValueError("不支持的单次测试配置。")
        timeout, attempts = trial["phase_timeout_seconds"], trial["max_candidate_attempts"]
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 10 <= timeout <= 300
        ):
            raise ValueError("单阶段超时必须在 10～300 秒之间。")
        if type(attempts) is not int or not 1 <= attempts <= 256:
            raise ValueError("候选尝试上限必须在 1～256 之间。")
        for key in ("map_settle_seconds", "selection_settle_seconds"):
            value = trial[key]
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0.1 <= value <= min(30, timeout)
            ):
                raise ValueError(f"{key} 必须在 0.1～30 秒之间且不超过阶段超时。")
        if set(trial["hooks"]) & set(profile["hooks"]) or set(trial["calls"]) & set(
            profile["hooks"]
        ):
            raise ValueError("单次测试配置不能覆盖原观察入口。")
        profile["hooks"].update(trial["hooks"])
        profile["calls"] = trial["calls"]
        profile["single_trial"] = trial
        from nms_scanner.star_filter import StarFilter, validate_definition

        profile["star_filter"] = validate_definition(
            json.loads((root / "native/star_filter.json").read_text(encoding="utf-8"))
        )
        profile["star_filter_selection"] = StarFilter().as_dict()
        upload = json.loads((root / "native/upload.json").read_text(encoding="utf-8"))
        if (
            upload.get("schema_version") != 1
            or upload.get("mode") != "native_upload_all"
            or upload.get("default_enabled") is not False
        ):
            raise ValueError("自动上传必须默认关闭，只允许本次会话按 F2 开启。")
        delay, limit = upload["settle_seconds"], upload["max_pending_records"]
        if (
            type(delay) not in (int, float)
            or not math.isfinite(delay)
            or not 0.1 <= delay <= 30
            or type(limit) is not int
            or not 1 <= limit <= 1_000_000
        ):
            raise ValueError("上传等待时间或发现数量检查上限无效。")
        if set(upload["calls"]) & (set(profile["hooks"]) | set(profile["calls"])):
            raise ValueError("上传配置不能覆盖其他入口。")
        profile["calls"].update(upload["calls"])
        profile["upload"] = upload
        trial["upload_settle_seconds"] = delay
    if exploration:
        run = validate_exploration(
            json.loads((root / "native/exploration.json").read_text(encoding="utf-8"))
        )
        if set(run["calls"]) & (set(profile["hooks"]) | set(profile["calls"])):
            raise ValueError("循环配置不能覆盖已核查的函数入口。")
        profile["calls"].update(run["calls"])
        profile["exploration"] = run
    return profile
