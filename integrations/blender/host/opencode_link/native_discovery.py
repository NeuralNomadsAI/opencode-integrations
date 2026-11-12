# SPDX-License-Identifier: GPL-3.0-or-later
"""Native RNA discovery and bounded read queries. No operator/function dispatch."""

import fnmatch
import hashlib
import json
import math
import re
import secrets
from collections import OrderedDict

import bpy

from .native_plans import MAX_OPERATIONS, NODE_SOCKET_ALLOWANCE, SUPPORTED_TREES
from . import limits
from .native_scene import document_entities, json_value, supported_tree

PAGE = limits.value("page")
RELATIONS_PER_READ = limits.value("relations_per_read")
REFERENCE_SELECTION = limits.value("reference_selection")
CURSOR_RECEIPTS = limits.value("cursor_receipts")
ID_CHARS = limits.value("id_chars")
from .wire import HostFailure

IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
LIMITS = {"page": PAGE, "relations_per_read": RELATIONS_PER_READ, "filter_length": limits.value("filter_chars"),
          "reference_selection": REFERENCE_SELECTION, "cursor_receipts": CURSOR_RECEIPTS, "operations": MAX_OPERATIONS,
          "node_socket_allowance": NODE_SOCKET_ALLOWANCE, "writes": "native_plan",
          "scope": "scene_graph_and_selected_native_data"}
STRUCTURAL = frozenset(("name", "name_full", "parent", "data", "location", "rotation_euler", "rotation_quaternion",
    "rotation_axis_angle", "rotation_mode", "scale", "matrix_world", "matrix_local", "matrix_basis", "matrix_parent_inverse"))
# Display data whose effect is the stored value itself, per family.
DISPLAY = {"Object": ("color", "display_type", "hide_viewport", "hide_render", "hide_select"),
           "Collection": ("hide_viewport", "hide_render", "hide_select", "color_tag"),
           "Node": ("label", "use_custom_color", "color", "color_tag", "hide", "show_options", "show_preview", "show_texture")}
CALLBACKS = frozenset(("update", "get", "set", "get_transform", "set_transform"))
# Published data semantics a native property.set may change, per family: direct names and one-level
# `struct.name` paths. Their effect is the stored value itself (the capture verifies it); pointers name
# an Object by reference. Render engine settings stay with the user.
QUALIFIED = {
    "Camera": ("type", "lens", "lens_unit", "sensor_fit", "sensor_width", "sensor_height", "clip_start", "clip_end",
               "shift_x", "shift_y", "ortho_scale", "dof.use_dof", "dof.focus_object", "dof.focus_subtarget",
               "dof.focus_distance", "dof.aperture_fstop", "dof.aperture_blades", "dof.aperture_rotation",
               "dof.aperture_ratio"),
    "Light": ("energy", "color", "use_shadow", "shadow_soft_size", "angle", "spot_size", "spot_blend", "show_cone",
              "shape", "size", "size_y"),
    "World": ("color",),
    "Scene": ("camera", "frame_start", "frame_end", "frame_step", "frame_current", "render.fps", "render.fps_base",
              "render.resolution_x", "render.resolution_y", "render.resolution_percentage", "render.pixel_aspect_x",
              "render.pixel_aspect_y", "render.film_transparent", "view_settings.view_transform",
              "view_settings.look", "view_settings.exposure", "view_settings.gamma"),
}
# Settings structs owned by an object: every published simple setting and Object pointer is data whose
# effect is evaluated (geometry, world matrices), never a structural edit of the document.
STRUCTS = ("Modifier", "Constraint")
ACTIONS = {"Collection": ["entity.create", "entity.rename", "property.set", "relation.link", "relation.unlink"],
           "Object": ["entity.create", "entity.rename", "property.set", "relation.link", "relation.unlink", "object.parent"],
           "Node": ["entity.create", "entity.rename", "property.set"],
           "NodeTree": ["entity.rename", "property.set"], "Scene": ["entity.rename", "property.set"],
           "Camera": ["entity.rename", "property.set"], "Light": ["entity.rename", "property.set"],
           "World": ["entity.rename", "property.set"],
           "Modifier": ["entity.create", "property.set"], "Constraint": ["entity.create", "property.set"]}


def fail(message):
    raise HostFailure("invalid_args", message)


