# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded scene-graph evidence, reconstructed from current RNA after undo.

Scope: ID inventories, scene roots and rigid body world collections, collection DAG/membership
with each edge's light-linking state, object data, parenting/channels/matrices, material slots,
rigid body/rigid body constraint presence, modifier/constraint topology and their direct ID
references, GN/shader trees and explicitly selected simple RNA properties (qualified one-level paths
and Object pointers included). Geometry buffers, animation,
render settings and arbitrary extension effects require their own qualified scope.

The whole document is always captured as an inventory: IDs, collection edges with their light-linking
state, object records (data, parenting, channels, matrices, modifiers and constraints with their ID
references, material slots, rigid bodies) and node-tree fingerprints (nodes, links, interface). The detail
(node-tree states with socket values and properties, and pose channels and animation keys of the `poses`
objects) is captured for the batch's `scope` (ID keys); without a scope, for the whole document. No count
limits the capture: the document limits itself (memory, resources.py).
"""

import math

import bpy

from .native_plans import ABS_TOLERANCE, REL_TOLERANCE, Snapshot, same_state  # noqa: F401
from .native_refs import identity
from .wire import HostFailure

QUALIFIED_LEGACY = frozenset(("scene.create_object", "scene.delete_object", "scene.transform_object", "scene.set_active_camera"))


def _key(value):
    if value is None:
        return None
    token = identity(value)
    if token is None:
        raise HostFailure("invalid_args", "Capture incomplete: native ID identity unavailable.")
    return "%s:%d" % token


def document_ids():
    """Live inventories including unused data, scene roots and embedded shader trees."""
    seen = set()
    for prop in bpy.data.bl_rna.properties:
        if prop.type != "COLLECTION":
            continue
        for value in getattr(bpy.data, prop.identifier):
            if isinstance(value, bpy.types.ID):
                key = _key(value)
                if key not in seen:
                    seen.add(key)
                    yield value, value
    for scene in bpy.data.scenes:
        if _key(scene.collection) not in seen:
            seen.add(_key(scene.collection))
            yield scene.collection, scene
    for data in (*bpy.data.materials, *bpy.data.worlds, *bpy.data.lights):
        tree = data.node_tree
        if tree is not None and _key(tree) not in seen:
            seen.add(_key(tree))
            yield tree, data


def supported_tree(tree):
    return isinstance(tree, bpy.types.NodeTree) and tree.bl_idname in ("GeometryNodeTree", "ShaderNodeTree")


def document_entities():
    for value, owner in document_ids():
        yield value, owner
        if supported_tree(value):
            for node in value.nodes:
                yield node, value
        elif isinstance(value, bpy.types.Object):
            for struct in (*value.modifiers, *value.constraints):
                yield struct, value


def _node_key(tree, node):
    # Logical state key, rebuilt after undo; public refs use separate live identities.
    return (_key(tree), node.name, node.bl_idname)


def struct_key(obj, struct):
    """Logical key of an object's modifier or constraint: owner, kind and its unique name there."""
    return ("S", _key(obj), "modifier" if isinstance(struct, bpy.types.Modifier) else "constraint", struct.name)


def target_key(entity):
    """Logical capture key for a resolved native target; independent of public refs."""
    if isinstance(entity, bpy.types.Node):
        return _node_key(entity.id_data, entity)
    if isinstance(entity, (bpy.types.Modifier, bpy.types.Constraint)):
        return struct_key(entity.id_data, entity)
    return _key(entity)


_ID_POINTERS = {}


def _id_pointers(rna):
    """Direct RNA pointer identifiers whose fixed type derives from ID, cached per struct type."""
    names = _ID_POINTERS.get(rna.identifier)
    if names is None:
        names = []
        for prop in rna.properties:
            base = prop.fixed_type if prop.type == "POINTER" else None
            while base is not None and base.identifier != "ID":
                base = base.base
            if base is not None:
                names.append(prop.identifier)
        names = _ID_POINTERS[rna.identifier] = tuple(names)
    return names


def _struct_refs(struct, skip=()):
    """(path, ID) for a constraint/modifier's direct ID pointers and constraint target items."""
    refs = [(name, getattr(struct, name)) for name in _id_pointers(struct.bl_rna) if name not in skip]
    if isinstance(struct, bpy.types.Constraint) and "targets" in struct.bl_rna.properties:
        for index, item in enumerate(struct.targets):
            refs.extend(("targets[%d].%s" % (index, name), getattr(item, name)) for name in _id_pointers(item.bl_rna))
    return refs


