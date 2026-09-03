"""One warp/scan state machine. Native work runs only inside game callbacks."""

from __future__ import annotations

import threading
import time
from collections import deque


class ActionInterrupted(Exception):
    pass


class UploadDisabled(ActionInterrupted):
    """F2 was switched off before the native upload invocation."""


class NativePreconditionError(Exception):
    """Only contains a fixed diagnostic code, never game addresses or coordinates."""


class SingleRun:
    def __init__(self, engine, config: dict, emit, clock=time.monotonic):
        self.engine, self.config, self.emit, self.clock = engine, config, emit, clock
        self.lock = threading.RLock()
        self.state_lock = threading.Lock()
        self.states = deque(maxlen=32)
        self.state_overflow = False
        self.start_requested = threading.Event()
        self.stop_requested = threading.Event()
        self.paused = threading.Event()
        self.auto_upload = threading.Event()  # Never restored from a prior session/config.
        self.upload_delay = 0
        self.upload_batches = self.upload_records = 0
        self.stage = "idle"
        self.remaining = config["phase_timeout_seconds"]
        self.last_poll = clock()
        self.attempts = self.warps = self.scans = self.planet_index = self.planet_count = 0
        self.map_wait_notice = None
        self.engine.checkpoint = self.checkpoint

    def _notice_map_wait(self):
        reason = self.engine.map_wait_reason
        key = (self.stage, reason)
        if key != self.map_wait_notice:
            self.map_wait_notice = key
            self.emit("map_waiting", reason=reason, stage=self.stage, **self.engine.map_status)

    def _notice_map_ready(self):
        self.map_wait_notice = None
        self.emit("map_ready", stage=self.stage, **self.engine.map_status)

    def command(self, action: str):
        # Do not wait for a native call's lock to acknowledge pause/stop.
        if action == "stop":
            self.stop_requested.set()
        elif action == "start":
            self.start_requested.set()
        elif action == "pause" and self.start_requested.is_set():
            if self.paused.is_set():
                self.paused.clear()
            else:
                self.paused.set()

    def toggle_upload(self):
        if self.auto_upload.is_set():
            self.auto_upload.clear()
        else:
            self.auto_upload.set()
        return self.auto_upload.is_set()

    def checkpoint(self):
        if self.stop_requested.is_set() or self.paused.is_set():
            raise ActionInterrupted()

    def _begin_run(self):
        self._set_stage("wait_map")

    def _control_check(self):
        return False

    def _additional_phase(self, phase, context, elapsed):
        return False

    def _complete_cycle(self):
        self.stage = "complete"
        self.emit(
            "single_completed",
            warps=self.warps,
            scans=self.scans,
            planet_submissions=self.planet_count,
            candidate_attempts=self.attempts,
        )

    def _scan_finished(self):
        self.scans += 1
        self.emit("scan_completed", warps=self.warps, scans=self.scans,
                  planet_submissions=self.planet_count)
        if self.auto_upload.is_set():
            self.upload_delay = self.config["upload_settle_seconds"]
            self._set_stage("wait_upload")
        else:
            self._complete_cycle()

    def _poll_upload(self, phase, elapsed):
        if self.stage != "wait_upload":
            return False
        if not self.auto_upload.is_set():
            self.emit("upload_skipped", reason="disabled_before_submission")
            self._complete_cycle()
            return True
        self.upload_delay -= elapsed
        if self.upload_delay > 0 or phase != "application":
            return True
        self.checkpoint()
        try:
            count = self.engine.upload_all(self.auto_upload.is_set)
        except UploadDisabled:
            self.emit("upload_skipped", reason="disabled_before_submission")
            self._complete_cycle()
            return True
        if count is None:
            return True  # Game pause/state queue; bounded by this phase's timeout.
        if type(count) is not int or count < 0:
            raise NativePreconditionError("upload_result_invalid")
        if count:
            self.upload_batches += 1
            self.upload_records += count
        self.emit("upload_queued" if count else "upload_nothing_pending",
                  records=count, upload_batches=self.upload_batches,
                  upload_records=self.upload_records)
        self._complete_cycle()
        return True

    def _set_stage(self, stage):
        self.stage = stage
        self.remaining = self.config["phase_timeout_seconds"]
        self.emit("single_stage", stage=stage)

    def _fail(self, reason):
        self.stage = "failed"
        self.emit(
            "single_failed",
            reason=reason,
            warps=self.warps,
            scans=self.scans,
            candidate_attempts=self.attempts,
        )

    def state_changed(self, state: str):
        # Do not acquire the native-work lock on another game job thread. Preserve
        # the transition sequence even while an action holds that lock or is paused.
        with self.state_lock:
            if len(self.states) == self.states.maxlen:
                self.state_overflow = True
            self.states.append(state)

    def _process_states(self):
        with self.state_lock:
            states = list(self.states)
            self.states.clear()
        if self.state_overflow:
            self._fail("state_event_overflow")
            return
        for state in states:
            if self.stage == "wait_load" and state == "APPLOCALLOAD":
                self._set_stage("loading")
            elif self.stage == "loading" and state == "APPVIEW":
                self._set_stage("wait_scan")
            elif state in {"APPSHUTDOWN", "MODESELECTOR", "APPGLOBALLOAD", "YOUAREDEAD"}:
                self._fail("unexpected_game_state")
            elif self.stage in {"searching", "dispatch"} and state != "GALAXYMAP":
                self._fail("map_closed_before_dispatch")
            elif self.stage in {"wait_scan", "scanning", "wait_upload"} and state != "APPVIEW":
                self._fail("left_loaded_world")

    def poll(self, phase="watchdog", context=None):
        if not self.lock.acquire(blocking=False):
            return
        try:
            now = self.clock()
            elapsed, self.last_poll = max(0, now - self.last_poll), now
            if self.stop_requested.is_set():
                if self.stage not in {"stopped", "complete", "failed"}:
                    self._set_stage("stopped")
                    self.emit(
                        "run_stopped", reason="stop_requested", warps=self.warps, scans=self.scans
                    )
                return
            if self.stage == "idle":
                with self.state_lock:
                    self.states.clear()
                    self.state_overflow = False
                if not self.start_requested.is_set():
                    return
                self._begin_run()
                elapsed = 0
            if self.stage in {"complete", "failed", "stopped"}:
                return
            if self._control_check():
                return
            self._process_states()
            if self.stage == "failed" or self.paused.is_set():
                return
            self.remaining -= elapsed
            if self.remaining <= 0:
                self._fail("timeout_" + self.stage)
                return
            if self._poll_upload(phase, elapsed):
                return
            if self._additional_phase(phase, context, elapsed):
                return
            if phase == "map":
                if self.stage == "wait_map":
                    if not self.engine.begin(context):
                        self._notice_map_wait()
                        return
                    self._notice_map_ready()
                    self._set_stage("searching")
                if self.stage == "searching":
                    self.checkpoint()
                    chosen = self.engine.choose(context)
                    if chosen is None:
                        self._notice_map_wait()
                    if chosen is not None:
                        self.attempts += 1
                    if chosen:
                        self._set_stage("dispatch")
                    elif self.attempts >= self.config["max_candidate_attempts"]:
                        self._fail("no_reachable_candidate")
                elif self.stage == "dispatch":
                    self.checkpoint()
                    if not self.engine.selection_ready(context):
                        self._notice_map_wait()
                        return
                    self._notice_map_ready()
                    # Arm loading observation before dispatch in case transition is synchronous.
                    self._set_stage("wait_load")
                    try:
                        dispatched = self.engine.dispatch(context)
                    except ActionInterrupted:
                        self._set_stage("dispatch")
                        raise
                    if not dispatched:
                        self._fail("warp_request_rejected")
            elif phase == "world":
                if self.stage == "wait_scan":
                    count = self.engine.begin_scan(context)
                    if count is not None:
                        self.warps += 1  # begin_scan verifies the actual loaded target.
                        self.planet_count = count
                        self._set_stage("scanning")
                if self.stage == "scanning":
                    self.checkpoint()
                    self.engine.scan_planet(context, self.planet_index)
                    self.planet_index += 1
                    if self.planet_index == self.planet_count:
                        self._scan_finished()
        except ActionInterrupted:
            pass  # Pause/stop prevents the next native call; cannot cancel a call already started.
        except NativePreconditionError as exc:
            self._fail(str(exc))
        except Exception as exc:
            self._fail("native_" + type(exc).__name__)
        finally:
            self.lock.release()
