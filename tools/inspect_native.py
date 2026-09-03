"""Offline instruction inspection. Reads an EXE file; never opens a game process."""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path

import pefile
from iced_x86 import Decoder, Formatter, FormatterSyntax


def number(value):
    return int(value, 0)


def main():
    parser = argparse.ArgumentParser(
        description="只读反汇编文件中的指定 RVA 区间或查找直接调用候选。"
    )
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--rva", type=number, required=True)
    parser.add_argument("--size", type=number, default=0x200)
    parser.add_argument("--xrefs", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.exe.resolve():
        parser.error("输出不能覆盖输入 EXE。")
    if args.rva < 0 or not 1 <= args.size <= 0x10000:
        parser.error("RVA 必须为非负数，读取长度必须在 1 到 65536 字节之间。")
    raw = args.exe.read_bytes()
    pe = pefile.PE(data=raw, fast_load=True)
    try:
        if pe.FILE_HEADER.Machine != 0x8664:
            raise ValueError("Expected x64 PE")
        lines = [f"EXE SHA256: {hashlib.sha256(raw).hexdigest()}", "Mode: offline_file_only"]
        if args.xrefs:
            lines.append("Raw E8 rel32 candidates; verify instruction boundaries before use.")
            for section in pe.sections:
                if not section.Characteristics & 0x20000000:
                    continue
                data = section.get_data()[: section.Misc_VirtualSize]
                index = data.find(b"\xe8")
                while 0 <= index <= len(data) - 5:
                    rva = section.VirtualAddress + index
                    target = rva + 5 + struct.unpack_from("<i", data, index + 1)[0]
                    if target == args.rva:
                        lines.append(f"{rva:08X} -> {target:08X}")
                    index = data.find(b"\xe8", index + 1)
        else:
            section = pe.get_section_by_rva(args.rva)
            if not section or not section.Characteristics & 0x20000000:
                raise ValueError("RVA is outside executable sections")
            if args.rva + args.size > section.VirtualAddress + min(
                section.SizeOfRawData, section.Misc_VirtualSize
            ):
                raise ValueError("Requested range exceeds executable section")
            lines.append("Caller supplies start alignment. Decoded code is not an ABI guarantee.")
            formatter = Formatter(FormatterSyntax.NASM)
            for instruction in Decoder(64, pe.get_data(args.rva, args.size), ip=args.rva):
                lines.append(f"{instruction.ip:08X} {formatter.format(instruction)}")
        report = "\n".join(lines) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding="utf-8")
            print(f"离线分析已保存：{args.output}")
        else:
            print(report, end="")
    finally:
        pe.close()


if __name__ == "__main__":
    main()