def strict(value, keys, label):
    if not isinstance(value, dict) or value.keys() - set(keys):
        fail("%s contains unsupported fields." % label)


def _simple(prop):
    return (prop.type in ("BOOLEAN", "INT", "FLOAT", "STRING", "ENUM") and
            getattr(prop, "array_length", 0) <= 32 and prop.subtype not in ("FILE_PATH", "DIR_PATH", "BYTE_STRING", "PASSWORD"))


def _lineage(rna):
    names = set()
    while rna is not None:
        names.add(rna.identifier)
        rna = rna.base
    return names


def _storage_only(classes, name):
    """True for an extension property declared by bpy.props without update/get/set callbacks."""
    for cls in classes:
        deferred = getattr(cls, name, None)
        if type(deferred).__name__ != "_PropertyDeferred":
            deferred = cls.__dict__.get("__annotations__", {}).get(name)
        if type(deferred).__name__ == "_PropertyDeferred":
            return not set(getattr(deferred, "keywords", None) or {}) & CALLBACKS
    return False


def _object_pointer(prop):
    base = prop.fixed_type if prop.type == "POINTER" else None
    while base is not None and base.identifier != "Object":
        base = base.base
    return base is not None


def _document_animated():
    from .native_scene import _animated, document_ids
    return any(_animated(value) for value, _ in document_ids())


def _entry(prop, name, lineage, classes, instance, display=False, qualified=False, struct=False):
    pointer = prop.type == "POINTER" and _object_pointer(prop) and (qualified or struct)
    readable = _simple(prop) or pointer
    reason = "unsupported_data_shape" if not readable else "unqualified_semantic_effect"
    candidate = False
    if readable and not prop.is_readonly:
        # A concrete, verified data semantic, not blanket permission from is_readonly.
        if display or ("Object" in lineage and name.startswith("show_")):
            candidate, reason = True, "display_data"
        elif qualified:
            candidate, reason = True, "qualified_data"
        elif struct and name.endswith("_pending"):
            # Evaluation triggers (Child Of set_inverse_pending): Blender clears them, they hold no data.
            reason = "evaluation_trigger"
        elif struct and name != "name":
            candidate, reason = True, "struct_setting"
    if not qualified and (name in STRUCTURAL or name.startswith(("matrix_", "delta_", "parent_"))):
        candidate, reason = False, "dedicated_structural_semantics"
    elif getattr(prop, "is_runtime", False):
        # Extension properties are qualified only as plain storage: callbacks are trusted host code.
        candidate = readable and not prop.is_readonly and _storage_only(classes, name)
        reason = "extension_storage" if candidate else "trusted_extension_effect_unqualified"
    if prop.is_readonly:
        candidate, reason = False, "readonly"
    if instance is not None:
        owner = instance.id_data
        if instance.is_property_readonly(name):
            candidate, reason = False, "instance_readonly"
        if instance.is_property_set(name) and instance.is_property_hidden(name):
            candidate, reason = False, "instance_hidden"
        if owner.library is not None or owner.override_library is not None or not owner.is_editable:
            candidate, reason = False, "library_override_or_noneditable"
        if candidate and name == "frame_current" and isinstance(instance, bpy.types.Scene) and _document_animated():
            # A frame change re-evaluates animation into channels and sockets outside the verified scope.
            candidate, reason = False, "animation_evaluation_unverified"
    info = {"type": prop.type, "subtype": prop.subtype if readable else None, "readable": readable,
            "data_candidate": candidate, "writable": candidate, "reason": reason,
            "readonly": bool(prop.is_readonly), "runtime": bool(getattr(prop, "is_runtime", False)),
            "shape": list(getattr(prop, "array_dimensions", ())) if getattr(prop, "is_array", False) else []}
    if pointer:
        info.update(subtype=None, fixed_type="Object", value='{"ref": <Object reference>} or null')
    if prop.type in ("INT", "FLOAT"):
        info.update(minimum=prop.hard_min, maximum=prop.hard_max)
    if prop.type == "ENUM":
        info["enum"] = [item.identifier for item in prop.enum_items if item.identifier]
        info["enum_flag"] = bool(prop.is_enum_flag)
    return info


