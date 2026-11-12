"""Host tests of OpenCode Link inside a background Blender >= 5.3.0 (run by blender.test.ts).

Covers the background part of the acceptance criteria: the authenticated loopback listener,
apply requirements and replay, Object mode only, one undo step per apply with rollback model B,
quarantine, expectations, revisions, every read and operation of the catalog, captures as image
data, the code-execution gate and the consent switch, and every bundled Blender Lab tool run by name
(lab_tool). Prints one line: RESULT <json>.
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
lab_module = importlib.import_module(MODULE + ".lab_tools")
session = addon.session()
core = session.core
app = session.app
prefs = addon.preferences()
# Enabled, the add-on offers the session at once; recorded here, then stopped: every other test starts
# without a listener and opens its own.
DEFAULTS = {"listening": bool(session.listener and session.listener.running),
            "code": "execute_code" in core.capabilities()["methods"]}
session.stop()

results = {"passed": [], "failed": [], "coverage": {}, "lab": {}}
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
def an_enabled_add_on_offers_the_session_with_code_execution():
    check(DEFAULTS == {"listening": True, "code": True}, "enabled add-on: %s" % DEFAULTS)
    check(not hasattr(prefs, "offer_session") and not hasattr(prefs, "allow_code_execution"), "no consent settings")


@test
def hello_is_valid_and_lists_code_execution():
    hello = core._hello({})
    check(hello["protocol"] == 1, "protocol 1")
    check(hello["host_version"] == core_module.manifest_version(os.path.dirname(core_module.__file__)), "version from manifest")
    check(hello["capability_hash"] == core_module.capability_hash(hello["capabilities"]), "capability hash")
    check(hello["capabilities"]["undo"] == "grouped_best_effort", "undo level")
    check(hello["capabilities"]["background"] is True, "background flag")
    check(hello["app_version"] == bpy.app.version_string, "app version")
    check("execute_code" in hello["capabilities"]["methods"], "execute_code listed")
    check(core._hello({"code_execution": False})["capabilities"] == hello["capabilities"], "the old flag gates nothing")


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
        check(listener.token not in json.dumps(reply), "no token in hello")
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
            if core.operations[name].get("undoable"):
                # Unknown legacy effects have no complete restoration proof, including absent add-ons.
                check(result["restored"] is False and core.quarantined, "%s: unqualified restoration quarantines" % name)
                check(result["verification"]["complete"] is False, "%s: no false proof" % name)
                check(result["verification"]["reason"] == "legacy_effect_scope_unqualified", "%s: scope reason" % name)
                bpy.ops.opencode_link.reset()  # Explicit acknowledgement inside this private factory scene.
            coverage[name] = "absent add-on path"
        else:
            check(ok == expect_ok, "%s: %s %s" % (name, result["status"], message))
            coverage[name] = "applied"
        return result

    def run_read(name, /, expect_ok=True, absent=False, contains=None, **args):
        bpy.context.view_layer.update()  # settle earlier changes
        revision = app.revision()
        output = core._context(dict(state(), reads=[{"name": name, "args": args}]))["results"][0]
        # Blender's next evaluation, outside the read: a read leaves the document and its revision unchanged.
        bpy.context.view_layer.update()
        check(app.revision() == revision, "%s advanced the revision %d -> %d" % (name, revision, app.revision()))
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
    run_read("scene.get_object_modifiers", name="OC_Cube")
    run_read("scene.get_node_editor_screenshot", contains="background")
    run_read("shader.get_shader_editor_screenshot", contains="background")
    run_read("scene.get_evaluated_bounds", objects=["OC_Cube"])
    run_read("scene.capture_view", contains="interface", objects=["OC_Cube"])
    run_read("scene.contact_sheet", frames=[1], camera="OC_Camera", width=32)

    run_op("animation.set_timeline_settings", frame_start=1, frame_end=48, fps=24)
    run_op("animation.keyframe_object_transform", name="OC_Cube", frame=1, location=[0, 0, 0])
    run_op("animation.create_turntable_animation", object_name="OC_Cylinder")
    run_read("animation.get_object_animation_info", name="OC_Cube")
    rig = bpy.data.objects.new("OC_CovRig", bpy.data.armatures.new("OC_CovRig"))
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    rig.data.edit_bones.new("Bone").tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    run_read("animation.get_rig_info", armature="OC_CovRig")
    run_op("animation.aim_bone", armature="OC_CovRig", bone="Bone", direction=[1, 0, 0])
    run_op("animation.edit_keyframes", name="OC_Cube", operation="delete", frame_start=1, frame_end=1, data_path="location")
    run_op("animation.attach_to_bone", name="OC_Empty", armature="OC_CovRig", bone="Bone")
    run_op("scene.place_on_ground", name="OC_Cone", height=1.0)
    run_op("scene.duplicate_hierarchy", name="OC_CovRig")
    run_op("render.render_animation", expect_ok=False)  # never saved: refused, nothing started

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
    glb = os.path.join(SCRATCH, "coverage.glb")
    bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB")
    imported = run_op("assets.import_file", source=glb)["results"][0]["result"]["task"]
    check(imported["state"] == "done" and imported["created"], "import_file: %s" % imported)
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
    # Background Blender has no screen: the encoder of captures runs on a generated PNG instead.
    captures = importlib.import_module(MODULE + ".handlers.captures")
    check("background" in str(_raises(lambda: captures.find_area("VIEW_3D"))), "no capture without the interface")
    path = os.path.join(SCRATCH, "capture.png")
    image = bpy.data.images.new("CaptureSource", 64, 32)
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    bpy.context.view_layer.update()  # settle the fixture
    revision, images = app.revision(), len(bpy.data.images)
    encoded = captures.encode_png(path, 16)
    data = __import__("base64").b64decode(encoded["image"]["data"])
    check(encoded["image"]["mime"] == "image/png" and data[:4] == b"\x89PNG", "PNG image data")
    check((encoded["width"], encoded["height"]) == (16, 8), "scaled to max_size")
    # The encoder never adds data to the document: the next evaluation reports no change.
    bpy.context.view_layer.update()
    check(len(bpy.data.images) == images, "no image datablock left")
    check(app.revision() == revision, "encoding a capture advanced the revision %d -> %d" % (revision, app.revision()))


@test
def reload_is_offered_and_refused_outside_object_mode_or_during_an_apply():
    check("reload" in core.capabilities()["methods"], "the add-on offers its hot reload")
    scheduled, original = [], app.reload_request
    app.reload_request = lambda: scheduled.append(True)
    try:
        app.mode = lambda: "EDIT_MESH"
        check(code_of(lambda: core._reload(state())) == "mode_conflict", "refused outside Object mode")
        del app.mode
        core.busy = lambda: True
        check(code_of(lambda: core._reload(state())) == "busy", "refused while an apply runs")
        del core.busy
        check(code_of(lambda: core._reload(dict(state(), script_handle="blend-other"))) == "stale_handle", "same document only")
        revision = app.revision()
        check(core._reload(state()) == {"outcome": "scheduled", "host_version": core.version}, "scheduled")
        check(scheduled == [True] and app.revision() == revision, "scheduled once, nothing changed")
    finally:
        app.reload_request = original


@test
def document_changes_outside_opencode_advance_the_revision():
    obj = bpy.data.objects.new("OC_UserEdit", None)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    for label, change in (("move", lambda: setattr(obj, "location", (4.0, 5.0, 6.0))),
                          ("rename", lambda: setattr(obj, "name", "OC_UserEdited")),
                          ("select", lambda: obj.select_set(not obj.select_get()))):
        revision = app.revision()
        change()
        bpy.context.view_layer.update()
        check(app.revision() > revision, "%s did not advance the revision" % label)
    bpy.data.objects.remove(obj)
    bpy.context.view_layer.update()


@test
def network_searches_run_off_the_main_thread():
    addons_module = importlib.import_module(MODULE + ".handlers.addons")
    original = addons_module._http_get_json
    threads = []

    def fake(url, headers=None):
        threads.append(threading.current_thread() is threading.main_thread())
        return {"results": [{"name": "Chair", "uid": "u2"}]}

    addons_module._http_get_json = fake
    try:
        params = dict(state(), command_id="worker-1", operations=[op("addons.search_sketchfab_models", query="chair")])
        result = {}
        thread = threading.Thread(target=lambda: result.update(core.handle("apply", params)))
        thread.start()
        while thread.is_alive():
            addon.pump(0.05)
        check(result.get("status") == "applied", "search applied: %s" % result.get("message"))
        check(threads == [False], "the network call ran off the main thread")
        check(core._apply(params).get("replayed") is True, "a worker result is remembered")
    finally:
        addons_module._http_get_json = original


@test
def a_command_id_names_one_change():
    params = dict(state(), command_id="digest-1", operations=[op("scene.create_object", primitive="EMPTY", name="Digest")])
    check(core._apply(params)["status"] == "applied", "first apply")
    other = dict(params, if_revision=app.revision(), operations=[op("scene.delete_object", name="Digest")])
    check(code_of(lambda: core._apply(other)) == "invalid_args", "another change under the same command_id is refused")
    check("Digest" in objects(), "nothing applied")


@test
def a_failure_after_the_operations_is_rolled_back_too():
    user_step("UserFlush")
    before = objects()
    original = app.undo_push

    def push(message):
        if message == "OpenCode: late failure":
            raise RuntimeError("no window for the undo step")
        original(message)

    app.undo_push = push
    try:
        result = apply([op("scene.create_object", primitive="EMPTY", name="Late")], summary="late failure")
    finally:
        app.undo_push = original
    check(result["status"] == "failed" and result["restored"] is True, "reported and restored: %s" % result)
    check(objects() == before, "the change is gone")
    check(not core.quarantined, "no quarantine after a successful restoration")


@test
def supplied_code_cannot_quit_blender():
    result = core.handle("execute_code", {"script_handle": app.script_handle(), "code": "raise SystemExit(3)"})
    check(result["status"] == "failed" and "SystemExit" in result["message"], "SystemExit caught")
    check("SystemExit" in result["traceback"] and result["changed"] is False, "traceback, nothing changed")


def _raises(fn):
    try:
        fn()
    except Exception as error:  # noqa: BLE001
        return error
    return None


@test
def code_runs_report_output_and_group_their_changes():
    marker = os.path.join(SCRATCH, "executed.txt")
    run = lambda code: core.handle("execute_code", {"script_handle": app.script_handle(), "code": code})  # noqa: E731
    revision = app.revision()
    quiet = run("open(%r, 'w').write('ran')\nprint(len(bpy.data.objects) > 0)\nimport sys; print('warn', file=sys.stderr)" % marker)
    check(quiet["status"] == "executed" and os.path.exists(marker), "runs: %s" % quiet)
    check(quiet["stdout"].strip() == "True" and quiet["stderr"].strip() == "warn", "stdout and stderr returned")
    check(quiet["changed"] is False and "undo_step" not in quiet, "no document change, no step")
    check(quiet["revision_before"] == quiet["revision_after"] == revision == app.revision(), "revision kept")
    pushes, original = [], app.undo_push
    app.undo_push = lambda message: (pushes.append(message), original(message))[1]
    try:
        made = run("bpy.data.objects.new('OC_Coded', None)\nbpy.context.scene.collection.objects.link(bpy.data.objects['OC_Coded'])")
        check(made["status"] == "executed" and made["changed"] is True and "OC_Coded" in objects(), "a change: %s" % made)
        check(made["undo_step"] == "OpenCode: code" and pushes[-1] == "OpenCode: code", "one named step: %s" % pushes)
        check(made["revision_after"] == made["revision_before"] + 1, "revision advanced once")
        before = objects()
        failed = run("bpy.data.objects.new('OC_Half', None)\nbpy.context.scene.collection.objects.link(bpy.data.objects['OC_Half'])\nraise ValueError('late')")
        check(failed["status"] == "failed" and "ValueError: late" in failed["traceback"], "traceback: %s" % failed)
        check(failed["changed"] is True and failed["restored"] is True and objects() == before, "restored to the barrier")
        check(not core.quarantined, "no quarantine after a proven restoration")
    finally:
        app.undo_push = original


# -- Blender Lab tools (lab_tool) ---------------------------------------------------------------
# Sample arguments by name; a required argument without one fails the test (a new Lab tool needs one).
LAB_SAMPLES = {"name": "OC_Lab", "output_path": "opencode_lab.png", "area_ui_type": "VIEW_3D", "space_type": "VIEW_3D"}


def lab(tool, **args):
    return core.handle("lab_tool", {"script_handle": app.script_handle(), "tool": tool, "args": args})


def lab_args(tool):
    args = {}
    for name, spec in tool["args"].items():
        if name in LAB_SAMPLES:
            args[name] = LAB_SAMPLES[name]
        elif spec.get("required"):
            raise Failure("no sample for %s.%s: add one to LAB_SAMPLES" % (tool["name"], name))
    return args


@test
def every_bundled_lab_tool_runs_by_name():
    with open(os.path.join(os.path.dirname(core_module.__file__), "lab", "tools.json"), encoding="utf-8") as handle:
        listed = sorted(tool["name"] for tool in json.load(handle)["tools"])
    capabilities = core.capabilities()
    check("lab_tool" in capabilities["methods"] and listed == sorted(core.lab.tools), "listed")
    obj = bpy.data.objects.new("OC_Lab", bpy.data.meshes.new("OC_Lab"))
    bpy.context.scene.collection.objects.link(obj)
    # Lab's renders use the scene's engine: on a runner without a GPU, EEVEE renders in software for minutes
    # (Lab lowers EEVEE's samples only for Blender 4's BLENDER_EEVEE_NEXT). The tools run the same with Workbench.
    bpy.context.scene.render.engine = "BLENDER_WORKBENCH"
    for name in listed:
        tool = core.lab.tools[name]
        response = lab(name, **lab_args(tool))
        expected = "error" if tool["interface"] else "ok"
        check(response["status"] == expected and isinstance(response.get("revision"), int), "%s: %s" % (name, response))
        result = response["result"]
        if tool["interface"]:
            check(result.get("status") == "error" and "background" in str(result.get("message")).lower(),
                  "%s refuses background mode: %s" % (name, result))
            results["lab"][name] = "refused in background (needs a window)"
        else:
            check(result.get("status", "ok") == "ok", "%s: %s" % (name, result))
            results["lab"][name] = "ran"
    check(lab("get_object_detail_summary", name="OC_Lab")["result"]["name"] == "OC_Lab", "object detail")
    check(lab("get_object_detail_summary", name="OC_Nothing")["result"]["status"] == "error", "Lab reports a missing object")
    thumb = lab("render_thumbnail_to_path", output_path="opencode_thumb.png")["result"]
    check(os.path.isfile(thumb["filepath"]) and thumb["filepath"].startswith(bpy.app.tempdir), "thumbnail: %s" % thumb)


@test
def lab_tool_refuses_unknown_tools_arguments_and_code():
    check(code_of(lambda: lab("execute_blender_code", code="print(1)")) == "invalid_args", "no code tool by name")
    check(code_of(lambda: lab("nope")) == "invalid_args", "unknown tool")
    check(code_of(lambda: lab("get_object_detail_summary")) == "invalid_args", "missing argument")
    check(code_of(lambda: lab("get_object_detail_summary", name=3)) == "invalid_args", "argument type")
    check(code_of(lambda: lab("get_object_detail_summary", name="x", code="print(1)")) == "invalid_args", "extra argument")
    check(code_of(lambda: lab("get_screenshot_of_area_as_image", area_ui_type="NOPE")) == "invalid_args", "enum")
    stale = {"script_handle": "stale", "tool": "get_objects_summary", "args": {}}
    check(code_of(lambda: core.handle("lab_tool", stale)) == "stale_handle", "the open document's handle only")


@test
def a_changing_lab_tool_is_one_undo_step_and_a_failing_one_is_undone():
    folder = os.path.join(SCRATCH, "labfake")
    os.makedirs(os.path.join(folder, "lab"), exist_ok=True)
    tools = [{"name": "make", "title": "Make", "runs": "toolcode", "file": "make_toolcode.py", "call_params": ["name"],
              "read_only": False, "interface": False, "deferred": False, "image": False,
              "args": {"name": {"type": "string", "required": True}}},
             {"name": "boom", "title": "Boom", "runs": "toolcode", "file": "boom_toolcode.py", "call_params": [],
              "read_only": False, "interface": False, "deferred": False, "image": False, "args": {}}]
    sources = {
        "make_toolcode.py": "from typing import NamedTuple\nclass Params(NamedTuple):\n    name: str\nclass Result(NamedTuple):\n    status: str\n    name: str\ndef main(params):\n    import bpy\n    obj = bpy.data.objects.new(params.name, None)\n    bpy.context.scene.collection.objects.link(obj)\n    print('made')\n    return Result('ok', obj.name)\n",
        "boom_toolcode.py": "def main(params):\n    import bpy\n    bpy.context.scene.collection.objects.link(bpy.data.objects.new('OC_Boom', None))\n    raise ValueError('late')\n",
    }
    with open(os.path.join(folder, "lab", "tools.json"), "w", encoding="utf-8") as handle:
        json.dump({"tools": tools}, handle)
    for name, text in sources.items():
        with open(os.path.join(folder, "lab", name), "w", encoding="utf-8") as handle:
            handle.write(text)
    real = core.lab
    core.lab = lab_module.LabTools(folder)
    try:
        revision = app.revision()
        made = lab("make", name="OC_LabMade")
        check(made["status"] == "ok" and made["result"] == {"status": "ok", "name": "OC_LabMade"}, "made: %s" % made)
        check(made["stdout"].strip() == "made" and made["undo_step"] == "OpenCode: Make", "output and one named step")
        check(made["changed"] is True and made["revision"] == revision + 1, "revision advanced once")
        before = objects()
        boom = lab("boom")
        check(boom["status"] == "error" and "ValueError: late" in boom["message"], "Lab's error envelope: %s" % boom)
        check(boom["restored"] is True and objects() == before and not core.quarantined, "undone to the barrier")
    finally:
        core.lab = real


@test
def code_runs_return_the_result_variable():
    run = lambda code: core.handle("execute_code", {"script_handle": app.script_handle(), "code": code})  # noqa: E731
    done = run("result = {'count': len(bpy.data.objects), 'first': bpy.data.objects[0]}")
    check(done["status"] == "executed" and isinstance(done["result"]["count"], int), "result: %s" % done)
    check(done["result"]["first"].startswith("bpy.data.objects["), "values JSON cannot hold become their repr")
    deferred = run("check_is_finished = lambda: {'done': True}")
    check(deferred["status"] == "failed" and "interface" in deferred["message"], "deferred needs the interface")


@test
def stopping_the_offer_closes_the_listener():
    check(session.listener is None, "stopped at the top of these tests")
    session.start(prefs)
    try:
        listener = session.listener
        check(listener is not None and listener.running, "listener while offered")
        port = listener.port
    finally:
        session.stop()
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