def _ref_state(struct, skip=()):
    return tuple((name, _key(value)) for name, value in _struct_refs(struct, skip))


def _ref_edges(struct, skip=()):
    return sum(value is not None for _, value in _struct_refs(struct, skip))


def _expected_uses(user, used):
    """References from `user` to object `used` that this capture models, counted like Blender."""
    if isinstance(user, bpy.types.Collection):
        return sum(o == used for o in user.objects)
    if isinstance(user, bpy.types.Scene):
        # Reading view-layer objects synchronizes bases before Blender counts them.
        bases = sum(any(o == used for o in layer.objects) for layer in user.view_layers)
        return int(user.camera == used) + sum(o == used for o in user.collection.objects) + bases
    if isinstance(user, bpy.types.Object):
        count = int(user.parent == used)
        for struct in (*user.constraints, *user.modifiers):
            count += sum(value == used for _, value in _struct_refs(struct))
        return count
    return None


def incoming_covered(obj):
    """True when every reference to `obj` lies in a captured edge, so its removal is attested.

    Blender's own per-user reference count must equal the modeled count; any other user type,
    or any extra channel (drivers, custom properties, node inputs, markers...), is unattested.
    """
    if obj is None:
        return True
    for user in bpy.data.user_map(subset={obj}).get(obj, ()):
        if user == obj:
            continue
        expected = _expected_uses(user, obj)
        if expected is None or expected != user.user_of_id(obj):
            return False
    return True


def _matrix(value):
    return tuple(tuple(float(v) for v in row) for row in value)


def json_value(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HostFailure("invalid_args", "Capture incomplete: non-finite RNA value.")
        return value
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(value))
    if isinstance(value, bpy.types.ID):
        return ("ID", _key(value))
    try:
        return tuple(json_value(v) for v in value)
    except TypeError as error:
        raise HostFailure("invalid_args", "Unsupported RNA data shape.") from error


def _socket(socket):
    value = None
    prop = socket.bl_rna.properties.get("default_value")
    if prop is not None:
        value = json_value(socket.default_value)
    return (socket.identifier, socket.bl_idname, socket.name, value)


def _tree_state(tree):
    from .native_discovery import property_schema, read_properties
    nodes = []
    for node in tree.nodes:
        schemas = property_schema(node.bl_rna, node)
        nodes.append((_node_key(tree, node), node.label, tuple(node.location),
                      _node_key(tree, node.parent) if node.parent else None,
                      read_properties(node, schemas),
                      tuple(_socket(s) for s in node.inputs), tuple(_socket(s) for s in node.outputs)))
    links = sorted((_node_key(tree, link.from_node), link.from_socket.identifier,
                    _node_key(tree, link.to_node), link.to_socket.identifier,
                    link.multi_input_sort_id) for link in tree.links)
    interface = tuple((item.item_type, item.name, getattr(item, "identifier", None),
                       getattr(item, "in_out", None), getattr(item, "socket_type", None),
                       item.parent.name if item.parent else None) for item in tree.interface.items_tree)
    return (tuple(sorted(nodes)), tuple(links), interface)


def structural_count(ids=None):
    """Count entities + edges before materializing values; includes every modeled edge."""
    ids = list(document_ids()) if ids is None else ids
    count = len(ids)
    for value, _ in ids:
        if isinstance(value, bpy.types.Scene):
            count += 1 + int(value.camera is not None) + sum(c is not None for c in _physics(value))
        elif isinstance(value, bpy.types.Collection):
            count += len(value.children) + len(value.objects)
        elif isinstance(value, bpy.types.Object):
            count += int(value.parent is not None) + int(value.data is not None)
            count += len(value.modifiers) + len(value.constraints)
            count += sum(slot.material is not None for slot in value.material_slots)
            count += sum(getattr(m, "node_group", None) is not None for m in value.modifiers)
            count += sum(_ref_edges(m, ("node_group",)) for m in value.modifiers)
            count += sum(_ref_edges(c) for c in value.constraints)
        if supported_tree(value):
            count += len(value.nodes) + len(value.links) + len(value.interface.items_tree)
            count += sum(1 + len(n.inputs) + len(n.outputs) + int(n.parent is not None) for n in value.nodes)
    count += sum(value != owner and not isinstance(value, bpy.types.Collection) for value, owner in ids)
    return count


