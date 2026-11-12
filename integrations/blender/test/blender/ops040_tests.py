"""The typed operations of 0.4.0, drawn from what agents wrote in Python in real missions, each as a test (run by
blender.test.ts in a private background Blender 5.3): they failed on 0.3.9 and pass on 0.4.0. Prints RESULT <json>.
Usage: blender -b --factory-startup --python ops040_tests.py -- <scratch folder> <host/ops.json>
"""

import importlib
import json
import math
import sys
import traceback

import addon_utils
import bpy

MODULE = "bl_ext.user_default.opencode_link"
addon_utils.enable(MODULE, default_set=True, handle_error=None)
addon = importlib.import_module(MODULE + ".addon")
wire = importlib.import_module(MODULE + ".wire")
session = addon.session()
core, app = session.core, session.app
session.stop()
OPS_JSON = sys.argv[sys.argv.index("--") + 2]

results = {"passed": [], "failed": [], "host_version": core.version}
TESTS = []
counter = [0]


def test(fn):
    TESTS.append(fn)
    return fn


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def near(a, b, tolerance=1e-4):
    a, b = list(a), list(b)
    return len(a) == len(b) and all(abs(x - y) <= tolerance for x, y in zip(a, b))


def empty():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    core.quarantined = False
    bpy.context.view_layer.update()
    addon.pump(0.05)


def refused(*ops):
    """The HostFailure message of a batch refused before it runs (None when it ran)."""
    try:
        edit(*ops)
    except wire.HostFailure as failure:
        return failure.message
    return None


def edit(*ops, **params):
    counter[0] += 1
    request = {"script_handle": app.script_handle(), "if_revision": app.revision(), "command_id": "t-%d" % counter[0],
               "operations": [{"op": op, "args": args} for op, args in ops]}
    request.update(params)
    return core.handle("apply", request)


def applied(*ops):
    out = edit(*ops)
    check(out["status"] == "applied", "applied: %s" % {k: out.get(k) for k in ("status", "message", "restoration", "restored")})
    return out


def failed_and_restored(*ops):
    out = edit(*ops)
    check(out["status"] == "failed" and out["restored"] is True and not core.quarantined,
          "failed and restored: %s" % {k: out.get(k) for k in ("status", "message", "restoration", "restored")})
    return out


# A runtime failure (Blender clamps 40000 fps to 32767): the operations before it must be undone.
FAIL = ("scene_settings", {"fps": 40000})


def bsdf(material):
    return next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")


def obj(name):
    return bpy.data.objects[name]


def mesh(name, **extra):
    return ("create_object", dict({"kind": "mesh", "primitive": "cube", "name": name, "size": 1}, **extra))


