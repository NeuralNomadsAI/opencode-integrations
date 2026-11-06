from __future__ import annotations

import collections
from dataclasses import dataclass
import fnmatch
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import socket
import threading
import uuid
from typing import Any, Callable

import hou

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
JOURNAL_LIMIT = 256
COMMAND_LIMIT = 256
TOMBSTONE_LIMIT = 4096
OPERATIONS = (
    "node.create",
    "node.rename",
    "parm.set_value",
    "graph.connect",
    "graph.delete",
)
METHODS = ("list_scripts", "hello", "context", "sync", "apply", "cancel", "resume")
NODE_TYPE_REGISTRY = {
    "Object": frozenset(("cam", "envlight", "geo", "hlight", "light", "null", "subnet")),
    "Sop": frozenset(("box", "grid", "line", "merge", "null", "output", "sphere", "subnet", "xform")),
    "Lop": frozenset(
        (
            "additionalrendervars",
            "assignmaterial",
            "camera",
            "configurelayer",
            "configureprimitive",
            "distantlight",
            "domelight",
            "karma",
            "karmarenderproducts",
            "karmarenderproperties",
            "karmarendersettings",
            "karmastandardrendervars",
            "light",
            "materiallibrary",
            "merge",
            "null",
            "primitive",
            "reference",
            "rendergeometrysettings",
            "renderproduct",
            "rendersettings",
            "rendervar",
            "sphere",
            "sublayer",
            "subnet",
            "usd_rop",
            "usdrender_rop",
            "xform",
        )
    ),
    "Vop": frozenset(
        (
            "collect",
            "materialbuilder",
            "mtlxUsdPreviewSurface",
            "mtlxconstant",
            "mtlxdisplacement",
            "mtlximage",
            "mtlxnormalmap",
            "mtlxstandard_surface",
            "mtlxsurfacematerial",
            "null",
            "parameter",
            "subnet",
        )
    ),
    "Driver": frozenset(("karma", "merge", "null", "subnet")),
}
def capabilities():
    return {
        "operations": [{"name": name, "undoable": True} for name in OPERATIONS],
        "methods": list(METHODS),
        "undo": "grouped_best_effort" if hou.isUIAvailable() else "none",
        "background": not hou.isUIAvailable(),
    }


