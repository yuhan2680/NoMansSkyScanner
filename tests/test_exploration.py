import ctypes
import struct
import unittest
from pathlib import Path
from unittest.mock import Mock

from nms_scanner.compatibility import apply_run_limits, load_profile, validate_exploration
from nms_scanner.exploration_run import ExplorationRun
from nms_scanner.native_single import NativeSingleEngine
from nms_scanner.single_run import ActionInterrupted, NativePreconditionError

CONFIG = {
    "schema_version": 1,
    "mode": "experimental_exploration",
    "max_warps": 2,
    "max_runtime_seconds": 0,
    "cycle_interval_seconds": 5,
}


class FakeLoopEngine:
    def scan_ready(self, context):
        return True

    def __init__(self):
        self.calls = []
        self.cycle = 0
        self.map_ready = True
        self.map_wait_reason = "warp_transition_active"
        self.choose_result = True
        self.checkpoint = lambda: None
        self.map_status = {}

    def reset_cycle(self):
        self.cycle += 1
        self.calls.append(("reset", self.cycle))

    def request_map(self):
        self.checkpoint()
        self.calls.append(("map_request", self.cycle))
        return self.map_ready

    def begin(self, context):
        self.calls.append(("begin", context))
        return True

    def selection_matches(self, context):
        return True

    def selection_ready(self, context):
        return True

    def choose(self, context):
        return self.choose_result

    def dispatch(self, context):
        self.checkpoint()
        self.calls.append(("warp", context))
        return True

    def begin_scan(self, context):
        return self.cycle + 1  # Different counts across two systems expose stale scan indices.

    def scan_planet(self, context, index):
        self.checkpoint()
        self.calls.append(("scan", self.cycle, index))


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.events = []
        self.engine = FakeLoopEngine()
        self.run = ExplorationRun(
            self.engine,
            {"phase_timeout_seconds": 90, "max_candidate_attempts": 3},
            CONFIG,
            lambda event, **data: self.events.append((event, data)),
            lambda: self.now,
        )

    def start(self):
        self.run.command("start")
        self.run.poll()

    def open_map(self):
        self.now += 5
        self.run.poll("application")
        self.assertEqual(self.run.stage, "wait_map")

    def warp_and_scan(self):
        self.run.state_changed("GALAXYMAP")
        self.run.poll("map", self.engine.cycle)
        self.run.poll("map", self.engine.cycle)
        self.run.state_changed("APPVIEW")
        self.run.state_changed("APPLOCALLOAD")
        self.run.state_changed("APPVIEW")
        for _ in range(self.engine.cycle + 1):
            self.run.poll("world", self.engine.cycle)

    def test_two_cycles_open_new_maps_scan_every_planet_and_stop_at_limit(self):
        self.start()
        for _ in range(2):
            self.open_map()
            self.warp_and_scan()
        self.assertEqual((self.run.stage, self.run.warps, self.run.scans), ("complete", 2, 2))
        self.assertEqual(
            [c for c in self.engine.calls if c[0] == "scan"],
            [("scan", 1, 0), ("scan", 1, 1), ("scan", 2, 0), ("scan", 2, 1), ("scan", 2, 2)],
        )
        self.assertEqual(
            [c for c in self.engine.calls if c[0] == "map_request"],
            [("map_request", 1), ("map_request", 2)],
        )
        before = list(self.engine.calls)
        self.run.command("start")
        self.now += 100
        self.run.poll("application")
        self.assertEqual(self.engine.calls, before)
        self.assertEqual(self.events[-1][1]["reason"], "max_warps")

    def test_pause_freezes_inter_cycle_delay_and_f3_prevents_second_cycle(self):
        self.start()
        self.open_map()
        self.warp_and_scan()
        self.run.command("pause")
        self.now += 100
        self.run.poll("application")
        self.assertEqual(self.run.cycle_delay, 5)
        self.run.command("stop")
        self.run.command("pause")
        self.run.command("start")
        self.run.poll("application")
        self.assertEqual(self.run.stage, "stopped")
        self.assertEqual(self.engine.cycle, 1)
        self.assertEqual(self.events[-1][1], {"reason": "stop_requested", "warps": 1, "scans": 1})

    def test_runtime_limit_includes_pause_and_does_not_need_game_callbacks(self):
        self.run.run_config["max_runtime_seconds"] = 10
        self.start()
        self.run.command("pause")
        self.now = 10
        self.run.poll()  # Watchdog only; the game could be frozen.
        self.assertEqual(self.run.stage, "complete")
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.events[-1][1]["reason"], "max_runtime_seconds")

    def test_deadline_checkpoint_blocks_a_native_call_between_polls(self):
        self.run.run_config["max_runtime_seconds"] = 10
        self.start()
        self.now = 10
        with self.assertRaises(ActionInterrupted):
            self.run.checkpoint()
        self.run.poll()
        self.assertEqual(self.run.stage, "complete")

    def test_map_request_only_runs_on_application_callback_and_busy_wait_times_out(self):
        self.engine.map_ready = False
        self.start()
        self.now = 5
        self.run.poll("world", 1)
        self.run.poll("map", 1)
        self.assertEqual(self.engine.calls, [("reset", 1)])
        self.run.poll("application")
        self.assertEqual(self.run.stage, "wait_ready")
        self.now += 91
        self.run.poll()
        self.assertEqual(self.run.stage, "failed")
        self.assertEqual(self.events[-1][1]["reason"], "timeout_wait_ready")
        self.assertFalse(any(c[0] == "warp" for c in self.engine.calls))

    def test_second_cycle_failure_keeps_first_result_and_does_not_restart(self):
        self.start()
        self.open_map()
        self.warp_and_scan()
        self.engine.choose_result = False
        self.open_map()
        for _ in range(3):
            self.run.poll("map", 2)
        self.assertEqual((self.run.stage, self.run.warps, self.run.scans), ("failed", 1, 1))
        self.assertEqual(self.run.attempts, 3)  # Per-cycle limit, not prior attempt + 2.
        before = list(self.engine.calls)
        self.run.command("start")
        self.run.poll("application")
        self.assertEqual(self.engine.calls, before)

    def test_unbounded_mode_still_stops_on_f3(self):
        self.run.run_config["max_warps"] = 0
        self.start()
        for _ in range(3):
            self.open_map()
            self.warp_and_scan()
        self.assertEqual(self.run.stage, "between_cycles")
        self.assertEqual((self.run.warps, self.run.scans), (3, 3))
        self.run.command("stop")
        self.run.poll()
        self.assertEqual(self.run.stage, "stopped")


