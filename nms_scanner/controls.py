"""Translate global hotkeys without ever sending keyboard input to a window."""


class SessionControls:
    def __init__(self, telemetry, run=None):
        self.telemetry, self.run = telemetry, run

    def handle(self, action):
        if self.telemetry.snapshot()["stopped"]:
            return
        if action == "stop":
            if self.run:
                self.run.command("stop")
            self.telemetry.command("stop")
            return
        if self.run and self.run.stage in {"complete", "failed", "stopped"}:
            self.telemetry.notice("control_ignored", reason="session_finished")
            return
        if action == "primary":
            started = (
                self.run.start_requested.is_set()
                if self.run else self.telemetry.snapshot()["active"]
            )
            command = "pause" if started else "start"
            if self.run:
                self.run.command(command)
            self.telemetry.command(command)
        elif action == "upload":
            if not self.run:
                self.telemetry.notice("control_ignored", reason="observation_only")
                return
            enabled = self.run.toggle_upload()
            self.telemetry.notice("auto_upload_changed", enabled=enabled)