def capability_hash():
    return hashlib.sha256(json.dumps(capabilities(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def host_version():
    return json.loads((Path(__file__).resolve().parents[2] / "manifest.json").read_text(encoding="utf-8"))["version"]


class HostError(RuntimeError):
    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data or {}


def _json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    try:
        return [_json_value(item) for item in value]
    except TypeError:
        return repr(value)


def _node_key(node: hou.OpNode) -> tuple[str, int]:
    return ("node", int(node.sessionId()))


def _parm_key(parm: hou.Parm) -> tuple[str, int, str]:
    return ("parm", int(parm.node().sessionId()), parm.name())


def _is_valid_name(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_]+", name) is not None


@dataclass
class _PlannedNode:
    parent: hou.OpNode | _PlannedNode
    type_name: str
    name: str
    node: hou.OpNode | None = None


@dataclass
class _PlannedParm:
    owner: _PlannedNode
    name: str


class ScriptState:
    """Revisioned state for Houdini's one current HIP file."""

    _NODE_EVENTS = (
        hou.nodeEventType.BeingDeleted,
        hou.nodeEventType.ChildCreated,
        hou.nodeEventType.ChildDeleted,
        hou.nodeEventType.ChildReordered,
        hou.nodeEventType.FlagChanged,
        hou.nodeEventType.InputRewired,
        hou.nodeEventType.NameChanged,
        hou.nodeEventType.ParmTupleChanged,
        hou.nodeEventType.PositionChanged,
        hou.nodeEventType.SelectionChanged,
        hou.nodeEventType.SpareParmTemplatesChanged,
    )

    def __init__(self, root: hou.OpNode | None = None) -> None:
        self.root = root or hou.node("/")
        if self.root is None:
            raise RuntimeError("Houdini root node is unavailable")
        self.handle = "hip:" + secrets.token_urlsafe(18)
        self.script_handle = self.handle
        self.epoch = str(uuid.uuid4())
        self.revision = 1
        self.quarantined = False
        self._refs_by_key: dict[tuple[Any, ...], str] = {}
        self._objects_by_ref: dict[str, hou.OpNode | hou.Parm] = {}
        self._tombstones: collections.OrderedDict[str, dict[str, Any]] = collections.OrderedDict()
        self._cards: dict[str, dict[str, Any]] = {}
        self._registered_nodes: dict[int, hou.OpNode] = {}
        self._dirty_nodes: set[str] = set()
        self._removed_refs: set[str] = set()
        self._summary_dirty = False
        self._flush_scheduled = False
        self._journal: collections.deque[dict[str, Any]] = collections.deque(maxlen=JOURNAL_LIMIT)
        self._commands: collections.OrderedDict[str, dict[str, Any]] = collections.OrderedDict()
        self._recent: collections.deque[str] = collections.deque(maxlen=12)
        self._cancellation: dict[str, tuple[int, bool]] = {}
        self._cancellation_lock = threading.Lock()
        self._closed = False
        self._register_tree(self.root)
        self._rebuild_cards()

    def close(self) -> None:
        self._closed = True
        for node in list(self._registered_nodes.values()):
            try:
                node.removeEventCallback(self._NODE_EVENTS, self._node_event)
            except (hou.Error, hou.ObjectWasDeleted):
                pass
        self._registered_nodes.clear()
        self._objects_by_ref.clear()
        self._cards.clear()

    def hello(self) -> dict[str, Any]:
        self.flush()
        return {
            "protocol": PROTOCOL_VERSION,
            "script_handle": self.handle,
            "script_epoch": self.epoch,
            "revision": self.revision,
            "capability_hash": capability_hash(),
            "host_version": host_version(),
            "app_version": hou.applicationVersionString(),
            "document": hou.hipFile.basename()[:120],
            "capabilities": capabilities(),
            "operations": list(OPERATIONS),
            "node_types": {category: sorted(types) for category, types in NODE_TYPE_REGISTRY.items()},
            "summary": self.summary(),
        }

    def summary(self) -> dict[str, Any]:
        selection = []
        for node in hou.selectedNodes():
            if self._belongs(node):
                selection.append(self._ref(node))
        file_name = hou.hipFile.path()
        return {
            "script": {
                "name": hou.hipFile.basename(),
                "file_name": file_name,
                "node_count": len(self._cards),
                "modified": bool(hou.hipFile.hasUnsavedChanges()),
            },
            "selection": selection,
            "focus": selection[-1] if selection else None,
            "recent": [ref for ref in self._recent if ref in self._objects_by_ref],
        }

    def context(self, query: dict[str, Any] | None = None) -> dict[str, Any]:
        query = query or {}
        if not isinstance(query, dict):
            raise HostError("invalid_request", "Context requires an object")
        refs, include = query.get("refs", []), query.get("include", [])
        if not isinstance(refs, list) or len(refs) > 50 or not all(isinstance(ref, str) for ref in refs):
            raise HostError("invalid_request", "At most 50 references are accepted")
        if not isinstance(include, list) or len(include) > 20 or not all(isinstance(item, str) for item in include):
            raise HostError("invalid_request", "At most 20 include sections are accepted")
        self.flush()
        requested_revision = query.get("after_revision", query.get("if_revision"))
        if requested_revision is not None and int(requested_revision) > self.revision:
            raise HostError(
                "revision_conflict",
                "Requested revision is newer than the Houdini graph",
                {"current_revision": self.revision},
            )
        limit = max(1, min(int(query.get("limit", 20)), 100))
        include = set(query.get("include", []))
        nodes: list[hou.OpNode] = []
        seen: set[int] = set()
        truncated = False

        def add_node(subject: Any) -> None:
            nonlocal truncated
            if isinstance(subject, hou.Parm):
                subject = subject.node()
            if not isinstance(subject, hou.OpNode) or subject == self.root or not self._belongs(subject):
                return
            key = subject.sessionId()
            if key in seen:
                return
            if len(nodes) >= limit:
                truncated = True
                return
            seen.add(key)
            nodes.append(subject)

        for identifier in query.get("refs", []):
            add_node(self._resolve(identifier))

        match = query.get("match") or {}
        if match:
            name_pattern = str(match.get("name", "*"))
            type_pattern = str(match.get("type", "*"))
            for ref, card in self._cards.items():
                if fnmatch.fnmatchcase(card["name"], name_pattern) and fnmatch.fnmatchcase(
                    card["type"], type_pattern
                ):
                    add_node(self._objects_by_ref.get(ref))
                    if truncated:
                        break

        if not nodes:
            for node in hou.selectedNodes():
                add_node(node)
        if not nodes:
            for node in self.root.children():
                add_node(node)
                if len(nodes) >= limit:
                    break

        radius = max(0, min(int(query.get("radius", 0)), 2))
        for _ in range(radius):
            for node in list(nodes):
                for neighbour in self._neighbours(node):
                    add_node(neighbour)

        entities = [self._node_card(node, include_values="values" in include or "parms" in include) for node in nodes]
        if "affordances" in include:
            for entity in entities:
                entity["affordances"] = list(OPERATIONS)
        result = {
            "script_handle": self.handle,
            "revision": self.revision,
            "capability_hash": capability_hash(),
            "summary": self.summary(),
            "entities": entities,
            "working_set": entities,
            "diagnostics": ["Remote writes are quarantined"] if self.quarantined else [],
            "truncated": truncated,
        }
        if "affordances" in include:
            result["capabilities"] = {
                "operations": list(OPERATIONS),
                "node_types": {category: sorted(types) for category, types in NODE_TYPE_REGISTRY.items()},
            }
        if requested_revision is not None:
            after_revision = int(requested_revision)
            changes = [entry for entry in self._journal if entry["revision"] > after_revision]
            if after_revision == self.revision:
                result["changes"] = []
            elif changes and changes[0]["previous_revision"] <= after_revision:
                result["changes"] = changes
            else:
                result["changes"] = []
                result["reset"] = True
        return result

    def sync(self, after_revision: int | None) -> dict[str, Any]:
        self.flush()
        after_revision = int(after_revision or 0)
        if after_revision == self.revision:
            return {"revision": self.revision, "changes": [], "summary": self.summary()}
        changes = [entry for entry in self._journal if entry["revision"] > after_revision]
        if changes and changes[0]["previous_revision"] <= after_revision:
            return {"revision": self.revision, "changes": changes, "summary": self.summary()}
        return {
            "revision": self.revision,
            "changes": [],
            "reset": True,
            "snapshot": self.context({"limit": 100}),
            "summary": self.summary(),
        }

    def cancellation_state(self, session_id: str) -> tuple[int, bool]:
        with self._cancellation_lock:
            return self._cancellation.get(session_id, (0, False))

    def set_cancelled(self, session_id: str, cancelled: bool) -> None:
        with self._cancellation_lock:
            generation, _ = self._cancellation.get(session_id, (0, False))
            if cancelled:
                generation += 1
            self._cancellation[session_id] = (generation, cancelled)

    def apply(
        self,
        request: dict[str, Any],
        cancellation_generation: int | None = None,
        request_was_cancelled: bool = False,
    ) -> dict[str, Any]:
        self.flush()
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise HostError("invalid_intent", "session_id is required")
        if cancellation_generation is None:
            cancellation_generation = self.cancellation_state(session_id)[0]
        self._check_cancelled(session_id, cancellation_generation, request_was_cancelled)
        if self.quarantined:
            raise HostError("script_quarantined", "Remote writes are disabled until the HIP is reloaded")

        command_id = str(request.get("command_id", ""))
        if not command_id or len(command_id) > 128:
            raise HostError("invalid_intent", "command_id must contain 1 to 128 characters")
        request_digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        previous = self._commands.get(command_id)
        if previous is not None:
            if previous["digest"] != request_digest:
                raise HostError("command_id_reused", "command_id was already used for another intent")
            return previous["result"]
        # ponytail: bounded replay ledger; reload the HIP after 256 successful commands.
        if len(self._commands) >= COMMAND_LIMIT:
            raise HostError("busy", "Command ledger is full; reload the HIP before making more changes")

        expected_revision = int(request.get("if_revision", -1))
        if expected_revision != self.revision:
            raise HostError(
                "revision_conflict",
                "The Houdini graph changed after the intent was prepared",
                {"expected_revision": expected_revision, "current_revision": self.revision},
            )
        operations = request.get("operations")
        if operations is None:
            operations = (request.get("patch") or request.get("intent") or {}).get("operations")
        if not isinstance(operations, list) or not operations or len(operations) > 50:
            raise HostError("invalid_intent", "operations must contain between 1 and 50 items")

        plans, aliases = self._preflight(operations)
        expectations = request.get("expect", [])
        if not isinstance(expectations, list) or len(expectations) > 50:
            raise HostError("invalid_request", "At most 50 expectations are accepted")
        # Expectations are preconditions on the existing graph; operation verification is separate.
        self._verify(expectations, {})
        if request.get("dry_run", True) is not False:
            return {
                "status": "preflight",
                "script_handle": self.handle,
                "revision": self.revision,
                "command_id": command_id,
                "operations": len(plans),
            }
        if request.get("confirm") is not True:
            raise HostError("confirmation_required", "Mutations require dry_run=false and confirm=true")
        if not hou.isUIAvailable():
            raise HostError("mode_conflict", "Headless Houdini mutations are refused because undo is unavailable")

        before = self.revision
        undo_labels_before = tuple(hou.undos.undoLabels())
        completed_operations = 0
        try:
            with hou.undos.group("OpenCode Houdini Apply"):
                for plan in plans:
                    self._check_cancelled(session_id, cancellation_generation, request_was_cancelled)
                    self._execute_plan(plan)
                    completed_operations += 1
            self._check_cancelled(session_id, cancellation_generation, request_was_cancelled)
            verification = self._verify_plans(plans)
            for planned in aliases.values():
                if planned.node is not None:
                    self._mark_node(planned.node)
            self.flush(cause="opencode", command_id=command_id)
        except Exception as error:
            failure = error if isinstance(error, HostError) else HostError("execution_failed", str(error))
            has_undo = len(tuple(hou.undos.undoLabels())) > len(undo_labels_before)
            rollback_error = self._rollback(undo_labels_before) if has_undo else None
            if not has_undo and completed_operations == 0:
                self._reconcile_after_rollback()
                raise HostError(
                    "execution_failed",
                    str(failure),
                    {"cause": failure.code, "current_revision": self.revision},
                ) from error
            if not has_undo:
                rollback_error = RuntimeError("The OpenCode undo item was not available")
            if rollback_error is not None:
                self.quarantined = True
                self._reconcile_after_rollback()
                self.flush(cause="rollback_failed", command_id=command_id)
                raise HostError(
                    "rollback_failed",
                    str(rollback_error),
                    {"cause": failure.code, "current_revision": self.revision},
                ) from rollback_error
            self._reconcile_after_rollback()
            raise HostError(
                "execution_failed_rolled_back",
                str(failure),
                {"cause": failure.code, "current_revision": self.revision},
            ) from error

        result = {
            "status": "applied",
            "script_handle": self.handle,
            "revision": self.revision,
            "revision_before": before,
            "command_id": command_id,
            "verification": verification,
            "summary": self.summary(),
            "aliases": {
                name: self._ref(planned.node)
                for name, planned in aliases.items()
                if planned.node is not None
            },
        }
        self._commands[command_id] = {"digest": request_digest, "result": result}
        self._commands.move_to_end(command_id)
        return result

    def flush(self, cause: str = "user", command_id: str | None = None) -> bool:
        self._flush_scheduled = False
        if not self._dirty_nodes and not self._removed_refs and not self._summary_dirty:
            return False
        put = []
        for ref in sorted(self._dirty_nodes):
            node = self._objects_by_ref.get(ref)
            if isinstance(node, hou.OpNode) and self._belongs(node) and node != self.root:
                card = self._node_card(node)
                previous_card = self._cards.get(ref) or {}
                current_parms = {parm["ref"] for parm in card["parms"]}
                for old_parm in previous_card.get("parms", []):
                    if old_parm["ref"] not in current_parms:
                        self._tombstone_ref(old_parm["ref"], "parm", old_parm["path"])
                self._cards[ref] = card
                put.append(card)
                self._recent.append(ref)
        previous_revision = self.revision
        self.revision += 1
        self._journal.append(
            {
                "previous_revision": previous_revision,
                "revision": self.revision,
                "cause": cause,
                "command_id": command_id,
                "put": put,
                "removed": sorted(self._removed_refs),
            }
        )
        self._dirty_nodes.clear()
        self._removed_refs.clear()
        self._summary_dirty = False
        return True

    def _check_cancelled(self, session_id: str, generation: int, initially_cancelled: bool) -> None:
        if self._closed:
            raise HostError("cancelled", "The Houdini host has stopped")
        current_generation, cancelled = self.cancellation_state(session_id)
        if initially_cancelled or cancelled or current_generation != generation:
            raise HostError("cancelled", "The current OpenCode turn was interrupted")

    def _ref(self, subject: hou.OpNode | hou.Parm) -> str:
        key = _parm_key(subject) if isinstance(subject, hou.Parm) else _node_key(subject)
        ref = self._refs_by_key.get(key)
        if ref is None:
            prefix = "parm" if isinstance(subject, hou.Parm) else ("hip" if subject == self.root else "node")
            ref = prefix + ":" + secrets.token_urlsafe(12)
            self._refs_by_key[key] = ref
        self._objects_by_ref[ref] = subject
        self._tombstones.pop(ref, None)
        return ref

    def _resolve(
        self,
        identifier: Any,
        aliases: dict[str, _PlannedNode] | None = None,
        allow_planned: bool = False,
        planned_paths: dict[str, _PlannedNode] | None = None,
    ) -> hou.OpNode | hou.Parm | _PlannedNode | _PlannedParm:
        aliases = aliases or {}
        planned_paths = planned_paths or {}
        if not isinstance(identifier, str) or not identifier:
            raise HostError("invalid_operation", "Houdini references must be non-empty strings")
        if identifier.startswith("$"):
            root_name, separator, relative = identifier.partition(".")
            planned = aliases.get(root_name[1:])
            if planned is None:
                raise HostError("unknown_ref", "Unknown intent alias " + root_name)
            if not separator:
                if allow_planned or planned.node is not None:
                    return planned.node or planned
                raise HostError("unknown_ref", "Alias has not been created yet: " + identifier)
            if planned.node is not None:
                parm = planned.node.parm(relative)
                if parm is None:
                    raise HostError("unknown_ref", "Alias parameter does not exist: " + identifier)
                return parm
            if allow_planned:
                return _PlannedParm(planned, relative)
            raise HostError("unknown_ref", "Alias has not been created yet: " + identifier)
        subject = self._objects_by_ref.get(identifier)
        if subject is None:
            subject = hou.node(identifier) or hou.parm(identifier)
        if subject is None and allow_planned:
            planned = planned_paths.get(identifier)
            if planned is not None:
                return planned
            node_path, separator, parm_name = identifier.rpartition("/")
            planned = planned_paths.get(node_path)
            if separator and planned is not None:
                return _PlannedParm(planned, parm_name)
        if subject is None:
            raise HostError("unknown_ref", "Unknown Houdini reference or path: " + identifier)
        if isinstance(subject, hou.OpNode) and not self._belongs(subject):
            raise HostError("unknown_ref", "Node is not in the current HIP: " + identifier)
        return subject

    def _belongs(self, node: hou.OpNode) -> bool:
        try:
            return node == self.root or node.path().startswith("/")
        except hou.ObjectWasDeleted:
            return False

    def _register_tree(self, node: hou.OpNode) -> None:
        for item in (node,) + tuple(node.allSubChildren()):
            session_id = int(item.sessionId())
            self._ref(item)
            if session_id in self._registered_nodes:
                continue
            item.addEventCallback(self._NODE_EVENTS, self._node_event)
            self._registered_nodes[session_id] = item

    def _rebuild_cards(self) -> None:
        self._cards = {}
        for node in self.root.allSubChildren():
            self._cards[self._ref(node)] = self._node_card(node)

    def _node_card(self, node: hou.OpNode, include_values: bool = False) -> dict[str, Any]:
        parms = []
        for parm in node.parms()[:250]:
            template = parm.parmTemplate()
            entry: dict[str, Any] = {
                "ref": self._ref(parm),
                "path": parm.path(),
                "name": parm.name(),
                "type": str(template.type()),
                "disabled": bool(parm.isDisabled()),
                "locked": bool(parm.isLocked()),
            }
            if include_values:
                try:
                    entry["value"] = _json_value(parm.eval())
                except hou.Error as error:
                    entry["value_error"] = str(error)
            parms.append(entry)
        connections = []
        for connection in node.inputConnections():
            input_node = connection.inputNode()
            if input_node is None:
                continue
            connections.append(
                {
                    "input_index": connection.inputIndex(),
                    "source": self._ref(input_node),
                    "output_index": connection.outputIndex(),
                }
            )
        parent = node.parent()
        position = node.position()
        return {
            "ref": self._ref(node),
            "path": node.path(),
            "name": node.name(),
            "type": node.type().nameWithCategory(),
            "parent": self._ref(parent) if parent is not None else None,
            "position": [position[0], position[1]],
            "inputs": connections,
            "parms": parms,
            "parms_truncated": len(node.parms()) > 250,
        }

    def _neighbours(self, node: hou.OpNode) -> list[hou.OpNode]:
        result = []
        seen = set()
        for other in tuple(item for item in node.inputs() if item is not None) + tuple(node.outputs()):
            if other is None:
                continue
            key = other.sessionId()
            if key not in seen:
                seen.add(key)
                result.append(other)
        return result

    def _preflight(
        self, operations: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, _PlannedNode]]:
        plans = []
        aliases: dict[str, _PlannedNode] = {}
        planned_paths: dict[str, _PlannedNode] = {}
        deleted: set[int] = set()
        planned_names: set[tuple[int, str]] = set()
        for operation in operations:
            if not isinstance(operation, dict):
                raise HostError("invalid_operation", "Each operation must be an object")
            kind = str(operation.get("op", ""))
            if kind in ("node.create", "create_node"):
                parent = self._resolve(operation.get("parent", self._ref(self.root)), aliases, True, planned_paths)
                if not isinstance(parent, (hou.OpNode, _PlannedNode)):
                    raise HostError("type_mismatch", "node.create parent must be a network node")
                category = self._child_type_category(parent)
                if category is None:
                    raise HostError("read_only", "The requested parent cannot contain nodes")
                type_name = operation.get("type")
                allowed_types = NODE_TYPE_REGISTRY.get(category.name(), frozenset())
                if not isinstance(type_name, str) or type_name not in allowed_types:
                    raise HostError(
                        "unsupported_node_type",
                        "Node type is not in the host registry for " + category.name() + ": " + str(type_name),
                    )
                if type_name not in category.nodeTypes():
                    raise HostError("unsupported_node_type", "Node type is not valid under " + parent.path())
                name = operation.get("name") or type_name.rsplit("::", 1)[-1]
                if not isinstance(name, str) or not _is_valid_name(name):
                    raise HostError("invalid_operation", "node.create name must contain only letters, numbers, and underscores")
                if isinstance(parent, hou.OpNode) and parent.node(name) is not None:
                    raise HostError("name_conflict", "A child named " + name + " already exists")
                parent_key = parent.sessionId() if isinstance(parent, hou.OpNode) else id(parent)
                name_key = (parent_key, name)
                if name_key in planned_names:
                    raise HostError("name_conflict", "The intent creates duplicate child name " + name)
                planned_names.add(name_key)
                planned = _PlannedNode(parent, type_name, name)
                planned_paths[self._planned_path(planned)] = planned
                alias = operation.get("bind")
                if alias:
                    if not isinstance(alias, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias) is None:
                        raise HostError("invalid_operation", "Intent aliases must be simple identifiers")
                    if alias in aliases:
                        raise HostError("invalid_operation", "Duplicate intent alias: " + alias)
                    aliases[alias] = planned
                plans.append({"kind": "create", "planned": planned})
            elif kind in ("node.rename", "rename"):
                subject = self._resolve(operation.get("target"), aliases, True, planned_paths)
                if not isinstance(subject, (hou.OpNode, _PlannedNode)) or subject == self.root:
                    raise HostError("type_mismatch", "node.rename requires a non-root node")
                self._check_not_deleted(subject, deleted)
                name = operation.get("name")
                if not isinstance(name, str) or not _is_valid_name(name):
                    raise HostError("invalid_operation", "node.rename name must contain only letters, numbers, and underscores")
                parent = subject.parent() if isinstance(subject, hou.OpNode) else subject.parent
                collision = parent.node(name) if isinstance(parent, hou.OpNode) else None
                if collision is not None and collision != subject:
                    raise HostError("name_conflict", "A sibling named " + name + " already exists")
                parent_key = parent.sessionId() if isinstance(parent, hou.OpNode) else id(parent)
                name_key = (parent_key, name)
                if name_key in planned_names and not (
                    isinstance(subject, _PlannedNode) and subject.name == name
                ):
                    raise HostError("name_conflict", "The intent already reserves sibling name " + name)
                planned_names.add(name_key)
                if isinstance(subject, _PlannedNode):
                    planned_paths[self._planned_path(_PlannedNode(parent, subject.type_name, name))] = subject
                plans.append({"kind": "rename", "subject": subject, "name": name})
            elif kind in ("parm.set_value", "set_value"):
                parm = self._resolve(operation.get("target"), aliases, True, planned_paths)
                if not isinstance(parm, (hou.Parm, _PlannedParm)):
                    raise HostError("type_mismatch", "parm.set_value requires a parameter")
                if "value" not in operation:
                    raise HostError("invalid_operation", "parm.set_value requires value")
                if isinstance(parm, hou.Parm):
                    self._check_not_deleted(parm.node(), deleted)
                    if parm.isLocked():
                        raise HostError("read_only", "The target parameter is locked")
                    value = self._coerce_value(parm, operation["value"])
                else:
                    node_type = self._node_type(parm.owner)
                    template = self._planned_parm_template(node_type, parm.name)
                    if template is None:
                        raise HostError(
                            "unknown_ref", "Created node type has no parameter named " + parm.name
                        )
                    value = self._coerce_data_type(template.dataType(), operation["value"])
                plans.append({"kind": "set_value", "parm": parm, "value": value})
            elif kind in ("graph.connect", "connect"):
                source = self._resolve(operation.get("source"), aliases, True, planned_paths)
                destination = self._resolve(operation.get("destination"), aliases, True, planned_paths)
                if not isinstance(source, (hou.OpNode, _PlannedNode)) or not isinstance(
                    destination, (hou.OpNode, _PlannedNode)
                ):
                    raise HostError("type_mismatch", "graph.connect requires source and destination nodes")
                self._check_not_deleted(source, deleted)
                self._check_not_deleted(destination, deleted)
                input_index = int(operation.get("input_index", 0))
                output_index = int(operation.get("output_index", 0))
                if input_index < 0 or output_index < 0:
                    raise HostError("invalid_connection", "Connection indexes must be non-negative")
                source_parent = source.parent() if isinstance(source, hou.OpNode) else source.parent
                destination_parent = (
                    destination.parent() if isinstance(destination, hou.OpNode) else destination.parent
                )
                if source_parent != destination_parent:
                    raise HostError("invalid_connection", "Connected nodes must share a network parent")
                source_type = self._node_type(source)
                destination_type = self._node_type(destination)
                if destination_type.maxNumInputs() >= 0 and input_index >= destination_type.maxNumInputs():
                    raise HostError("invalid_connection", "Destination input index is out of range")
                if source_type.maxNumOutputs() >= 0 and output_index >= source_type.maxNumOutputs():
                    raise HostError("invalid_connection", "Source output index is out of range")
                plans.append(
                    {
                        "kind": "connect",
                        "source": source,
                        "destination": destination,
                        "input_index": input_index,
                        "output_index": output_index,
                    }
                )
            elif kind in ("graph.delete", "delete"):
                subject = self._resolve(operation.get("target"), aliases, True, planned_paths)
                if not isinstance(subject, hou.OpNode) or subject == self.root:
                    raise HostError("read_only", "Only existing non-root nodes can be deleted")
                self._check_not_deleted(subject, deleted)
                if subject.isInsideLockedHDA() and not subject.isEditableInsideLockedHDA():
                    raise HostError("read_only", "Nodes inside this locked asset cannot be deleted")
                deleted.add(subject.sessionId())
                plans.append({"kind": "delete", "subject": subject, "path": subject.path()})
            else:
                raise HostError("unsupported_operation", "Unsupported Houdini operation: " + kind)
        return plans, aliases

    def _check_not_deleted(self, subject: hou.OpNode | _PlannedNode, deleted: set[int]) -> None:
        if isinstance(subject, hou.OpNode):
            current = subject
            while current is not None:
                if current.sessionId() in deleted:
                    raise HostError("invalid_operation_order", "An operation targets a node deleted earlier in the intent")
                current = current.parent()

    def _coerce_value(self, parm: hou.Parm, value: Any) -> Any:
        return self._coerce_data_type(parm.parmTemplate().dataType(), value)

    def _coerce_data_type(self, data_type: hou.parmData, value: Any) -> Any:
        if data_type == hou.parmData.Int:
            if isinstance(value, bool):
                return int(value)
            if not isinstance(value, int):
                raise HostError("type_mismatch", "Integer parameters require JSON integers")
            return value
        if data_type == hou.parmData.Float:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise HostError("type_mismatch", "Float parameters require finite JSON numbers")
            return float(value)
        if data_type == hou.parmData.String:
            raise HostError("permission_denied", "String parameter writes are disabled: they can contain executable expressions")
        raise HostError("unsupported_value_type", "The parameter type cannot be set safely")

    def _node_type(self, subject: hou.OpNode | _PlannedNode) -> hou.NodeType:
        if isinstance(subject, hou.OpNode):
            return subject.type()
        category = self._child_type_category(subject.parent)
        if category is None:
            raise HostError("read_only", "The requested parent cannot contain nodes")
        return category.nodeTypes()[subject.type_name]

    def _child_type_category(self, subject: hou.OpNode | _PlannedNode) -> hou.NodeTypeCategory | None:
        if isinstance(subject, hou.OpNode):
            return subject.childTypeCategory()
        return self._node_type(subject).childTypeCategory()

    def _planned_path(self, subject: _PlannedNode) -> str:
        parent = subject.parent
        parent_path = parent.path() if isinstance(parent, hou.OpNode) else self._planned_path(parent)
        return parent_path.rstrip("/") + "/" + subject.name

    def _planned_parm_template(self, node_type: hou.NodeType, parm_name: str) -> hou.ParmTemplate | None:
        group = node_type.parmTemplateGroup()
        exact = group.find(parm_name)
        if exact is not None:
            return exact
        suffixes = {
            hou.parmNamingScheme.XYZW: "xyzw",
            hou.parmNamingScheme.RGBA: "rgba",
            hou.parmNamingScheme.UVW: "uvw",
            hou.parmNamingScheme.XYWH: "xywh",
            hou.parmNamingScheme.MinMax: ("min", "max"),
            hou.parmNamingScheme.MaxMin: ("max", "min"),
            hou.parmNamingScheme.StartEnd: ("start", "end"),
            hou.parmNamingScheme.BeginEnd: ("begin", "end"),
        }
        for template in group.entriesWithoutFolders():
            count = template.numComponents()
            if count <= 1:
                continue
            scheme = template.namingScheme()
            if scheme == hou.parmNamingScheme.Base1:
                names = tuple(template.name() + str(index + 1) for index in range(count))
            else:
                parts = suffixes.get(scheme, ())
                names = tuple(template.name() + str(parts[index]) for index in range(min(count, len(parts))))
            if parm_name in names:
                return template
        return None

    def _actual_node(self, subject: hou.OpNode | _PlannedNode) -> hou.OpNode:
        if isinstance(subject, hou.OpNode):
            return subject
        if subject.node is None:
            raise HostError("invalid_operation_order", "A referenced node has not been created")
        return subject.node

    def _actual_parm(self, subject: hou.Parm | _PlannedParm) -> hou.Parm:
        if isinstance(subject, hou.Parm):
            return subject
        node = self._actual_node(subject.owner)
        parm = node.parm(subject.name)
        if parm is None:
            raise HostError("unknown_ref", "Created node has no parameter named " + subject.name)
        return parm

    def _execute_plan(self, plan: dict[str, Any]) -> None:
        kind = plan["kind"]
        if kind == "create":
            planned = plan["planned"]
            parent = self._actual_node(planned.parent)
            planned.node = parent.createNode(
                planned.type_name, node_name=planned.name, exact_type_name=True
            )
            self._register_tree(planned.node)
        elif kind == "rename":
            node = self._actual_node(plan["subject"])
            node.setName(plan["name"], unique_name=False)
        elif kind == "set_value":
            parm = self._actual_parm(plan["parm"])
            parm.set(self._coerce_value(parm, plan["value"]))
        elif kind == "connect":
            destination = self._actual_node(plan["destination"])
            source = self._actual_node(plan["source"])
            destination.setInput(plan["input_index"], source, plan["output_index"])
        elif kind == "delete":
            plan["subject"].destroy()

    def _verify_plans(self, plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for plan in plans:
            kind = plan["kind"]
            if kind == "create":
                node = self._actual_node(plan["planned"])
                expected_name = plan["planned"].name
                for later in plans:
                    if later["kind"] == "rename" and later["subject"] is plan["planned"]:
                        expected_name = later["name"]
                ok = node.parent() == self._actual_node(plan["planned"].parent) and node.name() == expected_name
            elif kind == "rename":
                ok = self._actual_node(plan["subject"]).name() == plan["name"]
            elif kind == "set_value":
                parm = self._actual_parm(plan["parm"])
                ok = _json_value(parm.eval()) == _json_value(self._coerce_value(parm, plan["value"]))
            elif kind == "connect":
                destination = self._actual_node(plan["destination"])
                connections = {
                    (item.inputIndex(), item.outputIndex(), input_node.sessionId())
                    for item in destination.inputConnections()
                    for input_node in (item.inputNode(),)
                    if input_node is not None
                }
                ok = (
                    plan["input_index"],
                    plan["output_index"],
                    self._actual_node(plan["source"]).sessionId(),
                ) in connections
            else:
                ok = hou.node(plan["path"]) is None
            if not ok:
                raise HostError("verification_failed", "Operation postcondition failed: " + kind)
            results.append({"predicate": kind, "ok": True})
        return results

    def _verify(
        self, expectations: list[dict[str, Any]] | None, aliases: dict[str, _PlannedNode]
    ) -> list[dict[str, Any]]:
        results = []
        for expectation in expectations or []:
            if not isinstance(expectation, dict):
                raise HostError("invalid_expectation", "Each expectation must be an object")
            predicate = expectation.get("predicate")
            expected = expectation.get("value")
            if predicate == "exists":
                try:
                    subject = self._resolve(expectation.get("subject"), aliases)
                    observed = isinstance(subject, (hou.OpNode, hou.Parm))
                except HostError as error:
                    if error.code != "unknown_ref":
                        raise
                    observed = False
            elif predicate == "name_equals":
                subject = self._resolve(expectation.get("subject"), aliases)
                if not isinstance(subject, hou.OpNode):
                    raise HostError("type_mismatch", "name_equals requires a node")
                observed = subject.name()
            elif predicate == "value_equals":
                subject = self._resolve(expectation.get("subject"), aliases)
                if not isinstance(subject, hou.Parm):
                    raise HostError("type_mismatch", "value_equals requires a parameter")
                observed = _json_value(subject.eval())
            elif predicate == "connected_to":
                subject = self._resolve(expectation.get("subject"), aliases)
                expected_node = self._resolve(expected, aliases)
                if not isinstance(subject, hou.OpNode) or not isinstance(expected_node, hou.OpNode):
                    raise HostError("type_mismatch", "connected_to requires nodes")
                input_index = int(expectation.get("input_index", 0))
                inputs = subject.inputs()
                observed = input_index < len(inputs) and inputs[input_index] == expected_node
                expected = True
            else:
                raise HostError("unsupported_expectation", "Unsupported verification predicate: " + str(predicate))
            ok = observed is True if predicate == "exists" and expected is None else observed == expected
            if not ok:
                raise HostError(
                    "expectation_failed",
                    "Precondition failed: " + str(predicate),
                    {"observed": observed, "expected": expected},
                )
            results.append({"predicate": predicate, "ok": True, "observed": observed})
        return results

    def _rollback(self, labels_before: tuple[str, ...]) -> Exception | None:
        try:
            labels_after = tuple(hou.undos.undoLabels())
            if len(labels_after) <= len(labels_before):
                raise RuntimeError("The OpenCode undo item was not available")
            hou.undos.performUndo()
            # A no-op undo item prevents the failed remote transaction being redone later.
            barrier_key = "__opencode_rollback_" + uuid.uuid4().hex
            with hou.undos.group("OpenCode Rollback Barrier"):
                self.root.setUserData(barrier_key, "1")
                self.root.destroyUserData(barrier_key)
            return None
        except Exception as error:
            return error

    def _reconcile_after_rollback(self) -> None:
        self._dirty_nodes.clear()
        self._removed_refs.clear()
        self._summary_dirty = False
        self._objects_by_ref = {
            ref: subject
            for ref, subject in self._objects_by_ref.items()
            if (
                isinstance(subject, hou.OpNode)
                and self._belongs(subject)
                or isinstance(subject, hou.Parm)
                and self._parm_belongs(subject)
            )
        }
        self._register_tree(self.root)
        self._rebuild_cards()

    def _mark_node(self, node: hou.OpNode | None) -> None:
        if node is None or node == self.root:
            self._summary_dirty = True
        elif self._belongs(node):
            self._dirty_nodes.add(self._ref(node))
        self._schedule_flush()

    def _drop_tree(self, node: hou.OpNode) -> None:
        try:
            path = node.path()
            session_id = node.sessionId()
        except hou.ObjectWasDeleted:
            return
        affected = [
            (ref, card)
            for ref, card in self._cards.items()
            if card["path"] == path or card["path"].startswith(path + "/")
        ]
        for ref, card in affected:
            subject = self._objects_by_ref.pop(ref, None)
            self._cards.pop(ref, None)
            self._tombstone_ref(ref, "node", card["path"])
            for parm in card.get("parms", []):
                self._tombstone_ref(parm["ref"], "parm", parm["path"])
            if isinstance(subject, hou.OpNode):
                self._registered_nodes.pop(subject.sessionId(), None)
        self._registered_nodes.pop(session_id, None)

    def _tombstone_ref(self, ref: str, kind: str, path: str) -> None:
        self._objects_by_ref.pop(ref, None)
        self._removed_refs.add(ref)
        self._tombstones[ref] = {"kind": kind, "path": path}
        self._tombstones.move_to_end(ref)
        while len(self._tombstones) > TOMBSTONE_LIMIT:
            old_ref, _ = self._tombstones.popitem(last=False)
            for key, value in list(self._refs_by_key.items()):
                if value == old_ref:
                    self._refs_by_key.pop(key, None)

    def _parm_belongs(self, parm: hou.Parm) -> bool:
        try:
            return self._belongs(parm.node())
        except hou.ObjectWasDeleted:
            return False

    def _node_event(self, node: hou.OpNode, event_type: hou.nodeEventType, **kwargs: Any) -> None:
        if self._closed:
            return
        if event_type == hou.nodeEventType.ChildCreated:
            child = kwargs.get("child_node")
            if child is not None:
                self._register_tree(child)
                self._mark_node(child)
            self._mark_node(node)
        elif event_type in (hou.nodeEventType.ChildDeleted, hou.nodeEventType.BeingDeleted):
            child = kwargs.get("child_node", node)
            self._drop_tree(child)
            self._mark_node(node if event_type == hou.nodeEventType.ChildDeleted else node.parent())
        elif event_type == hou.nodeEventType.NameChanged:
            self._mark_node(node)
            try:
                for child in node.allSubChildren():
                    self._mark_node(child)
            except hou.ObjectWasDeleted:
                pass
        elif event_type in (hou.nodeEventType.SelectionChanged,):
            self._summary_dirty = True
            if node.isSelected():
                self._recent.append(self._ref(node))
            self._schedule_flush()
        else:
            self._mark_node(node)

    def _schedule_flush(self) -> None:
        if self._flush_scheduled or not hou.isUIAvailable() or not hasattr(hou, "ui"):
            return
        self._flush_scheduled = True

        def callback() -> None:
            if not self._closed:
                self.flush()

        hou.ui.postEventCallback(callback)


class UIThreadGate:
    def __init__(self) -> None:
        self._ui_thread = threading.get_ident()

    def run(self, callback: Callable[[], Any]) -> Any:
        if not hou.isUIAvailable() or not hasattr(hou, "ui") or threading.get_ident() == self._ui_thread:
            return callback()
        complete = threading.Event()
        expired = threading.Event()
        result: list[Any] = []

        def invoke() -> None:
            if expired.is_set():
                complete.set()
                return
            try:
                result.append((True, callback()))
            except Exception as error:
                result.append((False, error))
            finally:
                complete.set()

        if hasattr(hou.ui, "postEventCallback"):
            hou.ui.postEventCallback(invoke)
        else:
            def event_loop_callback() -> None:
                hou.ui.removeEventLoopCallback(event_loop_callback)
                invoke()

            hou.ui.addEventLoopCallback(event_loop_callback)
        if not complete.wait(120):
            expired.set()
            raise HostError("ui_timeout", "Timed out waiting for Houdini's UI thread")
        succeeded, value = result[0]
        if not succeeded:
            raise value
        return value


class Host:
    def __init__(self) -> None:
        self.ui = UIThreadGate()
        self.state = ScriptState()
        hou.hipFile.addEventCallback(self._hip_event)

    def close(self) -> None:
        try:
            hou.hipFile.removeEventCallback(self._hip_event)
        except hou.Error:
            pass
        self.state.close()

    def reset(self) -> None:
        self.state.close()
        self.state = ScriptState()

    def script_handle(self, script: Any = None) -> str:
        return self.state.handle

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method in ("cancel", "resume"):
            state = self._require_state(params)
            session_id = params.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise HostError("invalid_request", "session_id is required")
            state.set_cancelled(session_id, method == "cancel")
            return {"cancelled": method == "cancel"}

        cancellation = None
        state = self._require_state(params) if method not in ("list_scripts", "hello") else self.state
        if method == "apply":
            session_id = params.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise HostError("invalid_request", "session_id is required")
            cancellation = state.cancellation_state(session_id)

        def execute() -> Any:
            current = self.state
            if current._closed:
                raise HostError("busy", "The Houdini host has stopped")
            if method == "list_scripts":
                return [current.hello()]
            if state is not current:
                raise HostError("unknown_script", "Unknown or expired script_handle")
            if method == "hello":
                return state.hello()
            if method == "context":
                return state.context(params)
            if method == "sync":
                return state.sync(params.get("after_revision", 0))
            if method == "apply":
                return state.apply(params, cancellation[0], cancellation[1])
            raise HostError("unknown_method", "Unknown Houdini host method: " + method)

        return self.ui.run(execute)

    def _require_state(self, params: dict[str, Any]) -> ScriptState:
        if str(params.get("script_handle", "")) != self.state.handle:
            raise HostError("unknown_script", "Unknown or expired script_handle")
        return self.state

    def _hip_event(self, event_type: hou.hipFileEventType) -> None:
        if event_type in (hou.hipFileEventType.AfterLoad, hou.hipFileEventType.AfterClear):
            self.reset()
        elif event_type in (hou.hipFileEventType.AfterSave, hou.hipFileEventType.AfterMerge):
            self.state._summary_dirty = True
            self.state._schedule_flush()


class HostServer:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.token = secrets.token_hex(32)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(16)
        self.port = self.socket.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._serve, name="OpenCodeHoudiniHost", daemon=True)
        self.thread.start()

    def info(self) -> dict[str, Any]:
        return {"host": "127.0.0.1", "port": self.port, "token": self.token, "protocol": PROTOCOL_VERSION}

    def close(self) -> None:
        self.running = False
        self.token = secrets.token_hex(32)
        # Wake a blocking accept before closing the listener on every platform.
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass

    def _serve(self) -> None:
        while self.running:
            try:
                client, _ = self.socket.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _handle_client(self, client: socket.socket) -> None:
        request_id = None
        try:
            client.settimeout(130)
            buffer = b""
            while b"\n" not in buffer:
                chunk = client.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_REQUEST_BYTES:
                    raise HostError("request_too_large", "Houdini host request exceeded the size limit")
            request = json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
            request_id = request.get("id")
            if not secrets.compare_digest(str(request.get("token", "")), self.token):
                raise HostError("unauthorized", "Invalid Houdini host token")
            result = self.host.dispatch(str(request.get("method", "")), request.get("params") or {})
            response = {"id": request_id, "ok": True, "result": result}
            if len(json.dumps(response).encode("utf-8")) > 8 * 1024 * 1024:
                raise HostError("invalid_request", "Narrow the requested working set")
        except HostError as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {"code": {"unknown_script": "stale_handle", "revision_conflict": "stale_revision", "script_quarantined": "quarantined", "unknown_method": "method_not_found", "ui_timeout": "busy"}.get(error.code, error.code if error.code in ("unauthorized", "invalid_request", "expectation_failed", "mode_conflict", "permission_denied") else "invalid_args"), "message": "Houdini refused the request"},
            }
        except Exception as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {"code": "internal", "message": "Houdini host request failed"},
            }
        try:
            client.sendall((json.dumps(response, allow_nan=False) + "\n").encode("utf-8"))
        finally:
            client.close()