def inventory_count(ids):
    """Elements of the whole-document inventory (cheap: no socket values, properties or references)."""
    count = len(ids)
    for value, _ in ids:
        if isinstance(value, bpy.types.Collection):
            count += len(value.children) + len(value.objects)
        elif isinstance(value, bpy.types.Object):
            count += 2 + len(value.modifiers) + len(value.constraints) + len(value.material_slots)
        if supported_tree(value):
            count += len(value.nodes) + len(value.links) + len(value.interface.items_tree)
    return count


def _tree_fingerprint(tree):
    """A tree outside the scope: its nodes, links and interface, without socket values or properties."""
    nodes = tuple(sorted((_node_key(tree, node), node.label, _node_key(tree, node.parent) if node.parent else None)
                         for node in tree.nodes))
    links = tuple(sorted((_node_key(tree, link.from_node), link.from_socket.identifier,
                          _node_key(tree, link.to_node), link.to_socket.identifier) for link in tree.links))
    interface = tuple((item.item_type, item.name, getattr(item, "identifier", None)) for item in tree.interface.items_tree)
    # The shape of _tree_state (nodes first: the planner reads node names there), marked as a fingerprint.
    return (nodes, links, interface, "fingerprint")


def _animation_state(owner):
    """Keys of an ID's action in its slot (Blender 5 layered actions), or None."""
    data = getattr(owner, "animation_data", None)
    if data is None:
        return None
    action, slot = data.action, getattr(data, "action_slot", None)
    curves = ()
    if action is not None:
        bag = None
        if slot is not None:
            try:
                from bpy_extras import anim_utils

                bag = anim_utils.action_get_channelbag_for_slot(action, slot)
            except (ImportError, AttributeError):
                bag = None
        source = bag.fcurves if bag is not None else getattr(action, "fcurves", ())
        curves = tuple(sorted((c.data_path, c.array_index, tuple((tuple(p.co), p.interpolation) for p in c.keyframe_points))
                              for c in source))
    return (_key(action), slot.identifier if slot is not None else None, curves, len(data.drivers), len(data.nla_tracks))


def _pose_state(obj):
    """Pose channels (armatures) and animation keys of an object in a pose-scoped batch."""
    bones = ()
    if obj.type == "ARMATURE" and obj.pose is not None:
        bones = tuple((b.name, b.rotation_mode, tuple(b.location), tuple(b.rotation_quaternion), tuple(b.rotation_euler),
                       tuple(b.rotation_axis_angle), tuple(b.scale), tuple((c.name, c.type) for c in b.constraints))
                      for b in obj.pose.bones)
    return (bones, _animation_state(obj))


def _pose_cost(obj):
    bones = len(obj.pose.bones) if obj.type == "ARMATURE" and obj.pose is not None else 0
    state = _animation_state(obj)
    return 1 + bones + (sum(len(curve[2]) for curve in state[2]) if state else 0)


def _physics(scene):
    """Rigid body world collections: a direct member gets a rigid body or constraint on link."""
    world = scene.rigidbody_world
    return (world.collection, world.constraints) if world is not None else (None, None)


def _edge_states(collection):
    """Light-linking state of each child/object edge (CollectionChild/Object align with the lists)."""
    return tuple(sorted([("children", _key(c), e.light_linking.link_state)
                         for c, e in zip(collection.children, collection.collection_children)] +
                        [("objects", _key(o), e.light_linking.link_state)
                         for o, e in zip(collection.objects, collection.collection_objects)]))


class SceneCapture:
    def __init__(self, state, count, complete=True, reason=None, targets=(), scope_ids=None, poses=(), inventory=0):
        self.state, self.count, self.complete, self.reason = state, count, complete, reason
        self.targets, self.scope_ids, self.poses, self.inventory = targets, scope_ids, poses, inventory
        # What the evidence covers: the whole document's detail, or its inventory and the batch's scope.
        self.scope = ("scene_graph_and_selected_native_data" if scope_ids is None
                      else "document_inventory_and_batch_scope")

    def __eq__(self, other):
        return (isinstance(other, SceneCapture) and self.complete and other.complete and
                self.count == other.count and same_state(self.state, other.state))


