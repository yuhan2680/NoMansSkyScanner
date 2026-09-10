import ctypes
import hashlib
import importlib.util
import os
import tempfile
import threading
import types
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import Mock, patch

from nms_scanner.compatibility import validate
from nms_scanner.observation import Observation
from tools.audit_native import compile_signature, locate


class SignatureTests(unittest.TestCase):
    def test_wildcard_matches_newline_and_metacharacters_are_literal(self):
        self.assertEqual(locate("2E ? 5B", [(100, b"x.\n[y.\x00[")]), [101, 105])

    def test_overlapping_matches_are_ambiguous(self):
        self.assertEqual(locate("41 41", [(4096, b"AAA")]), [4096, 4097])

    def test_invalid_signatures_rejected(self):
        for signature in ["", "? ??", "4G", "A", "48 **"]:
            with self.subTest(signature=signature), self.assertRaises(ValueError):
                compile_signature(signature)

    def test_matches_do_not_cross_sections(self):
        self.assertEqual(locate("41 42", [(10, b"A"), (11, b"B")]), [])


class CompatibilityTests(unittest.TestCase):
    def test_modified_executable_refused_before_loading_pe(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "NMS.exe"
            exe.write_bytes(b"updated game")
            with patch("nms_scanner.compatibility.pefile.PE") as parse:
                with self.assertRaisesRegex(ValueError, "游戏版本"):
                    validate(exe, {"exe_sha256": "old"})
                parse.assert_not_called()

    def test_data_matches_ignored_but_duplicate_executable_matches_rejected(self):
        def section(rva, executable):
            return types.SimpleNamespace(
                VirtualAddress=rva,
                SizeOfRawData=2,
                Misc_VirtualSize=2,
                Characteristics=0x20000000 if executable else 0,
                get_data=lambda: b"AB",
            )

        pe = types.SimpleNamespace(
            FILE_HEADER=types.SimpleNamespace(Machine=0x8664),
            OPTIONAL_HEADER=types.SimpleNamespace(Magic=0x20B),
            sections=[section(4096, True), section(8192, False)],
            close=Mock(),
        )
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "NMS.exe"
            exe.write_bytes(b"test")
            profile = {
                "exe_sha256": hashlib.sha256(b"test").hexdigest(),
                "hooks": {"test": {"signature": "41 42", "rva": "0x1000"}},
            }
            with patch("nms_scanner.compatibility.pefile.PE", return_value=pe):
                self.assertEqual(validate(exe, profile), {"test": 4096})
                pe.sections.append(section(12288, True))
                with self.assertRaisesRegex(ValueError, "不唯一"):
                    validate(exe, profile)


class ControlTests(unittest.TestCase):
    def test_focus_is_captured_with_event_before_delayed_writer_and_not_per_frame(self):
        focus = Mock(return_value=True)
        state = Observation(focus_reader=focus)
        state.command("start")
        before = focus.call_count
        for _ in range(20):
            state.record("frame", sampled=True)
        self.assertEqual(focus.call_count, before)
        focus.return_value = False
        state.record("warp_dispatch_candidate_enter")
        focus.return_value = True  # The user switches back before the log writer drains.
        events = state.drain()
        self.assertTrue(events[0]["game_foreground"])
        self.assertFalse(events[-1]["game_foreground"])

    def test_focus_read_failure_stays_unknown_and_does_not_break_stop(self):
        state = Observation(focus_reader=Mock(side_effect=OSError("unavailable")))
        state.command("start")
        state.command("stop")
        self.assertTrue(state.stopped)
        self.assertTrue(all(event["game_foreground"] is None for event in state.drain()))

    def test_pause_and_stop_prevent_events_and_f1_cannot_reverse_stop(self):
        state = Observation()
        state.command("start")
        state.record("scan")
        state.command("pause")
        state.record("scan")
        state.command("pause")
        state.record("scan")
        state.command("stop")
        state.command("start")
        state.command("pause")
        state.record("scan")
        self.assertEqual(state.snapshot()["counts"], {"scan": 2})
        self.assertTrue(state.snapshot()["stopped"])

    def test_concurrent_producer_cannot_record_after_stop(self):
        state = Observation()
        state.command("start")
        barrier = threading.Barrier(2)

        def producer():
            barrier.wait()
            for _ in range(1000):
                state.record("frame", sampled=True)

        thread = threading.Thread(target=producer)
        thread.start()
        state.command("stop")
        barrier.wait()
        thread.join()
        self.assertEqual(state.snapshot()["counts"], {})

    def test_bounded_queue_reports_drops(self):
        state = Observation(capacity=3)
        state.command("start")
        for _ in range(20):
            state.record("event")
        self.assertEqual(len(state.drain()), 3)
        self.assertGreater(state.snapshot()["dropped"], 0)


class CallbackTests(unittest.TestCase):
    def test_callbacks_do_not_override_arguments_or_return_values(self):
        """Load observer with inert framework/Win32 substitutes; no process is touched."""
        hooks = {}

        def manual_hook(name, **kwargs):
            hooks[name] = kwargs
            return lambda function: function

        profile = {
            "hooks": {},
            "interaction_data_offset": 48,
            "interaction_type_offset": 544,
            "scanner_type": 35,
            "image_size": 0x100000,
            "warp_result_status_offset": 8,
        }
        names = [
            "application_update",
            "simulation_update",
            "state_change",
            "interaction",
            "discovery",
            "warp_check",
            "warp_candidate",
        ]
        offsets = dict.fromkeys(names, 4096)
        profile["hooks"] = {name: {"signature": "41 42"} for name in names}
        substitutes = {
            "pymhf": types.SimpleNamespace(
                Mod=object, FUNCDEF=namedtuple("FUNCDEF", "restype argtypes")
            ),
            "pymhf.core": types.SimpleNamespace(
                _internal=types.SimpleNamespace(
                    IS_INJECTED=True, CONFIG={"expected_pid": os.getpid()}
                )
            ),
            "pymhf.core.hooking": types.SimpleNamespace(manual_hook=manual_hook),
            "nms_scanner.compatibility": types.SimpleNamespace(
                load_profile=lambda _, **kwargs: profile,
                validate=lambda *args: offsets,
                apply_run_limits=lambda config, limits: {**config, **limits},
            ),
            "nms_scanner.native_windows": types.SimpleNamespace(
                K=types.SimpleNamespace(GetModuleHandleW=lambda _: 0x100000),
                current_exe=lambda: Path("NMS.exe"),
                read_own=lambda *args: b"AB",
                Hotkeys=Mock(),
                foreground_pid=lambda: 0,
                game_callstack=lambda *args: [],
            ),
        }
        path = Path(__file__).resolve().parent.parent / "native/observer.py"
        with patch.dict("sys.modules", substitutes):
            spec = importlib.util.spec_from_file_location("test_observer", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        # Avoid __init__: it would start the real worker. Only exercise pure callback behavior.
        observer = module.NativeObserver.__new__(module.NativeObserver)
        observer.single = None
        observer.telemetry = Observation()
        observer.telemetry.command("start")
        for function, args in [
            (observer.application_update, (123,)),
            (observer.simulation_update, (123, 0.1)),
            (observer.discovery, (123, 456, 0, True)),
            (observer.warp_check, (123, 456, 100.0, False, 789, 0)),
            (observer.warp_candidate_before, (123, True)),
            (observer.warp_candidate_before, (123, False)),
            (observer.warp_candidate_after, (123, False, True)),
            (observer.warp_candidate_after, (123, False, False)),
        ]:
            self.assertIsNone(function(*args))
        self.assertIs(hooks["NMSScannerObserve_discovery"]["func_def"].restype, ctypes.c_bool)
        self.assertIs(hooks["NMSScannerObserve_warp_candidate"]["func_def"].restype, ctypes.c_bool)
        module.read_own = Mock(return_value=(1).to_bytes(4, "little"))
        self.assertIsNone(observer.warp_check(123, 456, 100.0, False, 789, 0))
        module.read_own.assert_called_once_with(464, 4)
        self.assertEqual(observer.telemetry.snapshot()["counts"]["warp_capability_status_1"], 1)
        module.read_own = Mock(
            side_effect=[(4096).to_bytes(8, "little"), (35).to_bytes(4, "little")]
        )
        self.assertIsNone(observer.interaction(123))
        self.assertEqual(observer.telemetry.snapshot()["counts"]["scanner_action"], 1)
        module.read_own = Mock(side_effect=OSError("invalid pointer"))
        self.assertIsNone(observer.warp_check(123, 456, 100.0, False, 789, 0))
        self.assertEqual(observer.telemetry.snapshot()["counts"]["warp_result_unreadable"], 1)
        self.assertIsNone(observer.interaction(123))
        self.assertEqual(observer.telemetry.snapshot()["counts"]["interaction_unreadable"], 1)
        observer.telemetry.command("stop")
        module.read_own = Mock()
        observer.interaction(123)
        observer.warp_check(123, 456, 100.0, False, 789, 0)
        module.read_own.assert_not_called()
        # Regression: optimized Application::Update passes no usable this pointer.
        # An idle/null RCX must not erase the independently resolved singleton.
        application = 0x146B44DA0
        observer.single = types.SimpleNamespace(
            engine=types.SimpleNamespace(application=application),
            state_changed=Mock(),
            command=Mock(),
        )
        observer.application_update(None)
        observer.application_update(123)
        self.assertEqual(observer.single.engine.application, application)
        module.read_own = Mock(return_value=b"APPLOCALLOAD\0\0\0\0")
        observer.state_change_after(application, 456, None, False)
        observer.single.state_changed.assert_called_once_with("APPLOCALLOAD")
        module.read_own.reset_mock()
        observer.state_change_after(123, 456, None, False)
        module.read_own.assert_not_called()


if __name__ == "__main__":
    unittest.main()