class NativeMapTests(unittest.TestCase):
    def make_engine(self):
        """All addresses below are dictionary keys; only our ID buffer is dereferenced."""
        root = Path(__file__).resolve().parent.parent
        profile = load_profile(root, exploration=True)
        engine = NativeSingleEngine.__new__(NativeSingleEngine)
        engine.profile, engine.layout = profile, profile["single_trial"]["layout"]
        layout = profile["exploration"]["layout"]
        engine.base, engine.application = 0x140000000, 0x146B44DA0
        engine.expected_system, engine.map_wait_reason = None, None
        engine.checkpoint = Mock()
        engine.stack = lambda: [layout["application_update_caller"]]
        data, current = 0x200000000, 0x146B45000
        env = data + engine.layout["player_environment"]
        pending = engine.application + layout["fsm_pending_state"]
        empty = b"FSM_NOSTATE".ljust(16, b"\0")
        memory = {
            engine.application + engine.layout["application_data"]: struct.pack("<Q", data),
            engine.application + engine.layout["fsm_current_state"]: struct.pack("<Q", current),
            current + engine.layout["fsm_state_name"]: b"APPVIEW".ljust(16, b"\0"),
            current + layout["state_owner"]: struct.pack("<Q", engine.application),
            engine.application + layout["application_paused"]: b"\0",
            env + layout["environment_location"]: struct.pack("<I", 10),
            env + layout["environment_stable_location"]: struct.pack("<I", 10),
            data + layout["warp_request"]: bytes(4),
            data + layout["warp_transition"]: bytes(4),
            data + engine.layout["simulation"] + engine.layout["current_ua"]: struct.pack("<Q", 7),
            engine.base + int(layout["empty_state_rva"], 16): empty,
            pending: empty,
        }
        engine.read = lambda address, size: memory[address][:size]

        def queue(state, name, user_data, force):
            self.assertEqual(state, current)
            self.assertIsNone(user_data)
            self.assertFalse(force)
            memory[pending] = ctypes.string_at(name, 16)

        engine.functions = {
            "own_freighter": Mock(return_value=True),
            "queue_map_state": Mock(side_effect=queue),
        }
        return engine, memory, data, current, pending

    def test_request_uses_live_sentinel_and_copies_only_galaxy_map_id(self):
        engine, memory, _, _, pending = self.make_engine()
        self.assertTrue(engine.request_map())
        self.assertEqual(memory[pending], b"GALAXYMAP".ljust(16, b"\0"))
        engine.functions["queue_map_state"].assert_called_once()
        # A second call may not overwrite the game's pending request.
        self.assertFalse(engine.request_map())
        engine.functions["queue_map_state"].assert_called_once()

    def test_paused_or_warping_game_never_receives_map_request(self):
        for field, size, reason in (
            ("application_paused", 1, "game_paused"),
            ("warp_request", 4, "warp_request_pending"),
            ("warp_transition", 4, "warp_transition_active"),
        ):
            engine, memory, data, _, _ = self.make_engine()
            layout = engine.profile["exploration"]["layout"]
            owner = engine.application if field == "application_paused" else data
            memory[owner + layout[field]] = (1).to_bytes(size, "little")
            self.assertFalse(engine.request_map())
            self.assertEqual(engine.map_wait_reason, reason)
            engine.functions["queue_map_state"].assert_not_called()

    def test_foreign_state_owner_and_changed_system_fail_before_queue(self):
        for case in ("owner", "system", "not_inside", "wrong_thread"):
            engine, memory, data, current, _ = self.make_engine()
            layout = engine.profile["exploration"]["layout"]
            if case == "owner":
                memory[current + layout["state_owner"]] = struct.pack("<Q", 42)
            elif case == "system":
                engine.expected_system = 8
            elif case == "not_inside":
                env = data + engine.layout["player_environment"]
                memory[env + layout["environment_stable_location"]] = struct.pack("<I", 9)
            else:
                engine.stack = lambda: []
            with self.assertRaises(NativePreconditionError):
                engine.request_map()
            engine.functions["queue_map_state"].assert_not_called()

    def test_f3_after_read_only_check_blocks_queue_call(self):
        engine, _, _, _, _ = self.make_engine()
        engine.checkpoint.side_effect = [None, ActionInterrupted()]
        with self.assertRaises(ActionInterrupted):
            engine.request_map()
        engine.functions["own_freighter"].assert_called_once()
        engine.functions["queue_map_state"].assert_not_called()

    def test_next_cycle_clears_object_handles_but_preserves_expected_location(self):
        engine, _, _, _, _ = self.make_engine()
        engine.target, engine.source, engine.solar, engine.map_object = 7, 6, 100, 200
        engine.planet_count = 6
        engine.map_first_clock = engine.map_last_clock = engine.selection_clock = 100
        engine.reset_cycle()
        self.assertEqual(engine.expected_system, 7)
        self.assertEqual(
            (engine.target, engine.source, engine.solar, engine.map_object),
            (None, None, None, None),
        )
        self.assertEqual(
            (engine.map_first_clock, engine.map_last_clock, engine.selection_clock),
            (None, None, None),
        )
        self.assertTrue(engine.request_map())


class RunLimitTests(unittest.TestCase):
    def test_invalid_limits_and_nonfinite_delays_are_rejected(self):
        for key, value in (
            ("max_warps", -1),
            ("max_warps", True),
            ("max_warps", 1.5),
            ("max_runtime_seconds", float("nan")),
            ("max_runtime_seconds", -1),
            ("cycle_interval_seconds", 0),
            ("cycle_interval_seconds", float("inf")),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_exploration({**CONFIG, key: value})
        with self.assertRaises(ValueError):
            apply_run_limits(CONFIG, {"calls": {}})
        self.assertEqual(apply_run_limits(CONFIG, {"max_warps": 1})["max_warps"], 1)
        self.assertEqual(CONFIG["max_warps"], 2)


if __name__ == "__main__":
    unittest.main()
