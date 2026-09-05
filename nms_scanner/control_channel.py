"""Small authenticated command channel between the desktop UI and injected runtime."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

VALID_ACTIONS = frozenset({"primary", "upload", "stop"})
MAX_READ_BYTES = 64 * 1024
MAX_PENDING_BYTES = 4096


class CommandWriter:
    """Append ordered commands to a per-session file."""

    def __init__(self, path: Path, token: str):
        self.path = Path(path)
        self.token = token
        self.sequence = 0
        self.lock = threading.Lock()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"")

    def send(self, action: str):
        if action not in VALID_ACTIONS:
            raise ValueError(f"未知控制命令：{action}")
        with self.lock:
            self.sequence += 1
            command = {
                "time": datetime.now(UTC).isoformat(),
                "token": self.token,
                "sequence": self.sequence,
                "action": action,
            }
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(command, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
            return self.sequence


class CommandTail:
    """Read only new, ordered commands carrying this session's unguessable token."""

    def __init__(self, path: Path, token: str):
        self.path = Path(path)
        self.token = token
        self.identity = None
        self.position = 0
        self.pending = b""
        self.last_sequence = 0

    def poll(self):
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self.identity is not None and (
                    self.identity != identity or stat.st_size < self.position
                ):
                    self.position, self.pending = 0, b""
                self.identity = identity
                stream.seek(self.position)
                data = stream.read(MAX_READ_BYTES)
                self.position = stream.tell()
        except OSError:
            return []

        *lines, self.pending = (self.pending + data).split(b"\n")
        if len(self.pending) > MAX_PENDING_BYTES:
            self.pending = b""
        actions = []
        for raw in lines:
            if len(raw) > MAX_PENDING_BYTES:
                continue
            try:
                item = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(item, dict) or item.get("token") != self.token:
                continue
            sequence, action = item.get("sequence"), item.get("action")
            if type(sequence) is not int or sequence <= self.last_sequence:
                continue
            if action not in VALID_ACTIONS:
                continue
            self.last_sequence = sequence
            actions.append(action)
        return actions
