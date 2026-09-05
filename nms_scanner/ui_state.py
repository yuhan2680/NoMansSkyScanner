"""UI state projection kept separate from Tk so it can be tested without a display."""

from __future__ import annotations

from dataclasses import dataclass

STAGE_LABELS = {
    "idle": "等待启动",
    "between_cycles": "等待下一轮",
    "wait_ready": "核对货船状态",
    "wait_map": "等待银河地图",
    "searching": "查找可达恒星",
    "dispatch": "准备跃迁",
    "wait_load": "等待开始加载",
    "loading": "跃迁加载中",
    "wait_scan": "等待扫描",
    "scanning": "扫描恒星系",
    "wait_upload": "等待上传",
    "complete": "运行完成",
    "failed": "运行出错",
    "stopped": "已停止",
}


@dataclass
class DashboardState:
    connection: str = "disconnected"
    run_state: str = "disconnected"
    stage: str = "idle"
    warps: int = 0
    scans: int = 0
    upload_records: int = 0
    upload_enabled: bool = False
    game_foreground: bool | None = None
    terminal_reason: str | None = None

    def apply(self, event: dict):
        kind = event.get("event")
        if kind == "ready":
            self.connection, self.run_state = "connected", "ready"
        elif kind == "started":
            self.connection, self.run_state = "connected", "running"
        elif kind == "paused":
            self.run_state = "paused"
        elif kind == "resumed":
            self.run_state = "running"
        elif kind == "stopped":
            self.run_state, self.stage = "stopped", "stopped"
        elif kind == "single_stage":
            self.stage = str(event.get("stage") or self.stage)
        elif kind == "auto_upload_changed":
            self.upload_enabled = bool(event.get("enabled"))
        elif kind in {"run_completed", "single_completed"}:
            self.run_state, self.stage = "complete", "complete"
            self.terminal_reason = event.get("reason")
        elif kind in {"single_failed", "observer_error"}:
            self.run_state, self.stage = "failed", "failed"
            self.terminal_reason = event.get("reason")

        if "automated_warps" in event:
            self.warps = max(self.warps, int(event.get("automated_warps") or 0))
        elif "warps" in event:
            self.warps = max(self.warps, int(event.get("warps") or 0))
        if "automated_scans" in event:
            self.scans = max(self.scans, int(event.get("automated_scans") or 0))
        elif "scans" in event:
            self.scans = max(self.scans, int(event.get("scans") or 0))
        if "upload_records" in event:
            self.upload_records = max(self.upload_records, int(event.get("upload_records") or 0))
        if "auto_upload_enabled" in event:
            self.upload_enabled = bool(event.get("auto_upload_enabled"))
        if "game_foreground" in event:
            self.game_foreground = event.get("game_foreground")

    @property
    def stage_label(self):
        return STAGE_LABELS.get(self.stage, self.stage or "等待状态")

    @property
    def status_label(self):
        return {
            "disconnected": "尚未连接游戏",
            "connecting": "正在核查并连接游戏",
            "ready": "已连接，等待启动",
            "running": self.stage_label,
            "paused": "已暂停",
            "complete": "本次运行已完成",
            "failed": "已安全停止，请查看日志",
            "stopped": "本次会话已停止",
        }.get(self.run_state, self.stage_label)
