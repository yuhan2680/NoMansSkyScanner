import argparse
import copy
import ctypes
import json
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_single_run as fixtures

from nms_scanner.compatibility import load_profile
from nms_scanner.console_status import status_message
from nms_scanner.gui import ExplorerApp
from nms_scanner.single_run import NativePreconditionError, SingleRun
from nms_scanner.star_filter import (
    COLORS,
    GROUPS,
    RACE_IDS,
    StarFilter,
    add_filter_arguments,
    apply_star_filter,
    classify_attributes,
    map_tag,
    selection_from_args,
    spectrum,
    validate_definition,
)

ROOT = Path(__file__).resolve().parent.parent
DEFINITION = json.loads((ROOT / "native/star_filter.json").read_text(encoding="utf-8"))


def attributes(star=0, race=0, abandoned=0, pirate=0):
    raw = bytearray(48)
    struct.pack_into("<II", raw, 12, race, star)
    raw[36], raw[37] = abandoned, pirate
    return bytes(raw)


class StarFilterTests(unittest.TestCase):
    def test_default_off_and_disabled_cli_has_no_filter_arguments(self):
        selection = StarFilter()
        self.assertFalse(selection.enabled)
        self.assertTrue(selection.matches({}))
        self.assertEqual(selection.arguments(), [])
        profile = load_profile(ROOT, exploration=True)
        apply_star_filter(profile, selection.as_dict())
        self.assertEqual(profile["single_trial"]["max_candidate_attempts"], 64)

    def test_or_within_each_group_and_between_groups(self):
        selection = StarFilter(
            True,
            ("F", "B"),
            ("pirate", "abandoned"),
            ("0", "9"),
            ("none", "pf"),
            ("water",),
            ("gek",),
        )
        good = dict(letter="F", digit="9", suffix="pf", category="pirate", tag="water", race="gek")
        self.assertTrue(selection.matches(good))
        for key, value in dict(
            letter="G", digit="5", suffix="p", category="normal", tag="none", race="korvax"
        ).items():
            self.assertFalse(selection.matches({**good, key: value}))
        self.assertTrue(selection.matches({**good, "tag": "none"}, include_tag=False))

    def test_empty_group_rejected_when_enabled_and_ignored_when_disabled(self):
        for field in GROUPS:
            data = StarFilter(True, categories=("normal",)).as_dict()
            data[field] = []
            with self.assertRaisesRegex(ValueError, "至少勾选"):
                StarFilter.from_dict(data)
            data["enabled"] = False
            self.assertEqual(StarFilter.from_dict(data).arguments(), [])

    def test_unknown_duplicate_and_malformed_options_rejected(self):
        for field, value in (
            ("enabled", 1),
            ("letters", "B"),
            ("letters", ["Z"]),
            ("letters", ["B", "B"]),
            ("categories", [False]),
        ):
            data = StarFilter(True).as_dict()
            data[field] = value
            with self.assertRaises(ValueError):
                StarFilter.from_dict(data)

    def test_ui_arguments_round_trip_into_launcher_selection(self):
        parser = argparse.ArgumentParser()
        add_filter_arguments(parser)
        selection = StarFilter(True, ("M", "X"), ("abandoned", "empty"))
        self.assertEqual(selection_from_args(parser.parse_args(selection.arguments())), selection)
        self.assertFalse(selection_from_args(parser.parse_args([])).enabled)
        with self.assertRaises(ValueError):
            selection_from_args(parser.parse_args(["--star-letters", "M"]))

    def test_empty_ui_selection_blocks_before_process_lookup(self):
        app = ExplorerApp.__new__(ExplorerApp)
        app.filter_enabled = SimpleNamespace(get=lambda: True)
        app.filter_groups = {
            key: {
                option: SimpleNamespace(get=lambda key=key: key != "categories")
                for option in labels
            }
            for key, (labels, _) in GROUPS.items()
        }
        app._parse_limits = Mock(return_value=(1, 180))
        app.state = SimpleNamespace()
        app._render, app._append_log = Mock(), Mock()
        with patch("nms_scanner.gui.running_game") as find_game:
            app._connect_and_start()
            find_game.assert_not_called()
        self.assertIn("星系类别至少勾选", app.last_message)

    def test_color_enum_and_four_categories(self):
        for number, color in ((0, "yellow"), (1, "green"), (2, "blue"), (3, "red"), (4, "purple")):
            for category, race, abandoned, pirate in (
                ("normal", 0, 0, 0),
                ("pirate", 1, 0, 1),
                ("abandoned", 2, 1, 0),
                ("empty", 7, 0, 0),
            ):
                with self.subTest(color=color, category=category):
                    self.assertEqual(
                        classify_attributes(
                            attributes(number, race, abandoned, pirate), DEFINITION["layout"]
                        ),
                        (color, category, RACE_IDS[race]),
                    )

    def test_empty_takes_precedence_over_retained_abandoned_flag(self):
        self.assertEqual(
            classify_attributes(attributes(race=7, abandoned=1), DEFINITION["layout"]),
            ("yellow", "empty", "none"),
        )

    def test_unknown_or_conflicting_classification_never_counts_as_normal(self):
        for raw in (
            bytes(47),
            attributes(star=5),
            attributes(race=8),
            attributes(abandoned=2),
            attributes(pirate=2),
            attributes(abandoned=1, pirate=1),
            attributes(race=7, pirate=1),
        ):
            with self.assertRaises(NativePreconditionError):
                classify_attributes(raw, DEFINITION["layout"])

    def test_definition_bounds_and_default_protected(self):
        self.assertEqual(validate_definition(DEFINITION), DEFINITION)
        for key, value in (
            ("star_type_offset", 47),
            ("race_offset", 16),
            ("attributes_size", 4096),
            ("empty_race", 8),
        ):
            config = copy.deepcopy(DEFINITION)
            config["layout"][key] = value
            with self.assertRaises(ValueError):
                validate_definition(config)
        for key, value in (("default_enabled", True), ("max_candidate_attempts", 0)):
            with self.assertRaises(ValueError):
                validate_definition({**DEFINITION, key: value})

    def test_no_suffix_and_no_map_tag_are_independent(self):
        selection = StarFilter(True, suffixes=("none",), tags=("none",))
        details = dict(
            letter="F", digit="0", suffix="none", tag="none", category="normal", race="gek"
        )
        self.assertTrue(selection.matches(details))
        self.assertFalse(selection.matches({**details, "suffix": "p"}))
        self.assertFalse(selection.matches({**details, "tag": "water"}))

    def test_empty_and_populated_categories_preserve_race_selection_through_cli(self):
        selection = StarFilter(True, categories=("empty", "normal"), races=("gek", "none"))
        self.assertEqual(selection.races, ("gek", "none"))
        self.assertEqual(StarFilter.from_dict(selection.as_dict()), selection)
        parser = argparse.ArgumentParser()
        add_filter_arguments(parser)
        self.assertEqual(selection_from_args(parser.parse_args(selection.arguments())), selection)

    def test_mixed_races_match_both_uninhabited_and_selected_populated_systems(self):
        selection = StarFilter(True, categories=("empty", "normal"), races=("gek", "none"))
        details = dict(letter="F", digit="0", suffix="none", tag="none")
        for category, race, expected in (
            ("normal", "gek", True),
            ("empty", "none", True),
            ("normal", "korvax", False),
            ("pirate", "gek", False),
        ):
            self.assertEqual(
                selection.matches({**details, "category": category, "race": race}), expected
            )
        # Merely including empty in categories must not disable the selected race restriction.
        selection = StarFilter(True, categories=("empty", "normal"), races=("gek",))
        self.assertFalse(selection.matches({**details, "category": "empty", "race": "none"}))

    def test_none_race_can_be_selected_alone_without_bypassing_category(self):
        selection = StarFilter(True, categories=("empty",), races=("none",))
        self.assertEqual(StarFilter.from_dict(selection.as_dict()), selection)
        details = dict(letter="M", digit="3", suffix="f", tag="none", category="empty", race="none")
        self.assertTrue(selection.matches(details))
        self.assertFalse(StarFilter(True, categories=("normal",)).matches(details))

    def test_race_group_must_not_be_empty_even_when_empty_category_selected(self):
        selection = StarFilter(True, categories=("empty", "pirate"), races=())
        with self.assertRaisesRegex(ValueError, "主导种族至少勾选"):
            StarFilter.from_dict(selection.as_dict())

    def test_map_label_priority_and_invalid_data(self):
        for flags, expected in (
            (b"\0\0\0\0", "none"),
            (b"\1\0\0\0", "water"),
            (b"\1\0\1\0", "dissonant"),
            (b"\1\1\1\0", "worm"),
            (b"\1\1\1\1", "gas_giant"),
        ):
            self.assertEqual(map_tag(flags), expected)
        for flags in (bytes(3), bytes(5), b"\0\2\0\0"):
            with self.assertRaises(NativePreconditionError):
                map_tag(flags)

    def test_spectrum_seed_zero_and_64_bit_limits(self):
        # Direct trace: seed 0 initializes state=1, carry=0. Green skips the first draw.
        self.assertEqual(spectrum(0, "green"), dict(letter="E", digit="3", suffix="p"))
        for seed in (0, 1, (1 << 32) - 1, 1 << 40, (1 << 64) - 1):
            for color in COLORS:
                result = spectrum(seed, color)
                self.assertIn(result["letter"], GROUPS["letters"][0])
                self.assertIn(result["digit"], GROUPS["digits"][0])
                self.assertIn(result["suffix"], GROUPS["suffixes"][0])
        for seed, color in ((-1, "yellow"), (1 << 64, "blue"), (0, "pink")):
            with self.assertRaises(NativePreconditionError):
                spectrum(seed, color)


