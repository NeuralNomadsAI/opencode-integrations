"""The 0.4.4 exercises' findings (QA journal, Luna section: ex06, ex09): the agent checked the open document and
concluded while the saved file lacked its last changes. Results now say when the file lacks changes, and what a
save wrote. Each test fails on the 0.4.4 add-on and passes on 0.4.5, in a private background Blender 5.3 (run by
blender.test.ts). Prints RESULT <json>.
Usage: blender -b --factory-startup --python ops045_tests.py -- <scratch folder>
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
session = addon.session()
core, app = session.core, session.app
session.stop()
SCRATCH = sys.argv[sys.argv.index("--") + 1]


def relay(handler):
    """The add-on's own handler, its session stopped here (Blender passes a save the path written, a copy's too)."""

    @bpy.app.handlers.persistent
    def run(*args):
        addon._session = session
        try:
            handler(*args)
        finally:
            addon._session = None

    return run


bpy.app.handlers.save_post.append(relay(addon._on_save))
bpy.app.handlers.load_post.append(relay(addon._on_load))

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


def scene():
    return core.handle("scene", {"script_handle": app.script_handle()})


@test
def u_a_never_saved_document_says_so_with_the_steps_made():
    empty()
    out = run_code("import bpy\nbpy.data.objects.new('Robot', None)")
    check(out["status"] == "executed", "runs: %s" % out)
    check(out.get("unsaved") == {"file": None, "never_saved": True, "opencode_steps": ["OpenCode: code"]},
          "the result says the file lacks it: %s" % out.get("unsaved"))
    check(scene().get("unsaved", {}).get("never_saved") is True, "the context too")


@test
def u09_after_a_save_a_later_change_is_named_until_the_next_save():
    empty()
    path = os.path.join(SCRATCH, "shot010.blend")
    run_code("import bpy\nbpy.data.objects.new('Robot', None)")
    saved = run_code("import bpy\nbpy.ops.wm.save_as_mainfile(filepath=%r)" % path)
    check(saved.get("saved") is True and saved.get("saved_steps") == ["OpenCode: code"], "the save wrote: %s" % saved)
    check("unsaved" not in saved and "unsaved" not in scene(), "nothing unsaved after it: %s" % saved.get("unsaved"))
    # Luna 09: the pose changed after the save.
    posed = run_code("import bpy\nbpy.data.objects['Robot'].rotation_euler.z = 1.2")
    check(posed.get("unsaved") == {"file": "shot010.blend", "opencode_steps": ["OpenCode: code"]},
          "the file lacks the pose: %s" % posed.get("unsaved"))
    check(scene().get("unsaved", {}).get("file") == "shot010.blend", "the context says it")
    read = run_code("import bpy\nresult = {'z': bpy.data.objects['Robot'].rotation_euler.z}", read_only=True)
    check(read.get("unsaved", {}).get("opencode_steps") == ["OpenCode: code"], "a read says it too")


@test
def u_a_copy_leaves_the_open_file_unsaved():
    empty()
    path = os.path.join(SCRATCH, "shot.blend")
    run_code("import bpy\nbpy.ops.wm.save_as_mainfile(filepath=%r)" % path)
    run_code("import bpy\nbpy.data.objects.new('Lamp', None)")
    copy = run_code("import bpy\nbpy.ops.wm.save_as_mainfile(filepath=%r, copy=True)" % os.path.join(SCRATCH, "copy.blend"),
                    read_only=True)
    check(copy["status"] == "executed" and "saved" not in copy, "a copy is not a save of the open file: %s" % copy)
    check(copy.get("unsaved", {}).get("file") == "shot.blend", "the open file still lacks the lamp: %s" % copy.get("unsaved"))


@test
def u_a_file_opened_by_code_is_as_saved_and_gets_no_step():
    empty()
    path = os.path.join(SCRATCH, "opened.blend")
    run_code("import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new('Crate', None))\n"
             "bpy.ops.wm.save_as_mainfile(filepath=%r)" % path)
    run_code("import bpy\nbpy.data.objects.new('Other', None)")
    opened = run_code("import bpy\nbpy.ops.wm.open_mainfile(filepath=%r)" % path)
    check(opened["status"] == "executed" and "undo_step" not in opened, "opened, no step pushed into it: %s" % opened)
    check("unsaved" not in opened and "unsaved" not in scene(), "the file as saved: %s" % opened.get("unsaved"))
    check("Crate" in bpy.data.objects and "Other" not in bpy.data.objects, "the saved content")


# -- the 0.4.5 trial (ex06 #16, ex09 #9-#16): saves inside a code run, failed runs, a reopened bake -----------------
@test
def t_a_failed_run_restored_is_not_a_step_of_the_file():
    empty()
    run_code("import bpy\nbpy.ops.wm.save_as_mainfile(filepath=%r)" % os.path.join(SCRATCH, "failed.blend"))
    out = run_code("import bpy\nbpy.data.objects.new('Stray', None)\nraise RuntimeError('like the trial')")
    check(out["status"] == "failed" and out.get("restored") is True, "failed and restored: %s" % out)
    steps = (out.get("unsaved") or {}).get("opencode_steps", [])
    check(not any("failed" in s for s in steps), "the restoration is no step of the file: %s" % steps)


@test
def t06_a_run_that_changes_then_saves_leaves_nothing_unsaved():
    empty()
    run_code("import bpy\nbpy.ops.wm.save_as_mainfile(filepath=%r)" % os.path.join(SCRATCH, "ex06.blend"))
    run_code("import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new('Ground', None))")
    out = run_code("import bpy\ns = bpy.context.scene\ns.collection.objects.link(bpy.data.objects.new('Brick', None))\n"
                   "s.frame_end = 120\ns.frame_set(120)\nbpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)")
    check(out["status"] == "executed" and out.get("saved") is True, "saved: %s" % out)
    check("unsaved" not in out, "nothing unsaved right after the save: %s" % out.get("unsaved"))
    check(out.get("saved_steps") == ["OpenCode: code", "OpenCode: code (this run)"], "what it wrote: %s" % out.get("saved_steps"))
    again = run_code("import bpy\nbpy.ops.wm.save_mainfile()")
    check(again.get("saved_steps") == [], "a save with nothing pending says so: %s" % again.get("saved_steps"))
    later = run_code("import bpy\nbpy.ops.wm.save_mainfile()\nbpy.data.objects['Brick'].location.x = 3")
    check(later.get("unsaved", {}).get("file") == "ex06.blend", "a change after the save stays unsaved: %s" % later.get("unsaved"))


@test
def t06_a_reopened_rigid_body_bake_is_explained():
    empty()
    path = os.path.join(SCRATCH, "baked.blend")
    built = run_code(
        "import bpy\nbpy.ops.mesh.primitive_cube_add(location=(0, 0, 2))\nbpy.ops.rigidbody.object_add()\n"
        "s = bpy.context.scene\npc = s.rigidbody_world.point_cache\npc.frame_end = 30\n"
        "with bpy.context.temp_override(scene=s, point_cache=pc):\n    bpy.ops.ptcache.bake(bake=True)\n"
        "bpy.ops.wm.save_as_mainfile(filepath=%r)\nbpy.ops.wm.open_mainfile(filepath=%r)" % (path, path))
    check(built["status"] == "executed", "built, baked, saved, reopened: %s" % built.get("message"))
    program = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "programs", "verify.py")
    code = "import json\nPARAMS = json.loads(%r)\n" % json.dumps({"criteria": [{"expr": "frame", "op": "==", "value": 20}], "frame": 20})
    out = run_code(code + open(program, encoding="utf-8").read(), read_only=True)
    caches = out["result"].get("caches") or []
    check(caches and caches[0]["cache"] == "rigid body world" and caches[0].get("outdated") is True, "flagged: %s" % caches)
    check("when its file is opened" in caches[0].get("note", ""), "and explained: %s" % caches[0])


@test
def u_an_apply_result_says_it_too():
    empty()
    revision = scene()["revision"]
    out = core.handle("apply", {"script_handle": app.script_handle(), "if_revision": revision, "command_id": "u-1",
                                "summary": "lamp", "operations": [{"op": "create_object", "args": {"kind": "empty", "name": "Lamp"}}]})
    check(out.get("status") == "applied", "applied: %s" % json.dumps(out)[:400])
    steps = out.get("unsaved", {}).get("opencode_steps", [])
    check(len(steps) == 1 and steps[0].startswith("OpenCode: "), "its undo step named: %s" % out.get("unsaved"))


for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1500:]})
print("RESULT " + json.dumps(results), flush=True)
