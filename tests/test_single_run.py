import ctypes
import json
import random
import struct
import sys
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

from nms_scanner.native_single import AlignedBuffer, NativeSingleEngine, resolve_application
from nms_scanner.runtime_bootstrap import bootstrap_script
from nms_scanner.single_run import ActionInterrupted, NativePreconditionError, SingleRun
from nms_scanner.star_filter import StarFilter


class FakeEngine:
    scan_wait_reason = "test_not_ready"

    def scan_ready(self, context):
        return True

    def __init__(self):
        self.calls = []
        self.choices = deque([True])
        self.checkpoint = lambda: None
        self.before_dispatch = lambda: None
        self.map_wait_reason = "map_cache_busy"
        self.map_status = {}

    def begin(self, context):
        self.checkpoint()
        self.calls.append("begin")
        return True

    def selection_ready(self, context):
        return True

    def selection_matches(self, context):
        return True

    def choose(self, context):
        self.checkpoint()
        return self.choices.popleft() if self.choices else False

    def dispatch(self, context):
        self.before_dispatch()
        self.checkpoint()
        self.calls.append("warp")
        return True

    def begin_scan(self, context):
        self.checkpoint()
        self.calls.append("verify_loaded_target")
        return 4

    def scan_planet(self, context, index):
        self.checkpoint()
        self.calls.append(("scan", index))


class SingleRunTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.engine = FakeEngine()
        self.events = []
        self.run = SingleRun(
            self.engine,
            {"phase_timeout_seconds": 90, "max_candidate_attempts": 3},
            lambda event, **details: self.events.append((event, details)),
            lambda: self.now,
        )

    def dispatch(self):
        self.run.command("start")
        self.run.poll("map", 1)
        self.run.poll("map", 1)

    def loaded(self):
        self.run.state_changed("APPLOCALLOAD")
        self.run.state_changed("APPVIEW")

    def test_complete_sequence_requires_loading_and_runs_exactly_once(self):
        self.dispatch()
        self.run.state_changed("APPVIEW")  # Map exit is not completion.
        self.run.poll("world", 2)
        self.assertEqual(self.run.stage, "wait_load")
        self.assertNotIn("verify_loaded_target", self.engine.calls)
        self.loaded()
        for _ in range(4):
            self.run.poll("world", 2)
        self.assertEqual(self.run.stage, "complete")
        self.assertEqual((self.run.warps, self.run.scans), (1, 1))
        self.run.command("start")
        for _ in range(10):
            self.run.poll("map", 1)
            self.run.poll("world", 2)
        self.assertEqual(self.engine.calls.count("warp"), 1)
        self.assertEqual(
            [c for c in self.engine.calls if isinstance(c, tuple)], [("scan", i) for i in range(4)]
        )

    def test_pause_preserves_load_events_and_stop_prevents_remaining_planets(self):
        self.dispatch()
        self.run.command("pause")
        self.loaded()
        self.now = 500
        self.run.poll("world", 2)
        self.assertEqual(self.run.stage, "wait_scan")
        self.assertNotIn("verify_loaded_target", self.engine.calls)
        self.run.command("pause")
        self.run.poll("world", 2)
        self.assertIn(("scan", 0), self.engine.calls)
        self.run.command("stop")
        self.run.command("start")
        self.run.poll("world", 2)
        self.assertNotIn(("scan", 1), self.engine.calls)
        self.assertEqual(self.run.scans, 0)

    def test_stop_during_dispatch_preparation_does_not_call_warp(self):
        self.engine.before_dispatch = lambda: self.run.command("stop")
        self.dispatch()
        self.run.poll("world", 2)
        self.assertEqual(self.run.stage, "stopped")
        self.assertNotIn("warp", self.engine.calls)

    def test_busy_cache_does_not_exhaust_candidate_attempts(self):
        self.engine.choices = deque([None] * 10 + [False] * 3)
        self.run.command("start")
        for _ in range(10):
            self.run.poll("map", 1)
        self.assertEqual(self.run.attempts, 0)
        for _ in range(3):
            self.run.poll("map", 1)
        self.assertEqual(self.run.stage, "failed")
        self.assertNotIn("warp", self.engine.calls)

    def test_missing_load_transition_times_out_without_scanning(self):
        self.dispatch()
        self.now = 91
        self.run.poll()
        self.assertEqual(self.run.stage, "failed")
        self.assertEqual(self.run.scans, 0)

    def test_map_exit_before_dispatch_prevents_warp(self):
        self.run.command("start")
        self.run.poll("map", 1)
        self.run.state_changed("APPVIEW")
        self.run.poll("map", 1)
        self.assertEqual(self.run.stage, "failed")
        self.assertNotIn("warp", self.engine.calls)

    def test_game_job_does_not_block_or_lose_transition_while_action_lock_is_held(self):
        self.dispatch()
        with self.run.lock:
            worker = threading.Thread(target=self.loaded)
            worker.start()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
        self.run.poll()
        self.assertEqual(self.run.stage, "wait_scan")

    def test_target_verification_failure_does_not_increment_success_counts(self):
        self.engine.begin_scan = Mock(side_effect=NativePreconditionError("loaded_system_mismatch"))
        self.dispatch()
        self.loaded()
        self.run.poll("world", 2)
        self.assertEqual(self.run.stage, "failed")
        self.assertEqual((self.run.warps, self.run.scans), (0, 0))

    def test_waiting_for_open_and_selected_ui_never_consumes_attempts_or_warps(self):
        self.engine.begin = Mock(return_value=False)
        self.engine.choose = Mock(return_value=True)
        self.engine.selection_ready = Mock(return_value=False)
        self.run.command("start")
        for _ in range(10):
            self.run.poll("map", 1)
        self.assertEqual((self.run.stage, self.run.attempts), ("wait_map", 0))
        self.engine.choose.assert_not_called()
        self.engine.begin.return_value = True
        self.run.poll("map", 1)
        for _ in range(10):
            self.run.poll("map", 1)
        self.assertEqual((self.run.stage, self.run.attempts), ("dispatch", 1))
        self.assertNotIn("warp", self.engine.calls)
        self.run.command("pause")
        self.engine.selection_ready.return_value = True
        self.run.poll("map", 1)
        self.assertNotIn("warp", self.engine.calls)
        self.run.command("stop")
        self.run.command("pause")
        self.run.poll("map", 1)
        self.assertEqual(self.run.stage, "stopped")
        self.assertNotIn("warp", self.engine.calls)

    def test_selection_that_never_becomes_ready_times_out_before_warp(self):
        self.engine.selection_ready = Mock(return_value=False)
        self.dispatch()
        self.now = 91
        self.run.poll("map", 1)
        self.assertEqual(self.run.stage, "failed")
        self.assertEqual(self.events[-1][1]["reason"], "timeout_dispatch")
        self.assertNotIn("warp", self.engine.calls)