def property_schema(rna, instance=None, cls=None):
    """Published direct RNA properties and qualified one-level paths; `writable` names a qualified,
    verifiable native semantic."""
    result = {}
    lineage = _lineage(rna)
    display = next((names for family, names in DISPLAY.items() if family in lineage), ())
    qualified = next((names for family, names in QUALIFIED.items() if family in lineage), ())
    struct = bool(lineage & set(STRUCTS))
    owner = type(instance) if instance is not None else cls or getattr(bpy.types, rna.identifier, None)
    classes = owner.__mro__ if isinstance(owner, type) else ()
    for prop in rna.properties:
        name = prop.identifier
        if IDENTIFIER.fullmatch(name):
            result[name] = _entry(prop, name, lineage, classes, instance, name in display, name in qualified, struct)
    for path in qualified:
        head, _, tail = path.partition(".")
        parent = rna.properties.get(head) if tail else None
        nested_rna = parent.fixed_type if parent is not None and parent.type == "POINTER" else None
        prop = nested_rna.properties.get(tail) if nested_rna is not None else None
        if prop is not None:
            nested = getattr(instance, head) if instance is not None else None
            result[path] = _entry(prop, tail, lineage, (), nested, qualified=True)
    return result


def holder(entity, name):
    """The struct and identifier a published name or qualified path addresses."""
    head, _, tail = name.partition(".")
    return (getattr(entity, head), tail) if tail else (entity, name)


def get_value(entity, name):
    target, attr = holder(entity, name)
    return getattr(target, attr)  # a published identifier or qualified path only


def validate_value(schema, value):
    """JSON→RNA validation only; no setter and no authorization implied."""
    kind = schema["type"]
    shape = [dim for dim in schema["shape"] if dim]
    if shape:
        if not isinstance(value, list) or len(value) != shape[0]:
            fail("RNA array has the wrong shape.")
        child = dict(schema, shape=shape[1:])
        return [validate_value(child, v) for v in value]
    if kind == "BOOLEAN":
        valid = type(value) is bool
    elif kind == "INT":
        valid = type(value) is int
    elif kind == "FLOAT":
        valid = type(value) in (int, float) and schema["minimum"] <= value <= schema["maximum"] and math.isfinite(value)
    elif kind == "STRING":
        valid = isinstance(value, str) and len(value) <= 4096 and "\x00" not in value
    elif kind == "ENUM":
        valid = (isinstance(value, list) and all(isinstance(v, str) and v in schema["enum"] for v in value) and len(value) == len(set(value))) if schema.get("enum_flag") else isinstance(value, str) and value in schema["enum"]
    else:
        valid = False
    if not valid:
        fail("RNA value has an unsupported type, enum or non-finite value.")
    if kind in ("FLOAT", "INT") and not schema["minimum"] <= value <= schema["maximum"]:
        fail("RNA value is out of bounds.")
    return value


def read_properties(entity, schemas=None):
    schemas = schemas if schemas is not None else property_schema(entity.bl_rna, entity)
    values = {}
    for name, schema in schemas.items():
        if schema["readable"]:
            value = json_value(get_value(entity, name))  # published identifier or qualified path only
            if isinstance(value, str) and len(value) > 4096:
                fail("RNA string exceeds read budget.")
            values[name] = value
    return values


def _classes():
    classes = {}
    pending = [bpy.types.ID, bpy.types.Node, bpy.types.Modifier, bpy.types.Constraint]
    seen = set()
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.add(cls)
        pending.extend(cls.__subclasses__())
        rna = getattr(cls, "bl_rna", None)
        if rna is not None and issubclass(cls, (bpy.types.ID, bpy.types.Node)):
            registered = bpy.types.Node.bl_rna_get_subclass_py(rna.identifier) if issubclass(cls, bpy.types.Node) else bpy.types.ID.bl_rna_get_subclass_py(rna.identifier)
            if registered is not None:
                classes[rna.identifier] = registered
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name)
        if (isinstance(cls, type) and issubclass(cls, (bpy.types.ID, bpy.types.Node, bpy.types.Modifier,
                                                        bpy.types.Constraint)) and hasattr(cls, "bl_rna")):
            classes[cls.bl_rna.identifier] = cls
    return classes


def _family(cls):
    for base, family in ((bpy.types.Collection, "Collection"), (bpy.types.Object, "Object"),
                         (bpy.types.Node, "Node"), (bpy.types.NodeTree, "NodeTree"), (bpy.types.Scene, "Scene"),
                         (bpy.types.Camera, "Camera"), (bpy.types.Light, "Light"), (bpy.types.World, "World"),
                         (bpy.types.Modifier, "Modifier"), (bpy.types.Constraint, "Constraint")):
        if issubclass(cls, base):
            return family
    return "unsupported"


