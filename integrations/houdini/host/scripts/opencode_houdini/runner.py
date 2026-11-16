"""execute_code and release: Python run in Houdini, grouped as one undo step, undone when it fails.

A run that changes the scene is one undo group ("OpenCode: code"). When it raises after a change, the group is
undone and the node graph compared with its state before the run: restored, or the scene is quarantined (changes
refused, reads and read_only runs allowed) until the agent read it and releases it with that read's revision.
read_only runs (the plugin's fixed programs, inspections) make no undo group: a change they make is reported.
Hints (hints.py) are computed in the except block, before anything is undone, while the failing frames live.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import traceback
from typing import Any

import hou

STDOUT_MAX = 64 * 1024
TRACE_MAX = 16 * 1024
FILENAME_MAX = 1024
QUARANTINED = (
    "A failed change could not be proven undone: changes are refused until you read the scene (houdini_context) "
    "and release the quarantine with that read's revision (houdini_context release_quarantine)."
)


class _Capture(io.StringIO):
    """stdout or stderr of one run, bounded."""

    def write(self, text: str) -> int:
        if self.tell() < STDOUT_MAX * 2:
            return super().write(text)
        return len(text)


def fingerprint(root: Any) -> str:
    """The node graph's structure: every node's path, type and inputs (what an undone change must give back)."""
    digest = hashlib.sha256()
    try:
        nodes = root.allSubChildren(recurse_in_locked_nodes=False)
    except TypeError:
        nodes = root.allSubChildren()
    for node in nodes:
        try:
            inputs = [connected.path() if connected is not None else "" for connected in node.inputs()]
            digest.update(("%s|%s|%s\n" % (node.path(), node.type().nameWithCategory(), ",".join(inputs))).encode())
        except hou.ObjectWasDeleted:
            continue
    return digest.hexdigest()


def _json_repr(value: Any) -> Any:
    return json.loads(json.dumps(value, default=repr))


def _open_file_mtime() -> float | None:
    """The open file's date on disk: a save writes it; a copy (hscript mwrite -n, the save and verify programs)
    fires Houdini's save events too, but leaves it as it was."""
    try:
        path = hou.hipFile.path()
        return os.path.getmtime(path) if path and os.path.isfile(path) else None
    except Exception:  # noqa: BLE001 - a stub hou in tests, a path being written
        return None


def _undo_labels() -> tuple[str, ...]:
    try:
        return tuple(hou.undos.undoLabels())
    except Exception:  # noqa: BLE001 - undo unavailable
        return ()


def execute_code(host: Any, state: Any, params: dict[str, Any], explain: Any = None) -> dict[str, Any]:
    from .host import HostError  # noqa: PLC0415 - host imports this module

    code = params.get("code")
    if not isinstance(code, str) or not code.strip():
        raise HostError("invalid_request", "code is required")
    read_only = params.get("read_only") is True
    if state.quarantined and not read_only:
        raise HostError("script_quarantined", QUARANTINED, {"quarantine": state.quarantine or {}})
    filename = params.get("filename")
    if filename is not None and (
        not isinstance(filename, str) or not 0 < len(filename) <= FILENAME_MAX or any(ord(c) < 32 for c in filename)
    ):
        raise HostError("invalid_request", "filename must be a file path of at most 1024 characters")
    namespace: dict[str, Any] = {"hou": hou, "__name__": "__opencode__", "result": {}}
    if filename:
        namespace["__file__"] = filename
    state.flush()
    revision_before = state.revision
    loads = host.loads
    written = _open_file_mtime()
    labels_before = _undo_labels()
    before = None if read_only else fingerprint(state.root)
    out, err = _Capture(), _Capture()
    error = trace = hint = None
    group = contextlib.nullcontext() if read_only else hou.undos.group("OpenCode: code")
    with group:
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                exec(compile(code, filename or "<opencode execute_code>", "exec"), namespace)  # noqa: S102
        except BaseException as exc:  # noqa: BLE001 - SystemExit from supplied code must not quit Houdini
            error = ("%s: %s" % (type(exc).__name__, exc))[:2000]
            trace = traceback.format_exc()[-TRACE_MAX:]
            if explain is not None:
                try:
                    hint = explain(exc, code, filename)
                except Exception:  # noqa: BLE001 - a hint never hides the error
                    hint = None
    replaced = host.loads > loads or host.state is not state
    now_written = _open_file_mtime()
    saved = now_written is not None and now_written != written
    output: dict[str, Any] = {}
    restored = None
    if replaced:
        # The run opened or cleared a file: the old scene's undo group means nothing for the new one.
        changed = True
        current = host.state
    else:
        current = state
        # A change is to nodes (created, removed, edited) or an undo item; a selection, or the summary a save or a
        # copy refreshes, is none.
        touched = bool(getattr(state, "_dirty_nodes", None) or getattr(state, "_removed_refs", None))
        state.flush(cause="opencode")
        changed = touched or len(_undo_labels()) > len(labels_before) or saved
        if error is not None and changed and not read_only:
            grew = len(_undo_labels()) > len(labels_before)
            rollback = state._rollback(labels_before) if grew else RuntimeError("the run left no undo item to undo")
            state._reconcile_after_rollback()
            restored = rollback is None and before == fingerprint(state.root)
            if not restored:
                state.quarantined = True
                state.quarantine = {
                    "cause": ("failed code: %s" % error)[:200],
                    "release": "read the scene (houdini_context), check what may have changed, then release the "
                    "quarantine with that read's revision",
                    **({"undo_error": str(rollback)[:300]} if rollback is not None else {}),
                }
            state._summary_dirty = True
            state.flush(cause="rollback")
    output.update(
        status="failed" if error else "executed",
        script_handle=current.handle,
        result=_json_repr(namespace.get("result", {})),
        stdout=out.getvalue()[:STDOUT_MAX],
        stderr=err.getvalue()[:STDOUT_MAX],
        truncated=len(out.getvalue()) > STDOUT_MAX or len(err.getvalue()) > STDOUT_MAX,
        revision_before=revision_before,
        revision_after=current.revision,
        changed=changed,
    )
    if changed and not read_only and error is None and not replaced:
        output["undo_step"] = "OpenCode: code"
    if saved:
        output["saved"] = True
    if replaced:
        output["document_replaced"] = True
    if read_only and changed and not replaced:
        output["warnings"] = ["OpenCode: this read_only run changed the scene; no undo group holds it."]
    if error:
        output.update(message=error, traceback=trace)
        if hint:
            output["hint"] = hint
        if restored is not None:
            output["restored"] = restored
            if not restored:
                output["quarantine"] = state.quarantine
    return output


def release(state: Any, params: dict[str, Any]) -> dict[str, Any]:
    from .host import HostError  # noqa: PLC0415

    if not state.quarantined:
        return {"released": False, "revision": state.revision}
    revision = params.get("revision")
    state.flush()
    if not isinstance(revision, int) or isinstance(revision, bool) or revision != state.revision:
        raise HostError(
            "revision_conflict",
            "Read the scene again (houdini_context) and release with its revision.",
            {"current_revision": state.revision},
        )
    state.quarantined = False
    state.quarantine = None
    return {"released": True, "revision": state.revision}
