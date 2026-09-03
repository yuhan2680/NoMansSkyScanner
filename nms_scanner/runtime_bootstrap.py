"""One persistent bootstrap, avoiding per-payload interpreter namespaces/finalization."""

from __future__ import annotations

from pathlib import Path


def bootstrap_script(payloads: list[tuple[str, bool]], paths: list[str], error_log: Path) -> str:
    canonical_paths = list(dict.fromkeys(Path(p).resolve().as_posix() for p in paths if p))
    # A single exec payload guarantees all setup and imports share one namespace.
    # Never return into pyrun-injected's Py_FinalizeEx while hosted in a running game.
    return f"""
import sys, time
sys.path = {canonical_paths!r}
_nms_bootstrap_scope = {{'__name__': '__main__'}}
try:
    for _nms_value, _nms_is_file in {payloads!r}:
        if _nms_is_file:
            _nms_bootstrap_scope['__file__'] = _nms_value
            with open(_nms_value, 'rb') as _nms_source:
                _nms_code = compile(_nms_source.read(), _nms_value, 'exec')
        else:
            _nms_code = compile(_nms_value, '<nms-bootstrap>', 'exec')
        exec(_nms_code, _nms_bootstrap_scope, _nms_bootstrap_scope)
except BaseException as _nms_error:
    try:
        import traceback
        _nms_message = traceback.format_exc()
    except BaseException:
        _nms_message = repr(_nms_error)
    with open({str(error_log)!r}, 'w', encoding='utf-8') as _nms_log:
        _nms_log.write(_nms_message)
finally:
    # If framework setup/event loop exits, stop any observer without unloading
    # trampolines that might still be executing on game threads.
    try:
        _nms_manager = _nms_bootstrap_scope.get('mod_manager')
        if _nms_manager is not None:
            for _nms_mod in _nms_manager.mods.values():
                _nms_single = getattr(_nms_mod, 'single', None)
                if _nms_single is not None:
                    _nms_single.command('stop')
                _nms_telemetry = getattr(_nms_mod, 'telemetry', None)
                if _nms_telemetry is not None:
                    _nms_telemetry.command('stop')
    except BaseException:
        pass
    while True:
        time.sleep(1)
"""
