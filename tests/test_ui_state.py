import unittest

from nms_scanner.ui_state import DashboardState


class DashboardStateTests(unittest.TestCase):
    def test_initial_state_is_connectable(self):
        state = DashboardState()
        self.assertEqual(state.run_state, "disconnected")
        self.assertEqual(state.status_label, "尚未连接游戏")

    def test_heartbeat_projects_counts_stage_upload_and_focus(self):
        state = DashboardState(connection="connecting", run_state="connecting")
        state.apply({"event": "ready"})
        state.apply({"event": "started"})
        state.apply({"event": "single_stage", "stage": "loading"})
        state.apply(
            {
                "event": "heartbeat",
                "automated_warps": 2,
                "automated_scans": 1,
                "upload_records": 16,
                "auto_upload_enabled": True,
                "game_foreground": False,
            }
        )
        self.assertEqual((state.connection, state.run_state), ("connected", "running"))
        self.assertEqual((state.warps, state.scans, state.upload_records), (2, 1, 16))
        self.assertTrue(state.upload_enabled)
        self.assertFalse(state.game_foreground)
        self.assertEqual(state.status_label, "跃迁加载中")

    def test_terminal_event_cannot_reduce_final_counts(self):
        state = DashboardState(warps=2, scans=2)
        state.apply({"event": "run_completed", "reason": "max_warps", "warps": 1, "scans": 1})
        self.assertEqual((state.warps, state.scans), (2, 2))
        self.assertEqual((state.run_state, state.stage), ("complete", "complete"))


if __name__ == "__main__":
    unittest.main()
