"""The blockers of the 0.4.2 exercises (QA journal, Luna section), each replayed as a test in a private background
Blender 5.3 (run by blender.test.ts): they fail on the 0.4.2 add-on and pass on 0.4.3. Prints RESULT <json>.
Usage: blender -b --factory-startup --python ops043_tests.py -- <scratch folder>
"""

import importlib
import json
import os
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


def failure(method, params):
    try:
        core.handle(method, dict(params, script_handle=app.script_handle()))
    except wire.HostFailure as error:
        return error.code, error.message
    return None, None


def library():
    """A .blend holding a collection 'Characters' with one object (the exercise's character library)."""
    path = os.path.join(SCRATCH, "characters.blend")
    if not os.path.exists(path):
        group = bpy.data.collections.new("Characters")
        group.objects.link(bpy.data.objects.new("Robot", bpy.data.meshes.new("Robot")))
        bpy.data.libraries.write(path, {group}, fake_user=True)
        bpy.data.collections.remove(group)
    return path


# -- quarantine (Luna 02, 09): a failed script whose undo leaves only linked data is restored -----------------------
@test
def q09_a_failed_script_that_linked_a_library_is_restored_without_quarantine():
    path = library()
    empty()
    local = bpy.data.objects.new("Floor", None)
    bpy.context.scene.collection.objects.link(local)
    app.undo_push("setup")
    out = run_code(
        "import bpy\n"
        "with bpy.data.libraries.load(%r, link=True) as (src, dst):\n"
        "    dst.collections = ['Characters']\n"
        "bpy.context.scene.collection.children.link(dst.collections[0])\n"
        "bpy.data.objects['Floor'].location.x = 3\n"
        "dst.collections[0].name = 'Hero'\n" % path)
    check(out["status"] == "failed" and "read-only" in out["message"], "the rename fails: %s" % out.get("message"))
    check(out["restored"] is True and not core.quarantined, "restored, not quarantined: %s" % out.get("restoration"))
    check(bpy.data.objects["Floor"].location.x == 0, "the local change undone")
    check(not bpy.context.scene.collection.children, "the linked collection unlinked")
    kept = out.get("restoration", {}).get("kept_linked", [])
    check(any("Characters" in item for item in kept), "the linked data Blender keeps is named: %s" % kept)


@test
def q02_a_failed_script_that_entered_sculpt_mode_is_restored_without_quarantine():
    empty()
    app.undo_push("setup")
    out = run_code("import bpy\nbpy.ops.mesh.primitive_uv_sphere_add()\nbpy.ops.object.mode_set(mode='SCULPT')\n"
                   "x = mathutils.Vector((0, 0, 1))")
    check(out["status"] == "failed" and "NameError" in out["message"], "fails: %s" % out.get("message"))
    check(out["restored"] is True and not core.quarantined, "restored: %s" % out.get("restoration"))
    check(not bpy.data.objects and bpy.context.mode == "OBJECT", "the sphere gone, Object mode")


@test
def q_an_unproven_restoration_says_what_may_have_changed_and_the_agent_releases_it():
    empty()
    app.undo_push("setup")
    real_undo = app.undo
    app.undo = lambda: None  # an undo that does not return to the barrier
    try:
        out = run_code("import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new('Stray', None))\n"
                       "raise RuntimeError('late')")
    finally:
        app.undo = real_undo
    check(out["restored"] is False and core.quarantined, "quarantined")
    changed = out.get("restoration", {}).get("may_have_changed", [])
    check(any("Stray" in item for item in changed), "what may have changed is said: %s" % changed)
    scene = core.handle("scene", {"script_handle": app.script_handle()})
    check(scene["quarantined"] and any("Stray" in item for item in scene["quarantine"]["may_have_changed"]),
          "the read shows it: %s" % scene.get("quarantine"))
    read = run_code("import bpy\nresult = {'stray': 'Stray' in bpy.data.objects}", read_only=True)
    check(read["status"] == "executed" and read["result"] == {"stray": True}, "a read_only run still inspects")
    check(failure("execute_code", {"code": "x = 1"})[0] == "quarantined", "a change waits")
    check(failure("release", {"revision": scene["revision"] - 1})[0] == "stale_revision", "an old revision is refused")
    released = core.handle("release", {"script_handle": app.script_handle(), "revision": scene["revision"]})
    check(released["released"] is True and not core.quarantined, "released: %s" % released)
    check(run_code("x = 1")["status"] == "executed", "changes run again")
    check("release" in core.capabilities()["methods"], "hello lists release")


for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1500:]})
print("RESULT " + json.dumps(results), flush=True)
