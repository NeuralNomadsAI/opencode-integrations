# SPDX-License-Identifier: GPL-3.0-or-later
"""Composable native plans: strict request shape, symbolic preflight, final-state verification.

Pure Python, no bpy. The Blender adapter (native_scene.NativeExecution) supplies a Snapshot: the
structural capture of the document plus facts about every referenced entity, and executes the
normalized steps. Six intents compose: entity.create, entity.rename, property.set,
relation.link, relation.unlink and object.parent. None removes an ID, a modifier or a constraint:
every effect is a creation, a name, a targeted property value or an edge that the structural capture
monitors, so incoming references of existing IDs are never dropped by a plan.
"""

import hashlib
import json
import math
import re

from . import limits

from .native_refs import ALIAS, BatchAliases
from .wire import HostFailure

ABS_TOLERANCE = limits.value("abs_tolerance")
REL_TOLERANCE = limits.value("rel_tolerance")
# World matrices are float32 products: compared within 1e-5 x max(1, largest entry).
MATRIX_TOLERANCE = limits.value("matrix_tolerance")
# Sockets of a node type are only known once created: a node reserves this many elements.
NODE_SOCKET_ALLOWANCE = limits.value("node_socket_allowance")
MAX_OPERATIONS = limits.value("operations")
NAME_BYTES = limits.value("name_bytes")
ID_CHARS = limits.value("id_chars")
COMMAND_ID = re.compile(r"^[A-Za-z0-9._:-]{1,%d}$" % ID_CHARS)
REF = re.compile(r"^rna-[0-9a-f]{32}$")
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
RECIPE = re.compile(r"^(collection|object|primitive:[A-Z]{1,32}|node:[A-Za-z][A-Za-z0-9_]{0,127}|"
                    r"(modifier|constraint):[A-Z][A-Z0-9_]{0,63})$")
# A published identifier, or a qualified one-level path such as dof.focus_distance (never a call).
PROPERTY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}(\.[A-Za-z][A-Za-z0-9_]{0,63})?$")
OWNED = ("node", "modifier", "constraint")  # recipes created inside an owner
FIELDS = frozenset(("script_handle", "script_epoch", "schema_hash", "if_revision", "command_id",
                    "operations", "expect", "ensure", "summary"))
NATIVE_FIELDS = frozenset(("script_epoch", "schema_hash", "ensure"))
OPS = {
    "entity.create": (("recipe", "name", "bind"), ("owner", "data")),
    "entity.rename": (("target", "name"), ()),
    "property.set": (("target", "property", "value"), ()),
    "relation.link": (("owner", "relation", "target"), ()),
    "relation.unlink": (("owner", "relation", "target"), ()),
    "object.parent": (("target", "parent", "keep_world"), ()),
}
CLAUSES = {"exists": (), "equals": ("property", "value"), "linked": ("relation", "target"),
           "unlinked": ("relation", "target"), "parent": ("value",)}
RELATIONS = {"children": "Collection", "objects": "Object"}
FAMILIES = ("Collection", "Object", "Node", "NodeTree", "Scene", "Camera", "Light", "World", "Modifier", "Constraint")
STRUCT_FAMILIES = ("Modifier", "Constraint")  # owned by an object, named uniquely there, never renamed here
SUPPORTED_TREES = ("GeometryNodeTree", "ShaderNodeTree")
IDENTITY = tuple(tuple(float(i == j) for j in range(4)) for i in range(4))
# Capture tuple of an object (native_scene.capture).
DATA, PARENT, PARENT_TYPE, WORLD, BASIS, PARENT_INVERSE, ROTATION_MODE = 0, 1, 2, 5, 6, 7, 9
CHANNELS = (6, 8, 10, 11, 12, 13)  # basis, location, rotations, scale: rewritten by an unparent
MODIFIERS, CONSTRAINTS, RIGID_BODY, RIGID_CONSTRAINT = 18, 19, 21, 22
PHYSICS = slice(4, 6)  # scene tuple: rigid body world collection and constraints collection
INCLUDE = "INCLUDE"  # light-linking state of a new collection edge


def fail(message, code="invalid_args"):
    raise HostFailure(code, message)