# -- set_material -------------------------------------------------------------------------------------------
@test
def set_material_creates_reuses_updates_and_assigns_a_principled_material():
    empty()
    out = applied(mesh("Sol", **{"as": "sol"}), mesh("Caisse"),
                  ("set_material", {"ref": "$sol", "material": "Mat_Sol", "color": [0.85, 0.75, 0.55], "roughness": 0.9}))
    material = bpy.data.materials["Mat_Sol"]
    check(obj("Sol").material_slots[0].material == material, "assigned to the only slot")
    check(near(bsdf(material).inputs["Base Color"].default_value, (0.85, 0.75, 0.55, 1)), "base color")
    check(abs(bsdf(material).inputs["Roughness"].default_value - 0.9) < 1e-6, "roughness")
    check(near(material.diffuse_color, (0.85, 0.75, 0.55, 1)), "viewport colour")
    check({"op": 2, "name": "Mat_Sol", "type": "MATERIAL"} in out["created"], "created: %s" % out["created"])
    check(any(c["field"] == "Roughness" and c["after"] == 0.9 for c in out["changes"]), "before/after listed: %s" % out["changes"])
    applied(("set_material", {"ref": "Caisse", "material": "Mat_Sol"}),
            ("set_material", {"ref": "Sol", "material": "Mat_Sol", "metallic": 1}))
    check(len(bpy.data.materials) == 1 and obj("Caisse").material_slots[0].material == material, "reused, no Mat_Sol.001")
    check(bsdf(material).inputs["Metallic"].default_value == 1, "an existing material's value set")
    applied(("set_material", {"ref": "Sol", "material": "Mat_Bois", "slot": 1}))
    check([s.material.name for s in obj("Sol").material_slots] == ["Mat_Sol", "Mat_Bois"], "slot = count adds one")
    message = refused(("set_material", {"ref": "Sol", "material": "Mat_Sol"}))
    check(message and "give slot" in message, "several slots need slot: %s" % message)
    out = edit(("set_material", {"ref": "Caisse", "material": "M", "slot": 3}))
    check(out["status"] == "failed" and out["restored"] and "slot is 0 to 1" in out["message"], "slot past the end: %s" % out.get("message"))
    bpy.data.objects.new("Vide", None)
    bpy.context.scene.collection.objects.link(bpy.data.objects["Vide"])
    message = refused(("set_material", {"ref": "Vide", "material": "M"}))
    check(message and "no material slots" in message, "an empty: %s" % message)
    # Undone with the batch: the new material removed, the slot back, the shared values back.
    failed_and_restored(("set_material", {"ref": "Caisse", "material": "Mat_New", "color": [1, 0, 0]}),
                        ("set_material", {"ref": "Sol", "material": "Mat_Sol", "slot": 0, "roughness": 0.1}), FAIL)
    check("Mat_New" not in bpy.data.materials and obj("Caisse").material_slots[0].material.name == "Mat_Sol", "slot restored")
    check(abs(bsdf(bpy.data.materials["Mat_Sol"]).inputs["Roughness"].default_value - 0.9) < 1e-6, "value restored")
    # Shared data: the other objects show it too, and it is said.
    shared = bpy.data.objects.new("Jumeau", obj("Caisse").data)
    bpy.context.scene.collection.objects.link(shared)
    out = applied(("set_material", {"ref": "Caisse", "material": "Mat_Bois"}))
    check(any("shared by 2 objects" in w for w in out.get("warnings", [])), "shared data said: %s" % out.get("warnings"))


# -- duplicate --------------------------------------------------------------------------------------------------
def lamp():
    applied(("create_collection", {"name": "Rue"}),
            ("create_object", {"kind": "empty", "name": "Lampadaire", "as": "root", "location": [-6, -4, 0], "collection": "Rue"}),
            ("create_object", {"kind": "mesh", "primitive": "cylinder", "name": "Poteau", "as": "pot", "size": 1,
                               "location": [-6, -4, 1.5], "collection": "Rue", "parent": "$root"}),
            ("set_transform", {"ref": "$pot", "scale": [0.2, 0.2, 3]}),
            ("create_object", {"kind": "mesh", "primitive": "uv_sphere", "name": "Tete", "as": "tete", "size": 0.4,
                               "location": [-6, -4, 3.2], "collection": "Rue"}),
            ("set_parent", {"ref": "$tete", "parent": "$pot"}),
            ("set_material", {"ref": "$pot", "material": "Mat_Metal", "metallic": 1}))


def world(name):
    bpy.context.view_layer.update()
    return list(obj(name).matrix_world.translation)


