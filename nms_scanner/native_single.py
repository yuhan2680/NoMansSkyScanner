"""Native warp/scan operations; map opening is enabled only by exploration mode."""

from __future__ import annotations

import ctypes as C
import math
import random
import struct

from nms_scanner.single_run import NativePreconditionError


def resolve_application(base, image_size, definition, reader):
    """Validate both RIP-relative singleton references; Update's RCX is unused."""
    rva = int(definition["rva"], 16)
    references = definition["reference_rvas"]
    if not 0 <= rva < image_size - 0x40 or len(references) != 2:
        raise NativePreconditionError("application_reference_mismatch")
    for reference in references:
        at = int(reference, 16)
        if not 0 <= at <= image_size - 7:
            raise NativePreconditionError("application_reference_mismatch")
        instruction = reader(base + at, 7)
        if (
            len(instruction) != 7
            or instruction[:3] != b"\x48\x8d\x0d"
            or at + 7 + struct.unpack_from("<i", instruction, 3)[0] != rva
        ):
            raise NativePreconditionError("application_reference_mismatch")
    return base + rva


class AlignedBuffer:
    def __init__(self, size, data=b""):
        self.storage = C.create_string_buffer(size + 15)
        self.address = (C.addressof(self.storage) + 15) & ~15
        self.size = size
        if len(data) > size:
            raise ValueError("buffer_size")
        C.memmove(self.address, data, len(data))

    def bytes(self):
        return C.string_at(self.address, self.size)