class NativeGuardTests(unittest.TestCase):
    def test_application_references_resolve_with_aslr_and_signed_displacement(self):
        for base in (0x140000000, 0x7FF600000000):
            for rva in (0x4000, 0x18000):
                references = (0x10000, 0x10100)
                definition = {"rva": hex(rva), "reference_rvas": [hex(r) for r in references]}
                memory = {
                    base + at: b"\x48\x8d\x0d" + struct.pack("<i", rva - at - 7)
                    for at in references
                }
                reader = Mock(side_effect=lambda address, size, mem=memory: mem[address][:size])
                self.assertEqual(resolve_application(base, 0x20000, definition, reader), base + rva)
                self.assertEqual(reader.call_count, 2)

    def test_any_invalid_application_reference_prevents_resolution(self):
        definition = {"rva": "0x18000", "reference_rvas": ["0x10000", "0x10100"]}
        for at in (0x10000, 0x10100):
            valid = b"\x48\x8d\x0d" + struct.pack("<i", 0x18000 - at - 7)
            for invalid in (b"\x90" + valid[1:], valid[:-1], valid[:-1] + b"\x7f"):
                memory = {
                    r: b"\x48\x8d\x0d" + struct.pack("<i", 0x18000 - r - 7)
                    for r in (0x10000, 0x10100)
                }
                memory[at] = invalid
                with self.assertRaisesRegex(
                    NativePreconditionError, "application_reference_mismatch"
                ):
                    resolve_application(
                        0, 0x20000, definition, lambda address, size, mem=memory: mem[address]
                    )

    def test_aligned_native_output_buffers_have_room_and_are_zero_initialized(self):
        buffer = AlignedBuffer(64, b"ABCD")
        self.assertEqual(buffer.address % 16, 0)
        self.assertEqual(buffer.bytes(), b"ABCD" + bytes(60))
        ctypes.memmove(buffer.address + 60, b"WXYZ", 4)
        self.assertEqual(buffer.bytes()[-4:], b"WXYZ")

    def test_stop_checkpoint_runs_before_a_native_function(self):
        engine = NativeSingleEngine.__new__(NativeSingleEngine)
        engine.checkpoint = Mock(side_effect=ActionInterrupted())
        engine.functions = {"discovery": Mock()}
        with self.assertRaises(ActionInterrupted):
            engine._call("discovery", 1, 2, None)
        engine.functions["discovery"].assert_not_called()

    def test_wrong_world_update_phase_does_not_call_native_code(self):
        engine = NativeSingleEngine.__new__(NativeSingleEngine)
        profile_path = Path(__file__).resolve().parent.parent / "native/single_trial.json"
        engine.layout = json.loads(profile_path.read_text(encoding="utf-8"))["layout"]
        engine._app_data = lambda: 0x100000
        engine._state = lambda: "APPVIEW"
        engine.stack = lambda: ["0xDEADBEEF"]
        engine._call = Mock()
        with self.assertRaisesRegex(NativePreconditionError, "wrong_scan_update_phase"):
            engine._scan_context(0x100000 + engine.layout["simulation"])
        engine._call.assert_not_called()

    def test_framework_exit_stops_single_controller_before_parking_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "stopped"
            payload = (
                "from types import SimpleNamespace\n"
                "from pathlib import Path\n"
                f"marker = Path({str(marker)!r})\n"
                "controller = SimpleNamespace(command=lambda action: marker.write_text(action))\n"
                "mod = SimpleNamespace(single=controller)\n"
                "mod_manager = SimpleNamespace(mods={'single': mod})\n"
            )
            script = bootstrap_script([(payload, False)], sys.path, Path(directory) / "error.txt")
            with (
                patch.object(sys, "path", list(sys.path)),
                patch("time.sleep", side_effect=SystemExit),
            ):
                with self.assertRaises(SystemExit):
                    exec(script, {})
            self.assertEqual(marker.read_text(), "stop")


