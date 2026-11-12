# SPDX-License-Identifier: GPL-3.0-or-later

"""Every bound OpenCode Link applies, with where it comes from.

A bound is either `justified` (set by a real constraint: a protocol, Blender itself, a measured resource;
the source is cited) or `provisional` (a guess with no constraint behind it yet). Every value can be changed
at once, without a new release: OPENCODE_LINK_LIMITS (a JSON object, name to value) in Blender's environment,
or the same JSON in opencode_link_limits.json in Blender's user config folder (the environment wins). They
are read when the add-on loads; blender_install_host's hot reload reads them again. There is no structural
budget on the document: heavy work is checked against measured memory instead (resources.py).

This module is free of bpy at import (the host core runs without Blender in tests).
"""

import json
import os

PROVISIONAL, JUSTIFIED = "provisional", "justified"

# name: (default, basis, why)
DEFAULTS = {
    # Wire and session (the listener on 127.0.0.1)
    "request_bytes": (1 << 20, PROVISIONAL, "largest request body the listener reads"),
    "response_bytes": (8 << 20, PROVISIONAL, "largest response; captures travel as base64 PNG"),
    "read_timeout_s": (10.0, PROVISIONAL, "time to read one request from a connection"),
    "connections": (8, PROVISIONAL, "simultaneous connections the listener serves"),
    "message_chars": (300, JUSTIFIED, "error and detail text: spec/rpc-contract.md RPC-7 (detail at most 300 characters)"),
    "label_chars": (120, JUSTIFIED, "offer label: spec/rpc-contract.md RPC-7 (at most 120 characters)"),
    "renew_s": (5.0, JUSTIFIED, "offer renewal: spec/host-handshake.md HST-13 (renew every 5 s, lease 15 s)"),
    "ready_poll_s": (0.2, PROVISIONAL, "poll period while waiting for the session to be ready"),
    "drain_interval_s": (0.02, PROVISIONAL, "period of the main-thread queue timer in the interface"),
    "reload_delay_s": (0.3, PROVISIONAL, "delay before a scheduled hot reload, so its reply leaves first"),
    "quit_delay_s": (0.5, PROVISIONAL, "delay before a relaunch quits, so its reply leaves first"),
    "queue_wait_s": (10.0, PROVISIONAL, "wait for Blender's main thread before answering busy (modal operator, render)"),
    "run_limit_s": (600.0, PROVISIONAL, "longest main-thread job a request waits for"),
    # Calls
    "operations": (50, JUSTIFIED, "operations per apply: spec/integrations.md FAM-3 (at most 50 operations)"),
    "reads": (20, PROVISIONAL, "named reads per blender_context call"),
    "commands": (256, PROVISIONAL, "applied command_ids remembered for replay"),
    "stdout_bytes": (64 << 10, PROVISIONAL, "stdout and stderr kept from one code run"),
    "trace_bytes": (16 << 10, PROVISIONAL, "traceback kept from one failure"),
    "deferred_timeout_s": (3600.0, PROVISIONAL, "longest deferred Lab tool or code completion (check_is_finished)"),
    "deferred_poll_s": (0.1, PROVISIONAL, "poll period of a deferred completion"),
    # Edit batches and the compact context (thin.py)
    "max_frame": (1048574, JUSTIFIED, "frames from -1048574 to 1048574: Blender 5.3 DNA_scene_types.h MINAFRAME/MAXFRAME"),
    "report_digits": (6, JUSTIFIED, "significant digits of reported values: IEEE 754 32-bit floats hold about 7.2"),
    "aim_tolerance_deg": (1.0, PROVISIONAL, "default angle an aim_bone may miss by before the batch is refused and restored"),
    "change_entries": (300, PROVISIONAL, "before/after entries listed by one edit batch (the rest are counted)"),
    "context_page": (50, PROVISIONAL, "objects in a compact context page by default (limit up to page)"),
    "context_items": (200, PROVISIONAL, "collections, markers and selected names listed by a compact context (the rest are counted)"),
    "context_bones": (200, PROVISIONAL, "pose bones listed by one object's detail (bone_match narrows them)"),
    # Native reads
    "page": (100, PROVISIONAL, "entities or types per blender_context/blender_describe page"),
    "reference_selection": (100, PROVISIONAL, "references named by one entity read"),
    "cursor_receipts": (128, PROVISIONAL, "paging cursors remembered"),
    "id_chars": (128, PROVISIONAL, "length of a command_id, cursor or filter value"),
    "node_socket_allowance": (64, PROVISIONAL, "sockets counted per created node in a plan's element count (reported, never a ceiling)"),
    "filter_chars": (128, PROVISIONAL, "length of a name or type filter"),
    "relations_per_read": (4000, PROVISIONAL, "relations listed by one entity read (page the entities otherwise)"),
    "name_bytes": (255, JUSTIFIED, "ID names: Blender 5.3 DNA_ID.h MAX_ID_NAME 258 = 2-byte type code + 255 bytes + NUL"),
    "abs_tolerance": (1e-6, JUSTIFIED, "Blender stores RNA floats as 32-bit floats (about 1.2e-7 relative): below this is rounding"),
    "rel_tolerance": (1e-6, JUSTIFIED, "Blender's 32-bit RNA floats, relative to the value (as abs_tolerance)"),
    "matrix_tolerance": (1e-5, JUSTIFIED, "32-bit matrix products accumulate a few ULPs per product: 1e-5 x max(1, largest entry)"),
    # Reports and tasks
    "console_entries": (2000, PROVISIONAL, "console entries kept for blender_reports"),
    "console_text_chars": (4000, PROVISIONAL, "characters kept from one console entry"),
    "console_sync_s": (0.5, PROVISIONAL, "wait for the console readers before a reports read"),
    "tasks": (64, PROVISIONAL, "finished tasks kept for blender_tasks"),
    "task_wait_s": (120.0, PROVISIONAL, "longest wait of one blender_tasks call"),
    "main_retry_s": (1.0, PROVISIONAL, "retry period of a task waiting for a busy main thread"),
    # Imports and add-ons
    "http_timeout_s": (10.0, PROVISIONAL, "BlenderKit and Sketchfab web API calls"),
    "download_timeout_s": (30.0, PROVISIONAL, "silence before a download from a URL fails"),
    "download_chunk_bytes": (64 << 10, PROVISIONAL, "read size of a download"),
    "download_default_mb": (512, PROVISIONAL, "default max_mb of an import from a URL (also checked against free disk space)"),
    "sketchfab_max_bytes": (4 << 30, PROVISIONAL, "largest Sketchfab archive accepted (also checked against free disk space)"),
    "blenderkit_timeout_s": (1800.0, PROVISIONAL, "longest BlenderKit import task"),
    "blenderkit_stall_s": (180.0, PROVISIONAL, "no progress from BlenderKit for this long fails the task"),
    "blenderkit_client_grace_s": (45.0, PROVISIONAL, "BlenderKit's local client down for this long fails the task"),
    # Staging and renders
    "rig_bones_max": (1000, PROVISIONAL, "bones listed by one animation.get_rig_info call"),
    "bounds_objects_max": (500, PROVISIONAL, "per-object boxes listed by one scene.get_evaluated_bounds call"),
    "aim_iterations": (4, PROVISIONAL, "refinements of animation.aim_bone (it stops earlier once aligned)"),
    "render_poll_s": (1.0, PROVISIONAL, "progress poll period of an animation render"),
    "gpu_query_timeout_s": (3.0, PROVISIONAL, "longest wait for the GPU driver's memory query (nvidia-smi)"),
    "memory_margin": (1.25, PROVISIONAL, "factor on an estimated memory need before comparing it with what is available"),
}

