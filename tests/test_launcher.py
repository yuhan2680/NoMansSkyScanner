import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nms_scanner import launcher
from nms_scanner.star_filter import StarFilter


class LauncherFailureTests(unittest.TestCase):
    def test_loop_mode_and_limits_reach_injected_configuration(self):
        profile = launcher.load_profile(Path(__file__).resolve().parent.parent, exploration=True)
        process = SimpleNamespace(
            pid=1234,
            info={"exe": str(Path("C:/test/NMS.exe"))},
            memory_maps=lambda: [],
            is_running=lambda: True,
            exe=lambda: str(Path("C:/test/NMS.exe")),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(launcher, "__file__", str(root / "nms_scanner/launcher.py")),
                patch(
                    "sys.argv",
                    [
                        "launcher",
                        "--loop",
                        "--max-warps",
                        "2",
                        "--max-runtime-seconds",
                        "180",
                        "--star-filter-enabled",
                        "--star-letters",
                        "O",
                        "X",
                        "--system-types",
                        "pirate",
                        "--star-digits",
                        "6",
                        "--star-suffixes",
                        "none",
                        "--system-tags",
                        "water",
                        "--system-races",
                        "gek",
                    ],
                ),
                patch.object(launcher, "running_game", return_value=process),
                patch.object(launcher, "load_profile", return_value=profile),
                patch.object(launcher, "validate", return_value={}),
                patch.object(launcher, "version", return_value="0.2.4"),
                patch.object(launcher, "entry_points", return_value=[]),
                patch.object(launcher, "executor_port_available", return_value=True),
                patch.object(launcher, "ConsoleStatus"),
                patch.dict("sys.modules", {"pymhf": SimpleNamespace(run_module=Mock())}),
                patch("nms_scanner.attach_framework.attach_entrypoint") as attach,
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(launcher.main(), 0)
            attach.assert_called_once()
            config = attach.return_value.call_args.args[1]
            self.assertEqual(config["run_mode"], "loop")
            self.assertEqual(config["run_limits"], {"max_warps": 2, "max_runtime_seconds": 180})
            self.assertEqual(config["expected_pid"], 1234)
            self.assertEqual(
                config["star_filter"],
                StarFilter(
                    True, ("O", "X"), ("pirate",), ("6",), ("none",), ("water",), ("gek",)
                ).as_dict(),
            )
            self.assertEqual(profile["single_trial"]["max_candidate_attempts"], 256)

    def test_duplicate_runtime_is_logged_without_second_attach(self):
        process = SimpleNamespace(
            pid=1234,
            info={"exe": "C:/test/NMS.exe"},
            memory_maps=Mock(return_value=[SimpleNamespace(path="C:/test/python312.dll")]),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(launcher, "__file__", str(root / "nms_scanner/launcher.py")),
                patch("sys.argv", ["launcher", "--single"]),
                patch.object(launcher, "running_game", return_value=process),
                patch.object(launcher, "load_profile", return_value={}),
                patch.object(launcher, "validate", return_value={}),
                patch.object(launcher, "version", return_value="0.2.4"),
                patch.object(launcher, "entry_points", return_value=[]),
                patch("nms_scanner.attach_framework.attach_entrypoint") as attach,
                patch("sys.stderr", new_callable=io.StringIO) as error_output,
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(launcher.main(), 1)
                attach.assert_not_called()
            error = json.loads((root / "logs/launcher-errors.jsonl").read_text(encoding="utf-8"))
            self.assertEqual((error["phase"], error["pid"]), ("runtime_preflight", 1234))
            self.assertEqual(error["run_mode"], "single")
            self.assertIn("已阻止重复加载", error["message"])
            self.assertIn("完全退出游戏到桌面", error_output.getvalue())

    def test_occupied_executor_port_stops_before_attach(self):
        process = SimpleNamespace(
            pid=1234,
            info={"exe": "C:/test/NMS.exe"},
            memory_maps=Mock(return_value=[]),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(launcher, "__file__", str(root / "nms_scanner/launcher.py")),
                patch("sys.argv", ["launcher", "--single"]),
                patch.object(launcher, "running_game", return_value=process),
                patch.object(launcher, "load_profile", return_value={}),
                patch.object(launcher, "validate", return_value={}),
                patch.object(launcher, "version", return_value="0.2.4"),
                patch.object(launcher, "entry_points", return_value=[]),
                patch.object(launcher, "executor_port_available", return_value=False),
                patch("nms_scanner.attach_framework.attach_entrypoint") as attach,
                patch("sys.stderr", new_callable=io.StringIO),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(launcher.main(), 1)
                attach.assert_not_called()
            error = json.loads((root / "logs/launcher-errors.jsonl").read_text(encoding="utf-8"))
            self.assertIn("6770", error["message"])

    def test_early_process_access_error_is_preserved_before_framework_initializes(self):
        import psutil

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(launcher, "__file__", str(root / "nms_scanner/launcher.py")),
                patch("sys.argv", ["launcher", "--single"]),
                patch.object(launcher, "running_game", side_effect=psutil.AccessDenied(1234)),
                patch.object(launcher, "validate") as validate,
                patch("sys.stderr", new_callable=io.StringIO),
            ):
                self.assertEqual(launcher.main(), 1)
                validate.assert_not_called()
            error = json.loads((root / "logs/launcher-errors.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(error["phase"], "locate_game")
            self.assertEqual(error["exception_type"], "AccessDenied")
            self.assertIn("1234", error["message"])


if __name__ == "__main__":
    unittest.main()