class NativeSequenceTests(unittest.TestCase):
    def make_engine(self, status):
        """Only Python buffers and fake reads/functions; no real process or native entry."""
        root = Path(__file__).resolve().parent.parent
        profile = json.loads((root / "native/profile.json").read_text(encoding="utf-8"))
        trial = json.loads((root / "native/single_trial.json").read_text(encoding="utf-8"))
        profile["single_trial"] = trial
        layout = trial["layout"]
        engine = NativeSingleEngine.__new__(NativeSingleEngine)
        engine.layout, engine.profile = layout, profile
        engine.star_filter = StarFilter()
        profile["star_filter"] = json.loads(
            (root / "native/star_filter.json").read_text(encoding="utf-8")
        )
        engine.star_filter_layout = profile["star_filter"]["layout"]
        engine.filter_target, engine.filter_rejections = None, 0
        engine.candidate_classification = None
        engine.application, engine.map_object = 0x10000, None
        engine.expected_system = None
        engine.map_first_clock = engine.map_last_clock = engine.selection_clock = None
        engine.map_wait_reason, engine.map_status = None, {}
        engine.target, engine.source = None, None
        engine.rng, engine.checkpoint = random.Random(1), lambda: None
        data, state, map_object, selected = 0x20000, 0x30000, 0x40000, 0x50000
        source, target = (1 << 40) + 2, (1 << 40) + 1

        def query_bytes(ua, x):
            result = bytearray(64)
            struct.pack_into("<QhhhBBHH", result, 0, ua, x, 0, 0, 1, 0, 1, 0)
            struct.pack_into("<4f", result, 32, 1, 2, 3, 1)
            return bytes(result)

        memory = {
            engine.application + layout["application_data"]: struct.pack("<Q", data),
            engine.application + layout["fsm_current_state"]: struct.pack("<Q", state),
            engine.application + layout["application_paused"]: b"\0",
            data + layout["application_map_data"]: struct.pack("<Q", map_object),
            data + layout["warp_request"]: bytes(4),
            data + layout["warp_transition"]: bytes(4),
            state + layout["fsm_state_name"]: b"GALAXYMAP".ljust(16, b"\0"),
            map_object + layout["map_clock"]: struct.pack("<f", 0),
            map_object + layout["map_mode"]: bytes(4),
            map_object + layout["map_popup_state"]: struct.pack("<I", 3),
            map_object + layout["map_popup_selection"]: b"\1",
            data + layout["simulation"] + layout["current_ua"]: struct.pack("<Q", source),
            map_object + layout["map_initial"]: query_bytes(source, 2),
            map_object + layout["map_coordinate"]: struct.pack("<hhhBB", 2, 0, 0, 1, 0),
            map_object + layout["map_cache"] + layout["cache_busy"]: b"\0\1",
            map_object + layout["map_selected"]: struct.pack("<Q", selected),
            selected + layout["selected_query"]: struct.pack("<Q", source),
        }
        engine.read = lambda address, size: memory[address][:size]

        def query(cache, coordinate, origin, direction, result):
            self.assertEqual(origin % 16, 0)
            self.assertEqual(direction % 16, 0)
            ctypes.memmove(result, query_bytes(target, 1), 64)
            return 1

        def capability(this, output, distance, centre, target_arg, attributes):
            self.assertFalse(centre)
            self.assertEqual(target_arg, target)
            ctypes.memmove(output + 8, struct.pack("<I", status), 4)
            return output

        def select(context, result, selected_flag, alpha):
            self.assertEqual(context, map_object)
            memory[selected + layout["selected_query"]] = ctypes.string_at(result, 8)

        engine.functions = {
            "own_freighter": Mock(return_value=True),
            "query_star": Mock(side_effect=query),
            "star_distance": Mock(return_value=50.0),
            "classify_star": Mock(),
            "warp_check": Mock(side_effect=capability),
            "select_star": Mock(side_effect=select),
            "warp_candidate": Mock(return_value=True),
        }
        return engine, map_object, selected, memory

    def ready_map(self, engine, context, memory):
        self.assertFalse(engine.begin(context))
        self.set_clock(engine, context, memory, 5)
        self.assertTrue(engine.begin(context))

    def set_clock(self, engine, context, memory, value):
        memory[context + engine.layout["map_clock"]] = struct.pack("<f", value)

    def test_capability_rejection_cannot_select_or_dispatch(self):
        engine, map_object, _, memory = self.make_engine(5)
        self.ready_map(engine, map_object, memory)
        self.assertFalse(engine.choose(map_object))
        engine.functions["select_star"].assert_not_called()
        engine.functions["warp_candidate"].assert_not_called()

    def test_selection_change_prevents_dispatch_and_valid_target_uses_check_then_action(self):
        engine, map_object, selected, memory = self.make_engine(1)
        self.ready_map(engine, map_object, memory)
        self.assertTrue(engine.choose(map_object))
        self.set_clock(engine, map_object, memory, 6)
        key = selected + engine.layout["selected_query"]
        expected = memory[key]
        memory[key] = struct.pack("<Q", engine.source)
        with self.assertRaisesRegex(NativePreconditionError, "selected_target_changed"):
            engine.dispatch(map_object)
        engine.functions["warp_candidate"].assert_not_called()
        memory[key] = expected
        self.assertTrue(engine.dispatch(map_object))
        self.assertEqual(
            [c.args for c in engine.functions["warp_candidate"].call_args_list],
            [(map_object, True), (map_object, False)],
        )

    def test_new_map_and_selection_transitions_cannot_dispatch_early(self):
        engine, context, _, memory = self.make_engine(1)
        self.assertFalse(engine.begin(context))
        self.set_clock(engine, context, memory, 0.063)
        self.assertIsNone(engine.choose(context))
        engine.functions["query_star"].assert_not_called()
        self.set_clock(engine, context, memory, 5)
        self.assertTrue(engine.begin(context))
        self.assertTrue(engine.choose(context))
        self.assertFalse(engine.selection_ready(context))
        self.set_clock(engine, context, memory, 6)
        for state in (0, 1, 2, 4):
            memory[context + engine.layout["map_popup_state"]] = struct.pack("<I", state)
            self.assertFalse(engine.selection_ready(context))
            with self.assertRaisesRegex(NativePreconditionError, "map_not_ready_at_dispatch"):
                engine.dispatch(context)
        memory[context + engine.layout["map_popup_state"]] = struct.pack("<I", 3)
        memory[context + engine.layout["map_popup_selection"]] = b"\0"
        self.assertFalse(engine.selection_ready(context))
        engine.functions["warp_candidate"].assert_not_called()
        memory[context + engine.layout["map_popup_selection"]] = b"\1"
        self.assertTrue(engine.dispatch(context))

    def test_elapsed_time_alone_cannot_bypass_map_transition_or_cache(self):
        engine, context, _, memory = self.make_engine(1)
        self.assertFalse(engine.begin(context))
        self.set_clock(engine, context, memory, 20)
        memory[context + engine.layout["map_mode"]] = struct.pack("<I", 1)
        self.assertFalse(engine.begin(context))
        memory[context + engine.layout["map_mode"]] = bytes(4)
        memory[context + engine.layout["map_cache"] + engine.layout["cache_busy"]] = b"\1\0"
        self.assertFalse(engine.begin(context))
        self.assertIsNone(engine.choose(context))
        engine.functions["query_star"].assert_not_called()

    def test_pending_warp_clock_reset_and_stale_map_fail_without_actions(self):
        for case in ("pending", "reset", "stale", "nan"):
            with self.subTest(case=case):
                engine, context, _, memory = self.make_engine(1)
                self.ready_map(engine, context, memory)
                data = 0x20000
                if case == "pending":
                    memory[data + engine.layout["warp_request"]] = struct.pack("<I", 3)
                elif case == "stale":
                    memory[data + engine.layout["application_map_data"]] = struct.pack("<Q", 9)
                else:
                    self.set_clock(engine, context, memory, 0 if case == "reset" else float("nan"))
                with self.assertRaises(NativePreconditionError):
                    engine.choose(context)
                engine.functions["query_star"].assert_not_called()
                engine.functions["warp_candidate"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