def same_state(a, b):
    """Explicit numeric tolerance; booleans/integers/keys remain exact."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE)
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same_state(a[k], b[k]) for k in a)
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(same_state(x, y) for x, y in zip(a, b))
    return a == b


def _label(state, key):
    entry = state["ids"].get(key)
    return "%s %r" % (entry[1], entry[0]) if entry else str(key)


def restoration_residue(before, after):
    """(proven, may_have_changed, kept_linked) for a capture before a change and the one after its undo.

    Blender keeps linked data outside its undo: a run that linked a library (or entered a paint mode, which links
    the Essentials brush) leaves its Library and linked IDs after the undo, used by nothing local and never
    written on save. Those IDs, present after only, are kept_linked; any other difference (a local ID added,
    removed or changed, a linked ID still used by local data) is may_have_changed and the restoration is unproven.
    """
    if not (before.complete and after.complete):
        return False, ["the capture before the change was incomplete (%s)" % (before.reason or after.reason)], []
    old, new = before.state, after.state
    linked = {key for key in set(new["ids"]) - set(old["ids"])
              if new["ids"][key][3] is not None or new["ids"][key][1] == "Library"}
    trimmed = {section: {key: value for key, value in entries.items()
                         if (key[0] if isinstance(key, tuple) else key) not in linked}
               for section, entries in new.items()}
    changes = []
    for section in old:
        for key in sorted(set(old[section]) | set(trimmed[section]), key=repr):
            owner = key[0] if isinstance(key, tuple) else key
            label = _label(old if owner in old["ids"] else new, owner)
            if key not in trimmed[section]:
                changes.append("%s removed" % label if section == "ids" else "%s: %s differs" % (label, section))
            elif key not in old[section]:
                changes.append("%s added" % label if section == "ids" else "%s: %s differs" % (label, section))
            elif not same_state(old[section][key], trimmed[section][key]):
                changes.append("%s: %s differ" % (label, {"ids": "name or library", "objects": "transform, parenting, "
                               "modifiers, constraints or materials", "collections": "members", "scenes": "camera, "
                               "frame or collection", "trees": "nodes or links", "poses": "pose or keys",
                               "properties": "values"}[section]))
    unique = list(dict.fromkeys(changes))
    kept = sorted(_label(new, key) for key in linked)
    return not unique, unique, kept


def matrix_close(a, b):
    scale = max(1.0, *(abs(v) for row in (*a, *b) for v in row))
    return all(math.isfinite(x) and abs(x - y) <= MATRIX_TOLERANCE * scale
               for ra, rb in zip(a, b) for x, y in zip(ra, rb))


def _columns(m):
    return [[m[r][c] for r in range(3)] for c in range(3)]


def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def _affine(m):
    return all(math.isfinite(v) for row in m for v in row) and matrix_close(m[3:], IDENTITY[3:])


def _nonsingular(m):
    cols = _columns(m)
    norms = [_norm(c) for c in cols]
    if min(norms) < 1e-9:
        return False
    a, b, c = cols
    det = a[0] * (b[1] * c[2] - b[2] * c[1]) - b[0] * (a[1] * c[2] - a[2] * c[1]) + c[0] * (a[1] * b[2] - a[2] * b[1])
    return abs(det) / (norms[0] * norms[1] * norms[2]) > 1e-6


def _unsheared(m):
    cols = _columns(m)
    return all(abs(sum(x * y for x, y in zip(cols[i], cols[j]))) <= 1e-5 * _norm(cols[i]) * _norm(cols[j])
               for i, j in ((0, 1), (0, 2), (1, 2)))


# -- request shape ---------------------------------------------------------------------------
class Request:
    def __init__(self, params):
        self.command_id = params["command_id"]
        self.epoch = params["script_epoch"]
        self.schema = params["schema_hash"]
        self.revision = params["if_revision"]
        self.operations = [(op["op"], dict(op.get("args", {}))) for op in params["operations"]]
        self.expect = list(params.get("expect", []))
        self.ensure = list(params.get("ensure", []))
        summary = params.get("summary")
        self.summary = summary.strip()[:80] if isinstance(summary, str) and summary.strip() else None
        self.refs, self.properties = set(), set()

    def digest(self):
        change = {"mode": "native", "operations": self.operations, "expect": self.expect, "ensure": self.ensure,
                  "summary": self.summary, "schema_hash": self.schema, "script_epoch": self.epoch}
        return hashlib.sha256(json.dumps(change, sort_keys=True).encode("utf-8")).hexdigest()


def is_native(params, native_ops):
    operations = params.get("operations")
    named = [op.get("op") for op in operations if isinstance(op, dict)] if isinstance(operations, list) else []
    return bool(params.keys() & NATIVE_FIELDS) or any(name in native_ops for name in named)


def _handle(request, value, label, alias=True, null=False):
    if value is None and null:
        return
    if not isinstance(value, str):
        fail("%s must be a reference%s." % (label, " or null" if null else ""))
    if value.startswith("$"):
        if not alias or not ALIAS.fullmatch(value[1:]):
            fail("%s: aliases are simple $identifiers of earlier creations%s." % (label, "" if alias else " and are not allowed here"))
    elif REF.fullmatch(value):
        request.refs.add(value)
    else:
        fail("%s must be an opaque reference from context or describe." % label)


def _json(value, label, depth=0):
    if depth > 3 or isinstance(value, dict):
        fail("%s must be a JSON scalar or a short array." % label)
    if isinstance(value, list):
        if len(value) > 32:
            fail("%s has too many values." % label)
        for item in value:
            _json(item, label, depth + 1)
    elif not (value is None or isinstance(value, (bool, int, float, str))):
        fail("%s has an unsupported JSON type." % label)


def _name(value, label):
    if (not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > NAME_BYTES or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        fail("%s must be a printable name of 1 to %d bytes." % (label, NAME_BYTES))


def _clause(request, clause, label, alias):
    if not isinstance(clause, dict) or not isinstance(clause.get("predicate"), str) or clause["predicate"] not in CLAUSES:
        fail("%s needs a predicate among %s." % (label, ", ".join(sorted(CLAUSES))))
    fields = CLAUSES[clause["predicate"]]
    if clause.keys() != {"ref", "predicate", *fields}:
        fail("%s fields are ref, predicate%s." % (label, "".join(", " + f for f in fields)))
    _handle(request, clause["ref"], label + ".ref", alias)
    if "property" in fields:
        if not isinstance(clause["property"], str) or not PROPERTY.fullmatch(clause["property"]):
            fail("%s.property must be a published RNA identifier or qualified path." % label)
        _json(clause["value"], label + ".value")
        request.properties.add((clause["ref"], clause["property"]))
    if "relation" in fields:
        if clause["relation"] not in RELATIONS:
            fail("%s.relation must be children or objects." % label)
        _handle(request, clause["target"], label + ".target", alias)
    if clause["predicate"] == "parent":
        _handle(request, clause["value"], label + ".value", alias, null=True)


def parse(params, native_ops):
    """Closed fields and per-intent arguments, before any read of the document."""
    if params.keys() - FIELDS:
        fail("Native apply fields are %s." % ", ".join(sorted(FIELDS)))
    for field in ("script_epoch", "schema_hash"):
        if not isinstance(params.get(field), str) or not 1 <= len(params[field]) <= ID_CHARS:
            fail("%s from the latest context or describe is required." % field)
    if not isinstance(params.get("command_id"), str) or not COMMAND_ID.fullmatch(params["command_id"]):
        fail("command_id is required (letters, digits, . _ : -, at most %d)" % ID_CHARS)
    if type(params.get("if_revision")) is not int:
        fail("if_revision is required")
    operations = params.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        fail("operations must hold 1 to %d intents." % MAX_OPERATIONS)
    for index, operation in enumerate(operations):
        label = "operations[%d]" % index
        if not isinstance(operation, dict) or operation.keys() - {"op", "args"} or operation.get("op") not in native_ops:
            fail("%s: a native apply holds native intents only; apply legacy operations separately and read context between." % label)
        args = operation.get("args", {})
        required, optional = OPS[operation["op"]]
        if not isinstance(args, dict) or args.keys() - set(required) - set(optional) or set(required) - args.keys():
            fail("%s: %s takes %s." % (label, operation["op"], ", ".join(required + tuple(o + "?" for o in optional))))
    for name in ("expect", "ensure"):
        if not isinstance(params.get(name, []), list) or len(params.get(name, [])) > MAX_OPERATIONS:
            fail("%s must be a list of at most %d clauses." % (name, MAX_OPERATIONS))
    request = Request(params)
    for index, (op, args) in enumerate(request.operations):
        label = "operations[%d]" % index
        for field in ("target", "owner"):
            if field in args:
                _handle(request, args[field], "%s.%s" % (label, field))
        if "data" in args:
            _handle(request, args["data"], label + ".data", null=True)
        if "parent" in args:
            _handle(request, args["parent"], label + ".parent", null=True)
        if "name" in args:
            _name(args["name"], label + ".name")
        if op == "entity.create":
            if not isinstance(args["recipe"], str) or not RECIPE.fullmatch(args["recipe"]):
                fail("%s.recipe must be a recipe identifier from describe." % label)
            if not isinstance(args["bind"], str) or not ALIAS.fullmatch(args["bind"]):
                fail("%s.bind must be a simple identifier." % label)
        if op == "property.set":
            if not isinstance(args["property"], str) or not PROPERTY.fullmatch(args["property"]):
                fail("%s.property must be a published RNA identifier or qualified path, never a call." % label)
            value = args["value"]
            if isinstance(value, dict):
                # An Object pointer: {"ref": reference or $alias}.
                if value.keys() != {"ref"}:
                    fail("%s.value: a pointer value is {\"ref\": reference} or null." % label)
                _handle(request, value["ref"], label + ".value.ref")
            else:
                _json(value, label + ".value")
            request.properties.add((args["target"], args["property"]))
        if "relation" in args and args["relation"] not in RELATIONS:
            fail("%s.relation must be children or objects." % label)
        if op == "object.parent" and args["keep_world"] is not True:
            fail("%s.keep_world must be true: parenting preserves world transforms." % label)
    for index, clause in enumerate(request.expect):
        _clause(request, clause, "expect[%d]" % index, alias=False)
    for index, clause in enumerate(request.ensure):
        _clause(request, clause, "ensure[%d]" % index, alias=True)
    return request


def as_state(schema, value):
    """A validated JSON value in the representation of the structural capture."""
    shape = [d for d in schema["shape"] if d]
    if shape:
        return tuple(as_state(dict(schema, shape=shape[1:]), v) for v in value)
    if schema["type"] == "FLOAT":
        return float(value)
    if schema["type"] == "ENUM" and schema.get("enum_flag"):
        return tuple(sorted(value))
    return value


def _pointed(projected, key):
    """A projected pointer ("REF", handle) in the capture's representation ("ID", key)."""
    return ("ID", key(projected[1])) if isinstance(projected, tuple) and projected[:1] == ("REF",) else projected


