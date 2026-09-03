import json
import tempfile
import unittest
from pathlib import Path

from nms_scanner.console_status import LogTail, status_message


class ConsoleStatusTests(unittest.TestCase):
    def test_existing_history_is_skipped_and_partial_utf8_line_waits_for_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observer.jsonl"
            path.write_text('{"event":"started","pid":2}\n', encoding="utf-8")
            tail = LogTail(path)
            self.assertEqual(tail.poll(), [])
            payload = json.dumps({"event": "paused", "pid": 2, "note": "暂停"}, ensure_ascii=False)
            raw = payload.encode("utf-8")
            boundary = raw.index("暂".encode()) + 1
            with path.open("ab") as stream:
                stream.write(raw[:boundary])
            self.assertEqual(tail.poll(), [])
            with path.open("ab") as stream:
                stream.write(raw[boundary:] + b"\n")
            self.assertEqual(tail.poll(), [json.loads(payload)])

    def test_rotation_and_invalid_lines_do_not_replay_old_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observer.jsonl"
            path.write_text('{"event":"started","pid":1}\n', encoding="utf-8")
            tail = LogTail(path)
            path.rename(path.with_suffix(".old"))
            path.write_text('invalid\n[]\n{"event":"paused","pid":2}\n', encoding="utf-8")
            events = tail.poll()
            self.assertEqual(len(events), 1)
            self.assertIsNone(status_message(events[0], 1))
            self.assertIn("F1", status_message(events[0], 2))
            self.assertEqual(tail.poll(), [])

    def test_start_message_does_not_claim_automatic_warp_or_scan(self):
        message = status_message({"event": "started", "pid": 2}, 2)
        self.assertIn("开始记录", message)
        self.assertIn("手动", message)

    def test_single_failure_explains_zero_actions_and_game_restart_requirement(self):
        message = status_message(
            {
                "event": "single_failed",
                "run_mode": "single",
                "pid": 2,
                "reason": "application_not_observed",
                "warps": 0,
                "scans": 0,
            },
            2,
        )
        for expected in ("主游戏对象", "application_not_observed", "自动跃迁 0 次", "完全退出游戏"):
            self.assertIn(expected, message)

    def test_loop_completion_and_runtime_limit_do_not_claim_another_cycle_started(self):
        base = {"run_mode": "loop", "pid": 2, "warps": 2, "scans": 2}
        cycle = status_message({**base, "event": "cycle_completed", "planet_submissions": 6}, 2)
        done = status_message({**base, "event": "run_completed", "reason": "max_warps"}, 2)
        self.assertIn("第 2 轮完成", cycle)
        self.assertIn("达到跃迁次数上限", done)
        self.assertIn("不会再次自动开始", done)
        self.assertIn("自动探索器", status_message({**base, "event": "ready"}, 2))


if __name__ == "__main__":
    unittest.main()