@test
def duplicate_copies_a_hierarchy_with_names_offset_alias_and_shared_or_own_data():
    empty()
    lamp()
    out = applied(("duplicate", {"ref": "Lampadaire", "as": "l2", "name": "Lampadaire_2", "suffix": "_2", "offset": [4, 0, 0],
                                 "linked": True}),
                  ("set_transform", {"ref": "$l2", "location": [0, 1, 0], "delta": True}))
    check(obj("Tete_2").parent == obj("Poteau_2") and obj("Poteau_2").parent == obj("Lampadaire_2"), "the hierarchy copied")
    check(obj("Lampadaire_2").parent is None, "the root keeps the original's parent (none)")
    check(near(world("Tete_2"), [w + d for w, d in zip(world("Tete"), (4, 1, 0))]), "moved with its root: %s" % world("Tete_2"))
    check(near(obj("Poteau_2").matrix_world.to_scale(), obj("Poteau").matrix_world.to_scale()), "same world scale")
    check(obj("Poteau_2").data == obj("Poteau").data, "linked: the data shared")
    check([g.name for g in obj("Tete_2").users_collection] == ["Rue"], "in the original's collections")
    check([c["name"] for c in out["created"]] == ["Lampadaire_2", "Poteau_2", "Tete_2"] and out["created"][0]["as"] == "l2",
          "created listed: %s" % out["created"])
    applied(("duplicate", {"ref": "Lampadaire", "suffix": "_3", "offset": [8, 0, 0]}))
    check(obj("Poteau_3").data != obj("Poteau").data and obj("Poteau_3").data.materials[0].name == "Mat_Metal",
          "own data, the material shared")
    applied(("duplicate", {"ref": "Poteau", "name": "Seul", "children": False}))
    check(not obj("Seul").children and obj("Seul").parent == obj("Lampadaire"), "children false: alone, same parent")
    message = refused(("duplicate", {"ref": "Lampadaire", "suffix": "_2"}))
    check(message and "'Lampadaire_2' exists" in message, "a taken name refused before anything runs: %s" % message)
    message = refused(("duplicate", {"ref": "Lampadaire", "suffix": "_4"}), ("set_transform", {"ref": "Poteau_4", "location": [0, 0, 0]}))
    check(message and "from the next apply" in message, "a copied child is named from the next apply: %s" % message)
    count = len(bpy.data.objects), len(bpy.data.meshes)
    failed_and_restored(("duplicate", {"ref": "Lampadaire", "suffix": "_9"}), FAIL)
    check((len(bpy.data.objects), len(bpy.data.meshes)) == count, "the copies undone with their data")


# -- create_object text ------------------------------------------------------------------------------------------
@test
def create_object_text_with_body_size_extrude_and_alignment():
    empty()
    out = applied(("create_object", {"kind": "text", "name": "Titre", "as": "t", "body": "HIGH NOON", "size": 1.5,
                                     "extrude": 0.15, "align_x": "CENTER", "align_y": "BOTTOM", "location": [0, 6, 0],
                                     "rotation": {"euler_deg": [90, 0, 0]}}),
                  ("set_material", {"ref": "$t", "material": "Mat_Metal"}))
    title = obj("Titre")
    check(title.type == "FONT" and title.data.body == "HIGH NOON", "a text: %s" % out["created"])
    check((title.data.size, round(title.data.extrude, 6), title.data.align_x, title.data.align_y) == (1.5, 0.15, "CENTER", "BOTTOM"),
          "its settings")
    check(title.material_slots[0].material.name == "Mat_Metal", "a material on a text")
    bpy.context.view_layer.update()
    check(title.dimensions.x > 0 and abs(title.dimensions.z - 0.3) < 1e-3, "extruded both ways: %s" % list(title.dimensions))
    message = refused(("create_object", {"kind": "text", "name": "X"}))
    check(message and "missing body" in message, "a text needs body: %s" % message)
    message = refused(("create_object", {"kind": "text", "name": "X", "body": "x", "primitive": "cube"}))
    check(message and "does not apply to a text" in message, "mesh fields refused: %s" % message)
    message = refused(("create_object", {"kind": "mesh", "name": "X", "body": "x"}))
    check(message and "does not apply to a mesh" in message, "text fields refused on a mesh: %s" % message)