class NativeStarFilterTests(unittest.TestCase):
    def make_engine(self, raw, selection):
        helper = fixtures.NativeSequenceTests()
        engine, context, selected, memory = helper.make_engine(1)
        helper.ready_map(engine, context, memory)
        engine.star_filter = selection
        panel = context + DEFINITION["panel"]["map_offset"]
        target = (1 << 40) + 1
        memory[panel + DEFINITION["panel"]["query_offset"]] = struct.pack("<Q", target)
        memory[panel + DEFINITION["panel"]["rendered_query_offset"]] = struct.pack("<Q", target)
        memory[panel + DEFINITION["panel"]["star_type_offset"]] = raw[16:20]
        memory[panel + DEFINITION["panel"]["flags_offset"]] = bytes(4)
        engine.functions["classify_star"].side_effect = lambda target, output: ctypes.memmove(
            output, raw, len(raw)
        )
        return helper, engine, context, memory

    def test_mismatch_skips_before_capability_selection_and_warp(self):
        _, engine, context, _ = self.make_engine(attributes(), StarFilter(True, ("O", "B")))
        self.assertFalse(engine.choose(context))
        self.assertEqual(engine.filter_rejections, 1)
        for name in ("warp_check", "select_star", "warp_candidate"):
            engine.functions[name].assert_not_called()

    def test_disabled_does_not_decode_attributes_or_add_native_calls(self):
        _, engine, context, _ = self.make_engine(attributes(star=99), StarFilter())
        with patch("nms_scanner.native_single.classify_attributes") as decode:
            self.assertTrue(engine.choose(context))
            decode.assert_not_called()
        engine.functions["classify_star"].assert_called_once()
        self.assertIsNone(engine.candidate_classification)

    def test_matching_target_can_dispatch_without_an_extra_classification_call(self):
        helper, engine, context, memory = self.make_engine(
            attributes(star=4, pirate=1), StarFilter(True, ("X", "Y"), ("pirate",))
        )
        self.assertTrue(engine.choose(context))
        helper.set_clock(engine, context, memory, 6)
        self.assertTrue(engine.selection_matches(context))
        self.assertTrue(engine.dispatch(context))
        engine.functions["classify_star"].assert_called_once()
        self.assertEqual(engine.candidate_classification["color"], "purple")
        self.assertEqual(engine.candidate_classification["tag"], "none")

    def test_matching_color_but_wrong_category_is_rejected(self):
        _, engine, context, _ = self.make_engine(
            attributes(star=2, abandoned=1), StarFilter(True, ("O", "B"), ("pirate",))
        )
        self.assertFalse(engine.choose(context))
        engine.functions["select_star"].assert_not_called()

    def test_dispatch_requires_proof_for_the_actual_selected_target(self):
        helper, engine, context, memory = self.make_engine(attributes(), StarFilter(True))
        self.assertTrue(engine.choose(context))
        helper.set_clock(engine, context, memory, 6)
        engine.filter_target = (engine.source, dict(engine.candidate_classification))
        with self.assertRaisesRegex(NativePreconditionError, "star_filter_target_not_verified"):
            engine.dispatch(context)
        engine.functions["warp_candidate"].assert_not_called()

    def test_cycle_reset_drops_previous_acceptance_and_counts(self):
        _, engine, context, _ = self.make_engine(attributes(), StarFilter(True))
        self.assertTrue(engine.choose(context))
        engine.filter_rejections = 5
        engine.reset_cycle()
        self.assertIsNone(engine.filter_target)
        self.assertIsNone(engine.candidate_classification)
        self.assertEqual(engine.filter_rejections, 0)
        self.assertTrue(engine.star_filter.enabled)

    def test_digit_suffix_and_race_rejected_before_selection(self):
        for selection in (
            StarFilter(True, categories=("normal",), races=("korvax",)),
            StarFilter(
                True, digits=(str((int(spectrum((1 << 40) + 1, "yellow")["digit"]) + 1) % 10),)
            ),
            StarFilter(
                True,
                suffixes=tuple(
                    key
                    for key in GROUPS["suffixes"][0]
                    if key != spectrum((1 << 40) + 1, "yellow")["suffix"]
                ),
            ),
        ):
            _, engine, context, _ = self.make_engine(attributes(), selection)
            self.assertFalse(engine.choose(context))
            engine.functions["warp_check"].assert_not_called()
            engine.functions["select_star"].assert_not_called()
            engine.functions["warp_candidate"].assert_not_called()

    def test_tag_rejection_cannot_dispatch_and_does_not_generate(self):
        helper, engine, context, memory = self.make_engine(
            attributes(), StarFilter(True, tags=("water",))
        )
        self.assertTrue(engine.choose(context))
        helper.set_clock(engine, context, memory, 6)
        self.assertFalse(engine.selection_matches(context))
        self.assertIsNone(engine.filter_target)
        with self.assertRaisesRegex(NativePreconditionError, "star_filter_target_not_verified"):
            engine.dispatch(context)
        engine.functions["warp_candidate"].assert_not_called()
        self.assertEqual(engine.filter_rejections, 1)
        self.assertEqual(
            set(engine.functions),
            {
                "own_freighter",
                "query_star",
                "star_distance",
                "classify_star",
                "warp_check",
                "select_star",
                "warp_candidate",
            },
        )

    def test_panel_stale_waits_and_mismatched_spectrum_stops(self):
        _, engine, context, memory = self.make_engine(attributes(), StarFilter(True))
        self.assertTrue(engine.choose(context))
        layout = DEFINITION["panel"]
        panel = context + layout["map_offset"]
        address = panel + layout["rendered_query_offset"]
        memory[address] = struct.pack("<Q", engine.source)
        self.assertIsNone(engine.selection_matches(context))
        self.assertIsNone(engine.filter_target)
        memory[address] = struct.pack("<Q", engine.target)
        memory[panel + layout["star_type_offset"]] = struct.pack("<I", 4)
        with self.assertRaisesRegex(NativePreconditionError, "star_filter_panel_mismatch"):
            engine.selection_matches(context)
        engine.functions["warp_candidate"].assert_not_called()

    def test_recheck_at_dispatch_rejects_changed_map_label(self):
        helper, engine, context, memory = self.make_engine(
            attributes(), StarFilter(True, tags=("none",))
        )
        self.assertTrue(engine.choose(context))
        helper.set_clock(engine, context, memory, 6)
        self.assertTrue(engine.selection_matches(context))
        layout = DEFINITION["panel"]
        memory[context + layout["map_offset"] + layout["flags_offset"]] = b"\1\0\0\0"
        with self.assertRaisesRegex(NativePreconditionError, "star_filter_target_not_verified"):
            engine.dispatch(context)
        engine.functions["warp_candidate"].assert_not_called()

    def test_late_filter_rejection_retries_but_respects_attempt_limit(self):
        engine, events = fixtures.FakeEngine(), []
        engine.choices.extend([True])
        engine.selection_matches = Mock(side_effect=[None, False, False])
        run = SingleRun(
            engine,
            {"phase_timeout_seconds": 90, "max_candidate_attempts": 2},
            lambda name, **data: events.append({"event": name, **data}),
            lambda: 0,
        )
        run.command("start")
        run.poll("map", 1)
        run.poll("map", 1)
        self.assertEqual(run.stage, "dispatch")
        run.poll("map", 1)
        self.assertEqual(run.stage, "searching")
        run.poll("map", 1)
        run.poll("map", 1)
        self.assertEqual(run.stage, "failed")
        self.assertEqual(events[-1]["reason"], "no_matching_candidate")
        self.assertNotIn("warp", engine.calls)

    def test_no_match_exhaustion_stops_without_relaxing_filter(self):
        engine, events = fixtures.FakeEngine(), []
        engine.choices.clear()
        engine.star_filter = StarFilter(True, ("X", "Y"), ("empty",))
        run = SingleRun(
            engine,
            {"phase_timeout_seconds": 90, "max_candidate_attempts": 3},
            lambda name, **data: events.append({"event": name, **data}),
            lambda: 0,
        )
        run.command("start")
        for _ in range(4):
            run.poll("map", 1)
        self.assertEqual(run.stage, "failed")
        self.assertEqual(events[-1]["reason"], "no_matching_candidate")
        self.assertNotIn("warp", engine.calls)
        self.assertTrue(engine.star_filter.enabled)
        self.assertIn(
            "没有放宽筛选", status_message({**events[-1], "pid": 1, "run_mode": "loop"}, 1)
        )


if __name__ == "__main__":
    unittest.main()