def capture(targets=(), complete=True, reason=None, scope=None, poses=()):
    """targets are logical ID/node keys and published direct RNA property identifiers; scope is the ID keys
    whose detail the batch may change (None: the whole document); poses the object keys whose pose channels
    and animation keys are captured.

    No RNA wrapper survives this capture. A post-undo capture rebuilds from bpy.data.
    """
    ids = list(document_ids())
    inventory = inventory_count(ids)
    inside = None if scope is None else frozenset(scope)

    def scoped(value, owner):
        return inside is None or _key(value) in inside or _key(owner) in inside

    by_key = {_key(value): value for value, _ in ids}
    posed = [by_key[key] for key in sorted(set(poses)) if isinstance(by_key.get(key), bpy.types.Object)]
    # Reported (verification elements), never a ceiling.
    count = structural_count([(value, owner) for value, owner in ids if scoped(value, owner)]) + sum(_pose_cost(o) for o in posed)
    state = {"ids": {}, "objects": {}, "collections": {}, "scenes": {}, "trees": {}, "properties": {}, "poses": {}}
    entities = {}
    for value, owner in ids:
        key = _key(value)
        entities[key] = value
        state["ids"][key] = (value.name, value.bl_rna.identifier, _key(owner),
                              value.library.filepath if value.library else None)
        if isinstance(value, bpy.types.Object):
            state["objects"][key] = (_key(value.data), _key(value.parent), value.parent_type, value.parent_bone,
                tuple(value.parent_vertices), _matrix(value.matrix_world), _matrix(value.matrix_basis),
                _matrix(value.matrix_parent_inverse), tuple(value.location), value.rotation_mode,
                tuple(value.rotation_euler), tuple(value.rotation_quaternion), tuple(value.rotation_axis_angle),
                tuple(value.scale), tuple(value.delta_location), tuple(value.delta_rotation_euler),
                tuple(value.delta_rotation_quaternion), tuple(value.delta_scale),
                tuple((m.name, m.type, _key(getattr(m, "node_group", None)), _ref_state(m, ("node_group",)))
                      for m in value.modifiers),
                tuple((c.name, c.type, _ref_state(c)) for c in value.constraints),
                tuple((_key(s.material), s.link) for s in value.material_slots),
                value.rigid_body.type if value.rigid_body else None,
                value.rigid_body_constraint.type if value.rigid_body_constraint else None)
            entities.update((struct_key(value, s), s) for s in (*value.modifiers, *value.constraints))
        elif isinstance(value, bpy.types.Collection):
            state["collections"][key] = (tuple(sorted(_key(c) for c in value.children)),
                                         tuple(sorted(_key(o) for o in value.objects)), _edge_states(value))
        elif isinstance(value, bpy.types.Scene):
            state["scenes"][key] = (_key(value.collection), _key(value.camera), value.frame_current, value.frame_subframe,
                                    *(_key(c) for c in _physics(value)))
        if supported_tree(value):
            state["trees"][key] = _tree_state(value) if scoped(value, owner) else _tree_fingerprint(value)
            entities.update((_node_key(value, n), n) for n in value.nodes)
    for obj in posed:
        state["poses"][_key(obj)] = _pose_state(obj)
    from .native_discovery import get_value, property_schema
    for key, prop in targets:
        entity = entities.get(key)
        if entity is None:
            raise HostFailure("invalid_args", "Capture target is absent.")
        schema = property_schema(entity.bl_rna, entity).get(prop)
        if schema is None or not schema["readable"]:
            raise HostFailure("invalid_args", "Capture target property is unsupported.")
        state["properties"][(key, prop)] = json_value(get_value(entity, prop))
    if not same_state(state, state):
        raise HostFailure("invalid_args", "Capture incomplete: non-finite structural state.")
    return SceneCapture(state, count, complete, reason, tuple(targets), None if inside is None else inside,
                        tuple(sorted(set(poses))), inventory)


def batch_scope(entities, owners=()):
    """ID keys whose detail a batch on these entities may change: each ID, its owner, an object's data, a
    scene's root collection; the tree of a node; the object of a modifier or constraint."""
    scope = set()
    for entity in (*entities, *owners):
        if entity is None:
            continue
        if isinstance(entity, (bpy.types.Node, bpy.types.Modifier, bpy.types.Constraint)):
            entity = entity.id_data
        if not isinstance(entity, bpy.types.ID):
            continue
        scope.add(_key(entity))
        if isinstance(entity, bpy.types.Object) and entity.data is not None:
            scope.add(_key(entity.data))
        if isinstance(entity, bpy.types.Scene):
            scope.add(_key(entity.collection))
    return frozenset(scope)


