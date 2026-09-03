"""Read-only NMS executable/signature audit. Never imports or runs upstream code."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pefile

RELEVANT_FUNCTIONS = (
    "cGcApplication::Update",
    "cGcApplication::Construct",
    "cTkFSM::StateChange",
    "cTkFSMState::StateChange",
    "cGcGameState::Update",
    "cGcGameState::ComputeWarpCapability",
    "cGcGalaxyMap::Data::Update",
    "cGcGalaxyMap::Data::DoSolarPopup",
    "cGcGalaxyVoxelGenerator::Populate",
    "cGcGalaxyAttributeGenerator::ClassifyStarKeyAttributes",
    "cGcSolarSystemQuery::Run",
    "cGcSolarSystem::Generate",
    "cGcSolarSystem::Update",
    "cGcPlayerEnvironment::IsOnboardOwnFreighter",
    "cGcSimpleInteractionComponent::DoAction",
    "cGcDiscoveryManager::SubmitDiscoveryData",
    "cGcRewardManager::GiveGenericReward",
)


def compile_signature(signature: str) -> re.Pattern[bytes]:
    tokens = signature.split()
    if not tokens or not any(re.fullmatch(r"[0-9a-fA-F]{2}", token) for token in tokens):
        raise ValueError("Signature must contain at least one concrete byte")
    pattern = bytearray()
    for token in tokens:
        if token in {"?", "??"}:
            pattern.extend(b".")
        elif re.fullmatch(r"[0-9a-fA-F]{2}", token):
            pattern.extend(re.escape(bytes.fromhex(token)))
        else:
            raise ValueError(f"Invalid signature token: {token}")
    # Lookahead preserves overlapping matches; wildcard includes 0A (machine code is not text).
    return re.compile(b"(?=" + bytes(pattern) + b")", re.DOTALL)


def locate(signature: str, sections: list[tuple[int, bytes]]) -> list[int]:
    pattern = compile_signature(signature)
    return [rva + match.start() for rva, data in sections for match in pattern.finditer(data)]


def audit(exe: Path, definitions: Path) -> dict:
    raw = exe.read_bytes()
    pe = pefile.PE(data=raw, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Magic != 0x20B:
            raise ValueError("Expected a 64-bit Windows PE executable")
        sections = []
        for section in pe.sections:
            if section.Characteristics & 0x20000000:  # IMAGE_SCN_MEM_EXECUTE
                # Only mapped bytes; ignore disk alignment padding beyond the virtual section.
                length = min(section.SizeOfRawData, section.Misc_VirtualSize)
                start = section.PointerToRawData
                if start + length > len(raw):
                    raise ValueError("Executable section exceeds file length")
                sections.append((section.VirtualAddress, raw[start : start + length]))
        if not sections:
            raise ValueError("No executable sections")
        entries = json.loads(definitions.read_text(encoding="utf-8-sig"))
        if not isinstance(entries, list):
            raise ValueError("Expected a list of signature definitions")
        results = []
        for name in RELEVANT_FUNCTIONS:
            candidates = [entry for entry in entries if entry.get("name") == name]
            if not candidates:
                results.append({"name": name, "status": "not_in_upstream", "rvas": []})
            for entry in candidates:
                signature = entry.get("signature")
                matches = locate(signature, sections) if signature else []
                status = (
                    "unique" if len(matches) == 1 else "missing" if not matches else "ambiguous"
                )
                results.append(
                    {
                        "name": name,
                        "status": status,
                        "mangled_name": entry.get("mangled_name"),
                        "rvas": [hex(value) for value in matches],
                        "runtime_verified": False,
                    }
                )
        return {
            "generated_utc": datetime.now(UTC).isoformat(),
            "mode": "read_only_file_audit",
            "executable_name": exe.name,
            "exe_sha256": hashlib.sha256(raw).hexdigest(),
            "exe_size": len(raw),
            "pe_timestamp": pe.FILE_HEADER.TimeDateStamp,
            "signature_file_sha256": hashlib.sha256(definitions.read_bytes()).hexdigest(),
            "functions": results,
            "summary": {
                name: sum(r["status"] == name for r in results)
                for name in ("unique", "missing", "ambiguous", "not_in_upstream")
            },
            "ready_for_native_automation": False,
            "unresolved": [
                "ordinary_freighter_warp_dispatch_and_abi",
                "warp_capability_result_layout",
                "scanner_lifetime_or_independent_scan_entry",
                "warp_and_scan_completion_observation",
                "background_game_update_and_input_isolation",
            ],
            "source_abi_warnings": [
                "SubmitDiscoveryData: types.py omits the bool return declared by the C++ symbol.",
                "ComputeWarpCapability: WarpCapabilityResult layout is undefined upstream.",
            ],
            "note": "Unique matches do not prove safe calls or correct structure layouts.",
        }
    finally:
        pe.close()


def main():
    parser = argparse.ArgumentParser(
        description="只读核查 NMS.exe 中的公开函数签名，不启动或注入游戏。"
    )
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--definitions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {args.exe.resolve(), args.definitions.resolve()}:
        parser.error("报告路径不能覆盖输入文件。")
    report = audit(args.exe, args.definitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print("静态检查完成；未启动游戏、未注入、未发送输入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