# -- scene_settings -------------------------------------------------------------------------------------------
@test
def scene_settings_sets_the_range_current_frame_and_fps_never_pushing_an_end():
    empty()
    applied(("create_object", {"kind": "empty", "name": "K", "as": "k"}),
            ("set_transform", {"ref": "$k", "location": [0, 0, 0]}), ("insert_key", {"ref": "$k", "paths": ["location"], "frame": 1}),
            ("set_transform", {"ref": "$k", "location": [10, 0, 0]}), ("insert_key", {"ref": "$k", "paths": ["location"], "frame": 11}))
    scene = bpy.context.scene
    out = applied(("scene_settings", {"frame_start": 1, "frame_end": 60, "fps": 24, "frame_current": 6}))
    check((scene.frame_start, scene.frame_end, scene.render.fps, scene.render.fps_base, scene.frame_current) == (1, 60, 24, 1.0, 6),
          "set: %s" % out["changes"])
    check(0 < obj("K").location.x < 10, "the animation evaluated at the current frame: %s" % obj("K").location.x)
    applied(("scene_settings", {"fps": 23.976}))
    check(scene.render.fps == 24 and abs(scene.render.fps / scene.render.fps_base - 23.976) < 1e-4, "fractional fps")
    applied(("scene_settings", {"frame_start": 300, "frame_end": 400}))
    check((scene.frame_start, scene.frame_end) == (300, 400), "a range past the old end, never pushed")
    out = edit(("scene_settings", {"frame_end": 100}))
    check(out["status"] == "failed" and out["restored"] and "give both" in out["message"] and (scene.frame_start, scene.frame_end) == (300, 400),
          "one end crossing the other is refused, nothing moved: %s" % out.get("message"))
    message = refused(("scene_settings", {"frame_start": 10, "frame_end": 5}))
    check(message and "after frame_end" in message, "start after end: %s" % message)
    message = refused(("scene_settings", {"frame_start": 1.5}))
    check(message and "whole frame" in message, "a whole frame: %s" % message)
    failed_and_restored(("scene_settings", {"frame_start": 1, "frame_end": 48, "frame_current": 20}), FAIL)
    check((scene.frame_start, scene.frame_end, scene.frame_current) == (300, 400, 6), "restored")


# -- shift_keys and a linear full turn ---------------------------------------------------------------------------------
def keys(name, path="rotation_euler", index=2):
    from bpy_extras import anim_utils

    data = obj(name).animation_data
    bag = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot)
    curve = next(c for c in bag.fcurves if c.data_path == path and c.array_index == index)
    return [tuple(round(v, 4) for v in p.co) for p in curve.keyframe_points], [round(p.handle_left[0], 4) for p in curve.keyframe_points]


def z_at(name, frame):
    bpy.context.scene.frame_set(int(frame), subframe=frame - int(frame))
    return math.degrees(obj(name).rotation_euler.z)


