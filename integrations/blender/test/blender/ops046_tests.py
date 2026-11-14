"""The 0.4.5 exercises' findings (QA journal, Luna section, ex06): an operator refused its context (ptcache.bake) and
the error said only "context is incorrect". The hint now says, read from this Blender by trying poll() only, which
operators of its group pass and which context member makes it pass. Each test fails on the 0.4.5 add-on and passes
on 0.4.6, in a private background Blender 5.3 (run by blender.test.ts). Prints RESULT <json>.
Usage: blender -b --factory-startup --python ops046_tests.py -- <scratch folder>
"""

import importlib
import json
import sys
import traceback

import addon_utils
import bpy

MODULE = "bl_ext.user_default.opencode_link"
addon_utils.enable(MODULE, default_set=True, handle_error=None)
addon = importlib.import_module(MODULE + ".addon")
session = addon.session()
core, app = session.core, session.app
session.stop()
SCRATCH = sys.argv[sys.argv.index("--") + 1]

results = {"passed": [], "failed": [], "host_version": core.version}
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def empty():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    core.quarantined = False
    core.quarantine = None
    bpy.context.view_layer.update()
    addon.pump(0.05)


def run_code(text, read_only=False):
    params = {"script_handle": app.script_handle(), "code": text}
    if read_only:
        params["read_only"] = True
    return core.handle("execute_code", params)


@test
def p06_a_refused_bake_says_which_context_member_makes_it_pass():
    empty()
    run_code("import bpy\nbpy.ops.mesh.primitive_plane_add()\nbpy.context.object.name = 'Cloth_Sheet'\n"
             "bpy.context.object.modifiers.new('Cloth', 'CLOTH')\nbpy.ops.mesh.primitive_cube_add(location=(0, 0, 3))\n"
             "bpy.ops.rigidbody.object_add()\nbpy.context.view_layer.objects.active = bpy.data.objects['Cloth_Sheet']")
    out = run_code("import bpy\nbpy.ops.ptcache.bake(bake=True)")
    check(out["status"] == "failed", "refused: %s" % out)
    hint = out.get("hint") or {}
    check(hint.get("operator") == "ptcache.bake", "the operator named: %s" % hint)
    check({"ptcache.bake_all", "ptcache.free_bake_all"} <= set(hint.get("same_group_passing", [])),
          "its group's passing operators: %s" % hint.get("same_group_passing"))
    overrides = [entry["temp_override"] for entry in hint.get("passes_with", [])]
    check(any(o.get("point_cache") == "bpy.context.active_object.modifiers['Cloth'].point_cache" for o in overrides),
          "the member that makes it pass, with its path: %s" % overrides)
    cache = bpy.data.objects["Cloth_Sheet"].modifiers["Cloth"].point_cache
    check(not cache.is_baked, "only poll() was tried: nothing baked")
    # The path given works as written.
    done = run_code("import bpy\nwith bpy.context.temp_override(point_cache=%s):\n    bpy.ops.ptcache.bake(bake=True)\n"
                    "result = {'baked': bpy.data.objects['Cloth_Sheet'].modifiers['Cloth'].point_cache.is_baked}"
                    % overrides[0]["point_cache"])
    check(done["status"] == "executed" and done["result"] == {"baked": True}, "the override runs it: %s" % done)


@test
def p_an_operator_needing_an_editor_gets_the_area_that_passes():
    empty()
    out = run_code("import bpy\nbpy.ops.view3d.view_all()")
    hint = out.get("hint") or {}
    check(hint.get("poll") == "Expected a view3d region", "Blender's own reason: %s" % hint.get("poll"))
    areas = [entry["temp_override"].get("area", "") for entry in hint.get("passes_with", [])]
    check(any("VIEW_3D" in area for area in areas), "the area that passes: %s" % hint.get("passes_with"))


@test
def p_nothing_found_keeps_the_generic_advice():
    empty()
    out = run_code("import bpy\nbpy.ops.sculpt.brush_stroke(stroke=[])")
    hint = out.get("hint") or {}
    check(hint.get("operator") == "sculpt.brush_stroke" and "passes_with" not in hint, "nothing passes here: %s" % hint)
    check("temp_override" in hint.get("give_context", ""), "the generic advice stays: %s" % hint)


for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1500:]})
print("RESULT " + json.dumps(results), flush=True)
