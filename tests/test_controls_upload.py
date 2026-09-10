import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from nms_scanner.controls import SessionControls
from nms_scanner.native_upload import NativeUpload
from nms_scanner.observation import Observation
from nms_scanner.single_run import SingleRun


class ControlTests(unittest.TestCase):
    def test_f1_starts_then_pauses_and_resumes_f2_only_toggles_upload(self):
        telemetry = Observation()
        run = SimpleNamespace(
            stage="idle",
            start_requested=Mock(),
            command=Mock(),
            toggle_upload=Mock(return_value=True),
        )
        run.start_requested.is_set.side_effect = [False, True, True]
        controls = SessionControls(telemetry, run)
        controls.handle("primary")
        controls.handle("primary")
        controls.handle("primary")
        controls.handle("upload")
        self.assertEqual(
            [x.args[0] for x in run.command.call_args_list], ["start", "pause", "pause"]
        )
        run.toggle_upload.assert_called_once_with()
        events = [x["event"] for x in telemetry.drain()]
        self.assertEqual(events, ["ready", "started", "paused", "resumed", "auto_upload_changed"])

    def test_f3_is_terminal_and_later_keys_are_ignored(self):
        telemetry = Observation()
        run = SimpleNamespace(stage="idle", command=Mock())
        controls = SessionControls(telemetry, run)
        controls.handle("stop")
        controls.handle("primary")
        controls.handle("upload")
        run.command.assert_called_once_with("stop")


class FakeUploadEngine:
    def scan_ready(self, context):
        return True

    def __init__(self):
        self.calls = []
        self.checkpoint = lambda: None
        self.map_wait_reason, self.map_status = None, {}

    def begin(self, context):
        return True

    def selection_matches(self, context):
        return True

    def selection_ready(self, context):
        return True

    def choose(self, context):
        return True

    def dispatch(self, context):
        self.calls.append("warp")
        return True

    def begin_scan(self, context):
        return 1

    def scan_planet(self, context, index):
        self.calls.append("scan")

    def upload_all(self, enabled):
        self.calls.append("upload")
        return 7


class UploadStageTests(unittest.TestCase):
    def make_run(self):
        self.now, self.events = 0, []
        self.engine = FakeUploadEngine()
        return SingleRun(
            self.engine,
            {"phase_timeout_seconds": 90, "max_candidate_attempts": 3,
             "upload_settle_seconds": 2},
            lambda name, **data: self.events.append((name, data)), lambda: self.now,
        )

    def reach_scan(self, run):
        run.command("start")
        run.poll("map", 1)
        run.poll("map", 1)
        run.state_changed("APPLOCALLOAD")
        run.state_changed("APPVIEW")
        run.poll("world", 2)

    def test_upload_is_off_by_default_and_no_upload_function_is_called(self):
        run = self.make_run()
        self.reach_scan(run)
        self.assertEqual(run.stage, "complete")
        self.assertNotIn("upload", self.engine.calls)

    def test_enabled_upload_runs_after_scan_and_before_completion(self):
        run = self.make_run()
        run.toggle_upload()
        self.reach_scan(run)
        self.assertEqual(run.stage, "wait_upload")
        self.now = 2
        run.poll("application")
        self.assertEqual(run.stage, "complete")
        self.assertEqual(self.engine.calls, ["warp", "scan", "upload"])
        self.assertEqual((run.upload_batches, run.upload_records), (1, 7))

    def test_f2_off_during_wait_skips_upload_and_finishes_cycle(self):
        run = self.make_run()
        run.toggle_upload()
        self.reach_scan(run)
        run.toggle_upload()
        self.now = 2
        run.poll("application")
        self.assertEqual(run.stage, "complete")
        self.assertNotIn("upload", self.engine.calls)
        self.assertIn(("upload_skipped", {"reason": "disabled_before_submission"}), self.events)


class NativeUploadTests(unittest.TestCase):
    def test_validates_real_page_and_every_record_before_calling_bulk_handler(self):
        base, data, manager, registry, array = 0x100000, 0x200000, 0x300000, 0x400000, 0x500000
        profile = {
            "upload": {"max_pending_records": 10, "layout": {
                "application_update_caller": "0x5f800b", "frontend_manager": 0x100,
                "frontend_vtable_rva": "0x900", "frontend_page": 0x20,
                "discovery_page": 0x30, "page_reward_total": 0x80,
                "page_reward_hint": 0x10, "manager_data": 8, "registry": 48,
                "eligible_count": 116, "eligible_array": 120, "queued_count": 160,
                "record_pointer": 0, "entry_status": 8, "record_type": 56,
                "max_record_type": 16, "fsm_pending_state": 24,
                "empty_state_rva": "0xa00",
            }},
            "single_trial": {"layout": {"player_environment": 0x200,
                "application_paused": 0x300, "warp_request": 0x400,
                "warp_transition": 0x404, "discovery_manager": 0x500}},
        }
        values = {
            data + 0x100: base + 0x900,
            data + 0x500 + 8: manager, manager + 48: registry,
            registry + 116: 2, registry + 120: array,
            array: 0x600000, array + 8: 0x600100,
            0x600000: 0x700000, 0x600100: 0x700100,
            0x700000 + 56: 2, 0x700100 + 56: 16,
        }
        called = []
        class Engine:
            application, target = 0x800000, 9
            def __init__(self):
                self.profile, self.layout = profile, profile["single_trial"]["layout"]
                self.base, self.stack = base, lambda: ["0x5f800b"]
                self.checkpoint = lambda: None
            def _app_data(self):
                return data
            def _state(self):
                return "APPVIEW"
            def _current_system(self, context):
                return 9
            def _fail(self, reason):
                raise RuntimeError(reason)
            def _integer(self, address, size=8):
                if address == registry + 116 and called:
                    return 0
                return values.get(address, 0)
            def read(self, address, size):
                return b"\0" * size
            def _call(self, name, *args):
                if name == "own_freighter":
                    return True
                called.append((name, args))
                return None
        count = NativeUpload(Engine()).submit(lambda: True)
        self.assertEqual(count, 2)
        self.assertEqual(called, [("upload_all", (data + 0x150,))])


if __name__ == "__main__":
    unittest.main()