@test
def shift_keys_moves_or_deletes_keys_of_layered_actions_and_a_linear_full_turn():
    empty()
    applied(mesh("Enseigne", **{"as": "e"}),
            ("set_transform", {"ref": "$e", "rotation": {"euler_deg": [0, 0, 0]}}),
            ("insert_key", {"ref": "$e", "paths": ["rotation", "location"], "frame": 1, "interpolation": "LINEAR"}),
            ("set_transform", {"ref": "$e", "rotation": {"euler_deg": [0, 0, 360]}}),
            ("insert_key", {"ref": "$e", "paths": ["rotation"], "frame": 48, "interpolation": "LINEAR"}))
    check(abs(z_at("Enseigne", 24.5) - 180) < 1e-3, "a linear full turn: half way at frame 24.5 (%s)" % z_at("Enseigne", 24.5))
    action = obj("Enseigne").animation_data.action
    check(len(action.layers) == 1 and len(action.slots) == 1, "a layered action (Blender 5): %s layers" % len(action.layers))
    _, handles = keys("Enseigne")
    out = applied(("shift_keys", {"ref": "Enseigne", "offset": 12}))
    points, moved = keys("Enseigne")
    check([p[0] for p in points] == [13, 60] and near(moved, [h + 12 for h in handles]), "keys and handles moved: %s" % points)
    check(abs(z_at("Enseigne", 36.5) - 180) < 1e-3, "still linear, half way at 36.5")
    change = next(c for c in out["changes"] if c["field"] == "keys")
    check(change["moved"] == 9 and change["frames"] == [1, 48] and change["to"] == [13, 60], "reported: %s" % change)
    applied(("shift_keys", {"ref": "Enseigne", "paths": ["rotation"], "frames": [60, 60], "delete": True}))
    check([p[0] for p in keys("Enseigne")[0]] == [13] and [p[0] for p in keys("Enseigne", "location", 0)[0]] == [13],
          "only the rotation key at 60 deleted")
    applied(("set_transform", {"ref": "Enseigne", "rotation": {"euler_deg": [0, 0, 90]}}),
            ("insert_key", {"ref": "Enseigne", "paths": ["rotation"], "frame": 20}),
            ("insert_key", {"ref": "Enseigne", "paths": ["rotation"], "frame": 30}))
    out = edit(("shift_keys", {"ref": "Enseigne", "paths": ["rotation"], "frames": [13, 20], "offset": 10}))
    check(out["status"] == "failed" and out["restored"] and "would land on its key at frame 30" in out["message"],
          "a collision refused: %s" % out.get("message"))
    before = keys("Enseigne")
    failed_and_restored(("shift_keys", {"ref": "Enseigne", "offset": -5}), ("shift_keys", {"ref": "Enseigne", "frames": [8, 8], "delete": True}), FAIL)
    check(keys("Enseigne") == before, "keys restored exactly")
    out = edit(("shift_keys", {"ref": "Enseigne", "frames": [100, 200], "offset": 1}))
    check(out["status"] == "failed" and "no keys in frames 100-200" in out["message"], "nothing in range: %s" % out.get("message"))
    message = refused(("shift_keys", {"ref": "Enseigne"}))
    check(message and "offset or delete" in message, "offset or delete: %s" % message)
    # A pose bone's keys, not the object's.
    armature = bpy.data.armatures.new("Rig")
    rig = bpy.data.objects.new("Rig", armature)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature.edit_bones.new("Bras")
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    applied(("insert_key", {"ref": "Rig", "paths": ["location"], "frame": 1}),
            ("insert_key", {"ref": "Rig", "bone": "Bras", "paths": ["rotation"], "frame": 1}),
            ("shift_keys", {"ref": "Rig", "bone": "Bras", "offset": 4}))
    check([p[0] for p in keys("Rig", 'pose.bones["Bras"].rotation_quaternion', 0)[0]] == [5] and
          [p[0] for p in keys("Rig", "location", 0)[0]] == [1], "the bone's keys moved, the object's not")
    message = refused(("shift_keys", {"ref": "Rig", "bone": "Jambe", "offset": 1}))
    check(message and "no bone 'Jambe'" in message, "an unknown bone: %s" % message)


# -- delete then create the same name ----------------------------------------------------------------------------------
@test
def delete_then_create_the_same_name_keeps_the_name():
    empty()
    # Operations 0.3.9-m knows: there it fails on the names (.001), not on the setup.
    applied(("create_object", {"kind": "empty", "name": "Lampadaire", "as": "root"}),
            ("create_object", {"kind": "mesh", "primitive": "cylinder", "name": "Poteau", "as": "pot", "parent": "$root"}),
            ("create_object", {"kind": "mesh", "primitive": "uv_sphere", "name": "Tete", "parent": "$pot"}))
    out = applied(("delete", {"refs": ["Tete", "Poteau", "Lampadaire"]}),
                  ("create_object", {"kind": "empty", "name": "Lampadaire", "as": "root", "location": [-6, -4, 0]}),
                  ("create_object", {"kind": "mesh", "primitive": "cylinder", "name": "Poteau", "as": "pot", "parent": "$root"}),
                  ("create_object", {"kind": "mesh", "primitive": "uv_sphere", "name": "Tete", "parent": "$pot"}))
    check([c["name"] for c in out["created"]] == ["Lampadaire", "Poteau", "Tete"], "the same names: %s" % out["created"])
    check(sorted(o.name for o in bpy.data.objects) == ["Lampadaire", "Poteau", "Tete"], "the old ones gone")
    check(sorted(m.name for m in bpy.data.meshes) == ["Poteau", "Tete"], "their data's names too: %s" % [m.name for m in bpy.data.meshes])
    check(not out.get("warnings"), "no suffix warning: %s" % out.get("warnings"))
    message = refused(("delete", {"refs": ["Tete"]}), ("set_transform", {"ref": "Tete", "location": [0, 0, 1]}))
    check(message and "'Tete' is deleted by operations[0]" in message, "a deleted name: %s" % message)
    message = refused(("delete", {"refs": ["Tete"]}), ("create_object", {"kind": "empty", "name": "Tete", "as": "t"}),
                      ("set_transform", {"ref": "Tete", "location": [0, 0, 1]}))
    check(message and "refer to it as $t" in message, "made again: its alias: %s" % message)
    failed_and_restored(("delete", {"refs": ["Poteau"]}), ("create_object", {"kind": "empty", "name": "Poteau"}), FAIL)
    check(obj("Poteau").type == "MESH" and obj("Poteau").data.name == "Poteau" and obj("Tete").parent == obj("Poteau"),
          "the deleted object back with its names")
    out = applied(("create_object", {"kind": "empty", "name": "Tete"}))
    check(any("Tete.001" in w for w in out.get("warnings", [])), "a taken name is said: %s" % out.get("warnings"))


