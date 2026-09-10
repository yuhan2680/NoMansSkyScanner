"""Native Windows desktop UI for the automatic explorer."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import uuid
from pathlib import Path

from nms_scanner import __version__
from nms_scanner.console_status import LogTail, status_message
from nms_scanner.control_channel import CommandWriter
from nms_scanner.launcher import running_game
from nms_scanner.star_filter import (
    GROUPS,
    StarFilter,
    add_filter_arguments,
    selection_from_args,
)
from nms_scanner.ui_state import DashboardState

BG = "#08111F"
PANEL = "#101D30"
PANEL_ALT = "#14243A"
TEXT = "#ECF4FF"
MUTED = "#91A5BE"
ACCENT = "#45D8C9"
ACCENT_DARK = "#193E43"
AMBER = "#F5B94C"
RED = "#FF6B72"
BORDER = "#233852"
FONT = "Microsoft YaHei UI"


class ExplorerApp:
    def __init__(self, window: tk.Tk, args):
        self.window, self.args = window, args
        self.root = Path(__file__).resolve().parent.parent
        self.game_version = json.loads(
            (self.root / "native/profile.json").read_text(encoding="utf-8")
        )["game_internal_version"]
        self.state = DashboardState()
        self.process = None
        self.game_pid = None
        self.writer = None
        self.tail = None
        self.output = queue.Queue()
        self.pending_start = False
        self.closing = False
        self.last_message = "请先进入测试存档，并站在自己的货船内部。"

        self._configure_window()
        self._build()
        self._render()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(150, self._poll)

    def _configure_window(self):
        self.window.title(f"No Man's Sky 自动探索器 {__version__}")
        self.window.configure(bg=BG)
        self.window.geometry("1060x1330")
        self.window.minsize(1000, 1280)
        self.window.resizable(True, True)
        self.window.option_add("*Font", f"{{{FONT}}} 10")
        try:
            self.window.iconname("NMS 自动探索器")
        except tk.TclError:
            pass

    def _build(self):
        outer = tk.Frame(self.window, bg=BG, padx=28, pady=22)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        tk.Label(
            header,
            text="NMS  自动探索器",
            bg=BG,
            fg=TEXT,
            font=(FONT, 22, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text=f"v{__version__}  ·  Steam {self.game_version}",
            bg=ACCENT_DARK,
            fg=ACCENT,
            padx=12,
            pady=5,
            font=(FONT, 9, "bold"),
        ).pack(side="right", pady=(5, 0))

        tk.Label(
            outer,
            text="货船随机跃迁、整系扫描与可选的发现上传",
            bg=BG,
            fg=MUTED,
            font=(FONT, 10),
        ).pack(anchor="w", pady=(3, 18))

        status = tk.Frame(
            outer, bg=PANEL, padx=18, pady=15, highlightthickness=1, highlightbackground=BORDER
        )
        status.pack(fill="x")
        status_top = tk.Frame(status, bg=PANEL)
        status_top.pack(fill="x")
        self.dot = tk.Canvas(status_top, width=14, height=14, bg=PANEL, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 10))
        self.dot_id = self.dot.create_oval(2, 2, 12, 12, fill=MUTED, outline="")
        self.status_var = tk.StringVar()
        tk.Label(
            status_top, textvariable=self.status_var, bg=PANEL, fg=TEXT, font=(FONT, 13, "bold")
        ).pack(side="left")
        self.focus_var = tk.StringVar()
        tk.Label(status_top, textvariable=self.focus_var, bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(
            side="right"
        )
        self.message_var = tk.StringVar()
        tk.Label(
            status,
            textvariable=self.message_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=730,
            font=(FONT, 9),
        ).pack(fill="x", pady=(8, 0))

        metrics = tk.Frame(outer, bg=BG)
        metrics.pack(fill="x", pady=14)
        self.warp_var = tk.StringVar()
        self.scan_var = tk.StringVar()
        self.upload_var = tk.StringVar()
        for column, (title, variable) in enumerate(
            (
                ("已跃迁", self.warp_var),
                ("已扫描星系", self.scan_var),
                ("已上传记录", self.upload_var),
            )
        ):
            card = tk.Frame(
                metrics,
                bg=PANEL_ALT,
                padx=16,
                pady=12,
                highlightthickness=1,
                highlightbackground=BORDER,
            )
            card.grid(
                row=0,
                column=column,
                sticky="ew",
                padx=(0 if column == 0 else 6, 0 if column == 2 else 6),
            )
            metrics.grid_columnconfigure(column, weight=1)
            tk.Label(card, text=title, bg=PANEL_ALT, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
            tk.Label(
                card, textvariable=variable, bg=PANEL_ALT, fg=TEXT, font=(FONT, 20, "bold")
            ).pack(anchor="w")

        controls = tk.Frame(
            outer, bg=PANEL, padx=18, pady=16, highlightthickness=1, highlightbackground=BORDER
        )
        controls.pack(fill="x")
        tk.Label(controls, text="运行控制", bg=PANEL, fg=TEXT, font=(FONT, 11, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w"
        )

        tk.Label(controls, text="最大跃迁", bg=PANEL, fg=MUTED).grid(
            row=1, column=0, sticky="w", pady=(13, 5)
        )
        tk.Label(controls, text="最长分钟", bg=PANEL, fg=MUTED).grid(
            row=1, column=2, sticky="w", pady=(13, 5), padx=(18, 0)
        )
        self.max_warps = tk.StringVar(value=str(self.args.max_warps))
        minutes = self.args.max_runtime_seconds / 60 if self.args.max_runtime_seconds else 0
        self.max_minutes = tk.StringVar(value=f"{minutes:g}")
        self.warps_entry = self._entry(controls, self.max_warps)
        self.warps_entry.grid(row=2, column=0, sticky="ew")
        tk.Label(controls, text="0 = 不限", bg=PANEL, fg=MUTED).grid(
            row=2, column=1, sticky="w", padx=(8, 0)
        )
        self.minutes_entry = self._entry(controls, self.max_minutes)
        self.minutes_entry.grid(row=2, column=2, sticky="ew", padx=(18, 0))
        tk.Label(controls, text="0 = 不限", bg=PANEL, fg=MUTED).grid(
            row=2, column=3, sticky="w", padx=(8, 0)
        )
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(2, weight=1)

        filters = tk.Frame(controls, bg=PANEL)
        filters.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        initial_filter = selection_from_args(self.args)
        self.filter_enabled = tk.BooleanVar(value=initial_filter.enabled)
        self.filter_toggle = self._checkbox(filters, "启用星系筛选", self.filter_enabled)
        self.filter_toggle.pack(anchor="w")
        self.filter_options = []
        self.filter_groups = {}
        for key, (labels, title) in GROUPS.items():
            variables = self.filter_groups[key] = {}
            selected = getattr(initial_filter, key)
            row = tk.Frame(filters, bg=PANEL)
            row.pack(fill="x", pady=(4, 0))
            tk.Label(row, text=title, bg=PANEL, fg=MUTED, width=9, anchor="w").pack(side="left")
            for option, label in labels.items():
                variable = tk.BooleanVar(value=option in selected)
                variables[option] = variable
                checkbox = self._checkbox(row, label, variable)
                checkbox.pack(side="left", padx=(0, 6))
                self.filter_options.append(checkbox)
            if key == "letters":
                tk.Label(
                    filters,
                    text="黄 F/G  ·  红 M/K  ·  绿 E  ·  蓝 O/B  ·  紫 X/Y",
                    bg=PANEL,
                    fg=MUTED,
                    font=(FONT, 9),
                ).pack(anchor="w", padx=(90, 0))
        tk.Label(
            filters,
            text=(
                "地图标签按游戏实际显示匹配。要同时包含吉克与无人星系，"
                "种族组勾选吉克和无主导种族，类别组也保留无人。"
            ),
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 9),
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        self.filter_hint = tk.Label(
            filters, bg=PANEL, fg=MUTED, anchor="w", justify="left", wraplength=720, font=(FONT, 9)
        )
        self.filter_hint.pack(fill="x", pady=(5, 0))

        button_row = tk.Frame(controls, bg=PANEL)
        button_row.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        self.primary_button = self._button(button_row, self.primary, ACCENT, "#061B1B")
        self.primary_button.pack(side="left", fill="x", expand=True)
        self.upload_button = self._button(button_row, self.toggle_upload, PANEL_ALT, TEXT)
        self.upload_button.pack(side="left", fill="x", expand=True, padx=10)
        self.stop_button = self._button(button_row, self.stop, "#3A202B", "#FF98A0")
        self.stop_button.pack(side="left", fill="x", expand=True)

        log_header = tk.Frame(outer, bg=BG)
        log_header.pack(fill="x", pady=(15, 7))
        tk.Label(log_header, text="最近状态", bg=BG, fg=TEXT, font=(FONT, 10, "bold")).pack(
            side="left"
        )
        tk.Label(
            log_header, text="热键仍可使用：F1 / F2 / F3", bg=BG, fg=MUTED, font=(FONT, 9)
        ).pack(side="right")
        log_frame = tk.Frame(outer, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(
            log_frame,
            bg=PANEL,
            fg=MUTED,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            height=8,
            wrap="word",
            state="disabled",
            font=(FONT, 9),
        )
        scrollbar = tk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self._append_log(self.last_message)

    @staticmethod
    def _entry(parent, variable):
        return tk.Entry(
            parent,
            textvariable=variable,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            justify="center",
        )

    def _checkbox(self, parent, text, variable):
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=self._render_filter,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            activeforeground=TEXT,
            selectcolor=BG,
            disabledforeground="#66798E",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )

    def _selected_filter(self):
        return StarFilter.from_dict(
            {
                "enabled": self.filter_enabled.get(),
                **{
                    key: [option for option, variable in variables.items() if variable.get()]
                    for key, variables in self.filter_groups.items()
                },
            }
        )

    def _render_filter(self):
        editable = self.state.run_state == "disconnected"
        self.filter_toggle.configure(state="normal" if editable else "disabled")
        enabled = self.filter_enabled.get()
        for checkbox in self.filter_options:
            checkbox.configure(state="normal" if editable and enabled else "disabled")
        try:
            self._selected_filter()
            hint = (
                "启用的各组至少勾选一项；组内任选、组间同时满足；全选表示该组不限。"
                if enabled
                else "筛选已关闭，按原有规则选择可达星系。"
            )
            hint += "连接前设置，本次运行固定。" if editable else "本次筛选设置已固定。"
            self.filter_hint.configure(text=hint, fg=MUTED)
        except ValueError as error:
            self.filter_hint.configure(text=str(error), fg=AMBER)

    @staticmethod
    def _button(parent, command, background, foreground):
        return tk.Button(
            parent,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=background,
            activeforeground=foreground,
            disabledforeground="#66798E",
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            cursor="hand2",
            font=(FONT, 10, "bold"),
        )

    def _parse_limits(self):
        try:
            warps = int(self.max_warps.get().strip())
            minutes = float(self.max_minutes.get().strip())
        except ValueError as error:
            raise ValueError("运行上限必须填写数字。") from error
        if warps < 0 or minutes < 0 or warps > 1_000_000:
            raise ValueError("运行上限不能为负数，最大跃迁不得超过 1000000。")
        return warps, minutes * 60

    def primary(self):
        if self.state.run_state == "disconnected":
            self._connect_and_start()
        elif self.state.run_state in {"ready", "running", "paused"}:
            self._send("primary")

    def _connect_and_start(self):
        try:
            max_warps, max_runtime = self._parse_limits()
            star_filter = self._selected_filter()
            game = running_game()
            self.game_pid = game.pid
            token = uuid.uuid4().hex
            control_file = self.root / "logs" / f"ui-control-{game.pid}-{token}.jsonl"
            self.writer = CommandWriter(control_file, token)
            self.writer.initialize()
            self.tail = LogTail(self.root / "logs/native-observer.jsonl")
            python = Path(sys.executable)
            if python.name.lower() == "pythonw.exe":
                console_python = python.with_name("python.exe")
                if console_python.exists():
                    python = console_python
            command = [
                str(python),
                "-u",
                "-X",
                "utf8",
                "-m",
                "nms_scanner.launcher",
                "--loop",
                "--max-warps",
                str(max_warps),
                "--max-runtime-seconds",
                str(max_runtime),
                "--control-file",
                str(control_file),
                "--control-token",
                token,
            ]
            command.extend(star_filter.arguments())
            startup = None
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            self.process = subprocess.Popen(
                command,
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
                startupinfo=startup,
            )
            threading.Thread(
                target=self._read_process, daemon=True, name="nms-ui-launcher-output"
            ).start()
            self.state.connection = self.state.run_state = "connecting"
            self.pending_start = True
            self.last_message = "正在核对游戏版本并加载自动探索器…"
            self._append_log(self.last_message)
            self._render()
        except Exception as error:
            self.state.connection, self.state.run_state = "disconnected", "disconnected"
            self.last_message = str(error)
            self._append_log(f"无法启动：{error}")
            self._render()

    def _read_process(self):
        try:
            for line in self.process.stdout:
                if line := line.strip():
                    self.output.put(("line", line))
            self.output.put(("exit", self.process.wait()))
        except Exception as error:
            self.output.put(("reader_error", str(error)))

    def _send(self, action):
        try:
            self.writer.send(action)
            if action == "stop":
                self.last_message = "停止命令已发送；已发起的跃迁仍会由游戏完成。"
            self._render()
        except (OSError, ValueError) as error:
            self.last_message = f"控制命令未能写入：{error}"
            self._append_log(self.last_message)
            self._render()

    def toggle_upload(self):
        if self.state.run_state in {"ready", "running", "paused"}:
            self._send("upload")

    def stop(self):
        if self.state.run_state in {"ready", "running", "paused"}:
            self._send("stop")

    def _poll(self):
        if self.closing:
            return
        while True:
            try:
                kind, value = self.output.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                self._append_log(value)
            elif kind == "reader_error":
                self._append_log(f"启动器输出读取失败：{value}")
            elif kind == "exit" and self.state.run_state not in {"complete", "failed", "stopped"}:
                self.state.connection, self.state.run_state = "disconnected", "failed"
                self.last_message = f"启动器已退出（代码 {value}）。请查看上方状态或 logs。"
                self._append_log(self.last_message)

        if self.tail and self.game_pid:
            for event in self.tail.poll():
                if event.get("pid") != self.game_pid:
                    continue
                self.state.apply(event)
                if message := status_message(event, self.game_pid):
                    self.last_message = message
                    self._append_log(message)
                if event.get("event") == "ready" and self.pending_start:
                    self.pending_start = False
                    self._send("primary")
        self._render()
        self.window.after(150, self._poll)

    def _append_log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 120:
            self.log.delete("1.0", f"{lines - 100}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _render(self):
        self.status_var.set(self.state.status_label)
        self.message_var.set(self.last_message)
        self.warp_var.set(str(self.state.warps))
        self.scan_var.set(str(self.state.scans))
        self.upload_var.set(str(self.state.upload_records))
        dot_color = {
            "running": ACCENT,
            "ready": ACCENT,
            "paused": AMBER,
            "connecting": AMBER,
            "failed": RED,
            "stopped": MUTED,
            "complete": ACCENT,
        }.get(self.state.run_state, MUTED)
        self.dot.itemconfigure(self.dot_id, fill=dot_color)
        self.focus_var.set(
            "游戏窗口：前台"
            if self.state.game_foreground is True
            else "游戏窗口：后台"
            if self.state.game_foreground is False
            else "窗口状态：等待"
        )
        label = {
            "disconnected": "连接并启动",
            "connecting": "正在连接…",
            "ready": "启动探索",
            "running": "暂停",
            "paused": "继续",
            "complete": "本次已完成",
            "failed": "本次已停止",
            "stopped": "本次已停止",
        }.get(self.state.run_state, "连接并启动")
        self.primary_button.configure(text=label)
        active = self.state.run_state in {"ready", "running", "paused"}
        self.primary_button.configure(
            state="normal"
            if self.state.run_state in {"disconnected", "ready", "running", "paused"}
            else "disabled"
        )
        self.upload_button.configure(
            text=f"自动上传：{'开启' if self.state.upload_enabled else '关闭'}",
            bg=ACCENT_DARK if self.state.upload_enabled else PANEL_ALT,
            fg=ACCENT if self.state.upload_enabled else TEXT,
            state="normal" if active else "disabled",
        )
        self.stop_button.configure(text="停止", state="normal" if active else "disabled")
        entry_state = "normal" if self.state.run_state == "disconnected" else "disabled"
        self.warps_entry.configure(state=entry_state)
        self.minutes_entry.configure(state=entry_state)
        self._render_filter()

    def close(self):
        if self.closing:
            return
        self.closing = True
        if self.writer and self.state.run_state in {"ready", "running", "paused", "connecting"}:
            try:
                self.writer.send("stop")
            except OSError:
                pass
            self.window.after(350, self._finish_close)
        else:
            self._finish_close()

    def _finish_close(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass
        self.window.destroy()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="No Man's Sky 自动探索器图形界面")
    parser.add_argument("--max-warps", type=int, default=0)
    parser.add_argument("--max-runtime-seconds", type=float, default=0)
    add_filter_arguments(parser)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
    window = tk.Tk()
    ExplorerApp(window, args)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
