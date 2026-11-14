# SPDX-License-Identifier: GPL-3.0-or-later

"""Host core of OpenCode Link: hello, bounded reads and revision-checked applies.

Pure Python: everything that touches Blender goes through an application
adapter (blender_app.BlenderApp in Blender, a stub in tests). The plugin sends
`{method, params}` of the fixed method table only; no method executes
supplied code except `execute_code`, listed when the application adapter
allows it (OpenCode asks its permission for every run). `lab_tool` runs the
Blender Lab tool code bundled in this add-on, by name (lab_tools.py). Results of
reads, applies and runs carry the errors and warnings Blender reported meanwhile
(console.py); asynchronous work is a task (tasks.py). Edit batches (thin_plan.py: typed operations on
named objects) are applied with an inverse journal, without capturing the scene; `scene` is the
compact context that goes with them.
"""

import collections
import contextlib
import hashlib
import io
import json
import os
import re
import threading
import time
import tomllib
import traceback

from . import console, lab_tools, native_plans, thin_plan
from . import tasks as tasks_module
from .wire import HostFailure, bounded_message
from . import limits

PROTOCOL = 1
MAX_OPERATIONS = limits.value("operations")
MAX_READS = limits.value("reads")
MAX_COMMANDS = limits.value("commands")
STDOUT_MAX = limits.value("stdout_bytes")
TRACE_MAX = limits.value("trace_bytes")
CODE_STEP = "OpenCode: code"
COMMAND_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
HERE = os.path.dirname(os.path.abspath(__file__))

BASE_METHODS = ("hello", "context", "apply")
CODE_METHOD = "execute_code"
# Hot update: reload the add-on from disk; a new offer, token and script handle follow.
RELOAD_METHOD = "reload"
LAB_METHOD = lab_tools.LAB_METHOD
REPORTS_METHOD = "reports"
TASKS_METHOD = "tasks"
APP_METHOD = "app"
# The compact context of the thin layer (its edit batches go through apply).
SCENE_METHOD = "scene"
RELAUNCH_METHOD = "relaunch"
# Lifts a quarantine once the scene was read again (the revision of that read).
RELEASE_METHOD = "release"
QUARANTINED = ("OpenCode Link is quarantined: a failed change could not be proven undone. Read the scene "
               "(blender_context shows what may have changed), then release it with that read's revision.")
# Methods whose results carry the errors and warnings Blender reported during the call.
REPORTING = ("context", "describe", "apply", CODE_METHOD, LAB_METHOD, SCENE_METHOD)
REPORT_LEVELS = ("error", "warning", "info", "output")


def manifest_version(folder=HERE):
    """The add-on version, read from its own manifest (never written in code)."""
    with open(os.path.join(folder, "blender_manifest.toml"), "rb") as handle:
        return tomllib.load(handle)["version"]


def _catalog(folder):
    with open(os.path.join(folder, "catalog.json"), encoding="utf-8") as handle:
        return json.load(handle)


def load_catalog(folder=HERE):
    """Legacy reads and handler operations; native plan intents are listed separately."""
    catalog = _catalog(folder)
    return ({entry["name"]: entry for entry in catalog["reads"]},
            {entry["name"]: entry for entry in catalog["operations"] if "handler" in entry["execution"]})


def load_native_operations(folder=HERE):
    return {entry["name"]: entry for entry in _catalog(folder)["operations"]
            if entry["execution"].get("strategy") == "native_plan" and entry["name"] in native_plans.OPS}


def require_app_version(version, folder=HERE):
    """Reject an unsupported Blender before registering any host resources."""
    with open(os.path.join(folder, "blender_manifest.toml"), "rb") as handle:
        minimum = tomllib.load(handle)["blender_version_min"]
    if tuple(version) < tuple(map(int, minimum.split("."))):
        raise RuntimeError("Blender %s or later is required" % minimum)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def capability_hash(capabilities):
    return hashlib.sha256(canonical_json(capabilities).encode("utf-8")).hexdigest()


_TYPES = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "any": lambda value: True,
}


def _check_value(value, spec, path):
    kind = spec.get("type", "any")
    if not _TYPES.get(kind, _TYPES["any"])(value):
        raise HostFailure("invalid_args", "%s must be of type %s" % (path, kind))
    if spec.get("enum") and str(value) not in spec["enum"]:
        raise HostFailure("invalid_args", "%s must be one of %s" % (path, ", ".join(spec["enum"])))
    if kind == "array" and isinstance(spec.get("items"), dict):
        for index, item in enumerate(value):
            _check_value(item, spec["items"], "%s[%d]" % (path, index))


def validate_args(entry, args):
    """Arguments checked against the manifest, with defaults applied; unknown names refused."""
    if not isinstance(args, dict):
        raise HostFailure("invalid_args", "%s: args must be an object" % entry["name"])
    specs = entry.get("args", {})
    normalized = {}
    for name, value in args.items():
        spec = specs.get(name)
        if spec is None:
            if not entry.get("additionalArgs"):
                raise HostFailure("invalid_args", "%s: unknown argument %s" % (entry["name"], name))
            normalized[name] = value
            continue
        if value is None:
            continue
        _check_value(value, spec, "%s.%s" % (entry["name"], name))
        normalized[name] = value
    for name, spec in specs.items():
        if name in normalized:
            continue
        if spec.get("required"):
            raise HostFailure("invalid_args", "%s: missing argument %s" % (entry["name"], name))
        if "default" in spec:
            normalized[name] = spec["default"]
    return normalized


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


