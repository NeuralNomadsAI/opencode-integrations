# SPDX-License-Identifier: GPL-3.0-or-later

"""Host core of OpenCode Link: hello, bounded reads and revision-checked applies.

Pure Python: everything that touches Blender goes through an application
adapter (blender_app.BlenderApp in Blender, a stub in tests). The plugin sends
`{method, params}` of the fixed method table only; no method executes
supplied code except `execute_code`, which is absent unless the add-on
preference and the plugin's own option both enable it.
"""

import collections
import contextlib
import hashlib
import io
import json
import os
import re
import tomllib

from .wire import HostFailure, bounded_message

PROTOCOL = 1
MAX_OPERATIONS = 50
MAX_READS = 20
MAX_COMMANDS = 256
STDOUT_MAX = 64 * 1024
COMMAND_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
HERE = os.path.dirname(os.path.abspath(__file__))

BASE_METHODS = ("hello", "context", "apply")
CODE_METHOD = "execute_code"


def manifest_version(folder=HERE):
    """The add-on version, read from its own manifest (never written in code)."""
    with open(os.path.join(folder, "blender_manifest.toml"), "rb") as handle:
        return tomllib.load(handle)["version"]


def load_catalog(folder=HERE):
    with open(os.path.join(folder, "catalog.json"), encoding="utf-8") as handle:
        catalog = json.load(handle)
    return {entry["name"]: entry for entry in catalog["reads"]}, {entry["name"]: entry for entry in catalog["operations"]}


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


class HostCore(object):
    def __init__(self, app, folder=HERE):
        self.app = app
        self.version = manifest_version(folder)
        self.reads, self.operations = load_catalog(folder)
        self._commands = collections.OrderedDict()
        self._commands_handle = None
        self.quarantined = False

    # -- capabilities -------------------------------------------------------------------------
    def capabilities(self, code_execution=False):
        methods = list(BASE_METHODS)
        if code_execution and self.app.code_execution_allowed():
            methods.append(CODE_METHOD)
        operations = []
        for name in sorted(self.operations):
            entry = self.operations[name]
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
        if method == "hello":
            return self.app.on_main(lambda: self._hello(params))
        if method == "context":
            return self.app.on_main(lambda: self._context(params))
        if method == "apply":
            result = self.app.on_main(lambda: self._apply(params, defer_worker=True))
            if isinstance(result, _Deferred):
                # A network-only operation: checked on the main thread, run on this request thread.
                return self._run_worker(result)
            return result
        if method == CODE_METHOD:
            # Refused before reaching the main thread unless both settings enable it.
            if not (params.get("code_execution") is True and self.app.code_execution_allowed()):
                raise HostFailure("method_disabled", "Code execution is disabled.")
            return self.app.on_main(lambda: self._execute_code(params))
        raise HostFailure("method_not_found", "Unknown host method.")

    # -- methods (main thread) ----------------------------------------------------------------
    def _hello(self, params):
        capabilities = self.capabilities(code_execution=params.get("code_execution") is True)
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
        return state

    def _context(self, params):
        self._check_handle(params)
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
            checked.append((entry, validate_args(entry, operation.get("args", {}))))
        if len(checked) > 1 and any(not entry.get("undoable") for entry, _ in checked):
            raise HostFailure("invalid_args", "An operation with side effects must be the only operation of its apply")
        summary = params.get("summary")
        summary = bounded_message(summary)[:80] if isinstance(summary, str) and summary.strip() else None
        return command_id, checked, expect, summary or ", ".join(entry["name"] for entry, _ in checked)[:80]

    def _remember(self, command_id, digest, result):
        handle = self.app.script_handle()
        if self._commands_handle != handle:
            self._commands.clear()
            self._commands_handle = handle
        self._commands[command_id] = (digest, result)
        while len(self._commands) > MAX_COMMANDS:
            self._commands.popitem(last=False)

    def _replayed(self, command_id, digest):
        if self._commands_handle != self.app.script_handle() or command_id not in self._commands:
            return None
        known, stored = self._commands[command_id]
        if known != digest:
            raise HostFailure("invalid_args", "This command_id was already used for another change: use a new one.")
        result = dict(stored)
        result["replayed"] = True
        return result

    def _apply(self, params, defer_worker=False):
        if self.quarantined:
            raise HostFailure("quarantined", "OpenCode Link is quarantined after a failed restoration: reset it.")
        self._check_handle(params)
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
        """Model B: push a failure step, then undo once, which returns to the barrier."""
        try:
            self.app.undo_push("OpenCode: failed %s" % summary)
            self.app.undo()
            return self.app.fingerprint() == before
        except Exception:  # noqa: BLE001
            return False

    def _run(self, checked, summary):
        """Rollback model B: barrier step, mutations, one named step; any failure after the barrier is rolled back."""
        step = "OpenCode: %s" % summary
        before = self.app.fingerprint()
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
                self.quarantined = True
            self.app.bump_revision()
            output = self._state()
            output.update(
                status="failed",
                failed_operation=min(index, len(checked) - 1),
                op=checked[min(index, len(checked) - 1)][0]["name"],
                message=bounded_message(error),
                restored=restored,
            )
            return output
        self.app.bump_revision()
        output = self._state()
        output.update(status="applied", undo_step=step, verified=self._verify(results), results=results)
        return output

    def _run_side_effect(self, checked):
        entry, args = checked
        try:
            value = self.app.handler(entry["execution"]["handler"])(**args)
            self.app.flush()
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

    def _execute_code(self, params):
        self._check_handle(params)
        code = params.get("code")
        if not isinstance(code, str) or not code.strip():
            raise HostFailure("invalid_args", "code is required")
        capture = io.StringIO()
        namespace = self.app.exec_namespace()
        # Supplied code owns its undo steps: nothing is grouped or rolled back.
        with self.app.quiet():
            error = None
            try:
                with contextlib.redirect_stdout(capture):
                    exec(compile(code, "<opencode execute_code>", "exec"), namespace)  # noqa: S102 - the dangerous method
                self.app.flush()
            except BaseException as exc:  # noqa: BLE001 - SystemExit from supplied code must not quit Blender
                error = bounded_message("%s: %s" % (type(exc).__name__, exc))
            self.app.bump_revision()
        stdout = capture.getvalue()
        output = self._state()
        output.update(status="failed" if error else "executed", stdout=stdout[:STDOUT_MAX],
                      truncated=len(stdout) > STDOUT_MAX)
        if error:
            output["message"] = error
        return output
