# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation of edit batches (the thin typed layer), before anything runs.

An edit batch is a list of `{op, args}` whose ops are the names of `SPEC`. Every field is closed and typed;
names are bounded by Blender's own limits; `$alias` references must follow the `create_object` that binds
them. The document checks (objects, bones, collections exist) come next, in thin.py, still before any change.

This module is free of bpy (tested without Blender).
"""

import math
import re

from . import limits

ORDERS = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")
AXES = ("Y", "-Y", "X", "-X", "Z", "-Z")
PATHS = ("location", "rotation", "scale")
# Whose animation an RNA path is keyed on: the object, its data (a camera's lens) or its data's shape keys.
OWNERS = ("object", "data", "shape_keys")
VALUE_ITEMS = 32
# Blender 5.3 TextCurve.align_x / align_y items (rna_curve.cc).
ALIGN_X = ("LEFT", "CENTER", "RIGHT", "JUSTIFY", "FLUSH")
ALIGN_Y = ("TOP", "TOP_BASELINE", "CENTER", "BOTTOM_BASELINE", "BOTTOM")


def _alias_pattern():
    return re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,%d}$" % (limits.value("id_chars") - 1))


class PlanError(ValueError):
    """A refused batch: nothing ran."""


def F(kind, required=False, default=None):
    return {"kind": kind, "required": required, "default": default}


ROTATION = F("rotation")

# op: {field: spec}, with a one-line description (blender_catalog lists both).
SPEC = {
    "set_transform": ({"ref": F("ref", True), "location": F("vec3"), "rotation": ROTATION, "scale": F("scale"),
                       "delta": F("bool", default=False)},
                      "Location, rotation (written in the object's own rotation mode) and scale; delta adds the location."),
    "fit_height": ({"ref": F("ref", True), "height": F("number"), "ground": F("bool", default=True),
                    "ground_z": F("number", default=0.0), "children": F("bool", default=True),
                    "scale_axes": F({"enum": ("z", "uniform")}, default="z"), "frame": F("whole_frame"),
                    "key": F("bool", default=False)},
                   "Scale to a height in metres (Z only by default; scale_axes uniform keeps the proportions) and/or put "
                   "the lowest point at ground_z, measured on the evaluated geometry (armature pose and modifiers "
                   "included) at frame (default: the current frame, which stays current); key keys the location (and "
                   "scale) there, so a falling body touches the ground at each of its key frames. Without height "
                   "nothing is scaled."),
    "pose_bone": ({"ref": F("ref", True), "bone": F("name", True), "location": F("vec3"), "rotation": ROTATION,
                   "scale": F("scale")},
                  "A pose bone's location, rotation (in the bone's rotation mode) and scale."),
    "aim_bone": ({"ref": F("ref", True), "bone": F("name", True), "target": F("target", True),
                  "axis": F({"enum": AXES}, default="Y"), "tolerance_deg": F("number")},
                 "Rotate a pose bone about its head so its axis points at a point, an object (or one of its bones) "
                 "or along a world direction; reports error_deg and refuses a miss above tolerance_deg."),
    "insert_key": ({"ref": F("ref", True), "bone": F("name"), "paths": F("paths"), "path": F("data_path"),
                    "index": F("index"), "owner": F({"enum": OWNERS}, default="object"), "value": F("value"),
                    "frame": F("frame", True), "interpolation": F({"enum": ("CONSTANT", "LINEAR", "BEZIER")})},
                   "Key at a frame the current location, rotation and/or scale (paths) of an object or pose bone, or any "
                   "animatable property by its RNA path (path), such as constraints[\"Grab\"].influence, "
                   "key_blocks[\"Blink\"].value with owner shape_keys, lens with owner data, or [\"prop\"]; with bone the "
                   "path is the pose bone's. value sets the property first; index keys one element of an array."),
    "delete_keys": ({"ref": F("ref", True), "bone": F("name"), "paths": F("paths"), "path": F("data_path"),
                     "index": F("index"), "owner": F({"enum": OWNERS}, default="object"), "frames": F("frames")},
                    "Delete the keys of paths (location, rotation, scale) or of one RNA path (each array element unless "
                    "index) in frames [first, last] (all without frames); an F-curve left without keys is removed."),
    "create_object": ({"kind": F({"enum": ("empty", "camera", "light", "mesh", "text")}, True), "name": F("name", True),
                       "as": F("alias"), "primitive": F({"enum": ("cube", "plane", "uv_sphere", "cylinder", "cone")}),
                       "size": F("positive"), "depth": F("positive"), "light_type": F({"enum": ("POINT", "SUN", "SPOT", "AREA")}),
                       "energy": F("number"), "lens": F("number"), "body": F("text"), "extrude": F("nonnegative"),
                       "align_x": F({"enum": ALIGN_X}), "align_y": F({"enum": ALIGN_Y}),
                       "location": F("vec3"), "rotation": ROTATION, "collection": F("name"), "parent": F("ref")},
                      "A new empty, camera, light, mesh primitive or text; as names it $alias for later operations. "
                      "Mesh size (default 1 m): a cube's edge, a plane's side, a uv_sphere's diameter, a cylinder's "
                      "diameter and height, a cone's base diameter and height; depth sets a cylinder's or cone's height "
                      "apart (a barrel 0.6 wide and 0.9 high: size 0.6, depth 0.9). Text: body, size (font size in metres, default 1), extrude (depth), "
                      "align_x, align_y."),
    "rename": ({"ref": F("ref", True), "name": F("name", True)}, "Rename an object."),
    "delete": ({"refs": F("refs"), "collections": F("names")},
               "Delete objects (refs) and empty collections (collections): their names are free for the batch's later "
               "operations at once (and an object's data's when only it used it); they leave the document when the "
               "batch succeeds."),
    "duplicate": ({"ref": F("ref", True), "as": F("alias"), "name": F("name"), "suffix": F("name"),
                   "offset": F("vec3"), "linked": F("bool", default=False), "children": F("bool", default=True)},
                  "Copy an object with its whole hierarchy (children false: alone) into the same collections, moved "
                  "by offset in world space. linked shares the data (Blender's Alt+D), otherwise each copy gets its own "
                  "(Shift+D); materials and actions stay shared. name names the copy of ref, suffix is added to every "
                  "copy's original name (Blender's .001 otherwise); as names the copy of ref $alias."),
    "set_material": ({"ref": F("ref", True), "material": F("name", True), "color": F("rgb"),
                      "roughness": F("nonnegative"), "metallic": F("nonnegative"), "slot": F("index")},
                     "Assign a material to an object's slot, creating it (Principled BSDF) when no material has that "
                     "name; color (linear RGB, also the viewport colour), roughness and metallic set its Principled "
                     "BSDF, also on an existing material. Without slot: its only slot (added when it has none); slot "
                     "equal to the number of slots adds one."),
    "scene_settings": ({"frame_start": F("whole_frame"), "frame_end": F("whole_frame"), "frame_current": F("whole_frame"),
                        "fps": F("positive")},
                       "The scene's frame range, current frame (animation evaluated there) and frames per second "
                       "(a fractional rate such as 23.976 sets fps and fps_base)."),
    "shift_keys": ({"ref": F("ref", True), "bone": F("name"), "paths": F("paths"), "frames": F("frames"),
                    "offset": F("number"), "delete": F("bool", default=False)},
                   "Move the keys of an object (or one pose bone) in frames [first, last] (all without frames) by "
                   "offset frames, handles included, or delete them; paths narrows them to location, rotation and/or "
                   "scale. The keys of its action's slot (Blender 5 layered actions)."),
    "link": ({"ref": F("ref", True), "collection": F("name", True)}, "Link an object into a collection."),
    "unlink": ({"ref": F("ref", True), "collection": F("name", True)},
               "Unlink an object from a collection (never from its last one: use delete)."),
    "create_collection": ({"name": F("name", True), "parent": F("name")}, "A new collection, in parent or the scene's."),
    "set_parent": ({"ref": F("ref", True), "parent": F("ref"), "bone": F("name"), "keep_transform": F("bool", default=True)},
                   "Parent an object (to a bone with bone), or clear its parent without parent; keeps its world transform."),
    "add_marker": ({"name": F("name", True), "frame": F("frame", True), "camera": F("ref")},
                   "A timeline marker, optionally bound to a camera."),
    "set_camera": ({"ref": F("ref", True)}, "Make a camera the scene's active camera."),
    "add_constraint": ({"ref": F("ref", True), "type": F({"enum": ("TRACK_TO", "DAMPED_TRACK", "COPY_LOCATION",
                                                                    "COPY_ROTATION", "COPY_TRANSFORMS", "CHILD_OF")}, True),
                        "target": F("ref", True), "subtarget": F("name"), "name": F("name"), "influence": F("nonnegative")},
                       "An object constraint targeting an object (subtarget: one of its bones), with its influence (0 to 1)."),
    "switch_parent": ({"ref": F("ref", True), "parent": F("ref"), "bone": F("name"), "frame": F("whole_frame", True)},
                      "From frame on, the object follows parent (one of its bones with bone; the world without parent) "
                      "without a jump: an anchor empty is made under parent where the object is at that frame, and a "
                      "Copy Transforms constraint to it takes over at that frame (influences keyed CONSTANT: this "
                      "switch's 1, earlier switches' 0). Before frame nothing changes. A revolver goes holster, hand, "
                      "ground in three operations."),
}
OPS = tuple(SPEC)
REFERENCES = ("ref", "parent", "camera", "target")

# The rules across fields that validate() checks, as ops.json and the catalog state them.
RULES = {
    "set_transform": ("give location, rotation or scale",),
    "pose_bone": ("give location, rotation or scale",),
    "fit_height": ("give height or ground true", "height above 0"),
    "create_object": ("primitive and size apply to a mesh; depth to a cylinder or cone; light_type and energy to a "
                      "light; lens to a camera; "
                      "body, size, extrude, align_x and align_y to a text",
                      "a text needs body"),
    "set_parent": ("bone needs parent",),
    "insert_key": ("give paths or path (one of them)", "value and index go with path", "owner other than object "
                   "goes with path, without bone"),
    "delete_keys": ("give paths or path (one of them)", "index goes with path", "owner other than object goes with "
                    "path, without bone", "frames is [first, last] with first at most last"),
    "add_constraint": ("influence from 0 to 1",),
    "switch_parent": ("bone needs parent", "parent is not the object or one of its children"),
    "delete": ("give refs or collections", "a collection is deleted empty: its objects and child collections are "
               "deleted by the same batch or moved first"),
    "duplicate": ("name, suffix and as apply to the copy of ref (suffix to every copy)",),
    "set_material": ("color, roughness and metallic are optional: without them an existing material is only assigned",),
    "scene_settings": ("give at least one field", "frame_start at most frame_end when both are given; one alone may not "
                       "cross the scene's other end (refused, never pushed)"),
    "shift_keys": ("give offset (not 0) or delete true, not both", "frames is [first, last] with first at most last",
                   "a key may not land on a key that does not move (refused)"),
}


def is_edit_batch(operations):
    """True when every op is an edit op (the thin path); mixed batches are refused by the caller."""
    return isinstance(operations, list) and bool(operations) and all(
        isinstance(op, dict) and op.get("op") in SPEC for op in operations)


def names_edit_op(operations):
    return isinstance(operations, list) and any(isinstance(op, dict) and op.get("op") in SPEC for op in operations)


def _fail(path, message):
    raise PlanError("%s: %s" % (path, message))


def _number(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(path, "expected a finite number")
    return float(value)


def _vector(value, path, size):
    if not isinstance(value, list) or len(value) != size:
        _fail(path, "expected %d numbers" % size)
    return [_number(item, "%s[%d]" % (path, index)) for index, item in enumerate(value)]


def _name(value, path):
    # Blender ID names: MAX_ID_NAME 258 = 2-byte type code + 255 bytes + NUL (limits.py name_bytes).
    if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf-8")) > limits.value("name_bytes"):
        _fail(path, "expected a name of 1 to %d bytes" % limits.value("name_bytes"))
    return value


def _ref(value, path):
    name = _name(value, path)
    if name.startswith("$") and not _alias_pattern().match(name[1:]):
        _fail(path, "invalid $alias")
    return name


def _record(value, path, keys):
    if not isinstance(value, dict):
        _fail(path, "expected an object")
    unknown = sorted(set(value) - set(keys))
    if unknown:
        _fail(path, "unknown field %s" % unknown[0])
    return value


def _value(kind, value, path):
    if isinstance(kind, dict):
        if value not in kind["enum"]:
            _fail(path, "expected one of %s" % ", ".join(kind["enum"]))
        return value
    if kind == "ref":
        return _ref(value, path)
    if kind == "name":
        return _name(value, path)
    if kind == "alias":
        if not isinstance(value, str) or not _alias_pattern().match(value):
            _fail(path, "expected an alias (letters, digits, _; not starting with a digit)")
        return value
    if kind == "number":
        return _number(value, path)
    if kind == "positive":
        number = _number(value, path)
        if number <= 0:
            _fail(path, "expected a number above 0")
        return number
    if kind == "nonnegative":
        number = _number(value, path)
        if number < 0:
            _fail(path, "expected a number of 0 or more")
        return number
    if kind == "index":
        if type(value) is not int or value < 0:
            _fail(path, "expected an integer of 0 or more")
        return value
    if kind == "text":
        if not isinstance(value, str) or not value or "\x00" in value:
            _fail(path, "expected a non-empty text")
        return value
    if kind == "rgb":
        color = _vector(value, path, 3)
        if any(item < 0 for item in color):
            _fail(path, "expected 3 numbers of 0 or more (linear RGB)")
        return color
    if kind == "whole_frame":
        number = _value("frame", value, path)
        if number != int(number):
            _fail(path, "expected a whole frame")
        return int(number)
    if kind == "frames":
        frames = [_value("frame", item, "%s[%d]" % (path, index)) for index, item in enumerate(_vector(value, path, 2))]
        if frames[0] > frames[1]:
            _fail(path, "expected [first, last] with first at most last")
        return frames
    if kind == "frame":
        # Blender's frame range: DNA_scene_types.h MINAFRAME/MAXFRAME (limits.py max_frame).
        number = _number(value, path)
        if abs(number) > limits.value("max_frame"):
            _fail(path, "expected a frame from -%d to %d" % (limits.value("max_frame"), limits.value("max_frame")))
        return number
    if kind == "bool":
        if not isinstance(value, bool):
            _fail(path, "expected true or false")
        return value
    if kind == "vec3":
        return _vector(value, path, 3)
    if kind == "scale":
        return [_number(value, path)] * 3 if not isinstance(value, list) else _vector(value, path, 3)
    if kind == "rotation":
        rotation = _record(value, path, ("euler_deg", "order", "quaternion"))
        if ("euler_deg" in rotation) == ("quaternion" in rotation):
            _fail(path, "expected euler_deg or quaternion")
        if "quaternion" in rotation:
            if "order" in rotation:
                _fail(path + ".order", "belongs to euler_deg")
            quaternion = _vector(rotation["quaternion"], path + ".quaternion", 4)
            if math.sqrt(sum(item * item for item in quaternion)) == 0.0:
                _fail(path + ".quaternion", "a zero quaternion is no rotation")
            return {"quaternion": quaternion}
        order = rotation.get("order", "XYZ")
        if order not in ORDERS:
            _fail(path + ".order", "expected one of %s" % ", ".join(ORDERS))
        return {"euler_deg": _vector(rotation["euler_deg"], path + ".euler_deg", 3), "order": order}
    if kind == "target":
        target = _record(value, path, ("point", "object", "bone", "direction"))
        if sum(1 for key in ("point", "object", "direction") if key in target) != 1:
            _fail(path, "expected exactly one of point, object, direction")
        if "bone" in target and "object" not in target:
            _fail(path + ".bone", "belongs to object")
        if "point" in target:
            return {"point": _vector(target["point"], path + ".point", 3)}
        if "direction" in target:
            direction = _vector(target["direction"], path + ".direction", 3)
            if not any(direction):
                _fail(path + ".direction", "a zero direction points nowhere")
            return {"direction": direction}
        out = {"object": _ref(target["object"], path + ".object")}
        if "bone" in target:
            out["bone"] = _name(target["bone"], path + ".bone")
        return out
    if kind == "data_path":
        if not isinstance(value, str) or not value or "\x00" in value or len(value) > 1024 or value.startswith("."):
            _fail(path, "expected an RNA path of 1 to 1024 characters (constraints[\"Grab\"].influence)")
        return value
    if kind == "value":
        if isinstance(value, bool):
            return value
        if isinstance(value, list):
            if not 1 <= len(value) <= VALUE_ITEMS:
                _fail(path, "expected 1 to %d numbers" % VALUE_ITEMS)
            return [_number(item, "%s[%d]" % (path, index)) for index, item in enumerate(value)]
        return _number(value, path)
    if kind == "names":
        if not isinstance(value, list) or not value:
            _fail(path, "expected a list of names")
        names = [_name(item, "%s[%d]" % (path, index)) for index, item in enumerate(value)]
        if len(set(names)) != len(names):
            _fail(path, "a name is listed twice")
        return names
    if kind == "refs":
        if not isinstance(value, list) or not value:
            _fail(path, "expected a list of object names")
        refs = [_ref(item, "%s[%d]" % (path, index)) for index, item in enumerate(value)]
        if len(set(refs)) != len(refs):
            _fail(path, "a name is listed twice")
        return refs
    if kind == "paths":
        if not isinstance(value, list) or not value or any(item not in PATHS for item in value):
            _fail(path, "expected a list of location, rotation and/or scale")
        return list(dict.fromkeys(value))
    raise AssertionError(kind)


def validate(operations):
    """Checked operations [(op, args)]; PlanError names the first refused field."""
    if not isinstance(operations, list) or not operations:
        raise PlanError("operations: expected a non-empty list")
    if len(operations) > limits.value("operations"):
        raise PlanError("operations: at most %d per apply" % limits.value("operations"))
    aliases = set()
    checked = []
    for index, raw in enumerate(operations):
        path = "operations[%d]" % index
        op = raw.get("op") if isinstance(raw, dict) else None
        if op not in SPEC:
            _fail(path + ".op", "unknown edit operation %s" % str(op)[:64])
        _record(raw, path, ("op", "args"))
        fields = SPEC[op][0]
        given = _record(raw.get("args", {}), path + ".args", fields)
        args = {}
        for key, spec in fields.items():
            if key not in given or given[key] is None:
                if spec["required"]:
                    _fail("%s.args" % path, "missing %s" % key)
                if spec["default"] is not None:
                    args[key] = spec["default"]
                continue
            args[key] = _value(spec["kind"], given[key], "%s.args.%s" % (path, key))
        for key in REFERENCES + ("refs",):
            values = args.get(key) if key == "refs" else [args.get(key)]
            if key == "target" and isinstance(args.get(key), dict):
                values = [args[key].get("object")]
            for value in values or ():
                if isinstance(value, str) and value.startswith("$") and value[1:] not in aliases:
                    _fail("%s.args.%s" % (path, key), "%s is used before the create_object that names it" % value)
        if op in ("create_object", "duplicate") and "as" in args:
            if args["as"] in aliases:
                _fail(path + ".args.as", "alias $%s is bound twice" % args["as"])
            aliases.add(args["as"])
        if op in ("set_transform", "pose_bone") and not any(key in args for key in ("location", "rotation", "scale")):
            _fail(path + ".args", "nothing to set: give location, rotation or scale")
        if op == "fit_height" and "height" not in args and not args["ground"]:
            _fail(path + ".args", "nothing to do: give height or ground")
        if op == "fit_height" and "height" in args and args["height"] <= 0:
            _fail(path + ".args.height", "expected a height above 0")
        if op == "create_object":
            extra = {"empty": (), "camera": ("lens",), "light": ("light_type", "energy"),
                     "mesh": ("primitive", "size", "depth"), "text": ("body", "size", "extrude", "align_x", "align_y")}[args["kind"]]
            for key in ("primitive", "size", "depth", "light_type", "energy", "lens", "body", "extrude", "align_x", "align_y"):
                if key in args and key not in extra:
                    _fail("%s.args.%s" % (path, key), "does not apply to a %s" % args["kind"])
            if "depth" in args and args.get("primitive") not in ("cylinder", "cone"):
                _fail(path + ".args.depth", "applies to a cylinder or cone")
            if args["kind"] == "text" and "body" not in args:
                _fail(path + ".args", "missing body (a text's characters)")
        if op in ("insert_key", "delete_keys"):
            if ("paths" in args) == ("path" in args):
                _fail(path + ".args", "give paths or path (one of them)")
            for key in ("value", "index") if op == "insert_key" else ("index",):
                if key in args and "path" not in args:
                    _fail("%s.args.%s" % (path, key), "goes with path")
            if args["owner"] != "object" and ("path" not in args or "bone" in args):
                _fail(path + ".args.owner", "%s goes with path, without bone" % args["owner"])
        if op == "add_constraint" and args.get("influence", 0) > 1:
            _fail(path + ".args.influence", "expected 0 to 1")
        if op == "switch_parent" and "bone" in args and "parent" not in args:
            _fail(path + ".args.bone", "belongs to parent")
        if op == "delete" and "refs" not in args and "collections" not in args:
            _fail(path + ".args", "nothing to delete: give refs or collections")
        if op == "set_parent" and "bone" in args and "parent" not in args:
            _fail(path + ".args.bone", "belongs to parent")
        if op == "scene_settings":
            if not any(key in args for key in ("frame_start", "frame_end", "frame_current", "fps")):
                _fail(path + ".args", "nothing to set: give frame_start, frame_end, frame_current or fps")
            if "frame_start" in args and "frame_end" in args and args["frame_start"] > args["frame_end"]:
                _fail(path + ".args.frame_start", "is after frame_end")
        if op == "shift_keys":
            if args["delete"] == ("offset" in args):
                _fail(path + ".args", "give offset or delete true (one of them)")
            if "offset" in args and args["offset"] == 0:
                _fail(path + ".args.offset", "an offset of 0 moves nothing")
        checked.append((op, args))
    return checked


def catalog():
    """The edit operations as blender_catalog lists them: name, description and argument kinds."""
    def kind(spec):
        value = spec["kind"]
        text = "|".join(value["enum"]) if isinstance(value, dict) else value
        return text + ("*" if spec["required"] else "")
    return [dict({"name": op, "kind": "edit", "description": description,
                  "args": {key: kind(spec) for key, spec in fields.items()}},
                 **({"rules": list(RULES[op])} if op in RULES else {}))
            for op, (fields, description) in SPEC.items()]


def kinds():
    """What each field kind of SPEC accepts, as _value checks it (ops.json publishes it for the plugin)."""
    name = {"type": "string", "minLength": 1, "x-maxUtf8Bytes": limits.value("name_bytes"), "x-noNul": True}
    number = {"type": "number", "x-finite": True}
    frame = dict(number, minimum=-limits.value("max_frame"), maximum=limits.value("max_frame"))
    vec3 = {"type": "array", "items": number, "minItems": 3, "maxItems": 3}
    alias = {"type": "string", "pattern": _alias_pattern().pattern}
    return {
        "name": name,
        "ref": dict(name, description="an object name, or $alias of an object created or copied earlier in the batch "
                                      "($ then an alias: %s)" % _alias_pattern().pattern),
        "alias": alias,
        "number": number,
        "positive": dict(number, exclusiveMinimum=0),
        "nonnegative": dict(number, minimum=0),
        "index": {"type": "integer", "minimum": 0},
        "text": {"type": "string", "minLength": 1, "x-noNul": True},
        "bool": {"type": "boolean"},
        "frame": frame,
        "whole_frame": dict(frame, type="integer", description="a whole number (2.0 is accepted as 2)"),
        "frames": {"type": "array", "items": frame, "minItems": 2, "maxItems": 2, "description": "[first, last], first <= last"},
        "vec3": vec3,
        "rgb": dict(vec3, items=dict(number, minimum=0), description="linear RGB"),
        "scale": {"oneOf": [number, vec3], "description": "one number for all three axes, or three"},
        "rotation": {"oneOf": [
            {"type": "object", "required": ["euler_deg"], "additionalProperties": False,
             "properties": {"euler_deg": vec3, "order": {"enum": list(ORDERS), "default": "XYZ"}}},
            {"type": "object", "required": ["quaternion"], "additionalProperties": False,
             "properties": {"quaternion": {"type": "array", "items": number, "minItems": 4, "maxItems": 4,
                                           "description": "w, x, y, z; not all 0"}}}]},
        "target": {"type": "object", "additionalProperties": False,
                   "description": "exactly one of point, object, direction; bone only with object; direction not 0",
                   "properties": {"point": vec3, "object": {"$ref": "#/kinds/ref"}, "bone": name, "direction": vec3}},
        "refs": {"type": "array", "items": {"$ref": "#/kinds/ref"}, "minItems": 1, "uniqueItems": True},
        "names": {"type": "array", "items": name, "minItems": 1, "uniqueItems": True},
        "data_path": {"type": "string", "minLength": 1, "maxLength": 1024, "x-noNul": True,
                      "description": "an RNA path relative to the owner (or the pose bone): constraints[\"Grab\"].influence"},
        "value": {"oneOf": [number, {"type": "boolean"}, {"type": "array", "items": number, "minItems": 1,
                                                           "maxItems": VALUE_ITEMS}]},
        "paths": {"type": "array", "items": {"enum": list(PATHS)}, "minItems": 1},
    }
