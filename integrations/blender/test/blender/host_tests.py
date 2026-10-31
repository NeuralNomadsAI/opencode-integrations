"""Host tests of OpenCode Link inside a background Blender 5.2 (run by blender.test.ts).

Covers the background part of the acceptance criteria: the authenticated loopback listener,
apply requirements and replay, Object mode only, one undo step per apply with rollback model B,
quarantine, expectations, revisions, every read and operation of the catalog, captures as image
data, the code-execution gate and the consent switch. Prints one line: RESULT <json>.
Usage: blender -b --factory-startup --python host_tests.py -- <scratch folder>
"""

import importlib
import json
import os
import socket
import sys
import threading
import traceback

import addon_utils
import bpy

MODULE = "bl_ext.user_default.opencode_link"
SCRATCH = sys.argv[sys.argv.index("--") + 1]

addon_utils.enable(MODULE, default_set=True, handle_error=None)
addon = importlib.import_module(MODULE + ".addon")
wire = importlib.import_module(MODULE + ".wire")
core_module = importlib.import_module(MODULE + ".core")
session = addon.session()
core = session.core
app = session.app
prefs = addon.preferences()

results = {"passed": [], "failed": [], "coverage": {}}
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Failure(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise Failure(message)


def code_of(fn):
    try:
        fn()
    except wire.HostFailure as failure:
        return failure.code
    return None


def state():
    return {"script_handle": app.script_handle(), "if_revision": app.revision()}


counter = [0]


def apply(operations, **extra):
    counter[0] += 1
    params = {"command_id": "cmd-%d" % counter[0], "operations": operations}
    params.update(state())
    params.update(extra)
    return core._apply(params)


def op(name, /, **args):
    return {"op": name, "args": args}


def objects():
    return sorted(obj.name for obj in bpy.data.objects)


def undo():
    return bpy.ops.ed.undo()


def user_step(name):
    """A change of the user's own, with its own undo step."""
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    bpy.ops.ed.undo_push(message="User: %s" % name)


def request(port, payload, timeout=5.0):
    """One raw request from a client thread while the main thread serves (pump)."""
    box = {}

    def run():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
                connection.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                data = b""
                while not data.endswith(b"\n"):
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                box["reply"] = json.loads(data) if data else None
        except OSError as error:
            box["error"] = repr(error)

    thread = threading.Thread(target=run)
    thread.start()
    while thread.is_alive():
        addon.pump(0.05)
    return box


# -- hello and the listener --------------------------------------------------------------------
@test
def hello_is_valid_and_code_execution_absent_by_default():
    hello = core._hello({})
    check(hello["protocol"] == 1, "protocol 1")
    check(hello["host_version"] == core_module.manifest_version(os.path.dirname(core_module.__file__)), "version from manifest")
    check(hello["capability_hash"] == core_module.capability_hash(hello["capabilities"]), "capability hash")
    check(hello["capabilities"]["undo"] == "grouped_best_effort", "undo level")
    check(hello["capabilities"]["background"] is True, "background flag")
    check(hello["app_version"] == bpy.app.version_string, "app version")
    check("execute_code" not in hello["capabilities"]["methods"], "execute_code absent by default")
    check("execute_code" not in core._hello({"code_execution": True})["capabilities"]["methods"], "absent without the preference")
    prefs.allow_code_execution = True
    try:
        check("execute_code" in core._hello({"code_execution": True})["capabilities"]["methods"], "present with both")
        check("execute_code" not in core._hello({})["capabilities"]["methods"], "absent without the plugin option")
    finally:
        prefs.allow_code_execution = False


@test
def listener_is_loopback_ephemeral_and_authenticated():
    listener = wire.Listener(core.handle)
    try:
        address = listener._socket.getsockname()
        check(address[0] == "127.0.0.1", "bound to 127.0.0.1")
        check(listener.port not in (0, 9876), "ephemeral port")
        check(len(listener.token) == 64 and all(c in "0123456789abcdef" for c in listener.token), "256-bit hex token")
        base = {"id": "t", "method": "hello", "params": {}}
        for token in (None, "", "0" * 64, listener.token[:-1] + ("0" if listener.token[-1] != "0" else "1")):
            payload = dict(base)
            if token is not None:
                payload["token"] = token
            reply = request(listener.port, payload).get("reply")
            check(reply and reply["ok"] is False and reply["error"]["code"] == "unauthorized", "refused without the token")
        reply = request(listener.port, dict(base, token=listener.token))["reply"]
        check(reply["ok"] is True and reply["id"] == "t" and reply["result"]["protocol"] == 1, "hello with the token")
        check("token" not in json.dumps(reply["result"]) or listener.token not in json.dumps(reply), "no token in hello")
    finally:
        listener.close()
    box = request(listener.port, {"id": "t", "token": "x", "method": "hello"}, timeout=1.0)
    check("reply" not in box or box["reply"] is None, "closed listener answers nothing")


# -- apply requirements --------------------------------------------------------------------------
@test
def apply_requires_handle_revision_and_command_id():
    create = [op("scene.create_object", primitive="EMPTY", name="REQ")]
    check(code_of(lambda: core._apply({"if_revision": app.revision(), "command_id": "a", "operations": create})) == "stale_handle", "script_handle required")
    check(code_of(lambda: core._apply(dict(state(), script_handle="other", command_id="a", operations=create))) == "stale_handle", "exact handle")
    check(code_of(lambda: core._apply({"script_handle": app.script_handle(), "command_id": "a", "operations": create})) == "invalid_args", "if_revision required")
    check(code_of(lambda: core._apply(dict(state(), if_revision=app.revision() + 7, command_id="a", operations=create))) == "stale_revision", "exact revision")
    check(code_of(lambda: core._apply(dict(state(), operations=create))) == "invalid_args", "command_id required")
    many = [op("scene.create_object", primitive="EMPTY", name="M%d" % i) for i in range(51)]
    check(code_of(lambda: apply(many)) == "invalid_args", "more than 50 operations refused")
    check(code_of(lambda: apply([op("scene.create_object", primitive="EMPTY"), op("shot_manager.launch_batch_render")])) == "invalid_args", "side effect alone")
    check(code_of(lambda: apply([op("scene.create_object", primitive="EMPTY", colour="red")])) == "invalid_args", "unknown argument refused")
    check(code_of(lambda: apply([op("scene.nope")])) == "invalid_args", "unknown operation refused")
    check("REQ" not in objects(), "nothing applied by refused calls")


@test
def replaying_a_command_id_applies_nothing():
    params = dict(state(), command_id="replay-1", operations=[op("scene.create_object", primitive="EMPTY", name="Replay")])
    first = core._apply(params)
    check(first["status"] == "applied", "first apply")
    before = objects()
    second = core._apply(dict(params, if_revision=app.revision()))
    check(second.get("replayed") is True and objects() == before, "replay applies nothing")
    stale = core._apply(params)
    check(stale.get("replayed") is True, "replay answers even with the old revision")


@test
def mutations_outside_object_mode_conflict():
    mesh_object = bpy.data.objects.new("EditMe", bpy.data.meshes.new("EditMe"))
    bpy.context.scene.collection.objects.link(mesh_object)
    bpy.context.view_layer.objects.active = mesh_object
    mesh_object.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        check(app.mode() != "OBJECT", "in edit mode")
        check(code_of(lambda: apply([op("scene.create_object", primitive="EMPTY", name="Edit")])) == "mode_conflict", "mode_conflict")
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


# -- undo and rollback ---------------------------------------------------------------------------
@test
def one_apply_is_one_named_undo_step():
    user_step("UserBefore")
    result = apply([op("scene.create_object", primitive="CUBE", name="U%d" % i) for i in range(3)], summary="three cubes")
    check(result["status"] == "applied" and result["undo_step"] == "OpenCode: three cubes", "named step")
    check(all("U%d" % i in objects() for i in range(3)), "created")
    undo()
    check(not any("U%d" % i in objects() for i in range(3)), "one undo removes the whole apply")
    check("UserBefore" in objects(), "the user's step stays")
    bpy.ops.ed.redo()
    check(all("U%d" % i in objects() for i in range(3)), "one redo restores it")


@test
def a_failed_apply_rolls_back_to_the_barrier_only():
    user_step("UserKeep")
    before = objects()
    result = apply([op("scene.create_object", primitive="CUBE", name="Partial"), op("scene.delete_object", name="Missing")])
    check(result["status"] == "failed" and result["failed_operation"] == 1, "failure reported")
    check(result["restored"] is True and not core.quarantined, "restored without quarantine")
    check(objects() == before and "UserKeep" in objects(), "model B: back to the barrier, the user's step kept")


@test
def a_failed_restoration_quarantines_the_host():
    original = app.undo
    app.undo = lambda: (_ for _ in ()).throw(RuntimeError("no window"))
    try:
        result = apply([op("scene.create_object", primitive="EMPTY", name="Q1"), op("scene.delete_object", name="Missing")])
        check(result["status"] == "failed" and result["restored"] is False, "restoration failed")
        check(core.quarantined, "quarantined")
        check(code_of(lambda: apply([op("scene.create_object", primitive="EMPTY", name="Q2")])) == "quarantined", "mutations refused")
        check(core._context(dict(state(), reads=[{"name": "scene.get_scene_info"}]))["quarantined"] is True, "reads still answer")
    finally:
        app.undo = original
        bpy.ops.opencode_link.reset()
    check(not core.quarantined, "reset clears the quarantine")


@test
def expectations_are_checked_before_anything_runs():
    before = objects()
    check(code_of(lambda: apply([op("scene.create_object", primitive="EMPTY", name="Exp")], expect=[{"ref": "object:NoSuch", "predicate": "exists"}])) == "expectation_failed", "exists")
    check(objects() == before, "nothing ran")
    result = apply(
        [op("scene.transform_object", name="Cube", location=[1, 2, 3])],
        expect=[{"ref": "object:Cube", "predicate": "exists"}, {"ref": "object:Exp", "predicate": "absent"}],
    )
    check(result["status"] == "applied", "clauses hold")
    check(code_of(lambda: apply([op("scene.transform_object", name="Cube", location=[0, 0, 0])], expect=[{"ref": "object:Cube", "predicate": "equals", "path": "location", "value": [9, 9, 9]}])) == "expectation_failed", "equals")
    result = apply([op("scene.transform_object", name="Cube", location=[0, 0, 0])], expect=[{"ref": "object:Cube", "predicate": "equals", "path": "location", "value": [1, 2, 3]}])
    check(result["status"] == "applied", "equals holds")


@test
def the_revision_after_an_apply_stays_current():
    result = apply([op("scene.create_object", primitive="EMPTY", name="Rev")])
    addon.pump(0.2)
    check(result["revision"] == app.revision(), "no late depsgraph bump after an apply")
    again = apply([op("scene.transform_object", name="Rev", location=[1, 0, 0])], if_revision=result["revision"])
    check(again["status"] == "applied", "the returned revision is usable")
    user_step("UserEdit")
    check(app.revision() > again["revision"], "a user change bumps the revision")


# -- the catalog, read by read and operation by operation ----------------------------------------
def write_fixtures():
    library = os.path.join(SCRATCH, "library.blend")
    material = bpy.data.materials.new("LibMat")
    material2 = bpy.data.materials.new("LibMat2")
    obj = bpy.data.objects.new("LibObj", bpy.data.meshes.new("LibObj"))
    bpy.data.libraries.write(library, {material, material2, obj}, fake_user=True)
    bpy.data.materials.remove(material)
    bpy.data.materials.remove(material2)
    bpy.data.objects.remove(obj)
    folder = os.path.join(SCRATCH, "vrscene")
    os.makedirs(folder, exist_ok=True)
    vrscene = os.path.join(folder, "red.vrscene")
    with open(vrscene, "w", encoding="utf-8") as handle:
        handle.write("BRDFVRayMtl OC_Red {\n  diffuse=AColor(0.8, 0.1, 0.1, 1);\n  reflect_glossiness=0.6;\n}\n")
    return library, folder, vrscene


ABSENT_ADDON = ("not enabled", "not active", "not installed", "Cannot import")


@test
def every_read_and_operation_is_exercised():
    library, folder, vrscene = write_fixtures()
    bpy.data.node_groups.new("OC_ShaderGroup", "ShaderNodeTree")
    coverage = results["coverage"]

    def run_op(name, /, expect_ok=True, absent=False, **args):
        result = apply([op(name, **args)])
        ok = result["status"] == "applied"
        message = result.get("message", "")
        if absent:
            check(not ok and any(text in message for text in ABSENT_ADDON), "%s: absent add-on reported (%s)" % (name, message))
            coverage[name] = "absent add-on path"
        else:
            check(ok == expect_ok, "%s: %s %s" % (name, result["status"], message))
            coverage[name] = "applied"
        return result

    def run_read(name, /, expect_ok=True, absent=False, contains=None, **args):
        output = core._context(dict(state(), reads=[{"name": name, "args": args}]))["results"][0]
        if absent:
            check(not output["ok"] and any(text in output["error"] for text in ABSENT_ADDON), "%s: %s" % (name, output.get("error")))
            coverage[name] = "absent add-on path"
        elif contains:
            check(not output["ok"] and contains in output["error"], "%s: %s" % (name, output.get("error")))
            coverage[name] = "reported: " + contains
        else:
            check(output["ok"] == expect_ok, "%s: %s" % (name, output.get("error")))
            coverage[name] = "read"
        return output.get("result")

    for primitive in ("CUBE", "SPHERE", "CYLINDER", "CONE", "PLANE", "CIRCLE", "GRID", "EMPTY", "CAMERA", "LIGHT"):
        run_op("scene.create_object", primitive=primitive, name="OC_" + primitive.title())
    check(bpy.data.objects["OC_Cube"].type == "MESH" and len(bpy.data.objects["OC_Cube"].data.vertices) == 8, "a real cube")
    run_op("scene.transform_object", name="OC_Cube", location=[1, 2, 3], scale=[2, 2, 2])
    run_op("scene.set_active_camera", name="OC_Camera")
    run_op("scene.delete_object", name="OC_Sphere")
    run_read("scene.get_scene_info")
    run_read("scene.get_full_scene_hierarchy")
    check(run_read("scene.get_object_info", name="OC_Cube")["location"] == [1.0, 2.0, 3.0], "object info")
    run_read("scene.get_object_modifiers", name="OC_Cube")
    run_read("scene.get_viewport_screenshot", contains="background")
    run_read("scene.get_node_editor_screenshot", contains="background")
    run_read("shader.get_shader_editor_screenshot", contains="background")

    run_op("animation.set_timeline_settings", frame_start=1, frame_end=48, fps=24)
    run_op("animation.keyframe_object_transform", name="OC_Cube", frame=1, location=[0, 0, 0])
    run_op("animation.create_turntable_animation", object_name="OC_Cylinder")
    run_read("animation.get_object_animation_info", name="OC_Cube")

    run_op("shader.create_material", name="OC_Mat")
    run_op("shader.create_shader_material_from_template", name="OC_Tpl", base_color=[0.2, 0.4, 0.8, 1.0])
    run_op("shader.add_shader_node", node_type="ShaderNodeTexNoise", material_name="OC_Mat", name="Noise")
    run_op("shader.connect_shader_nodes", from_node="Noise", from_socket="Color", to_node="Principled BSDF", to_socket="Base Color", material_name="OC_Mat")
    run_op("shader.set_shader_node_input", node_name="Noise", input_name="Scale", value=3.0, material_name="OC_Mat")
    run_op("shader.disconnect_shader_nodes", from_node="Noise", from_socket="Color", to_node="Principled BSDF", to_socket="Base Color", material_name="OC_Mat")
    run_op("shader.remove_shader_node", node_name="Noise", material_name="OC_Mat")
    run_op("shader.add_shader_node_group_socket", group_name="OC_ShaderGroup", name="Amount")
    run_op("shader.assign_material", object_name="OC_Cube", material_name="OC_Mat")
    run_op("shader.delete_material", name="OC_Tpl")
    run_read("shader.list_materials")
    run_read("shader.get_material_info", name="OC_Mat")
    run_read("shader.list_shader_node_groups")
    run_read("shader.get_shader_node_group_info", name="OC_ShaderGroup")
    run_read("shader.list_shader_node_types", filter="BSDF")
    run_read("shader.analyze_vrscene_file", filepath=vrscene)
    converted = run_op("shader.convert_vrscene_file", filepath=vrscene)
    check(converted["undoable"] is False and "side_effect" in converted, "side effect announced")
    run_op("shader.convert_vrscene_folder", folder_path=folder, replace_existing=True)

    created = run_op("geometry_nodes.create_geometry_node_tree", name="OC_GN", object_name="OC_Cube")
    modifier = created["results"][0]["result"].get("modifier_name", "OC_GN")
    run_op("geometry_nodes.add_geometry_node", tree_name="OC_GN", node_type="GeometryNodeTransform", name="Xform")
    run_op("geometry_nodes.rename_geometry_node", tree_name="OC_GN", node_name="Xform", new_name="Xform2")
    run_op("geometry_nodes.set_geometry_node_location", tree_name="OC_GN", node_name="Xform2", location=[100, 0])
    run_op("geometry_nodes.connect_geometry_nodes", tree_name="OC_GN", from_node="Group Input", from_socket="Geometry", to_node="Xform2", to_socket="Geometry")
    run_op("geometry_nodes.disconnect_geometry_nodes", tree_name="OC_GN", from_node="Group Input", from_socket="Geometry", to_node="Xform2", to_socket="Geometry")
    run_op("geometry_nodes.set_node_input", tree_name="OC_GN", node_name="Xform2", input_name="Scale", value=[2, 2, 2])
    run_op("geometry_nodes.add_node_tree_socket", tree_name="OC_GN", name="Amount", in_out="INPUT", socket_type="NodeSocketFloat")
    run_op("geometry_nodes.set_modifier_input", object_name="OC_Cube", modifier_name=modifier, input_name="Amount", value=0.5)
    run_op("geometry_nodes.remove_geometry_node", tree_name="OC_GN", node_name="Xform2")
    run_op("geometry_nodes.attach_geometry_node_tree", object_name="OC_Plane", tree_name="OC_GN")
    run_op("geometry_nodes.create_gn_from_template", template_name="deform_with_noise", tree_name="OC_Noise", object_name="OC_Grid")
    run_read("geometry_nodes.list_geometry_node_trees")
    run_read("geometry_nodes.get_geometry_node_tree", tree_name="OC_GN")
    run_read("geometry_nodes.list_available_node_types", filter="Mesh")
    run_read("geometry_nodes.list_geometry_nodes_modifiers")
    run_read("geometry_nodes.list_gn_templates")

    run_op("assets.mark_asset", asset_type="MATERIAL", name="OC_Mat", generate_preview=False)
    run_read("assets.get_asset_info", asset_type="MATERIAL", name="OC_Mat")
    run_read("assets.list_current_file_assets")
    run_op("assets.clear_asset", asset_type="MATERIAL", name="OC_Mat")
    run_op("assets.import_blend_asset", filepath=library, asset_type="MATERIAL", name="LibMat")
    run_read("assets.list_asset_libraries")
    run_read("assets.list_library_blend_files")
    run_read("assets.list_blend_file_assets", filepath=library)

    run_op("workflows.create_material_and_assign", name="OC_WF", object_name="OC_Cube", roughness=0.3)
    run_op("workflows.create_string_to_curves_object", text="Hi", object_name="OC_Text")
    run_op("workflows.apply_library_material_to_object", object_name="OC_Cube", filepath=library, material_name="LibMat2")
    run_op("workflows.scatter_library_asset_on_surface", surface_object_name="OC_Plane", filepath=library, asset_name="LibObj")

    run_read("shot_manager.get_shot_manager_status")
    run_read("shot_manager.get_shot_list", absent=True)
    run_read("shot_manager.get_shot_details", absent=True, shot_name="A")
    run_op("shot_manager.create_shot", absent=True)
    run_op("shot_manager.modify_shot", absent=True, shot_name="A")
    run_op("shot_manager.enable_disable_shots", absent=True, shot_names=["A"], enabled=True)
    run_op("shot_manager.set_shot_manager_render_path", absent=True, path="renders")
    run_op("shot_manager.launch_batch_render", absent=True)

    run_read("addons.get_addon_status")
    run_read("addons.get_blenderkit_import_status", asset_id="x")
    run_read("addons.list_bradley_assets", absent=True)
    run_op("addons.ensure_bradley_asset_library", absent=True)
    run_op("addons.import_bradley_preset", absent=True, name="Preset")
    run_op("addons.import_blenderkit_asset", absent=True, asset_id="x")
    run_op("addons.import_sketchfab_model", absent=True, uid="x")
    run_op("addons.search_blenderkit_assets", absent=True, query="chair")
    addons_module = importlib.import_module(MODULE + ".handlers.addons")
    original = addons_module._http_get_json
    addons_module._http_get_json = lambda url, headers=None: {"results": [{"name": "Chair", "uid": "u1"}]}
    try:
        searched = run_op("addons.search_sketchfab_models", query="chair")
        check(searched["results"][0]["result"]["results"][0]["uid"] == "u1", "search result (network stubbed)")
    finally:
        addons_module._http_get_json = original

    missing = [name for name in sorted(set(core.reads) | set(core.operations)) if name not in coverage]
    check(not missing, "not exercised: %s" % ", ".join(missing))


# -- captures, the code gate, consent ------------------------------------------------------------
@test
def capture_results_carry_image_data():
    # Background Blender has no screen: the capture helper is exercised on a rendered image instead.
    captures = importlib.import_module(MODULE + ".handlers.captures")
    check("background" in str(_raises(lambda: captures.find_area("VIEW_3D"))), "no capture without the interface")


def _raises(fn):
    try:
        fn()
    except Exception as error:  # noqa: BLE001
        return error
    return None


@test
def code_execution_is_absent_and_refused_by_default():
    marker = os.path.join(SCRATCH, "executed.txt")
    code = "open(%r, 'w').write('ran')" % marker
    params = {"script_handle": app.script_handle(), "code": code, "code_execution": True}
    check(code_of(lambda: core.handle("execute_code", params)) == "method_disabled", "refused by default")
    check(not os.path.exists(marker), "nothing executed")
    prefs.allow_code_execution = True
    try:
        check(code_of(lambda: core.handle("execute_code", dict(params, code_execution=False))) == "method_disabled", "needs the plugin flag")
        check(not os.path.exists(marker), "still nothing executed")
        result = core.handle("execute_code", params)
        check(result["status"] == "executed" and os.path.exists(marker), "runs with both settings")
        printed = core.handle("execute_code", dict(params, code="print(len(bpy.data.objects) > 0)"))
        check(printed["stdout"].strip() == "True", "stdout returned")
    finally:
        prefs.allow_code_execution = False


@test
def consent_starts_and_stops_the_listener():
    check(session.listener is None, "no listener before consent")
    prefs.offer_session = True
    try:
        listener = session.listener
        check(listener is not None and listener.running, "listener while offered")
        port = listener.port
    finally:
        prefs.offer_session = False
    check(session.listener is None and not listener.running and listener.token is None, "closed and token invalidated")
    refused = socket.socket()
    refused.settimeout(1.0)
    check(refused.connect_ex(("127.0.0.1", port)) != 0, "port closed")
    refused.close()


@test
def loading_another_document_changes_the_handle():
    handle = app.script_handle()
    bpy.ops.wm.read_homefile(use_empty=True)
    check(app.script_handle() != handle, "new script handle")
    check(code_of(lambda: core._apply({"script_handle": handle, "if_revision": app.revision(), "command_id": "late", "operations": [op("scene.create_object", primitive="EMPTY")]})) == "stale_handle", "old handle refused")


cube = bpy.data.objects.new("Cube", bpy.data.meshes.new("Cube"))
bpy.context.scene.collection.objects.link(cube)
bpy.ops.ed.undo_push(message="Fixture")

for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1500:]})

print("RESULT " + json.dumps(results), flush=True)
