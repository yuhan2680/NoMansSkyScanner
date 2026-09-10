import ctypes
import struct
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_single_run as fixtures

from nms_scanner.compatibility import load_profile
from nms_scanner.native_single import NativeSingleEngine
from nms_scanner.single_run import NativePreconditionError, SingleRun

ROOT = Path(__file__).resolve().parent.parent


class CosmosScanTests(unittest.TestCase):
    def make_world(self):
        engine, _, _, memory = fixtures.NativeSequenceTests().make_engine(1)
        data, state, solar = 0x20000, 0x30000, 0x60000
        layout = engine.layout
        context = data + layout["simulation"]
        engine.source, engine.target = (1 << 40) + 2, (1 << 40) + 1
        engine.stack = lambda: [layout["scan_update_caller"]]
        memory[state + layout["fsm_state_name"]] = b"APPVIEW".ljust(16, b"\0")
        memory[context + layout["current_ua"]] = struct.pack("<Q", engine.target)
        memory[context + layout["solar_system"]] = struct.pack("<Q", solar)
        memory[solar + layout["planet_count"]] = struct.pack("<I", 2)
        for key in ("environment_location", "environment_stable_location"):
            memory[data + layout["player_environment"] + layout[key]] = struct.pack("<I", 10)
        for index in range(2):
            record = solar + layout["planet_discovery"] + index * layout["planet_stride"]
            memory[record] = struct.pack("<Q", engine.target)
        engine.functions["discovery"] = Mock(return_value=True)
        return engine, context, memory

    def test_scanner_passes_new_fourth_argument_and_uses_new_planet_stride(self):
        engine, context, _ = self.make_world()
        self.assertEqual(engine.begin_scan(context), 2)
        for index in range(2):
            engine.scan_planet(context, index)
        calls = engine.functions["discovery"].call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].args[1] - calls[0].args[1], 0xD9170)
        for call in calls:
            self.assertEqual(call.args[0], 0x20000 + 0x2CE838)
            self.assertEqual(call.args[2:], (None, True))

    def test_discovery_binding_declares_four_arguments(self):
        profile = load_profile(ROOT, exploration=True)
        offsets = {key: int(value["rva"], 16)
                   for key, value in (profile["hooks"] | profile["calls"]).items()}
        with (
            patch("nms_scanner.native_single.resolve_application", return_value=0x10000),
            patch("nms_scanner.native_single.C.CFUNCTYPE") as factory,
        ):
            NativeSingleEngine(0, offsets, profile, Mock(), Mock())
        factory.assert_any_call(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool
        )

    def test_interior_or_pending_transition_blocks_scan_and_later_change_stops(self):
        for field in ("environment_location", "environment_stable_location",
                      "warp_request", "warp_transition", "application_paused"):
            with self.subTest(field=field):
                engine, context, memory = self.make_world()
                self.assertEqual(engine.begin_scan(context), 2)
                base = (engine.application if field == "application_paused" else
                        0x20000 + engine.layout["player_environment"]
                        if field.startswith("environment_") else 0x20000)
                address = base + engine.layout[field]
                memory[address] = (1).to_bytes(1 if field == "application_paused" else 4, "little")
                self.assertIsNone(engine.begin_scan(context))
                with self.assertRaisesRegex(NativePreconditionError, "freighter_state_changed"):
                    engine.scan_planet(context, 0)
                engine.functions["discovery"].assert_not_called()


class SettlingTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.engine = fixtures.FakeEngine()
        self.engine.scan_ready = Mock(return_value=True)
        self.run = SingleRun(self.engine, {
            "phase_timeout_seconds": 90, "max_candidate_attempts": 3,
            "post_load_settle_seconds": 3.0, "post_load_max_frame_gap_seconds": 0.5,
        }, Mock(), lambda: self.now)
        self.run.command("start")
        self.run.poll("map", 1)
        self.run.poll("map", 1)
        self.run.state_changed("APPLOCALLOAD")
        self.run.state_changed("APPVIEW")

    def frame(self, delta=0.25):
        self.now += delta
        self.run.poll("world", 2)

    def test_continuous_ready_frames_required_and_f3_stops_wait(self):
        self.frame()
        for _ in range(11):
            self.frame()
        self.assertEqual(self.run.stage, "wait_scan")
        self.assertNotIn("verify_loaded_target", self.engine.calls)
        self.run.command("stop")
        self.frame()
        self.assertEqual(self.run.stage, "stopped")
        self.assertNotIn("verify_loaded_target", self.engine.calls)

    def test_pause_stall_and_transient_bad_state_restart_settling(self):
        for cause in ("pause", "stall", "not_ready"):
            with self.subTest(cause=cause):
                self.setUp()
                for _ in range(9):
                    self.frame()
                if cause == "pause":
                    self.run.command("pause")
                    self.frame(10)
                    self.run.command("pause")
                elif cause == "stall":
                    self.frame(10)
                else:
                    self.engine.scan_ready.return_value = False
                    self.frame()
                    self.engine.scan_ready.return_value = True
                self.frame()
                for _ in range(10):
                    self.frame()
                self.assertEqual(self.run.stage, "wait_scan")
                self.frame()
                self.frame()
                self.assertIn(("scan", 0), self.engine.calls)


if __name__ == "__main__":
    unittest.main()