# -- native plan execution -----------------------------------------------------------------------
OBJECT_DATA = tuple(t for t in (getattr(bpy.types, n, None) for n in (
    "Mesh", "Curve", "MetaBall", "Lattice", "Camera", "Light", "Speaker", "LightProbe", "Armature",
    "GreasePencil", "Volume", "PointCloud", "Curves")) if t is not None)


def _animated(id_data):
    data = getattr(id_data, "animation_data", None)
    return bool(data and (data.action or len(data.drivers) or len(data.nla_tracks)))


def _editable(id_data):
    return id_data.library is None and id_data.override_library is None and bool(id_data.is_editable)


def _spaces():
    return {p.fixed_type.identifier: p.identifier for p in bpy.data.bl_rna.properties if p.type == "COLLECTION"}


def _namespace(entity, spaces):
    rna = entity.bl_rna
    while rna is not None:
        if rna.identifier in spaces:
            return spaces[rna.identifier]
        rna = rna.base
    return None


class NativeExecution:
    """One native apply: live wrappers of the resolved refs, the Snapshot and the step executors.

    Wrappers stay valid until undo; the core never reuses this object after its restoration.
    """

    def __init__(self, refs, request):
        from .handlers.scene import PRIMITIVES
        from .native_discovery import family, property_schema, registered_classes
        self.refs, self.classes = refs, registered_classes()
        self.entities, self.owners, self.created, self.meshes = {}, {}, {}, {}
        spaces = _spaces()
        layer = bpy.context.view_layer
        evaluated = {_key(o) for o in layer.objects} if layer is not None else set()
        facts = {}
        for ref in sorted(request.refs):
            entity, owner = refs.resolve(ref), refs.owner(ref)
            key = target_key(entity)
            self.entities[key], self.owners[key] = entity, owner
            fact = {"key": key, "family": family(type(entity)), "type": entity.bl_rna.identifier, "name": entity.name,
                    "editable": _editable(owner if owner is not entity and not isinstance(entity, bpy.types.Node) else entity.id_data),
                    "root": isinstance(entity, bpy.types.Collection) and owner is not entity,
                    "embedded": isinstance(entity, bpy.types.NodeTree) and owner is not entity,
                    "namespace": _namespace(entity, spaces) if owner is entity else None,
                    "data_ok": isinstance(entity, OBJECT_DATA), "schemas": {}}
            if isinstance(entity, bpy.types.Object):
                fact.update(evaluated=key in evaluated, animated=_animated(entity), constrained=len(entity.constraints) > 0,
                            object_type=entity.type)
            if isinstance(entity, (bpy.types.Modifier, bpy.types.Constraint)):
                fact.update(editable=_editable(entity.id_data), object=_key(entity.id_data))
            if isinstance(entity, bpy.types.Node):
                fact.update(tree=_key(entity.id_data), tree_animated=_animated(entity.id_data))
            if isinstance(entity, bpy.types.NodeTree):
                fact["tree_type"] = entity.bl_idname
            facts[ref] = fact
        targets = set()
        for handle, prop in request.properties:
            if handle in facts:
                key = facts[handle]["key"]
                schema = property_schema(self.entities[key].bl_rna, self.entities[key]).get(prop)
                facts[handle]["schemas"][prop] = schema
                if schema is not None and schema["readable"]:
                    targets.add((key, prop))
        self.scope = batch_scope(self.entities.values(), self.owners.values())
        self.before = capture(targets=tuple(sorted(targets, key=repr)), scope=self.scope)
        names = {space: {v.name for v in getattr(bpy.data, space) if v.library is None} for space in spaces.values()}
        self.snapshot = Snapshot(self.before.state, self.before.count, facts, names, PRIMITIVES, self)

    # -- hooks of the pure planner ---------------------------------------------------------------
    def node_recipe(self, type_id, tree_key):
        cls = self.classes.get(type_id)
        if cls is None or not issubclass(cls, bpy.types.Node) or not cls.is_registered_node_type():
            return "unknown_node_type"
        if cls.__module__ != "bpy.types":
            # A registered extension class answers its native poll; built-in types are checked by nodes.new.
            try:
                if not cls.poll(self.entities[tree_key]):
                    return "native_poll_refused"
            except Exception:  # noqa: BLE001 - trusted extension code failing its poll refuses the recipe
                return "native_poll_failed"
        return None

    def struct_recipe(self, kind, type_id):
        """{type, owners, pointers} of a modifier:<TYPE> or constraint:<TYPE> recipe, or None."""
        from .native_discovery import struct_recipes
        return struct_recipes().get((kind, type_id))

    def type_schema(self, type_name, prop):
        from .native_discovery import property_schema
        cls = self.classes.get(type_name)
        return property_schema(cls.bl_rna, cls=cls).get(prop) if cls is not None else None

    def validate(self, schema, value):
        from .native_discovery import validate_value
        return validate_value(schema, value)

    # -- execution ------------------------------------------------------------------------------
    def live(self, handle):
        if isinstance(handle, tuple) and handle[0] == "$":
            return self.created[handle[1]]
        return self.entities[handle]

    def execute(self, step):
        import mathutils  # imported on use: the add-on's registration gate imports this module without Blender

        from .native_discovery import property_schema, validate_value
        op = step[0]
        if op == "entity.create":
            _, kind, detail, name, owner, data, alias = step
            if kind == "collection":
                value = bpy.data.collections.new(name)
            elif kind == "node":
                value = self.live(owner).nodes.new(detail)
                value.name = name
            elif kind == "modifier":
                value = self.live(owner).modifiers.new(name=name, type=detail)
            elif kind == "constraint":
                value = self.live(owner).constraints.new(type=detail)
                value.name = name
            else:
                if kind == "primitive":
                    from .handlers.scene import _primitive_mesh
                    self.meshes[alias] = data = _primitive_mesh(detail, name)
                    if data.name != name:
                        raise RuntimeError("Blender named the primitive mesh %s" % data.name)
                else:
                    data = self.live(data) if data is not None else None
                value = bpy.data.objects.new(name, data)
            if value.name != name:
                raise RuntimeError("Blender named the creation %s" % value.name)
            self.created[alias] = value
        elif op == "entity.rename":
            value = self.live(step[1])
            value.name = step[2]
            if value.name != step[2]:
                raise RuntimeError("Blender refused the exact name %s" % step[2])
        elif op == "property.set":
            from .native_discovery import holder
            _, handle, prop, value = step
            entity = self.live(handle)
            schema = property_schema(entity.bl_rna, entity).get(prop)
            if schema is None or not schema["writable"]:
                raise RuntimeError("%s is not writable on this instance" % prop)
            target, attr = holder(entity, prop)  # a published identifier or qualified path only
            if schema["type"] == "POINTER":
                pointed = self.live(value[1]) if value is not None else None
                setattr(target, attr, pointed)
                if getattr(target, attr) != pointed:
                    raise RuntimeError("Blender refused %s for %s" % (pointed.name if pointed else "nothing", prop))
            else:
                value = validate_value(schema, value)
                setattr(target, attr, set(value) if schema.get("enum_flag") else value)
        elif op in ("relation.link", "relation.unlink"):
            _, owner, relation, target = step
            edges = self.live(owner).children if relation == "children" else self.live(owner).objects
            (edges.link if op == "relation.link" else edges.unlink)(self.live(target))
        elif op == "object.parent":
            _, handle, parent, world, parent_world = step
            obj, world = self.live(handle), mathutils.Matrix(world)
            if parent is None:
                obj.parent = None
                obj.matrix_world = world
            else:
                basis = obj.matrix_basis.copy()
                obj.parent = self.live(parent)
                obj.parent_type = "OBJECT"
                obj.matrix_parent_inverse = mathutils.Matrix(parent_world).inverted() @ world @ basis.inverted()
        else:
            raise RuntimeError("unknown native step")

    def created_keys(self):
        return {alias: {"key": target_key(value), "mesh": _key(self.meshes[alias]) if alias in self.meshes else None}
                for alias, value in self.created.items()}

    def capture_after(self, plan):
        created = {item["key"] for item in self.created_keys().values() if isinstance(item["key"], str)}
        created |= {item["mesh"] for item in self.created_keys().values() if item["mesh"]}
        return capture(targets=plan.after_targets(self.before.targets, self.created_keys()), scope=self.scope | created)

    def card(self, handle):
        value = self.live(handle)
        return {"name": value.name, "type": value.bl_rna.identifier}

    def ref(self, handle):
        value = self.live(handle)
        child = isinstance(value, (bpy.types.Node, bpy.types.Modifier, bpy.types.Constraint))
        owner = value.id_data if child else self.owners.get(handle, value)
        return self.refs.ref(value, owner)