_values = {}
_sources = {}


def _overrides():
    found = {}
    path = None
    try:
        import bpy  # noqa: PLC0415 - optional: absent when the core runs without Blender

        path = os.path.join(bpy.utils.user_resource("CONFIG"), "opencode_link_limits.json")
    except Exception:  # noqa: BLE001
        path = None
    for source, text in (
        (path, _read(path) if path else None),
        ("OPENCODE_LINK_LIMITS", os.environ.get("OPENCODE_LINK_LIMITS")),
    ):
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            for name, value in data.items():
                if name in DEFAULTS and isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                    found[name] = (value, "OPENCODE_LINK_LIMITS" if source == "OPENCODE_LINK_LIMITS" else "opencode_link_limits.json")
    return found


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


_loaded = []


def load():
    """(Re)reads the overrides; returns the names that were overridden."""
    _values.clear()
    _sources.clear()
    _loaded[:] = [True]
    for name, (value, source) in _overrides().items():
        default = DEFAULTS[name][0]
        _values[name] = type(default)(value) if isinstance(default, int) and float(value).is_integer() else value
        _sources[name] = source
    return sorted(_values)


def value(name):
    if not _loaded:
        load()
    return _values.get(name, DEFAULTS[name][0])


def table():
    """Every bound with its value, basis, reason and where the value came from."""
    value("operations")  # loads the overrides once
    return [{"name": name, "value": _values.get(name, default), "default": default, "basis": basis, "why": why,
             "source": _sources.get(name, "default")} for name, (default, basis, why) in sorted(DEFAULTS.items())]