family = _family
registered_classes = _classes


_owners_cache = [None, {}]


def node_owners(classes):
    """The supported trees each registered node type can be created in, decided by Blender's own check
    (nodes.new; the poll Python sees is overridden by ShaderNode/GeometryNode and differs from it) on
    trees of a temporary Main. Cached per set of registered node classes."""
    nodes = {name: cls for name, cls in classes.items()
             if issubclass(cls, bpy.types.Node) and cls.is_registered_node_type()}
    key = tuple(sorted((name, id(cls)) for name, cls in nodes.items()))
    if _owners_cache[0] != key:
        owners = {name: [] for name in nodes}
        before = {entity.as_pointer() for entity in bpy.data.all_ids}
        try:
            with bpy.data.temp_data() as temp:
                for kind in SUPPORTED_TREES:
                    tree = temp.node_groups.new("OpenCode Link probe", kind)
                    for name in sorted(nodes):
                        try:
                            tree.nodes.remove(tree.nodes.new(name))
                        except Exception:  # noqa: BLE001 - refused by the tree, or an extension's failing poll
                            continue
                        owners[name].append(kind)
        finally:
            # A node's initialisation may add shared data to the open file (a font input node loads the
            # built-in font there): remove what the probe added, leaving the document as found.
            added = [entity for entity in bpy.data.all_ids if entity.as_pointer() not in before]
            if added:
                bpy.data.batch_remove(added)
        _owners_cache[:] = [key, owners]
    return _owners_cache[1]


_structs_cache = [None, {}]
# Object types a modifier recipe is probed on (data created in a temporary Main).
_PROBE_DATA = (("MESH", "meshes", ()), ("CURVE", "curves", ("CURVE",)), ("LATTICE", "lattices", ()),
               ("CURVES", "hair_curves", ()), ("POINTCLOUD", "pointclouds", ()), ("VOLUME", "volumes", ()),
               ("GREASEPENCIL", "grease_pencils", ()))


def struct_recipes():
    """modifier:<TYPE> and constraint:<TYPE> from the RNA type enums: each type's RNA struct, its ID
    pointers and the object types where Blender creates it (modifiers.new / constraints.new on objects of
    a temporary Main; any data this adds to the open file is removed). Cached per enum set."""
    kinds = {"modifier": [i.identifier for i in bpy.types.Modifier.bl_rna.properties["type"].enum_items],
             "constraint": [i.identifier for i in bpy.types.Constraint.bl_rna.properties["type"].enum_items]}
    from .native_scene import _id_pointers
    key = json.dumps(kinds, sort_keys=True)
    if _structs_cache[0] != key:
        recipes = {}
        before = {entity.as_pointer() for entity in bpy.data.all_ids}
        try:
            with bpy.data.temp_data() as temp:
                owners = [("EMPTY", temp.objects.new("probe", None))]
                for kind, space, args in _PROBE_DATA:
                    collection = getattr(temp, space, None)
                    if collection is not None:
                        owners.append((kind, temp.objects.new("probe", collection.new("probe", *args))))
                for kind, types in kinds.items():
                    for type_id in types:
                        for object_type, obj in owners:
                            structs = obj.modifiers if kind == "modifier" else obj.constraints
                            try:
                                made = structs.new(name="probe", type=type_id) if kind == "modifier" else structs.new(type_id)
                            except Exception:  # noqa: BLE001 - refused on this object type
                                continue
                            if made is None:  # refused without an error
                                continue
                            entry = recipes.setdefault((kind, type_id), {"type": made.bl_rna.identifier, "owners": [], "pointers": [
                                p for p in _id_pointers(made.bl_rna) if p != "node_group"]})
                            entry["owners"].append(object_type)
                            structs.remove(made)
        finally:
            added = [entity for entity in bpy.data.all_ids if entity.as_pointer() not in before]
            if added:
                bpy.data.batch_remove(added)
        # A constraint applies to any object.
        for (kind, _), entry in recipes.items():
            if kind == "constraint":
                entry["owners"] = ["ANY"]
        _structs_cache[:] = [key, recipes]
    return _structs_cache[1]