# -- a failure after editing what the batch created ----------------------------------------------------------------------
@test
def a_failure_after_editing_what_the_batch_created_is_restored_without_quarantine():
    empty()
    # The failure: fit_height refuses (while running) to stretch a tilted object along Z, in 0.3.9-m too.
    failed_and_restored(mesh("Neuf", **{"as": "n"}), ("set_transform", {"ref": "$n", "location": [1, 2, 3], "scale": 2,
                                                                         "rotation": {"euler_deg": [45, 0, 0]}}),
                        ("create_collection", {"name": "Lot"}), ("link", {"ref": "$n", "collection": "Lot"}),
                        ("fit_height", {"ref": "$n", "height": 4}))
    check("Neuf" not in bpy.data.objects and "Lot" not in bpy.data.collections, "nothing left")


# -- primitive sizes, documented ---------------------------------------------------------------------------------------
@test
def primitive_size_is_the_edge_or_diameter_and_the_catalog_says_so():
    empty()
    applied(*[("create_object", {"kind": "mesh", "primitive": p, "name": p, "size": 2}) for p in ("cube", "plane", "uv_sphere", "cylinder")])
    bpy.context.view_layer.update()
    dims = {p: [round(v, 4) for v in obj(p).dimensions] for p in ("cube", "plane", "uv_sphere", "cylinder")}
    check(dims == {"cube": [2, 2, 2], "plane": [2, 2, 0], "uv_sphere": [2, 2, 2], "cylinder": [2, 2, 2]}, "sizes: %s" % dims)
    plan = importlib.import_module(MODULE + ".thin_plan")
    description = next(e for e in plan.catalog() if e["name"] == "create_object")["description"]
    check("cube's edge" in description and "diameter and height" in description, "documented: %s" % description)


# -- the published table --------------------------------------------------------------------------------------------
@test
def ops_json_publishes_the_fields_the_add_on_validates():
    plan = importlib.import_module(MODULE + ".thin_plan")
    published = json.load(open(OPS_JSON, encoding="utf-8"))
    check(published["version"] == core.version, "for this version: %s" % published["version"])
    check(set(published["ops"]) == set(plan.SPEC), "the same operations: %s" % sorted(set(published["ops"]) ^ set(plan.SPEC)))
    for op, (fields, _description) in plan.SPEC.items():
        check(set(published["ops"][op]["args"]) == set(fields), "%s: the same fields" % op)
        for key, spec in fields.items():
            entry = published["ops"][op]["args"][key]
            kind = {"enum": list(spec["kind"]["enum"])} if isinstance(spec["kind"], dict) else {"kind": spec["kind"]}
            check(all(entry.get(k) == v for k, v in kind.items()) and bool(entry.get("required")) == spec["required"],
                  "%s.%s: %s" % (op, key, entry))


for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1200:]})
print("RESULT " + json.dumps(results), flush=True)