class NativeSingleEngine:
    def __init__(self, base, offsets, profile, reader, stack_reader):
        self.base, self.profile = base, profile
        self.layout = profile["single_trial"]["layout"]
        self.read, self.stack = reader, stack_reader
        self.checkpoint = lambda: None
        self.application = resolve_application(
            base, profile["image_size"], profile["single_trial"]["application"], reader
        )
        self.map_object = self.source = self.target = self.solar = None
        self.expected_system = None
        self.map_wait_reason = None
        self.map_status = {}
        self.map_first_clock = self.map_last_clock = self.selection_clock = None
        self.planet_count = 0
        self.rng = random.Random()
        void = C.c_void_p
        definitions = {
            "own_freighter": (C.c_bool, [void]),
            "query_star": (C.c_uint8, [void, void, void, void, void]),
            "select_star": (None, [void, void, C.c_bool, C.c_float]),
            "star_distance": (C.c_float, [void, void]),
            "classify_star": (None, [C.c_uint64, void]),
            "warp_check": (void, [void, void, C.c_float, C.c_bool, C.c_uint64, void]),
            "warp_candidate": (C.c_bool, [void, C.c_bool]),
            "discovery": (C.c_bool, [void, void, void]),
        }
        if "exploration" in profile:
            definitions["queue_map_state"] = (None, [void, void, void, C.c_bool])
        if "upload" in profile:
            definitions["upload_all"] = (None, [void])
        self.functions = {
            name: C.CFUNCTYPE(result, *arguments)(base + offsets[name])
            for name, (result, arguments) in definitions.items()
        }

    def upload_all(self, enabled):
        from nms_scanner.native_upload import NativeUpload

        return NativeUpload(self).submit(enabled)

    def reset_cycle(self):
        # The previous target is retained only to detect outside warps between cycles.
        self.expected_system = self.target
        self.map_object = self.source = self.target = self.solar = None
        self.planet_count = 0
        self.map_wait_reason = None
        self.map_status = {}
        self.map_first_clock = self.map_last_clock = self.selection_clock = None

    def request_map(self):
        """Called on the application thread; let the game consume its own state queue."""
        layout = self.profile["exploration"]["layout"]
        if layout["application_update_caller"] not in self.stack():
            self._fail("wrong_map_request_phase")
        data = self._app_data()
        state_name = self._state()
        if state_name not in {"APPVIEW", "GALAXYMAP"}:
            self._fail("cannot_open_map_in_current_state")
        if self.expected_system is not None and self._current_system(data) != self.expected_system:
            self._fail("system_changed_between_cycles")
        env = data + self.layout["player_environment"]
        if not self._call("own_freighter", env):
            self._fail("not_on_own_freighter")
        for offset in (layout["environment_location"], layout["environment_stable_location"]):
            if self._integer(env + offset, 4) != layout["freighter_internals"]:
                self._fail("not_inside_freighter")
        if state_name == "GALAXYMAP":
            return True
        for address, size, reason in (
            (self.application + layout["application_paused"], 1, "game_paused"),
            (data + layout["warp_request"], 4, "warp_request_pending"),
            (data + layout["warp_transition"], 4, "warp_transition_active"),
        ):
            if self._integer(address, size):
                self.map_wait_reason = reason
                return False
        current = self._integer(self.application + self.layout["fsm_current_state"])
        if self._integer(current + layout["state_owner"]) != self.application:
            self._fail("state_owner_mismatch")
        pending = self.application + layout["fsm_pending_state"]
        # Compare the same live sentinel the native routine uses. Its on-disk bytes
        # alone do not tell us whether static initialization changed it at runtime.
        empty = self.read(self.base + int(layout["empty_state_rva"], 16), 16)
        if self.read(pending, 16) != empty:
            self.map_wait_reason = "another_state_request_pending"
            return False
        name = AlignedBuffer(16, b"GALAXYMAP")
        self._call("queue_map_state", current, name.address, None, False)
        # The queue routine copies the ID; it does not keep the Python buffer.
        if self.read(pending, 16) != name.bytes():
            self._fail("map_request_not_queued")
        self.map_wait_reason = None
        return True

    def _fail(self, code):
        raise NativePreconditionError(code)

    def _call(self, name, *args):
        self.checkpoint()
        return self.functions[name](*args)

    def _integer(self, address, size=8):
        return int.from_bytes(self.read(address, size), "little")

    def _system(self, ua):
        return ua & self.layout["system_identity_mask"]

    def _app_data(self):
        if not self.application:
            self._fail("application_not_observed")
        data = self._integer(self.application + self.layout["application_data"])
        if data < 0x10000:
            self._fail("application_data_unavailable")
        return data

    def _state(self):
        state = self._integer(self.application + self.layout["fsm_current_state"])
        raw = self.read(state + self.layout["fsm_state_name"], 16)
        return raw.split(b"\0", 1)[0].decode("ascii")

    def _current_system(self, data):
        return self._system(
            self._integer(data + self.layout["simulation"] + self.layout["current_ua"])
        )

    def _map_context(self, context):
        data = self._app_data()
        if self._state() != "GALAXYMAP":
            self._fail("not_in_galaxy_map")
        if self.map_object is not None and self.map_object != context:
            self._fail("map_object_changed")
        if self._integer(data + self.layout["application_map_data"]) != context:
            self._fail("map_object_changed")
        if not self._call("own_freighter", data + self.layout["player_environment"]):
            self._fail("not_on_own_freighter")
        return data

    def _map_clock(self, context):
        value = struct.unpack("<f", self.read(context + self.layout["map_clock"], 4))[0]
        if not math.isfinite(value) or value < 0:
            self._fail("invalid_map_clock")
        if self.map_last_clock is not None and value < self.map_last_clock:
            self._fail("map_clock_reset")
        self.map_last_clock = value
        if self.map_first_clock is None:
            self.map_first_clock = value
        return value

    def _map_ready(self, context, data):
        clock = self._map_clock(context)
        mode = self._integer(context + self.layout["map_mode"], 4)
        cache = context + self.layout["map_cache"] + self.layout["cache_busy"]
        cache_ready = self.read(cache, 2) == b"\0\1"
        self.map_status = {
            "map_mode": mode,
            "map_elapsed_seconds": round(clock - self.map_first_clock, 3),
            "cache_ready": cache_ready,
        }
        if self._integer(data + self.layout["warp_request"], 4) or self._integer(
            data + self.layout["warp_transition"], 4
        ):
            self._fail("warp_already_pending")
        if self._integer(self.application + self.layout["application_paused"], 1):
            self.map_wait_reason = "game_paused"
        elif mode not in self.layout["interactive_map_modes"]:
            self.map_wait_reason = "map_transition_active"
        elif not cache_ready:
            self.map_wait_reason = "map_cache_busy"
        elif clock - self.map_first_clock < self.profile["single_trial"]["map_settle_seconds"]:
            self.map_wait_reason = "map_settling"
        else:
            self.map_wait_reason = None
            return True
        return False

    def begin(self, context):
        data = self._map_context(context)
        self.source = self._current_system(data)
        if self.expected_system is not None and self.source != self.expected_system:
            self._fail("system_changed_between_cycles")
        initial = self.read(context + self.layout["map_initial"], 64)
        if self._system(struct.unpack_from("<Q", initial)[0]) != self.source:
            self._fail("map_origin_mismatch")
        self.map_object = context
        return self._map_ready(context, data)

    def choose(self, context):
        data = self._map_context(context)
        if self._current_system(data) != self.source:
            self._fail("system_changed_before_warp")
        if not self._map_ready(context, data):
            return None
        cache = context + self.layout["map_cache"]
        initial = AlignedBuffer(64, self.read(context + self.layout["map_initial"], 64))
        coordinate = AlignedBuffer(8, self.read(context + self.layout["map_coordinate"], 8))
        start_coord = struct.unpack_from("<hhh", initial.bytes(), 8)
        map_coord = struct.unpack_from("<hhh", coordinate.bytes())
        local = struct.unpack_from("<fff", initial.bytes(), 32)
        # Address coordinate order is X,Z,Y; vectors use X,Y,Z.
        delta = [(start_coord[i] - map_coord[i]) * self.layout["voxel_size"] for i in (0, 2, 1)]
        origin_values = [local[i] + delta[i] for i in range(3)]
        if not all(math.isfinite(value) for value in origin_values):
            self._fail("invalid_map_origin")
        z, angle = self.rng.uniform(-1, 1), self.rng.uniform(0, 2 * math.pi)
        radius = math.sqrt(max(0, 1 - z * z))
        origin = AlignedBuffer(16, struct.pack("<4f", *origin_values, 1))
        direction = AlignedBuffer(
            16, struct.pack("<4f", radius * math.cos(angle), z, radius * math.sin(angle), 0)
        )
        result = AlignedBuffer(64)
        if (
            self._call(
                "query_star",
                cache,
                coordinate.address,
                origin.address,
                direction.address,
                result.address,
            )
            != 1
        ):
            return False
        raw = result.bytes()
        target = struct.unpack_from("<Q", raw)[0]
        solar_index, iteration = struct.unpack_from("<HH", raw, 16)
        if raw[14] != 1 or not 1 <= solar_index <= 4095 or iteration > 255:
            self._fail("invalid_query_result")
        if self._system(target) == self.source:
            return False
        # Exclude the ordinary map's special centre target; never select a centre route.
        if target & self.layout["centre_test_mask"] == self.layout["centre_target"]:
            return False
        distance = self._call("star_distance", initial.address, result.address)
        if not math.isfinite(distance) or distance <= 0:
            return False
        attributes, capability = AlignedBuffer(48), AlignedBuffer(16)
        self._call("classify_star", target, attributes.address)
        returned = self._call(
            "warp_check",
            data + self.layout["game_state"],
            capability.address,
            distance,
            False,
            target,
            attributes.address,
        )
        if returned != capability.address:
            self._fail("capability_output_mismatch")
        status = struct.unpack_from(
            "<I", capability.bytes(), self.profile["warp_result_status_offset"]
        )[0]
        if status != 1:
            return False
        # Use the game's selection routine so that it owns the selected object's lifetime.
        self._call("select_star", context, result.address, True, 1.0)
        selected = self._integer(context + self.layout["map_selected"])
        if self._system(self._integer(selected + self.layout["selected_query"])) != self._system(
            target
        ):
            self._fail("selection_not_confirmed")
        self.target = self._system(target)
        self.selection_clock = self._map_clock(context)
        return True

    def selection_ready(self, context):
        data = self._map_context(context)
        if self._current_system(data) != self.source or not self.target:
            self._fail("warp_context_changed")
        selected = self._integer(context + self.layout["map_selected"])
        if self._system(self._integer(selected + self.layout["selected_query"])) != self.target:
            self._fail("selected_target_changed")
        if not self._map_ready(context, data):
            return False
        state = self._integer(context + self.layout["map_popup_state"], 4)
        selection = self._integer(context + self.layout["map_popup_selection"], 1)
        if self.selection_clock is None:
            self._fail("selection_not_confirmed")
        age = self.map_last_clock - self.selection_clock
        self.map_status.update(
            popup_state=state, popup_selection=selection, selection_elapsed_seconds=round(age, 3)
        )
        if state != self.layout["popup_ready_state"] or selection != 1:
            self.map_wait_reason = "selection_transition_active"
        elif age < self.profile["single_trial"]["selection_settle_seconds"]:
            self.map_wait_reason = "selection_settling"
        else:
            self.map_wait_reason = None
            return True
        return False

    def dispatch(self, context):
        if not self.selection_ready(context):
            self._fail("map_not_ready_at_dispatch")
        if not self._call("warp_candidate", context, True):
            return False
        return self._call("warp_candidate", context, False)

    def _scan_context(self, context):
        data = self._app_data()
        if context != data + self.layout["simulation"] or self._state() != "APPVIEW":
            self._fail("wrong_scan_context")
        if self.layout["scan_update_caller"] not in self.stack():
            self._fail("wrong_scan_update_phase")
        if self._current_system(data) != self.target or self.target == self.source:
            self._fail("loaded_system_mismatch")
        if not self._call("own_freighter", data + self.layout["player_environment"]):
            self._fail("not_on_own_freighter_after_load")
        solar = self._integer(context + self.layout["solar_system"])
        return data, solar

    def begin_scan(self, context):
        _, solar = self._scan_context(context)
        count = self._integer(solar + self.layout["planet_count"], 4)
        if not 1 <= count <= self.layout["max_planets"]:
            self._fail("planet_count_out_of_bounds")
        self.solar, self.planet_count = solar, count
        # Validate every discovery record before any submission.
        for index in range(count):
            self._planet_record(solar, index)
        return count

    def _planet_record(self, solar, index):
        record = solar + self.layout["planet_discovery"] + index * self.layout["planet_stride"]
        if self._system(self._integer(record)) != self.target:
            self._fail("planet_record_system_mismatch")
        return record

    def scan_planet(self, context, index):
        data, solar = self._scan_context(context)
        if (
            solar != self.solar
            or self._integer(solar + self.layout["planet_count"], 4) != self.planet_count
        ):
            self._fail("solar_object_changed")
        record = self._planet_record(solar, index)
        # The scanner room uses a null locally-new output and ignores this bool result.
        self._call("discovery", data + self.layout["discovery_manager"], record, None)