def _relations(family):
    return {"Collection": ["children", "objects"], "Object": ["parent", "data", "collections", "modifiers", "constraints"],
            "Modifier": ["owner"], "Constraint": ["owner"],
            "Scene": ["root", "camera"], "NodeTree": ["nodes", "links"], "Node": ["owner", "parent"]}.get(family, [])


class NativeDiscovery:
    def __init__(self, app, refs):
        self.app, self.refs = app, refs
        self._cursors = OrderedDict()

    def reset(self):
        self._cursors.clear()

    def _schema(self):
        types, recipes = [], [{"id": "collection", "family": "Collection", "constructor": "data.collections.new"},
                              {"id": "object", "family": "Object", "constructor": "data.objects.new"}]
        classes = _classes()
        owners = node_owners(classes)
        for name, cls in sorted(classes.items()):
            family = _family(cls)
            recipe = None
            # Only the trees native plans edit, where Blender creates this type (Compositor and texture
            # nodes have none, so no recipe).
            if family == "Node" and owners.get(name):
                recipe = "node:" + name
                recipes.append({"id": recipe, "family": "Node", "type": name, "constructor": "owner.nodes.new",
                                "owners": owners[name], "requires_owner_poll": True})
            types.append({"id": name, "family": family,
                          "properties": property_schema(cls.bl_rna, cls=cls) if family != "unsupported" else {},
                          "relations": _relations(family), "recipe": recipe, "actions": ACTIONS.get(family, []),
                          "reason": None if family != "unsupported" else "unsupported_family"})
        for (kind, type_id), entry in sorted(struct_recipes().items()):
            recipes.append({"id": "%s:%s" % (kind, type_id), "family": kind.title(), "type": entry["type"],
                            "constructor": "owner.%ss.new" % kind, "owners": entry["owners"],
                            "pointers": entry["pointers"]})
        from .handlers.scene import PRIMITIVES
        recipes.extend({"id": "primitive:" + p, "family": "Object", "constructor": "host_bmesh_recipe",
                        "creates": ["Object", "Mesh"]} for p in PRIMITIVES)
        for recipe in recipes:
            recipe.update(available=True, source="native_rna_or_host_recipe")
        digest = hashlib.sha256(json.dumps({"types": types, "recipes": recipes, "limits": LIMITS}, sort_keys=True).encode()).hexdigest()
        return types, recipes, digest

    def schema_hash(self):
        return self._schema()[2]

    def _state(self, schema_hash):
        return {"script_handle": self.app.script_handle(), "script_epoch": self.refs.epoch,
                "revision": self.app.revision(), "schema_hash": schema_hash, "limits": dict(LIMITS)}

    def _page(self, query, items, state, channel):
        limit = query.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= PAGE:
            fail("limit must be an integer from 1 to %d." % PAGE)
        identity = (channel, state["script_handle"], state["script_epoch"], state["revision"], state["schema_hash"],
                    json.dumps({k: v for k, v in query.items() if k != "cursor"}, sort_keys=True))
        offset = 0
        if "cursor" in query:
            token = query["cursor"]
            if not isinstance(token, str) or token not in self._cursors:
                fail("Unknown or expired cursor.")
            expected, offset = self._cursors[token]
            if expected != identity:
                raise HostFailure("stale_revision", "Cursor document/epoch/revision/schema/query changed; restart pagination.")
        page = items[offset:offset + limit]
        next_cursor = None
        if offset + limit < len(items):
            next_cursor = secrets.token_hex(24)
            self._cursors[next_cursor] = (identity, offset + limit)
            while len(self._cursors) > CURSOR_RECEIPTS:
                self._cursors.popitem(last=False)
        return page, next_cursor

    @staticmethod
    def _pattern(value):
        if not isinstance(value, str) or not 1 <= len(value) <= ID_CHARS or "\x00" in value:
            fail("match must be a bounded string pattern.")
        return value

    def _card(self, entity, owner, include):
        family = _family(type(entity))
        card = {"ref": self.refs.ref(entity, owner), "name": entity.name, "type": entity.bl_rna.identifier, "family": family}
        schemas = property_schema(entity.bl_rna, entity)

        def ref(v):
            if v is None:
                return None
            return self.refs.ref(v, next(o for e, o in document_entities() if e == v))
        if "schema" in include:
            card.update(properties_schema=schemas, actions=ACTIONS.get(family, []))
        if "properties" in include:
            values = read_properties(entity, schemas)
            for name, schema in schemas.items():
                if schema["type"] == "POINTER" and schema["readable"]:
                    values[name] = ref(get_value(entity, name))  # an Object reference, never an internal key
            card["properties"] = values
        if "relations" in include:
            size = 0
            if isinstance(entity, bpy.types.Collection):
                size = len(entity.children) + len(entity.objects)
            elif isinstance(entity, bpy.types.Object):
                size = len(entity.users_collection) + 2
            elif supported_tree(entity):
                size = len(entity.nodes) + len(entity.links)
            if size > RELATIONS_PER_READ:
                fail("Entity relations exceed the read budget; query entities without relations.")
            relations = {}
            if isinstance(entity, bpy.types.Scene):
                relations = {"root": ref(entity.collection), "camera": ref(entity.camera)}
            elif isinstance(entity, bpy.types.Collection):
                relations = {"children": [ref(v) for v in entity.children], "objects": [ref(v) for v in entity.objects]}
            elif isinstance(entity, bpy.types.Object):
                relations = {"parent": ref(entity.parent), "data": ref(entity.data), "collections": [ref(v) for v in entity.users_collection],
                             "modifiers": [self.refs.ref(v, entity) for v in entity.modifiers],
                             "constraints": [self.refs.ref(v, entity) for v in entity.constraints]}
            elif isinstance(entity, (bpy.types.Modifier, bpy.types.Constraint)):
                relations = {"owner": ref(owner)}
            elif isinstance(entity, bpy.types.Node):
                relations = {"owner": ref(owner), "parent": ref(entity.parent)}
            elif supported_tree(entity):
                relations = {"nodes": [ref(v) for v in entity.nodes], "links": [
                    {"from": ref(l.from_node), "from_socket": l.from_socket.identifier,
                     "to": ref(l.to_node), "to_socket": l.to_socket.identifier} for l in entity.links]}
            card["relations"] = relations
        return card

    def describe(self, query):
        strict(query, ("kind", "match", "ref", "limit", "cursor"), "describe.query")
        kind = query.get("kind")
        if kind not in ("types", "recipes", "entity"):
            fail("kind must be types, recipes or entity.")
        if (kind != "entity" and "ref" in query) or (kind == "entity" and ("ref" not in query or "match" in query)):
            fail("entity requires only a reference selector.")
        types, recipes, digest = self._schema()
        state = self._state(digest)
        if kind == "entity":
            entity = self.refs.resolve(query["ref"])
            items = [self._card(entity, self.refs.owner(query["ref"]), ("schema", "relations"))]
        else:
            items = types if kind == "types" else recipes
            pattern = self._pattern(query.get("match", "*"))
            items = [v for v in items if fnmatch.fnmatchcase(v["id"], pattern)]
        state["items"], cursor = self._page(query, items, state, "describe")
        if cursor:
            state["next"] = cursor
        return state

    def context(self, query):
        strict(query, ("refs", "match", "include", "limit", "cursor"), "context.query")
        include = query.get("include", [])
        if not isinstance(include, list) or any(v not in ("properties", "relations", "schema") for v in include) or len(include) != len(set(include)):
            fail("include supports properties, relations and schema.")
        match = query.get("match", {})
        strict(match, ("type", "name"), "match")
        patterns = {k: self._pattern(v) for k, v in match.items()}
        requested = query.get("refs")
        if requested is not None:
            if not isinstance(requested, list) or len(requested) > REFERENCE_SELECTION or any(not isinstance(r, str) for r in requested) or len(set(requested)) != len(requested):
                fail("refs must be at most %d distinct references." % REFERENCE_SELECTION)
            entities = [(self.refs.resolve(r), self.refs.owner(r)) for r in requested]
        else:
            entities = list(document_entities())
        entities = [(v, o) for v, o in entities if all(fnmatch.fnmatchcase(v.name if k == "name" else v.bl_rna.identifier, p) for k, p in patterns.items())]
        entities.sort(key=lambda pair: (pair[0].bl_rna.identifier, pair[0].name, pair[0].as_pointer()))
        _, _, digest = self._schema()
        state = self._state(digest)
        page, cursor = self._page(query, entities, state, "context")
        state["entities"] = [self._card(v, o, include) for v, o in page]
        state["complete"] = cursor is None
        if cursor:
            state["next"] = cursor
        return state
