"""Display current-session log events outside the game, without sending commands."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class LogTail:
    def __init__(self, path: Path):
        self.path = path
        self.identity, self.position, self.pending = None, 0, b""
        try:
            stat = path.stat()
            self.identity, self.position = (stat.st_dev, stat.st_ino), stat.st_size
        except FileNotFoundError:
            pass

    def poll(self):
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self.identity != identity or stat.st_size < self.position:
                    self.position, self.pending = 0, b""
                self.identity = identity
                stream.seek(self.position)
                data = stream.read(2_000_000)
                self.position = stream.tell()
        except OSError:
            return []
        *lines, self.pending = (self.pending + data).split(b"\n")
        if len(self.pending) > 65536:
            self.pending = b""
        events = []
        for line in lines:
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
            except (ValueError, UnicodeError):
                continue
        return events


def status_message(event: dict, pid: int):
    if event.get("pid") != pid:
        return None
    if event.get("run_mode") in {"single", "loop"}:
        loop = event.get("run_mode") == "loop"
        label = "自动探索" if loop else "单次测试"
        kind = event.get("event")
        controls = {
            "ready": "单次测试已就绪。F1 启动并用于暂停/继续；F2 切换自动上传（默认关闭）。",
            "started": "已收到 F1：开始单次自动跃迁和扫描测试；自动上传默认关闭。",
            "paused": "已收到 F1：后续自动动作暂停。",
            "resumed": "已收到 F1：继续单次流程。",
            "stopped": "已收到 F3：不再发起自动动作。已发起的跃迁会由游戏完成。",
        }
        if loop:
            controls.update(
                ready="自动探索器已就绪。F1 启动并用于暂停/继续；F2 切换自动上传（默认关闭）。",
                started="已收到 F1：开始自动开图、跃迁和扫描循环。",
                paused="已收到 F1：循环暂停。",
                resumed="已收到 F1：继续循环。",
                stopped="已收到 F3：不再发起自动动作。已排队的开图或跃迁仍可能由游戏完成。",
            )
        if kind in controls:
            return controls[kind]
        if kind == "auto_upload_changed":
            return "自动上传已开启；每次扫描后会处理全部待上传发现。" if event.get(
                "enabled"
            ) else "自动上传已关闭；不会提交新的批量上传请求。"
        if kind == "upload_queued":
            return (
                f"游戏已接受“上传全部”处理，本次待上传记录 {event.get('records')} 条；"
                "服务器最终接收状态由游戏网络服务决定。"
            )
        if kind == "upload_nothing_pending":
            return "扫描后没有待上传发现，本轮继续。"
        if kind == "upload_skipped":
            return "提交前自动上传已关闭，本轮未调用“上传全部”。"
        if kind == "control_ignored":
            return "本次会话已经结束，热键不会重新启动；重新运行需完全退出游戏后再开。"
        if kind == "single_failed":
            reason = event.get("reason")
            explanation = {
                "application_not_observed": "程序未取得主游戏对象，需要修复对象获取方式",
                "application_data_unavailable": "主游戏数据尚不可用",
                "application_reference_mismatch": "游戏对象引用与已核查版本不一致",
                "not_in_galaxy_map": "当前不是银河地图界面",
                "not_on_own_freighter": "未确认角色在自己的货船上",
                "no_reachable_candidate": "本轮没有找到通过能力检查的候选目标",
                "map_closed_before_dispatch": "发起跃迁前银河地图已关闭",
                "warp_request_rejected": "游戏拒绝了跃迁请求",
                "not_inside_freighter": "角色未处于稳定的货船内部位置",
                "wrong_map_request_phase": "开图回调的游戏线程来源未通过检查",
                "cannot_open_map_in_current_state": "当前游戏界面不允许自动开图",
                "system_changed_between_cycles": "两轮之间的恒星系发生了外部变化",
                "state_owner_mismatch": "当前界面的状态机归属不符",
                "map_request_not_queued": "游戏没有确认开图请求",
                "timeout_wait_ready": "等待货船就绪超时，请查看前面的等待原因",
                "timeout_wait_map": "等待银河地图超时",
                "timeout_dispatch": "等待选星界面就绪超时，未再次提交跃迁",
                "timeout_wait_load": "跃迁调用已返回，但未观察到加载；不会重复提交请求",
                "timeout_wait_upload": "等待安全的上传调用时机超时",
                "wrong_upload_phase": "上传回调的游戏线程来源未通过检查",
                "upload_frontend_mismatch": "游戏前端对象与已核查版本不一致",
                "upload_queue_not_confirmed": "游戏未确认批量上传处理，禁止继续循环",
                "warp_already_pending": "游戏已有待处理的跃迁，禁止再次提交",
                "map_not_ready_at_dispatch": "跃迁前的地图状态发生变化",
                "map_clock_reset": "地图对象重新初始化，停止使用旧对象",
                "invalid_map_clock": "地图计时数据异常",
            }.get(reason, "运行检查未通过")
            return (
                f"{label}已停止：{explanation}（{reason}）。"
                f"自动跃迁 {event.get('warps', 0)} 次，整系扫描 {event.get('scans', 0)} 次。"
                "再次按 F1 不会重启本轮；请按 F3 并保留日志，完全退出游戏后才能重新测试。"
            )
        if kind == "run_limits":
            return (
                f"最大跃迁次数 {event.get('max_warps') or '不限'}；"
                f"最长运行秒数 {event.get('max_runtime_seconds') or '不限'}（含暂停）；"
                f"首次及每轮间隔 {event.get('cycle_interval_seconds')} 秒。"
            )
        if kind == "map_waiting":
            reason = {
                "game_paused": "游戏处于暂停状态",
                "map_transition_active": "银河地图仍在切换画面",
                "map_cache_busy": "星区缓存尚未就绪",
                "map_settling": "等待地图持续更新并完成开场",
                "selection_transition_active": "选星界面仍在过渡",
                "selection_settling": "等待选星界面稳定",
            }.get(event.get("reason"), "等待地图状态确认")
            return f"{reason}，暂不发起跃迁。"
        if kind == "cycle_completed":
            return (
                f"第 {event.get('scans')} 轮完成，扫描 {event.get('planet_submissions')} 个行星。"
                f"累计跃迁 {event.get('warps')} 次、整系扫描 {event.get('scans')} 次。"
            )
        if kind in {"run_completed", "run_stopped"}:
            reason = {
                "max_warps": "达到跃迁次数上限",
                "max_runtime_seconds": "达到运行时间上限",
                "stop_requested": "收到停止请求",
            }.get(event.get("reason"), event.get("reason"))
            return (
                f"运行已结束：{reason}。累计跃迁 {event.get('warps')} 次、"
                f"整系扫描 {event.get('scans')} 次。不会再次自动开始。"
            )
        if kind == "run_waiting":
            reason = {
                "game_paused": "游戏处于暂停状态",
                "warp_request_pending": "游戏还有待处理的跃迁请求",
                "warp_transition_active": "游戏跃迁过渡尚未结束",
                "another_state_request_pending": "游戏正在处理其他界面切换",
            }.get(event.get("reason"), event.get("reason"))
            return f"等待货船就绪：{reason}。"
        if kind == "single_completed":
            count = event.get("planet_submissions")
            return f"单次流程结束，已提交 {count} 个行星的扫描。请核对游戏结果。"
        if kind == "single_stage":
            return {
                "between_cycles": "等待下一轮，F1 可暂停，F3 可停止。",
                "wait_ready": "核对货船状态，准备自动打开银河地图。",
                "wait_map": "等待货船银河地图。",
                "searching": "正在从地图缓存中查找可达目标。",
                "dispatch": "目标已选中，等待选星界面就绪后再跃迁。",
                "wait_load": "正在发起跃迁，等待游戏进入加载状态。",
                "loading": "游戏正在加载，等待返回可操作状态。",
                "wait_scan": "已返回游戏，等待核对目标系统并扫描。",
                "scanning": "正在扫描当前系统。",
                "wait_upload": "扫描已完成，等待安全时机调用游戏的“上传全部”。",
                "stopped": "自动流程已停止。",
            }.get(event.get("stage"))
    return {
        "ready": "观察线程和热键已就绪。F1 开始并暂停/继续记录；F2 在观察模式中无动作。",
        "started": "已收到 F1：开始记录。可以手动测试一次普通货船跃迁和扫描。",
        "paused": "已收到 F1：记录暂停。",
        "resumed": "已收到 F1：记录继续。",
        "stopped": "已收到停止命令：记录结束。游戏退出后钩子和运行时才会完全卸载。",
        "observer_error": "观察线程出错，记录已停止。请查看 logs 中的错误记录。",
        "warp_dispatch_candidate_enter": "观察到候选跃迁入口被调用，等待后续加载证据。",
        "scanner_action": "观察到扫描室交互。",
    }.get(event.get("event"))


class ConsoleStatus:
    def __init__(self, path: Path, pid: int):
        self.tail, self.pid = LogTail(path), pid
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="nms-console-status")

    def _run(self):
        while not self.closed.wait(0.2):
            for event in self.tail.poll():
                if message := status_message(event, self.pid):
                    print(f"\n[运行状态] {message}", flush=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.closed.set()
        self.thread.join(timeout=1)