def _digest(params):
    """The change a command_id names: a replay must name the same one."""
    change = {key: params.get(key) for key in ("operations", "expect", "summary")}
    return hashlib.sha256(json.dumps(change, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class _Deferred(object):
    __slots__ = ("command_id", "digest", "entry", "args")

    def __init__(self, command_id, digest, entry, args):
        self.command_id = command_id
        self.digest = digest
        self.entry = entry
        self.args = args


class _Run(object):
    """A guarded run: its value (a result, or a deferred checker), output and document effect."""
    __slots__ = ("value", "error", "trace", "changed", "restored", "undo_step", "stdout", "stderr", "revision_before", "saved")

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields.get(name))


class _Pending(object):
    """A run whose result comes later (Lab's check_is_finished): polled on the request thread."""
    __slots__ = ("check", "finish")

    def __init__(self, check, finish):
        self.check = check
        self.finish = finish


class HostCore(object):
    def __init__(self, app, folder=HERE):
        self.app = app
        self.version = manifest_version(folder)
        self.reads, self.operations = load_catalog(folder)
        self.native_operations = load_native_operations(folder)
        self.lab = lab_tools.LabTools(folder)
        self.tasks = tasks_module.bind(app)
        self._commands = collections.OrderedDict()
        self._commands_handle = None
        self.quarantined = False
        # Why, and what the failed restoration may have left changed (shown while quarantined).
        self.quarantine = None
        # The last restoration's residue: may_have_changed, kept_linked.
        self.residue = None
        self._applying = 0
        self._applying_lock = threading.Lock()

    def busy(self):
        """True while an apply runs, including a network operation on its request thread."""
        with self._applying_lock:
            return self._applying > 0

    # -- capabilities -------------------------------------------------------------------------
    def capabilities(self):
        methods = list(BASE_METHODS)
        if hasattr(self.app, "native_describe"):
            methods.append("describe")
        if getattr(self.app, "reload_request", None) is not None:
            methods.append(RELOAD_METHOD)
        if self.app.code_execution_allowed():
            methods.append(CODE_METHOD)
        if self.lab.tools:
            methods.append(LAB_METHOD)
        methods += [REPORTS_METHOD, TASKS_METHOD]
        if hasattr(self.app, "app_info"):
            methods.append(APP_METHOD)
        if getattr(self.app, "relaunch_request", None) is not None:
            methods.append(RELAUNCH_METHOD)
        if hasattr(self.app, "thin_context"):
            methods.append(SCENE_METHOD)  # after the earlier methods: their order stays as it was
        methods.append(RELEASE_METHOD)
        operations = []
        listed = dict(self.operations, **(self.native_operations if self._native() else {}))
        for name in sorted(listed):
            entry = listed[name]
            operation = {"name": name, "undoable": bool(entry.get("undoable"))}
            if entry.get("network"):
                operation["network"] = True
            operations.append(operation)
        return {
            "methods": methods,
            "operations": operations,
            "undo": "grouped_best_effort",
            "background": bool(self.app.background),
        }

    # -- dispatch (called from the listener's request threads) --------------------------------
    def handle(self, method, params):
        if method not in REPORTING:
            return self._dispatch(method, params)
        mark = console.capture().cursor()
        result = self._dispatch(method, params)
        if isinstance(result, dict):
            problems = ([{"level": "warning", "text": text} for text in result.get("warnings", [])]
                        + console.output_problems(result.get("stdout"), result.get("stderr")) + console.new_problems(mark))
            if problems:
                result = dict(result, reports=problems[:20])
        return result

    def _dispatch(self, method, params):
        if method == "hello":
            return self.app.on_main(lambda: self._hello(params))
        if method == "context":
            return self.app.on_main(lambda: self._context(params))
        if method == SCENE_METHOD and hasattr(self.app, "thin_context"):
            return self.app.on_main(lambda: self._scene(params))
        if method == "describe" and hasattr(self.app, "native_describe"):
            return self.app.on_main(lambda: self._describe(params))
        if method == "apply":
            with self._applying_lock:
                self._applying += 1
            try:
                result = self.app.on_main(lambda: self._apply(params, defer_worker=True))
                if isinstance(result, _Deferred):
                    # A network-only operation: checked on the main thread, run on this request thread.
                    return self._run_worker(result)
                return result
            finally:
                with self._applying_lock:
                    self._applying -= 1
        if method == RELOAD_METHOD and getattr(self.app, "reload_request", None) is not None:
            return self.app.on_main(lambda: self._reload(params))
        if method == CODE_METHOD:
            # Refused before reaching the main thread when the adapter does not allow it.
            if not self.app.code_execution_allowed():
                raise HostFailure("method_disabled", "Code execution is disabled.")
            return self._settle(self.app.on_main(lambda: self._execute_code(params)))
        if method == LAB_METHOD and self.lab.tools:
            tool = self.lab.find(params)
            args = validate_args({"name": tool["name"], "args": tool["args"]}, params.get("args") or {})
            return self._settle(self.app.on_main(lambda: self._lab_tool(tool, args, params)))
        if method == REPORTS_METHOD:
            return self._reports(params)
        if method == TASKS_METHOD:
            return self.tasks.read(params)
        if method == APP_METHOD and hasattr(self.app, "app_info"):
            return self.app.on_main(self.app.app_info)
        if method == RELAUNCH_METHOD and getattr(self.app, "relaunch_request", None) is not None:
            return self.app.on_main(lambda: self._relaunch(params))
        if method == RELEASE_METHOD:
            return self.app.on_main(lambda: self._release(params))
        raise HostFailure("method_not_found", "Unknown host method.")

    def _reports(self, params):
        since = params.get("since", 0)
        if not isinstance(since, int) or isinstance(since, bool) or since < 0:
            raise HostFailure("invalid_args", "since must be a cursor from an earlier read")
        levels = params.get("levels")
        if levels is not None and (not isinstance(levels, list) or not set(levels) <= set(REPORT_LEVELS)):
            raise HostFailure("invalid_args", "levels must list error, warning, info or output")
        limit = params.get("limit", 100)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise HostFailure("invalid_args", "limit must be 1 to 500")
        return console.capture().read(since, levels, limit)

    def _relaunch(self, params):
        self._check_handle(params)
        if self.busy() or self.tasks.running():
            raise HostFailure("busy", "An apply or a task is running; relaunch Blender after it (blender_tasks).")
        result = self.app.relaunch(params.get("unsaved", "refuse"), params.get("offer_key"))
        self.app.relaunch_request()
        return result

    def _settle(self, output):
        """A deferred result is polled on this request thread; the main thread stays free between polls."""
        if not isinstance(output, _Pending):
            return output
        result, trace = lab_tools.poll(self.app.on_main, output.check)
        return self.app.on_main(lambda: output.finish(result, trace))

    # -- methods (main thread) ----------------------------------------------------------------
    def _hello(self, params):
        del params  # older plugins send code_execution; it no longer gates anything
        capabilities = self.capabilities()
        hello = {
            "protocol": PROTOCOL,
            "host_version": self.version,
            "app_version": self.app.app_version(),
            "capabilities": capabilities,
            "capability_hash": capability_hash(capabilities),
            "script_handle": self.app.script_handle(),
            "revision": self.app.revision(),
        }
        document = self.app.document()
        if document:
            hello["document"] = document
        return hello

    def _reload(self, params):
        self._check_handle(params)
        if self.app.mode() != "OBJECT":
            raise HostFailure("mode_conflict", "Blender is in %s mode; OpenCode Link reloads in Object mode only." % self.app.mode())
        if self.busy() or self.tasks.running(blocking_reload=True):
            # A render in its own Blender goes on across the reload (blender_tasks still follows it).
            raise HostFailure("busy", "An apply or a task is running; reload OpenCode Link after it (blender_tasks).")
        self.app.reload_request()
        return {"outcome": "scheduled", "host_version": self.version}

    def _check_handle(self, params):
        if params.get("script_handle") != self.app.script_handle():
            raise HostFailure("stale_handle", "Another document is open: read the context again.")

    def _state(self):
        state = {"script_handle": self.app.script_handle(), "revision": self.app.revision(), "mode": self.app.mode()}
        document = self.app.document()
        if document:
            state["document"] = document
        if self.quarantined:
            state["quarantined"] = True
            if self.quarantine:
                state["quarantine"] = self.quarantine
        return state

    def _context(self, params):
        self._check_handle(params)
        if "query" in params:
            self._check_native_read(params)
            output = self.app.native_context(params["query"])
            if self.quarantined:
                output["quarantined"] = True
            return output
        reads = params.get("reads", [])
        if not isinstance(reads, list) or len(reads) > MAX_READS:
            raise HostFailure("invalid_args", "reads must be a list of at most %d entries" % MAX_READS)
        checked = []
        for index, read in enumerate(reads):
            if not isinstance(read, dict) or not isinstance(read.get("name"), str):
                raise HostFailure("invalid_args", "reads[%d] must be an object with a name" % index)
            entry = self.reads.get(read["name"])
            if entry is None:
                raise HostFailure("invalid_args", "Unknown read %s" % bounded_message(read["name"])[:64])
            checked.append((entry, validate_args(entry, read.get("args", {}))))
        results = []
        with self.app.quiet():
            for entry, args in checked:
                try:
                    value = self.app.handler(entry["execution"]["handler"])(**args)
                    results.append({"name": entry["name"], "ok": True, "result": _json_safe(value)})
                except Exception as error:  # noqa: BLE001 - one failed read never fails the others
                    results.append({"name": entry["name"], "ok": False, "error": bounded_message(error)})
        output = self._state()
        output["results"] = results
        return output

    def _scene(self, params):
        self._check_handle(params)
        query = {key: value for key, value in params.items() if key != "script_handle"}
        try:
            found = self.app.thin_context(query)
        except thin_plan.PlanError as error:
            raise HostFailure("invalid_args", bounded_message(error)) from None
        output = self._state()
        output.update(found)
        return output

    def _check_native_read(self, params):
        if params.keys() - {"script_handle", "query"} or "query" not in params or not hasattr(self.app, "native_context"):
            raise HostFailure("invalid_args", "Use query or legacy reads separately; native read fields are script_handle and query.")

    def _describe(self, params):
        self._check_handle(params)
        self._check_native_read(params)
        output = self.app.native_describe(params["query"])
        if self.quarantined:
            output["quarantined"] = True
        return output

    def _command_scope(self):
        return (self.app.script_handle(), self.app.script_epoch()) if hasattr(self.app, "script_epoch") else self.app.script_handle()

    def _validate_apply(self, params):
        command_id = params.get("command_id")
        if not isinstance(command_id, str) or not COMMAND_ID.match(command_id):
            raise HostFailure("invalid_args", "command_id is required (letters, digits, . _ : -, at most 128)")
        if not isinstance(params.get("if_revision"), int) or isinstance(params.get("if_revision"), bool):
            raise HostFailure("invalid_args", "if_revision is required")
        operations = params.get("operations")
        if not isinstance(operations, list) or not operations:
            raise HostFailure("invalid_args", "operations must be a non-empty list")
        if len(operations) > MAX_OPERATIONS:
            raise HostFailure("invalid_args", "at most %d operations per apply" % MAX_OPERATIONS)
        expect = params.get("expect", [])
        if not isinstance(expect, list) or len(expect) > MAX_OPERATIONS:
            raise HostFailure("invalid_args", "expect must be a list of at most %d clauses" % MAX_OPERATIONS)
        checked = []
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
                raise HostFailure("invalid_args", "operations[%d] must be an object with an op" % index)
            entry = self.operations.get(operation["op"])
            if entry is None:
                raise HostFailure("invalid_args", "Unknown operation %s" % bounded_message(operation["op"])[:64])
            args = validate_args(entry, operation.get("args", {}))
            preflight = entry.get("execution", {}).get("preflight")
            if preflight:
                # Refusals that need the document (a constrained bone): before the barrier, nothing changed.
                try:
                    self.app.handler(preflight)(**args)
                except HostFailure:
                    raise
                except Exception as error:  # noqa: BLE001
                    raise HostFailure("invalid_args", "operations[%d] %s: %s" % (index, entry["name"], bounded_message(error))) from None
            checked.append((entry, args))
        if len(checked) > 1 and any(not entry.get("undoable") for entry, _ in checked):
            raise HostFailure("invalid_args", "An operation with side effects must be the only operation of its apply")
        summary = params.get("summary")
        summary = bounded_message(summary)[:80] if isinstance(summary, str) and summary.strip() else None
        return command_id, checked, expect, summary or ", ".join(entry["name"] for entry, _ in checked)[:80]

    def _remember(self, command_id, digest, result):
        handle = self._command_scope()
        if self._commands_handle != handle:
            self._commands.clear()
            self._commands_handle = handle
        self._commands[command_id] = (digest, result)
        while len(self._commands) > MAX_COMMANDS:
            self._commands.popitem(last=False)

    def _replayed(self, command_id, digest):
        if self._commands_handle != self._command_scope() or command_id not in self._commands:
            return None
        known, stored = self._commands[command_id]
        if known != digest:
            raise HostFailure("invalid_args", "This command_id was already used for another change: use a new one.")
        result = dict(stored)
        result["replayed"] = True
        return result

    def _apply(self, params, defer_worker=False):
        if self.quarantined:
            raise HostFailure("quarantined", QUARANTINED)
        self._check_handle(params)
        if thin_plan.names_edit_op(params.get("operations")):
            if not thin_plan.is_edit_batch(params.get("operations")) or not hasattr(self.app, "thin_run"):
                raise HostFailure("invalid_args", "An apply holds edit operations only, or catalog operations only: apply them separately.")
            return self._apply_thin(params)
        if native_plans.is_native(params, self.native_operations):
            return self._apply_native(params)
        command_id, checked, expect, summary = self._validate_apply(params)
        digest = _digest(params)
        replay = self._replayed(command_id, digest)
        if replay is not None:
            return replay
        if params["if_revision"] != self.app.revision():
            raise HostFailure("stale_revision", "The document changed: read the context again.")
        if self.app.mode() != "OBJECT":
            raise HostFailure("mode_conflict", "Blender is in %s mode; mutations run in Object mode only." % self.app.mode())
        with self.app.quiet():
            failures = [failure for failure in (self.app.check_expect(clause) for clause in expect) if failure]
            if failures:
                raise HostFailure("expectation_failed", "; ".join(failures))
            undoable = all(entry.get("undoable") for entry, _ in checked)
            if not undoable and checked[0][0].get("worker"):
                deferred = _Deferred(command_id, digest, checked[0][0], checked[0][1])
                if defer_worker:
                    return deferred
                return self._finish_worker(deferred, self._call_worker(deferred, lambda fn: fn()))
            result = self._run(checked, summary) if undoable else self._run_side_effect(checked[0])
        self._remember(command_id, digest, result)
        return result

    def _call_worker(self, deferred, main):
        try:
            value = self.app.handler(deferred.entry["execution"]["handler"])(_main=main, **deferred.args)
            return {"op": deferred.entry["name"], "result": _json_safe(value)}
        except Exception as error:  # noqa: BLE001
            return {"op": deferred.entry["name"], "error": bounded_message(error)}

    def _finish_worker(self, deferred, item):
        output = self._state()
        if "error" in item:
            output.update(status="failed", failed_operation=0, op=item["op"], message=item["error"], restored=False,
                          undoable=False)
        else:
            output.update(status="applied", undoable=False, side_effect=deferred.entry.get("sideEffect", ""),
                          verified=True, results=[item])
        self._remember(deferred.command_id, deferred.digest, output)
        return output

    def _run_worker(self, deferred):
        item = self._call_worker(deferred, self.app.on_main)
        return self.app.on_main(lambda: self._finish_worker(deferred, item))

    def _restore(self, before, summary):
        """Model B: push a failure step, then undo once, which returns to the barrier. Proven when the document's
        own data is back; linked data Blender keeps outside its undo (unused, never saved) is named, not counted
        (native_plans.restoration_residue). self.residue says what may have changed, and what was kept."""
        self.residue = None
        try:
            self.app.undo_push("OpenCode: failed %s" % summary)
            self.app.undo()
            after = self.app.capture_after_undo(before) if hasattr(self.app, "capture_after_undo") else self.app.fingerprint()
        except Exception as error:  # noqa: BLE001
            self.residue = {"may_have_changed": ["the undo failed: %s" % bounded_message(error)]}
            return False
        if not (hasattr(before, "state") and hasattr(after, "state")):
            return after == before
        proven, changes, kept = native_plans.restoration_residue(before, after)
        cap = limits.value("change_entries")
        self.residue = {key: value[:cap] for key, value in (("may_have_changed", changes), ("kept_linked", kept)) if value}
        return proven

    def _quarantine(self, cause):
        """A failed change whose restoration is unproven: changes wait until the scene is read and the quarantine
        released (release, or Reset in Blender's panel); reads, verify and read_only code still run."""
        self.quarantined = True
        self.quarantine = dict({"cause": bounded_message(cause)[:200],
                                "release": "read the scene (blender_context), check what may have changed, then "
                                           "release the quarantine with that read's revision"},
                               **{key: value for key, value in (self.residue or {}).items()})

    def _release(self, params):
        self._check_handle(params)
        if not self.quarantined:
            return dict(self._state(), released=False)
        revision = params.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision != self.app.revision():
            raise HostFailure("stale_revision", "Read the scene again (blender_context) and release with its revision.")
        self.quarantined = False
        self.quarantine = None
        self.app.reset_native() if hasattr(self.app, "reset_native") else None
        self.app.bump_revision()
        return dict(self._state(), released=True)

    def _run(self, checked, summary):
        """Rollback model B: barrier step, mutations, one named step; any failure after the barrier is rolled back."""
        step = "OpenCode: %s" % summary
        before = self.app.capture_for_apply(checked) if hasattr(self.app, "capture_for_apply") else self.app.fingerprint()
        self.app.undo_push("OpenCode: before %s" % summary)
        results = []
        index = 0
        try:
            for index, (entry, args) in enumerate(checked):
                value = self.app.handler(entry["execution"]["handler"])(**args)
                results.append({"op": entry["name"], "result": _json_safe(value)})
            index = len(checked)
            # Evaluate before the named step: a failure here still undoes back to the barrier.
            self.app.flush()
            self.app.undo_push(step)
        except Exception as error:  # noqa: BLE001 - every failure after the barrier is rolled back
            restored = self._restore(before, summary)
            if not restored:
                self._quarantine("failed %s: %s" % (summary, bounded_message(error)))
            self.app.bump_revision()
            output = self._state()
            output.update(
                status="failed",
                failed_operation=min(index, len(checked) - 1),
                op=checked[min(index, len(checked) - 1)][0]["name"],
                message=bounded_message(error),
                restored=restored,
            )
            if hasattr(before, "scope"):
                output["verification"] = {"scope": before.scope, "complete": restored,
                                          "elements": before.count, "reason": before.reason or (None if restored else "restoration_not_matched")}
            return output
        self.app.bump_revision()
        output = self._state()
        output.update(status="applied", undo_step=step, verified=self._verify(results) and getattr(before, "complete", True), results=results)
        if hasattr(before, "scope"):
            # What a failure's restoration would have been proven against: the scope and its size.
            output["verification"] = {"scope": before.scope, "complete": bool(before.complete), "elements": before.count,
                                      "inventory": getattr(before, "inventory", None), "reason": before.reason}
        return output

    # -- edit batches (thin layer) ---------------------------------------------------------------
    def _apply_thin(self, params):
        """Shape, replay, revision, mode, expect, the document checks, then the batch with its inverse journal.
        No barrier step and no capture: a failure runs the inverses and re-reads what they restored; success is
        one named undo step."""
        command_id = params.get("command_id")
        if not isinstance(command_id, str) or not COMMAND_ID.match(command_id):
            raise HostFailure("invalid_args", "command_id is required (letters, digits, . _ : -, at most 128)")
        if not isinstance(params.get("if_revision"), int) or isinstance(params.get("if_revision"), bool):
            raise HostFailure("invalid_args", "if_revision is required")
        expect = params.get("expect", [])
        if not isinstance(expect, list) or len(expect) > MAX_OPERATIONS:
            raise HostFailure("invalid_args", "expect must be a list of at most %d clauses" % MAX_OPERATIONS)
        try:
            checked = thin_plan.validate(params["operations"])
        except thin_plan.PlanError as error:
            raise HostFailure("invalid_args", bounded_message(error)) from None
        digest = _digest(params)
        replay = self._replayed(command_id, digest)
        if replay is not None:
            return replay
        if params["if_revision"] != self.app.revision():
            raise HostFailure("stale_revision", "The document changed: read the context again.")
        if self.app.mode() != "OBJECT":
            raise HostFailure("mode_conflict", "Blender is in %s mode; mutations run in Object mode only." % self.app.mode())
        summary = params.get("summary")
        summary = bounded_message(summary)[:80] if isinstance(summary, str) and summary.strip() else None
        summary = summary or ", ".join(dict.fromkeys(op for op, _ in checked))[:80]
        with self.app.quiet():
            failures = [failure for failure in (self.app.check_expect(clause) for clause in expect) if failure]
            if failures:
                raise HostFailure("expectation_failed", "; ".join(failures))
            try:
                self.app.thin_precheck(checked)
            except thin_plan.PlanError as error:
                raise HostFailure("invalid_args", bounded_message(error)) from None
            result = self._run_thin(checked, summary)
        self._remember(command_id, digest, result)
        return result

    def _run_thin(self, checked, summary):
        started = time.perf_counter()
        failure, batch = self.app.thin_run(checked)
        step = "OpenCode: %s" % summary
        if failure is None:
            try:
                self.app.flush()
                self.app.undo_push(step)
            except Exception as error:  # noqa: BLE001 - no step: undo the batch rather than leave it ungrouped
                failure = ("undo step: %s" % error, len(checked) - 1)
        changes = batch.changes[:limits.value("change_entries")]
        if failure is not None:
            restored, proofs = self.app.thin_rollback(batch)
            if not restored:
                self.residue = {"may_have_changed": list(proofs.get("unproved", [])) + list(proofs.get("errors", []))}
                self._quarantine("failed %s: %s" % (summary, bounded_message(failure[0])))
            self.app.bump_revision()
            output = self._state()
            output.update(status="failed", failed_operation=failure[1], op=checked[failure[1]][0],
                          message=bounded_message(failure[0]), restored=restored, restoration=proofs,
                          undone=changes)
            return output
        self.app.bump_revision()
        output = self._state()
        output.update(status="applied", undo_step=step, changes=changes, ms=round((time.perf_counter() - started) * 1000, 1))
        if len(batch.changes) > len(changes):
            output["changes_total"] = len(batch.changes)
        if batch.created:
            output["created"] = batch.created
        if batch.warnings:
            output["warnings"] = batch.warnings
        return output

    # -- native plans ---------------------------------------------------------------------------
    def _native(self):
        return hasattr(self.app, "native_prepare") and hasattr(self.app, "native_describe")

    def _apply_native(self, params):
        """Shape, document/epoch, replay, revision/schema/mode, whole-batch preflight, then the barrier."""
        if not self._native():
            raise HostFailure("invalid_args", "This host has no native plans: use the legacy operations of its catalog.")
        request = native_plans.parse(params, self.native_operations)
        if request.epoch != self.app.script_epoch():
            raise HostFailure("stale_handle", "script_epoch changed (undo, redo, load or reset): read the context again.")
        digest = request.digest()
        replay = self._replayed(request.command_id, digest)
        if replay is not None:
            if replay.get("revision") != self.app.revision():
                raise HostFailure("stale_revision", "The document changed since this command applied: read the context again.")
            return replay
        if request.revision != self.app.revision():
            raise HostFailure("stale_revision", "The document changed: read the context again.")
        if request.schema != self.app.native_schema_hash():
            raise HostFailure("invalid_args", "schema_hash changed (registered types or properties): describe again.")
        if self.app.mode() != "OBJECT":
            raise HostFailure("mode_conflict", "Blender is in %s mode; mutations run in Object mode only." % self.app.mode())
        with self.app.quiet():
            execution = self.app.native_prepare(request)
            plan = native_plans.Plan(request, execution.snapshot)
            failures = plan.evaluate(request.expect, execution.before.state)
            if failures:
                raise HostFailure("expectation_failed", "; ".join(failures))
            summary = request.summary or ", ".join(op for op, _ in request.operations)[:80]
            result = self._run_native(plan, execution, summary)
        self._remember(request.command_id, digest, result)
        return result

    def _native_failure(self, before, stage, index, op, error, restored):
        if not restored:
            self._quarantine("failed %s at %s: %s" % (op or "plan", stage, bounded_message(error)))
        self.app.bump_revision()
        output = self._state()
        output.update(status="failed", stage=stage, failed_operation=index, op=op, message=bounded_message(error),
                      restored=restored, script_epoch=self.app.script_epoch(),
                      verification={"scope": before.scope, "complete": restored, "elements": before.count,
                                    "reason": None if restored else "restoration_not_matched"})
        return output

    @staticmethod
    def _native_results(plan, execution):
        """References of the final state, minted in the current epoch (renames keep theirs)."""
        aliases = {alias: execution.ref(("$", alias)) for alias in execution.created}
        results = []
        for step in plan.steps:
            item = {"op": step[0]}
            if step[0] == "entity.create":
                item.update(alias=step[6], ref=aliases[step[6]], **execution.card(("$", step[6])))
            elif step[0].startswith("relation."):
                item.update(owner=execution.ref(step[1]), relation=step[2], target=execution.ref(step[3]))
            else:
                item.update(ref=execution.ref(step[1]), **execution.card(step[1]))
            results.append(item)
        return aliases, results

    def _run_native(self, plan, execution, summary):
        """Model B: barrier, steps, flush, verified final state, named step; any failure rolls back."""
        before = execution.before
        try:
            self.app.undo_push("OpenCode: before %s" % summary)
        except Exception as error:  # noqa: BLE001 - nothing ran: prove the document unchanged
            try:
                unchanged = self.app.capture_after_undo(before) == before
            except Exception:  # noqa: BLE001
                unchanged = False
            return self._native_failure(before, "barrier", None, None, error, unchanged)
        stage, index = "operation", 0
        try:
            for index, step in enumerate(plan.steps):
                execution.execute(step)
            stage, index = "flush", None
            self.app.flush()
            stage = "verification"
            created = execution.created_keys()
            after = execution.capture_after(plan)
            checks = plan.verify(before.state, after.state, created)
            stage = "ensure"
            failures = plan.evaluate(plan.request.ensure, after.state, created, "ensure")
            if failures:
                raise RuntimeError("; ".join(failures))
            checks["ensure"] = len(plan.request.ensure)
            stage = "finalize"
            aliases, results = self._native_results(plan, execution)
            step_name = "OpenCode: %s" % summary
            self.app.undo_push(step_name)
        except Exception as error:  # noqa: BLE001 - every failure after the barrier is rolled back
            op = plan.steps[index][0] if index is not None else None
            return self._native_failure(before, stage, index, op, error, self._restore(before, summary))
        self.app.bump_revision()
        output = self._state()
        output.update(status="applied", undo_step=step_name, verified=True, aliases=aliases, results=results,
                      script_epoch=self.app.script_epoch(),
                      verification={"scope": after.scope, "complete": True, "elements": after.count, "checks": checks})
        return output

    def _run_side_effect(self, checked):
        entry, args = checked
        try:
            value = self.app.handler(entry["execution"]["handler"])(**args)
            self.app.flush()
            if isinstance(value, dict) and value.get("status") == "error":
                # A handler that reports its failure in its result: a failed operation, never "applied".
                raise RuntimeError(value.get("message") or "The operation reported an error")
        except Exception as error:  # noqa: BLE001
            self.app.bump_revision()
            output = self._state()
            output.update(status="failed", failed_operation=0, op=entry["name"], message=bounded_message(error),
                          restored=False, undoable=False)
            return output
        self.app.bump_revision()
        output = self._state()
        output.update(
            status="applied",
            undoable=False,
            side_effect=entry.get("sideEffect", "This operation cannot be undone."),
            verified=self._verify([{"op": entry["name"], "result": _json_safe(value)}]),
            results=[{"op": entry["name"], "result": _json_safe(value)}],
        )
        return output

    def _verify(self, results):
        """Datablocks that results name as created must exist after the apply."""
        for item in results:
            for ref in self.app.created_refs(item["op"], item["result"]):
                if self.app.check_expect({"ref": ref, "predicate": "exists"}):
                    return False
        return True

    def _guarded(self, run, label, barrier):
        """Runs `run` with stdout and stderr captured. With a barrier (model B, like an apply): a barrier step,
        the run, then one named step when the document changed; a failure after a change is undone to the
        barrier and checked against the structural capture (quarantine when that cannot be proven)."""
        out, err = io.StringIO(), io.StringIO()
        self.residue = None
        revision = self.app.revision()
        before = None
        if barrier:
            try:
                before = self.app.capture_for_apply([])
            except HostFailure:
                before = None  # above the structural budget: a restoration cannot be proven
        evaluations = self.app.evaluations()
        # What Blender changes without a depsgraph update (scene settings, keys) is seen by a fingerprint.
        digest = self.app.change_digest() if hasattr(self.app, "change_digest") else None
        # A read-only run (verify, inspect, a probe) evaluates other frames, renders, makes temporary datablocks:
        # depsgraph updates all, which say nothing. It changed the document only when its content differs after.
        content = self.app.content_digest() if not barrier and hasattr(self.app, "content_digest") else None
        saves = self.app.saves() if hasattr(self.app, "saves") else 0
        value = error = trace = restored = step = None
        with self.app.quiet():
            if barrier:
                self.app.undo_push("OpenCode: before %s" % label)
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    value = run()
                self.app.flush()
            except BaseException as exc:  # noqa: BLE001 - SystemExit from supplied code must not quit Blender
                error = bounded_message("%s: %s" % (type(exc).__name__, exc))
                trace = traceback.format_exc()[-TRACE_MAX:]
                try:
                    self.app.flush()
                except Exception:  # noqa: BLE001 - the document is restored below
                    pass
            if content is not None:
                changed = self.app.content_digest() != content
            else:
                changed = self.app.evaluations() > evaluations or (digest is not None and self.app.change_digest() != digest)
            saved = (self.app.saves() if hasattr(self.app, "saves") else 0) > saves
            if changed and barrier and error is None and not saved:
                step = "OpenCode: %s" % label
                self.app.undo_push(step)
            elif changed and barrier and error is None:
                pass  # the run saved the file: a step now would mark the saved file as modified again
            elif changed and barrier:
                if before is None:
                    self.residue = {"may_have_changed": ["no capture before the run: the document is above the "
                                                         "structural budget"]}
                restored = before is not None and self._restore(before, label)
                if not restored:
                    self._quarantine("failed %s: %s" % (label, error))
            if changed:
                self.app.bump_revision()
        return _Run(value=value, error=error, trace=trace, changed=changed, restored=restored, undo_step=step,
                    stdout=out.getvalue(), stderr=err.getvalue(), revision_before=revision, saved=saved)

    def _execute_code(self, params):
        self._check_handle(params)
        code = params.get("code")
        if not isinstance(code, str) or not code.strip():
            raise HostFailure("invalid_args", "code is required")
        # Quarantined, a read_only run still inspects (verify and inspect are read_only runs): changes wait.
        if self.quarantined and params.get("read_only") is not True:
            raise HostFailure("quarantined", QUARANTINED)
        # A script run from a file (blender_execute_code path): its tracebacks name the file and show its lines.
        filename = params.get("filename")
        if filename is not None and (not isinstance(filename, str) or not 0 < len(filename) <= 1024
                                     or any(ord(char) < 32 for char in filename)):
            raise HostFailure("invalid_args", "filename must be a file path of at most 1024 characters")
        namespace = self.app.exec_namespace()
        namespace["result"] = {}
        if filename:
            namespace["__file__"] = filename
        # read_only: no undo barrier or step. The barrier is an undo push, which marks a saved file as
        # modified: inspections and runs after a save leave the file saved; a change is reported, never undone.
        read_only = params.get("read_only") is True
        rotations = self.app.rotation_snapshot() if hasattr(self.app, "rotation_snapshot") else None

        def run():
            exec(compile(code, filename or "<opencode execute_code>", "exec"), namespace)  # noqa: S102 - the dangerous method
            check = namespace.get("check_is_finished")
            return check if callable(check) else None

        done = self._guarded(run, "code", barrier=not read_only)
        warnings = self.app.rotation_warnings(rotations) if rotations is not None else []
        if read_only and done.changed:
            warnings.append("OpenCode: this read_only run changed the document; no undo step groups or undoes it.")

        def finish(deferred=None, deferred_error=None):
            output = self._state()
            failed = done.error or deferred_error
            output.update(status="failed" if failed else "executed",
                          result=_json_repr(deferred if deferred is not None else namespace.get("result", {})),
                          stdout=done.stdout[:STDOUT_MAX], stderr=done.stderr[:STDOUT_MAX],
                          truncated=len(done.stdout) > STDOUT_MAX or len(done.stderr) > STDOUT_MAX,
                          revision_before=done.revision_before, revision_after=self.app.revision(), changed=done.changed)
            if done.undo_step:
                output["undo_step"] = done.undo_step
            if done.saved:
                output["saved"] = True
            if warnings:
                output["warnings"] = warnings
            if done.error:
                output.update(message=done.error, traceback=done.trace)
                if done.changed:
                    output["restored"] = done.restored
                    if self.residue:
                        output["restoration"] = self.residue
            elif deferred_error:
                output.update(message=bounded_message(deferred_error), traceback=deferred_error[-TRACE_MAX:])
            return output

        if done.error is None and callable(done.value):
            if self.app.background:
                return finish(None, "Deferred completion (check_is_finished) needs the Blender interface.")
            return _Pending(done.value, finish)
        return finish()

    def _lab_tool(self, tool, args, params):
        """A bundled Blender Lab tool; read-only tools run without a barrier step."""
        self._check_handle(params)
        read_only = bool(tool.get("read_only"))
        if (tool.get("image") or tool["name"].startswith("get_screenshot")) and hasattr(self.app, "refresh_display"):
            # A screenshot shows what the interface last drew: draw the current document first.
            self.app.refresh_display()
        if self.quarantined and not read_only:
            raise HostFailure("quarantined", QUARANTINED)
        done = self._guarded(self.lab.runner(tool, args), (tool.get("title") or tool["name"])[:48], barrier=not read_only)

        def finish(deferred=None, deferred_error=None):
            if deferred_error is not None:
                response = lab_tools.envelope(None, deferred_error, deferred_error, done.stdout, done.stderr)
            else:
                value = deferred if deferred is not None else done.value
                response = lab_tools.envelope(value, done.error, done.trace, done.stdout, done.stderr)
            inner = response.get("result")
            if response.get("status") == "ok" and isinstance(inner, dict) and inner.get("status") == "error":
                # The tool ran and reported a failure (a render with every layer disabled): an error, not ok.
                response["status"] = "error"
                response["message"] = inner.get("message") or "The tool reported an error."
            response["revision"] = self.app.revision()
            if done.changed:
                response.update(changed=True, revision_before=done.revision_before)
                if done.undo_step:
                    response["undo_step"] = done.undo_step
                if done.restored is not None:
                    response["restored"] = done.restored
            return response

        if done.error is None and callable(done.value):
            return _Pending(done.value, finish)
        return finish()


def _json_repr(value):
    """Lab's rule for supplied code: values JSON cannot hold become their repr."""
    return json.loads(json.dumps(value, default=repr))
