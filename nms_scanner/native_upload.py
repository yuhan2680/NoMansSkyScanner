"""The game's Upload All path; no record edits, fabricated page object or network client."""

from nms_scanner.single_run import UploadDisabled


class NativeUpload:
    def __init__(self, engine):
        self.engine = engine
        self.config = engine.profile["upload"]
        self.layout = self.config["layout"]

    def submit(self, enabled):
        e, layout = self.engine, self.layout
        e.checkpoint()
        if not enabled():
            raise UploadDisabled()
        if layout["application_update_caller"] not in e.stack():
            e._fail("wrong_upload_phase")
        data = e._app_data()
        if e._state() != "APPVIEW" or e._current_system(data) != e.target:
            e._fail("upload_world_changed")
        if not e._call("own_freighter", data + e.layout["player_environment"]):
            e._fail("not_on_own_freighter_after_load")
        if any(e._integer(address, size) for address, size in (
            (e.application + e.layout["application_paused"], 1),
            (data + e.layout["warp_request"], 4),
            (data + e.layout["warp_transition"], 4),
        )):
            return None
        if e.read(e.application + layout["fsm_pending_state"], 16) != e.read(
            e.base + int(layout["empty_state_rva"], 16), 16
        ):
            return None
        frontend = data + layout["frontend_manager"]
        if e._integer(frontend) != e.base + int(layout["frontend_vtable_rva"], 16):
            e._fail("upload_frontend_mismatch")
        page = frontend + layout["frontend_page"] + layout["discovery_page"]
        # The real embedded object is constructed with the frontend manager. The native
        # bulk handler only writes these scalar UI reward fields, even with menus closed.
        e.read(page + layout["page_reward_hint"], 12)
        e.read(page + layout["page_reward_total"], 32)
        manager = e._integer(data + e.layout["discovery_manager"] + layout["manager_data"])
        registry = e._integer(manager + layout["registry"])
        count = e._integer(registry + layout["eligible_count"], 4)
        if count > self.config["max_pending_records"]:
            e._fail("upload_pending_count_invalid")
        if count == 0:
            return 0
        array = e._integer(registry + layout["eligible_array"])
        # Native code indexes a 17-entry reward array by record type. Validate every
        # eligible entry, while honoring F1/F3 and F2-off between bounded reads.
        for index in range(count):
            e.checkpoint()
            if not enabled():
                raise UploadDisabled()
            entry = e._integer(array + index * 8)
            record = e._integer(entry + layout["record_pointer"])
            if e._integer(record + layout["record_type"], 4) > layout["max_record_type"]:
                e._fail("upload_record_type_invalid")
            e.read(entry + layout["entry_status"], 4)
        if (
            e._integer(registry + layout["eligible_count"], 4) != count
            or e._integer(registry + layout["eligible_array"]) != array
            or e._integer(manager + layout["registry"]) != registry
        ):
            e._fail("upload_queue_changed")
        if not enabled():
            raise UploadDisabled()
        e._call("upload_all", page)
        # This proves local queue acceptance only, never server acknowledgement.
        if e._integer(registry + layout["eligible_count"], 4) != 0:
            e._fail("upload_queue_not_confirmed")
        return count
