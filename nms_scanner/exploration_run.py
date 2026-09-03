"""Repeat the validated single cycle, opening each map on the application thread."""

from __future__ import annotations

import time

from nms_scanner.compatibility import validate_exploration
from nms_scanner.single_run import ActionInterrupted, SingleRun


class ExplorationRun(SingleRun):
    def __init__(self, engine, single_config, run_config, emit, clock=time.monotonic):
        self.run_config = validate_exploration(dict(run_config))
        self.started_at = None
        self.total_attempts = 0
        self.waiting_reason = None
        self.cycle_delay = 0
        super().__init__(engine, single_config, emit, clock)

    def command(self, action):
        if action == "start" and self.started_at is None:
            self.started_at = self.clock()
        super().command(action)

    def _time_limit_reached(self):
        limit = self.run_config["max_runtime_seconds"]
        return bool(
            limit and self.started_at is not None and self.clock() - self.started_at >= limit
        )

    def checkpoint(self):
        super().checkpoint()
        if self._time_limit_reached():
            raise ActionInterrupted()  # The next poll records the terminal reason.

    def _begin_run(self):
        self._wait_between_cycles()
        self.emit(
            "run_limits",
            max_warps=self.run_config["max_warps"],
            max_runtime_seconds=self.run_config["max_runtime_seconds"],
            cycle_interval_seconds=self.run_config["cycle_interval_seconds"],
        )

    def _wait_between_cycles(self):
        self.cycle_delay = self.run_config["cycle_interval_seconds"]
        self._set_stage("between_cycles")
        self.remaining += self.cycle_delay

    def _finish(self, reason):
        self.stage = "complete"
        self.emit("run_completed", reason=reason, warps=self.warps, scans=self.scans)

    def _control_check(self):
        # Runtime is wall time from first F1, including pauses, and is checked by the
        # watchdog even when game callbacks stop. It only cancels future actions.
        if self._time_limit_reached():
            self._finish("max_runtime_seconds")
            return True
        return False

    def _additional_phase(self, phase, context, elapsed):
        if self.stage == "between_cycles":
            self.cycle_delay -= elapsed
            if self.cycle_delay > 0:
                return True
            self.engine.reset_cycle()
            self.attempts = self.planet_index = self.planet_count = 0
            self.waiting_reason = None
            self._set_stage("wait_ready")
        if self.stage == "wait_ready":
            if phase == "application":
                self.checkpoint()
                if self.engine.request_map():
                    self._set_stage("wait_map")
                elif self.engine.map_wait_reason != self.waiting_reason:
                    self.waiting_reason = self.engine.map_wait_reason
                    self.emit("run_waiting", reason=self.waiting_reason)
            return True
        return False

    def _complete_cycle(self):
        self.total_attempts += self.attempts
        self.emit(
            "cycle_completed",
            warps=self.warps,
            scans=self.scans,
            planet_submissions=self.planet_count,
            candidate_attempts=self.attempts,
            total_candidate_attempts=self.total_attempts,
        )
        limit = self.run_config["max_warps"]
        if limit and self.warps >= limit:
            self._finish("max_warps")
        else:
            self._wait_between_cycles()