class Snapshot:
    """What the adapter knows before any mutation; `hooks` answers recipe and schema questions.

    facts[ref]: key, family, type, name, editable, root, embedded, namespace, evaluated, animated,
    constrained, tree (node), tree_type (tree), tree_animated (node), data_ok and schemas{prop: schema}.
    """

    def __init__(self, state, count, facts, names, primitives, hooks):
        self.state, self.count, self.facts, self.names = state, count, facts, names
        self.primitives, self.hooks = primitives, hooks


# -- preflight -------------------------------------------------------------------------------
class Plan:
    """Symbolic overlay of the whole batch; holds the last projected state for verification."""

    def __init__(self, request, snapshot):
        self.request, self.snapshot = request, snapshot
        state = snapshot.state
        self.by_key = {fact["key"]: fact for fact in snapshot.facts.values()}
        self.children = {k: set(v[0]) for k, v in state["collections"].items()}
        self.members = {k: set(v[1]) for k, v in state["collections"].items()}
        self.edge_states = {k: {(r, t): s for r, t, s in v[2]} for k, v in state["collections"].items()}
        self.physics = {k for scene in state["scenes"].values() for k in scene[PHYSICS] if k is not None}
        self.parent = {k: v[PARENT] for k, v in state["objects"].items()}
        self.world = {k: v[WORLD] for k, v in state["objects"].items()}
        self.basis = {k: v[BASIS] for k, v in state["objects"].items()}
        self.names = {ns: set(v) for ns, v in snapshot.names.items()}
        self.node_names = {t: {n[0][1] for n in tree[0]} for t, tree in state["trees"].items()}
        self.aliases = BatchAliases()
        self.created, self.renamed, self.props = {}, {}, {}
        self.parented, self.basis_free, self.trees = set(), set(), set()
        # Created modifiers/constraints per owner object and kind; names in use per owner and kind.
        self.structs, self.struct_names = {}, {}
        # A constraint plan leaves world matrices to Blender's evaluation (they are not projected).
        self.world_free = False
        self.cost = snapshot.count
        self.steps = []
        for index, (op, args) in enumerate(request.operations):
            try:
                self.steps.append(getattr(self, "_" + op.replace(".", "_"))(args))
            except HostFailure as failure:
                fail("operations[%d] %s: %s" % (index, op, failure.message), failure.code)
        for index, clause in enumerate(request.ensure):
            try:
                self._clause_target(clause)
            except HostFailure as failure:
                fail("ensure[%d]: %s" % (index, failure.message))

    def handle(self, value):
        if value is None:
            return None
        if value.startswith("$"):
            return ("$", self.aliases.resolve(value))
        return self.snapshot.facts[value]["key"]

    def fact(self, handle):
        return self.created[handle[1]] if isinstance(handle, tuple) and handle[0] == "$" else self.by_key[handle]

    def _need(self, handle, *families, editable=False):
        fact = self.fact(handle)
        if fact["family"] not in families:
            fail("%s is a %s; expected %s." % (fact["name"], fact["type"], " or ".join(families)))
        if editable and not fact["editable"]:
            fail("%s is linked, overridden or not editable." % fact["name"])
        return fact

    def _schema(self, handle, prop):
        fact = self.fact(handle)
        schema = fact["schemas"].get(prop) if not fact.get("created") else self.snapshot.hooks.type_schema(fact["type"], prop)
        if schema is None:
            fail("%s has no published property %s." % (fact["type"], prop))
        return schema

    def _entity_create(self, args):
        recipe, name = args["recipe"], args["name"]
        kind, _, detail = recipe.partition(":")
        owner, data = self.handle(args.get("owner")), self.handle(args.get("data"))
        if (owner is not None) != (kind in OWNED) or (data is not None and kind != "object"):
            fail("owner is required for node, modifier and constraint recipes only; data belongs to the object recipe.")
        info = {"key": None, "name": name, "editable": True, "root": False, "embedded": False, "created": True,
                "evaluated": False, "animated": False, "constrained": False, "data_ok": False, "schemas": {}}
        if kind == "node":
            tree = self._need(owner, "NodeTree", editable=True)
            if tree.get("created") or tree.get("tree_type") not in SUPPORTED_TREES:
                fail("node recipes need a Geometry or Shader node tree owner.")
            reason = self.snapshot.hooks.node_recipe(detail, owner)
            if reason:
                fail("recipe %s: %s" % (recipe, reason))
            names = self.node_names.setdefault(owner, set())
            if name in names:
                fail("a node named %s exists in this tree." % name)
            names.add(name)
            self.trees.add(owner)
            self.cost += 2 + NODE_SOCKET_ALLOWANCE
            info.update(family="Node", type=detail, tree=owner)
        elif kind in ("modifier", "constraint"):
            obj = self._need(owner, "Object", editable=True)
            entry = self.snapshot.hooks.struct_recipe(kind, detail)
            if entry is None:
                fail("unknown recipe %s." % recipe)
            object_type = obj.get("object_type")
            if kind == "modifier" and object_type is not None and object_type not in entry["owners"]:
                fail("%s cannot be added to a %s object (%s only)." % (recipe, object_type, ", ".join(entry["owners"])))
            if kind == "constraint":
                if self.parented:
                    fail("apply constraints and parenting separately: both move world matrices.")
                self.world_free = True
            names = self.struct_names.get((owner, kind))
            if names is None:
                existing = self.snapshot.state["objects"].get(owner)
                names = {e[0] for e in existing[MODIFIERS if kind == "modifier" else CONSTRAINTS]} if existing else set()
                self.struct_names[(owner, kind)] = names
            if name in names:
                fail("a %s named %s exists on %s." % (kind, name, obj["name"]))
            names.add(name)
            self.structs.setdefault(owner, {"modifier": [], "constraint": []})[kind].append(args["bind"])
            self.cost += 1 + len(entry["pointers"])
            info.update(family=kind.title(), type=entry["type"], owner=owner, kind=kind, recipe_type=detail,
                        pointers=list(entry["pointers"]))
        else:
            if kind == "primitive" and detail not in self.snapshot.primitives:
                fail("unknown primitive %s." % detail)
            spaces = ("collections",) if kind == "collection" else ("objects", "meshes") if kind == "primitive" else ("objects",)
            for space in spaces:
                if name in self.names.setdefault(space, set()):
                    fail("%s %s exists: names are never suffixed silently." % (space, name))
            for space in spaces:
                self.names[space].add(name)
            if data is not None:
                target = self.fact(data)
                if target.get("created") or not target.get("data_ok"):
                    fail("data must be existing object data (mesh, curve, camera, light...).")
            self.cost += len(spaces) + int(kind == "primitive" or data is not None)
            family = "Collection" if kind == "collection" else "Object"
            info.update(family=family, type=family, namespace=spaces[0],
                        object_type="MESH" if kind == "primitive" else "EMPTY" if data is None else None)
        self.aliases.bind(args["bind"], args["bind"])
        handle = ("$", args["bind"])
        info["key"] = handle
        self.created[args["bind"]] = info
        if kind == "collection":
            self.children[handle], self.members[handle], self.edge_states[handle] = set(), set(), {}
        elif kind in ("collection", "object", "primitive"):
            self.parent[handle], self.world[handle], self.basis[handle] = None, IDENTITY, IDENTITY
        return ("entity.create", kind, detail, name, owner, data, args["bind"])

    def _entity_rename(self, args):
        handle, name = self.handle(args["target"]), args["name"]
        fact = self._need(handle, *(f for f in FAMILIES if f not in STRUCT_FAMILIES), editable=True)
        if fact["root"] or fact["embedded"]:
            fail("scene root collections and embedded trees keep their native names.")
        if fact["family"] == "Node":
            if fact.get("tree_animated"):
                fail("renaming a node of an animated tree rewrites animation paths outside the verified scope.")
            names = self.node_names.setdefault(fact["tree"], set())
            self.trees.add(fact["tree"])
        else:
            names = self.names.setdefault(fact["namespace"], set())
        current = self.renamed.get(handle, fact["name"])
        if name != current and name in names:
            fail("%s exists: names are never suffixed silently." % name)
        names.discard(current)
        names.add(name)
        self.renamed[handle] = name
        return ("entity.rename", handle, name)

    def _property_set(self, args):
        handle, prop = self.handle(args["target"]), args["property"]
        fact = self._need(handle, *FAMILIES, editable=True)
        schema = self._schema(handle, prop)
        if not schema.get("writable"):
            fail("%s.%s is not writable (%s)." % (fact["type"], prop, schema.get("reason")))
        if schema["type"] == "POINTER":
            if args["value"] is not None and not isinstance(args["value"], dict):
                fail("%s.%s takes {\"ref\": Object reference} or null." % (fact["type"], prop))
            target = self.handle(args["value"]["ref"]) if args["value"] is not None else None
            if target is not None:
                self._need(target, "Object")
            value, projected = (("REF", target) if target is not None else None,) * 2
        else:
            value = self.snapshot.hooks.validate(schema, args["value"])
            projected = as_state(schema, value)
        self.props[(handle, prop)] = (schema, projected)
        if fact["family"] == "Node":
            self.trees.add(fact["tree"])
        if fact["family"] == "Constraint":
            if self.parented:
                fail("apply constraints and parenting separately: both move world matrices.")
            self.world_free = True
        return ("property.set", handle, prop, value)

    def _descends(self, start, node):
        pending, seen = [start], set()
        while pending:
            current = pending.pop()
            if current == node:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(self.children.get(current, ()))
        return False

    def _relation(self, args, link):
        owner, target, relation = self.handle(args["owner"]), self.handle(args["target"]), args["relation"]
        owner_fact = self._need(owner, "Collection", editable=True)
        if owner in self.physics:
            fail("%s is a rigid body world collection: its membership creates physics data outside the verified scope."
                 % owner_fact["name"])
        target_fact = self._need(target, RELATIONS[relation])
        if relation == "children" and target_fact["root"]:
            fail("a scene root collection cannot become a child.")
        edges = (self.children if relation == "children" else self.members).setdefault(owner, set())
        states = self.edge_states.setdefault(owner, {})
        if link:
            if target in edges:
                fail("%s is already linked there." % target_fact["name"])
            if relation == "children" and self._descends(target, owner):
                fail("linking %s would create a collection cycle." % target_fact["name"])
            edges.add(target)
            states[(relation, target)] = INCLUDE
            self.cost += 1
        else:
            if target not in edges:
                fail("%s is not linked there." % target_fact["name"])
            edges.discard(target)
            states.pop((relation, target), None)
        return ("relation.link" if link else "relation.unlink", owner, relation, target)

    def _relation_link(self, args):
        return self._relation(args, True)

    def _relation_unlink(self, args):
        return self._relation(args, False)

    def _object_parent(self, args):
        target, parent = self.handle(args["target"]), self.handle(args["parent"])
        fact = self._need(target, "Object", editable=True)
        if self.world_free:
            fail("apply constraints and parenting separately: both move world matrices.")
        if not fact.get("created"):
            if self.snapshot.state["objects"][target][PARENT_TYPE] != "OBJECT":
                fail("only OBJECT parenting is supported; bone and vertex parents are refused.")
            if fact["constrained"] or fact["animated"]:
                fail("constrained, driven or animated objects cannot keep their world transform reliably.")
            if not fact["evaluated"]:
                fail("%s is not evaluated in the active view layer." % fact["name"])
        world = self.world[target]
        basis = world if target in self.basis_free else self.basis[target]
        if not _affine(world):
            fail("the world matrix of %s is not affine." % fact["name"])
        if parent is None:
            if self.parent[target] is None:
                fail("%s has no parent." % fact["name"])
            if not _nonsingular(world) or not _unsheared(world):
                fail("the world matrix of %s is singular or sheared: unparenting cannot keep it." % fact["name"])
            self.basis_free.add(target)
            pworld = None
        else:
            pfact = self._need(parent, "Object")
            if parent == self.parent[target]:
                fail("%s already has this parent." % fact["name"])
            cursor, seen = parent, set()
            while cursor is not None and cursor not in seen:
                if cursor == target:
                    fail("parenting %s would create a cycle." % fact["name"])
                seen.add(cursor)
                cursor = self.parent.get(cursor)
            if not pfact.get("created") and not pfact["evaluated"]:
                fail("%s is not evaluated in the active view layer." % pfact["name"])
            pworld = self.world[parent]
            if not _affine(pworld) or not _nonsingular(pworld) or not _nonsingular(basis):
                fail("a singular matrix prevents keeping the world transform of %s." % fact["name"])
            self.cost += int(self.parent[target] is None)
        self.parent[target] = parent
        self.parented.add(target)
        return ("object.parent", target, parent, world, pworld)

    # -- clauses -------------------------------------------------------------------------------
    def _clause_target(self, clause):
        handle = self.handle(clause["ref"])
        predicate = clause["predicate"]
        if predicate == "equals":
            schema = self._schema(handle, clause["property"])
            if not schema["readable"]:
                fail("%s is not readable." % clause["property"])
            self.snapshot.hooks.validate(dict(schema, minimum=-math.inf, maximum=math.inf), clause["value"])
        elif predicate in ("linked", "unlinked"):
            self._need(handle, "Collection")
            self._need(self.handle(clause["target"]), RELATIONS[clause["relation"]])
        elif predicate == "parent":
            self._need(handle, "Object")
            if clause["value"] is not None:
                self._need(self.handle(clause["value"]), "Object")
        return handle

    def key_after(self, handle, created):
        """The capture key of a plan handle in the final state."""
        if isinstance(handle, tuple) and handle[0] == "$":
            return created[handle[1]]["key"]
        if isinstance(handle, tuple) and handle in self.renamed:
            return (handle[0], self.renamed[handle], handle[2])
        return handle

    def after_targets(self, before_targets, created):
        targets = {(self.key_after(k, created), p) for k, p in before_targets}
        for handle, prop in self.props:
            targets.add((self.key_after(handle, created), prop))
        for clause in self.request.ensure:
            if clause["predicate"] == "equals":
                targets.add((self.key_after(self.handle(clause["ref"]), created), clause["property"]))
        return tuple(sorted(targets, key=repr))

    def evaluate(self, clauses, state, created=None, label="expect"):
        """Failures of clauses on a captured state (before: expect; final: ensure)."""
        failures = []
        for index, clause in enumerate(clauses):
            handle = self._clause_target(clause)
            key = self.key_after(handle, created) if created is not None else handle
            where = "%s[%d]" % (label, index)
            predicate = clause["predicate"]
            if predicate == "exists":
                if isinstance(key, tuple) and key[:1] == ("S",):
                    owner = state["objects"].get(key[1])
                    ok = owner is not None and any(e[0] == key[3] for e in owner[MODIFIERS if key[2] == "modifier" else CONSTRAINTS])
                else:
                    ok = key in state["ids"] or any(key in {n[0] for n in tree[0]} for tree in state["trees"].values())
            elif predicate == "equals":
                schema = self._schema(handle, clause["property"])
                value = self.snapshot.hooks.validate(dict(schema, minimum=-math.inf, maximum=math.inf), clause["value"])
                ok = same_state(state["properties"].get((key, clause["property"])), as_state(schema, value))
            elif predicate in ("linked", "unlinked"):
                other = self.handle(clause["target"])
                other = self.key_after(other, created) if created is not None else other
                edges = state["collections"].get(key, ((), ()))[0 if clause["relation"] == "children" else 1]
                ok = (other in edges) == (predicate == "linked")
            else:
                expected = self.handle(clause["value"]) if clause["value"] is not None else None
                if expected is not None and created is not None:
                    expected = self.key_after(expected, created)
                ok = key in state["objects"] and state["objects"][key][PARENT] == expected
            if not ok:
                failures.append("%s %s does not hold" % (where, predicate))
        return failures

    # -- verification of the last projected state ---------------------------------------------
    def verify(self, before, after, created):
        """Every captured section equals the projection of the plan; raises on the first difference."""
        key = lambda h: self.key_after(h, created)  # noqa: E731
        checks = {}

        def require(ok, message):
            if not ok:
                raise RuntimeError("verification: " + message)

        ids = dict(before["ids"])
        for handle, name in self.renamed.items():
            if handle in ids:
                ids[handle] = (name,) + ids[handle][1:]
        for alias, info in self.created.items():
            if info["family"] not in ("Node", *STRUCT_FAMILIES):
                ids[created[alias]["key"]] = (self.renamed.get(info["key"], info["name"]), info["type"], created[alias]["key"], None)
            if created[alias].get("mesh"):
                ids[created[alias]["mesh"]] = (info["name"], "Mesh", created[alias]["mesh"], None)
        require(after["ids"].keys() == ids.keys(), "the ID inventory differs from the plan")
        for k, value in ids.items():
            require(same_state(after["ids"][k], value), "name, type or owner of %s differs" % value[0])
        checks["ids"] = len(ids)

        collections = {key(h): (tuple(sorted(key(c) for c in self.children[h])), tuple(sorted(key(o) for o in self.members[h])),
                                tuple(sorted((r, key(t), s) for (r, t), s in self.edge_states[h].items())))
                       for h in self.children}
        require(after["collections"].keys() == collections.keys(), "the collection inventory differs")
        for k, value in collections.items():
            require(after["collections"][k] == value, "collection edges or their light-linking state differ from the plan")
        checks["collection_edges"] = sum(len(value[0]) + len(value[1]) for value in collections.values())

        require(after["objects"].keys() == {key(h) for h in self.parent}, "the object inventory differs")
        for handle in self.parent:
            got = after["objects"][key(handle)]
            parent = self.parent[handle]
            require(got[PARENT] == (key(parent) if parent is not None else None), "parent differs from the plan")
            require(self.world_free or matrix_close(got[WORLD], self.world[handle]), "a world matrix was not preserved")
            if isinstance(handle, tuple) and handle[0] == "$":
                info = self.created[handle[1]]
                data = created[handle[1]].get("mesh") or self._data_key(handle[1])
                require(got[DATA] == data and got[PARENT_TYPE] == "OBJECT" and got[RIGID_BODY] is None
                        and got[RIGID_CONSTRAINT] is None, "created object %s differs from its recipe" % info["name"])
                require(same_state((got[MODIFIERS], got[CONSTRAINTS]), self._structs_after(handle, ((), ()), key)),
                        "modifiers or constraints of %s differ from the plan" % info["name"])
                continue
            old = before["objects"][handle]
            require(same_state((got[MODIFIERS], got[CONSTRAINTS]), self._structs_after(handle, (old[MODIFIERS], old[CONSTRAINTS]), key)),
                    "the modifiers or constraints of an object differ from the plan")
            free = {PARENT, WORLD, MODIFIERS, CONSTRAINTS}
            if handle in self.parented:
                free |= {PARENT_TYPE, PARENT_INVERSE}
                require(got[PARENT_TYPE] == "OBJECT", "parent type differs")
            if handle in self.basis_free:
                free |= set(CHANNELS)
                if parent is None:
                    require(matrix_close(got[BASIS], got[WORLD]), "an unparented basis differs from its world matrix")
            for index, value in enumerate(old):
                require(index in free or same_state(got[index], value), "object data, channels or topology changed")
        checks["objects"] = len(self.parent)

        scenes = {k: list(v) for k, v in before["scenes"].items()}
        for (handle, prop), (_, projected) in self.props.items():
            if handle in scenes and prop == "camera":
                scenes[handle][1] = _pointed(projected, key)[1] if projected is not None else None
            elif handle in scenes and prop == "frame_current":
                scenes[handle][2] = projected
        require(same_state(after["scenes"], {k: tuple(v) for k, v in scenes.items()}),
                "scene roots, cameras or frames differ from the plan")
        checks["scenes"] = len(before["scenes"])

        require(after["trees"].keys() == before["trees"].keys(), "the node tree inventory differs")
        for tree, value in before["trees"].items():
            if tree in self.trees:
                self._verify_tree(tree, value, after["trees"][tree], created, require)
            else:
                require(same_state(after["trees"][tree], value), "an untouched node tree changed")
        checks["trees"] = len(before["trees"])

        for (k, prop), value in after["properties"].items():
            expected = None
            for (handle, name), (_, projected) in self.props.items():
                if name == prop and key(handle) == k:
                    expected = _pointed(projected, key)
            if prop == "name":  # names follow renames and recipes
                names = {("$", alias): info["name"] for alias, info in self.created.items()}
                names.update(self.renamed)
                expected = next((n for h, n in names.items() if key(h) == k), expected)
            if expected is None:
                origin = next((b for b in before["properties"] if b[1] == prop and key(b[0]) == k), None)
                require(origin is not None, "a property target has no prior value")
                expected = before["properties"][origin]
            require(same_state(value, expected), "%s differs from its last projected value" % prop)
        checks["properties"] = len(after["properties"])
        return checks

    def _structs_after(self, handle, before, key):
        """(modifiers, constraints) of an object as the plan leaves them: existing entries with their
        pointers as set, then the created ones in plan order, every other pointer empty."""
        out = []
        for index, kind in enumerate(("modifier", "constraint")):
            def refs(struct, pairs):
                sets = {p: projected for (h, p), (schema, projected) in self.props.items()
                        if h == struct and schema["type"] == "POINTER"}
                return tuple((p, (_pointed(sets[p], key)[1] if sets[p] is not None else None) if p in sets else v)
                             for p, v in pairs)
            entries = []
            for entry in before[index]:
                struct = ("S", handle, kind, entry[0])
                entries.append(entry[:-1] + (refs(struct, entry[-1]),))
            for alias in self.structs.get(handle, {}).get(kind, ()):
                info = self.created[alias]
                pointers = refs(("$", alias), ((p, None) for p in info["pointers"]))
                entries.append((info["name"], info["recipe_type"], None, pointers) if kind == "modifier"
                               else (info["name"], info["recipe_type"], pointers))
            out.append(tuple(entries))
        return tuple(out)

    def _data_key(self, alias):
        for step in self.steps:
            if step[0] == "entity.create" and step[6] == alias:
                return step[5]
        return None

    def _verify_tree(self, tree, before, after, created, require):
        key = lambda h: self.key_after(h, created) if h is not None else None  # noqa: E731
        nodes = {}
        for node in before[0]:
            props = dict(node[4])
            label = node[1]
            for (handle, prop), (_, value) in self.props.items():
                if handle == node[0]:
                    props[prop] = value
                    label = value if prop == "label" else label
            nodes[key(node[0])] = (key(node[0]), label, node[2], key(node[3]), props, node[5], node[6])
        made = {created[a]["key"]: a for a, info in self.created.items() if info["family"] == "Node" and info["tree"] == tree}
        got = {node[0]: node for node in after[0]}
        require(got.keys() == nodes.keys() | made.keys(), "the nodes of a tree differ from the plan")
        for k, expected in nodes.items():
            require(same_state(got[k], expected), "an existing node changed beyond the plan")
        for k, alias in made.items():
            for (handle, prop), (_, value) in self.props.items():
                if handle == ("$", alias):
                    require(same_state(got[k][4].get(prop), value), "a created node property differs")
        links = tuple(sorted((key(a), s, key(b), t, i) for a, s, b, t, i in before[1]))
        require(same_state(after[1], links) and same_state(after[2], before[2]), "tree links or interface changed")
