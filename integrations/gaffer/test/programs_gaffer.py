"""The fixed programs under a real Gaffer (opt-in: gaffer.test.ts with GAFFER_TEST_BIN = Gaffer's launcher).

Run with: <gaffer launcher> python programs_gaffer.py [test names...], in an isolated environment (private HOME,
USERPROFILE, TEMP and startup paths: gaffer.test.ts sets them, never the user's). Builds scripts in a temp folder,
runs each program as the plugin sends it (PARAMS, and JOB for verify_saved and render, prepended; exec with the Gaffer
modules, script, application and result) and checks the results. Evidence (each program's result) is written to
OPENCODE_GAFFER_EVIDENCE as programs_gaffer.json when that variable names a folder.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback

try:
    import Gaffer
    import GafferDispatch
    import GafferImage
    import GafferScene
    import IECore
    import IECoreScene
    import imath
except ImportError:
    print("SKIP: run with Gaffer's launcher: gaffer python programs_gaffer.py")
    raise SystemExit(0)

HERE = os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0].endswith(".py") else __file__))
PROGRAMS = os.path.join(HERE, "..", "plugin", "programs")
WORK = tempfile.mkdtemp(prefix="oc_gaffer_programs_")
EVIDENCE = {}
RESULTS = []
SCRIPT = None


def source(name):
    with open(os.path.join(PROGRAMS, name), encoding="utf-8") as fh:
        return fh.read()


def program(name, params, job=None):
    """As the plugin sends it: PARAMS (and JOB) prepended, run with exec in the installed part's namespace."""
    text = "import json\nPARAMS = json.loads(%s)\n" % json.dumps(json.dumps(params))
    if job is not None:
        text += "JOB = %s\n" % json.dumps(source(job))
    text += source(name)
    namespace = {"Gaffer": Gaffer, "GafferScene": GafferScene, "GafferImage": GafferImage, "GafferDispatch": GafferDispatch,
                 "IECore": IECore, "imath": imath, "script": SCRIPT, "application": None, "__name__": "__opencode__", "result": {}}
    exec(compile(text, "<opencode execute_code>", "exec"), namespace)  # noqa: S102
    return json.loads(json.dumps(namespace.get("result", {}), default=repr))


def failing(name, params, job=None):
    try:
        program(name, params, job)
    except Exception as exc:  # noqa: BLE001
        return "%s: %s" % (type(exc).__name__, exc)
    raise AssertionError("%s with %s did not fail" % (name, params))


def verify_saved(params, timeout=300):
    """gaffer_verify saved as the plugin runs it: the program starts the background gaffer python and returns at
    once; its result is followed from outside (saved-verify.ts). Returns (result, steps of its log, seconds to start)."""
    started_at = time.time()
    start = program("verify_saved.py", params, job="verify.py")["started"]
    start_s = time.time() - started_at
    deadline = time.time() + timeout
    while time.time() < deadline and not os.path.isfile(start["result"]):
        time.sleep(0.2)
    with open(start["log"], encoding="utf-8", errors="replace") as fh:
        log = fh.read()
    steps = [json.loads(line[14:]) for line in log.splitlines() if line.startswith("OPENCODE_STEP ")]
    if not os.path.isfile(start["result"]):
        raise AssertionError("no result after %d s; log:\n%s" % (timeout, log[-2000:]))
    with open(start["result"], encoding="utf-8") as fh:
        result = json.load(fh)
    if "error" in result:
        raise AssertionError(result["error"])
    result["read"] = {"file": start["file"], "bytes": start["bytes"], "seconds": round(time.time() - started_at, 1)}
    shutil.rmtree(start["work"], ignore_errors=True)
    return result, steps, start_s


def wait_job(started, timeout=300):
    """Follow the log as the plugin's task tracker does: markers until DONE or FAILED."""
    deadline = time.time() + timeout
    marks = []
    while time.time() < deadline:
        with open(started["log"], encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        marks = [line for line in lines if line.startswith("OPENCODE_")]
        if any(line.startswith(("OPENCODE_DONE ", "OPENCODE_FAILED ")) for line in marks):
            break
        time.sleep(0.5)
    return [(line.split(" ", 1)[0][9:], json.loads(line.split(" ", 1)[1])) for line in marks]


def check(condition, message):
    if not condition:
        raise AssertionError(message)


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def new_script():
    global SCRIPT
    SCRIPT = Gaffer.ScriptNode()
    return SCRIPT


def scene_script():
    """sphere (radius 2, in set 'hero') and cube -> group; camera; an expression driving cube size by frame; a
    sphere 'bad' whose radius expression divides by zero."""
    s = new_script()
    s["sphere"] = GafferScene.Sphere()
    s["sphere"]["radius"].setValue(2)
    s["sphere"]["sets"].setValue("hero")
    s["cube"] = GafferScene.Cube()
    s["camera"] = GafferScene.Camera()
    s["camera"]["transform"]["translate"].setValue(imath.V3f(0, 0, 8))
    s["group"] = GafferScene.Group()
    for i, name in enumerate(("sphere", "cube", "camera")):
        s["group"]["in"][i].setInput(s[name]["out"])
    s["attrs"] = GafferScene.StandardAttributes()
    s["attrs"]["in"].setInput(s["group"]["out"])
    s["attrs"]["attributes"]["scene:visible"]["enabled"].setValue(True)
    s["filter"] = GafferScene.PathFilter()
    s["filter"]["paths"].setValue(IECore.StringVectorData(["/group/sphere"]))
    s["attrs"]["filter"].setInput(s["filter"]["out"])
    s["cubeSize"] = Gaffer.Expression()
    s["cubeSize"].setExpression('parent["cube"]["dimensions"]["x"] = context.getFrame()', "python")
    s["bad"] = GafferScene.Sphere()
    s["badRadius"] = Gaffer.Expression()
    s["badRadius"].setExpression('parent["bad"]["radius"] = 1.0 / 0', "python")
    return s


def fingerprint():
    return SCRIPT.serialise(), SCRIPT.context().getFrame(), SCRIPT["fileName"].getValue()


# --- inspect ---------------------------------------------------------------------------------------------------
@test
def inspect_type():
    new_script()
    r = program("inspect.py", {"type": "GafferScene.Sphere"})
    EVIDENCE["inspect_type"] = r
    t = r["type"]
    check(r["gaffer"]["version"] == Gaffer.About.versionString() and r["gaffer"]["descriptions"], r["gaffer"])
    check(t["type"] == "GafferScene.Sphere" and t["description"].startswith("Produces scenes containing a sphere"),
          {k: v for k, v in t.items() if k != "plugs"})
    check(t["doc"].endswith("Reference/NodeReference/GafferScene/Sphere.html"), t["doc"])
    radius = next(p for p in t["plugs"] if p["name"] == "radius")
    check(radius["type"] == "FloatPlug" and radius["default"] == 1.0 and radius["description"] == "Radius of the sphere.", radius)
    translate = next(p for p in t["plugs"] if p["name"] == "transform.translate")
    check(translate["type"] == "V3fPlug" and translate["default"] == [0.0, 0.0, 0.0], translate)
    kind = next(p for p in t["plugs"] if p["name"] == "type")
    check(kind.get("presets"), kind)
    r = program("inspect.py", {"type": "Spheer"})
    check("error" in r["type"] and "GafferScene.Sphere" in r["type"]["closest"], r["type"])
    r = program("inspect.py", {"type": "Sphere", "match": "radius"})
    check([p["name"] for p in r["type"]["plugs"]] == ["radius"], r["type"]["plugs"])
    r = program("inspect.py", {"type": "GafferImage.ImageWriter"})
    check(r["type"].get("task") and any(p["name"] == "fileName" for p in r["type"]["plugs"]), r["type"])


@test
def inspect_node():
    scene_script()
    r = program("inspect.py", {"node": "attrs"})
    EVIDENCE["inspect_node_scene"] = r
    n = r["node"]
    check(n["type"] == "GafferScene.StandardAttributes" and n["description"], n)
    check(any(p["name"] == "in" and p["from"] == "group.out" for p in n["plugs_changed"]), n["plugs_changed"])
    scene = n["scene"]["out"]
    paths = [entry["path"] for entry in scene["locations"]]
    check(paths[:4] == ["/group", "/group/sphere", "/group/cube", "/group/camera"], paths)
    sphere = next(entry for entry in scene["locations"] if entry["path"] == "/group/sphere")
    check(sphere["object"] == "MeshPrimitive" and "scene:visible" in sphere["attributes"], sphere)
    check(scene["sets"].get("hero") == 1 and "__cameras" in scene["sets"], scene["sets"])
    check(scene["bound"]["min"][0] == -2.0, scene["bound"])
    r = program("inspect.py", {"node": "sphere", "match": "radius"})
    check(next(p for p in r["node"]["plugs_changed"] if p["name"] == "radius")["value"] == 2.0, r["node"])
    r = program("inspect.py", {"node": "bad"})
    EVIDENCE["inspect_node_error"] = r["node"].get("errors")
    check(r["node"].get("errors") and "ZeroDivisionError" in r["node"]["errors"][0]["error"], r["node"])
    r = program("inspect.py", {"node": "spheer"})
    check("sphere" in r["node"]["closest"], r["node"])
    r = program("inspect.py", {"node": "/"})
    check(r["node"]["path"] == "/" and any(c["name"] == "group" for c in r["node"]["children"]), r["node"])
    s = SCRIPT
    s["checker"] = GafferImage.Checkerboard()
    s["checker"]["format"].setValue(GafferImage.Format(64, 32))
    r = program("inspect.py", {"node": "checker"})
    check(r["node"]["image"]["out"]["format"] == [64, 32] and "R" in r["node"]["image"]["out"]["channels"], r["node"].get("image"))


@test
def inspect_gaffer_and_search():
    new_script()
    r = program("inspect.py", {"gaffer": "Gaffer.GraphComponent.setName"})
    check("setName(" in r["gaffer"]["doc"] and r["gaffer"]["kind"] == "callable", r["gaffer"])
    r = program("inspect.py", {"gaffer": "Gaffer.Node"})
    check(r["gaffer"]["kind"] == "class" and "GraphComponent" in str(r["gaffer"]["bases"]), r["gaffer"])
    r = program("inspect.py", {"gaffer": "Gaffer.Nod"})
    check("Gaffer.Node" in r["gaffer"]["closest"], r["gaffer"])
    started = time.time()
    r = program("inspect.py", {"search": "sphere", "limit": 15})
    first = time.time() - started
    EVIDENCE["inspect_search"] = r
    paths = [h["path"] for h in r["search"]["results"]]
    check("GafferScene.Sphere" in paths[:3], paths)
    started = time.time()
    r = program("inspect.py", {"search": "radius"})
    check(any(h["kind"] == "plug" and h["path"] == "radius" and ("GafferScene.Sphere" in h["on"] or h.get("on_count", 0) > 8)
              for h in r["search"]["results"]),
          r["search"]["results"][:5])
    EVIDENCE["inspect_search_seconds"] = {"first": round(first, 2), "cached": round(time.time() - started, 2)}
    message = failing("inspect.py", {})
    check("give search" in message, message)


# --- verify ----------------------------------------------------------------------------------------------------
CRITERIA = [
    {"expr": "child_names('group', '/group')", "op": "==", "value": ["sphere", "cube", "camera"], "label": "group children"},
    {"expr": "bound('sphere', '/sphere').size", "op": "approx", "value": [4, 4, 4], "tol": 1e-5},
    {"expr": "exists('attrs', '/group/sphere')", "op": "==", "value": True},
    {"expr": "attributes('attrs', '/group/sphere')['scene:visible']", "op": "==", "value": True},
    {"expr": "set_members('attrs', 'hero')", "op": "==", "value": ["/group/sphere"]},
    {"expr": "plug('sphere.radius')", "op": "==", "value": 2},
    {"expr": "plug('camera.transform.translate')", "op": "==", "value": [0, 0, 8]},
    {"expr": "translate('group', '/group/camera')", "op": "approx", "value": [0, 0, 8], "tol": 1e-5},
    {"expr": "world_bound('group', '/group/sphere').size", "op": "approx", "value": [4, 4, 4], "tol": 1e-5},
    {"expr": "bound('cube', '/cube').size[0]", "op": "approx", "value": 10, "frame": 10, "label": "cube follows the frame at 10"},
    {"expr": "object_type('group', '/group/cube')", "op": "==", "value": "MeshPrimitive"},
    {"expr": "len(script.children(Gaffer.Node))", "op": ">=", "value": 8},
    {"expr": "bound('sphere', '/sphere').size[0]", "op": ">", "value": 100, "label": "deliberately failing"},
    {"expr": "bound('nothing', '/')", "op": "==", "value": 0, "label": "missing node"},
    {"expr": "bound('bad', '/sphere')", "op": "==", "value": 0, "label": "compute error"},
]


@test
def verify_criteria_and_errors():
    scene_script()
    before = fingerprint()
    r = program("verify.py", {"criteria": CRITERIA})
    EVIDENCE["verify"] = r
    check(fingerprint() == before, "the script or frame changed")
    by = {c["label"]: c for c in r["criteria"]}
    check(r["passed"] == len(CRITERIA) - 3 and r["failed"] == 3,
          [(c["label"], c["passed"], c.get("measured"), c.get("error")) for c in r["criteria"]])
    check(by["deliberately failing"]["measured"] == 4.0 and "no node" in by["missing node"]["error"], by)
    check("ZeroDivisionError" in by["compute error"]["error"], by["compute error"])
    check(by["cube follows the frame at 10"]["passed"] and SCRIPT.context().getFrame() == 1, by["cube follows the frame at 10"])
    bad = [e for e in r["errors"] if e["node"] == "bad"]
    check(bad and not bad[0]["arises_here"] and "ZeroDivisionError" in bad[0]["errors"][0]["error"], r["errors"])
    check(bad[0]["errors"][0]["arose_in"] == "badRadius.__execute", bad)
    scan = r["facts"]["error_scan"]
    check(scan["errors_arise_in"] == ["badRadius"] and scan["nodes_with_errors"] == 1 and scan["nodes_read"] >= 9, scan)
    check([e["node"] for e in r["errors"]] == ["bad"], r["errors"])
    for expr in ("script.save()", "node('sphere')['radius'].setValue(3)", "script.serialiseToFile('x.gfr')",
                 "node('sphere').addChild(Gaffer.Node())", "application"):
        message = failing("verify.py", {"criteria": [{"expr": expr, "op": "==", "value": 1}]})
        check("not a read" in message or "names" in message, message)
    r = program("verify.py", {"roots": ["sphere"], "errors": True})
    check(r["facts"]["error_scan"]["nodes_read"] == 1 and not r["errors"], r["facts"])


@test
def verify_render():
    scene_script()
    before = fingerprint()
    started = time.time()
    r = program("verify.py", {"errors": False, "render": {"size": 96, "scene": "group"}})
    EVIDENCE["verify_render"] = dict(r, image_base64=len(r.get("image_base64") or ""))
    EVIDENCE["verify_render_seconds"] = round(time.time() - started, 1)
    check(fingerprint() == before, "the script changed")
    render = r["facts"]["render"]
    check(r.get("image_base64") and render["image"]["size"] == [96, 72] and not render["image"]["black"], render)
    check(render["camera"] == "/group/camera", render)
    message = failing("verify.py", {"errors": False, "render": {"size": 96}})
    check("give render.scene" in message, message)


# --- save and verify saved -------------------------------------------------------------------------------------
@test
def save_and_verify_saved():
    scene_script()
    message = failing("save.py", {})
    check("never saved" in message, message)
    first = os.path.join(WORK, "shot.gfr")
    r = program("save.py", {"path": first})
    EVIDENCE["save"] = r
    check(os.path.normcase(r["open_file"]) == os.path.normcase(first) and r["bytes"] > 1000 and not r["unsaved_changes"], r)
    copy = os.path.join(WORK, "copies", "shot_copy.gfr")
    r = program("save.py", {"path": copy, "copy": True})
    EVIDENCE["save_copy"] = r
    check(os.path.isfile(copy) and os.path.normcase(r["open_file"]) == os.path.normcase(first) and r["copy"], r)
    message = failing("save.py", {"path": copy, "copy": True})
    check("overwrite" in message, message)
    message = failing("save.py", {"path": os.path.join(WORK, "shot.hip")})
    check(".gfr" in message, message)
    reloaded = Gaffer.ScriptNode()
    reloaded["fileName"].setValue(copy)
    reloaded.load()
    check(reloaded["sphere"]["radius"].getValue() == 2 and "badRadius" in reloaded, reloaded.keys())
    saved_nodes = len(SCRIPT.children(Gaffer.Node))
    time.sleep(0.05)
    r = program("save.py", {})
    check(os.path.normcase(r["saved"]) == os.path.normcase(first), r)
    SCRIPT["unsaved"] = GafferScene.Plane()  # in the open script only
    criteria = [{"expr": "len(script.children(Gaffer.Node))", "op": "==", "value": saved_nodes, "label": "nodes in the file"},
                {"expr": "plug('sphere.radius')", "op": "==", "value": 2}]
    live = program("verify.py", {"criteria": criteria, "errors": False})
    check(live["failed"] == 1, live["criteria"])
    r, steps, start_s = verify_saved({"saved": True, "criteria": criteria})
    EVIDENCE["verify_saved"] = r
    EVIDENCE["verify_saved_steps"] = steps
    check(r["all_passed"] and r["read"]["bytes"] > 1000, r)
    check(any(e["node"] == "bad" for e in r["errors"]), r["errors"])
    # Started, not waited for, on the main thread; its log says each step.
    check(start_s < 3, start_s)
    check([s["step"] for s in steps][:4] == ["load", "loaded", "criterion", "criterion"] and steps[-1]["step"] == "done", steps)
    check(steps[2]["label"] == "nodes in the file" and steps[2]["index"] == 1 and steps[2]["of"] == 2, steps[2])
    check("errors" in [s["step"] for s in steps] and {"step": "cook", "node": "bad"} in [{k: v for k, v in s.items() if k != "at_s"} for s in steps], steps)
    r2, _, _ = verify_saved({"saved": copy, "criteria": criteria[1:], "errors": False, "render": {"size": 64, "scene": "group"}})
    EVIDENCE["verify_saved_render"] = dict(r2, image_base64=len(r2.get("image_base64") or ""))
    check(r2["all_passed"] and r2.get("image_base64") and "this background" in r2["facts"]["render"]["by"], r2)
    EVIDENCE["verify_saved_seconds"] = {"criteria_and_errors": r["read"]["seconds"], "with_render": r2["read"]["seconds"]}
    message = failing("verify_saved.py", {"saved": os.path.join(WORK, "missing.gfr")}, job="verify.py")
    check("no file" in message, message)


@test
def verify_saved_reports_load_errors():
    s = new_script()
    s["sphere"] = GafferScene.Sphere()
    path = os.path.join(WORK, "broken.gfr")
    s["fileName"].setValue(path.replace("\\", "/"))
    s.save()
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n__children[\"nothing\"][\"radius\"].setValue( 3 )\n")
    r, _, _ = verify_saved({"saved": True, "criteria": [{"expr": "plug('sphere.radius')", "op": "==", "value": 1}], "errors": False})
    EVIDENCE["verify_saved_load_warnings"] = r.get("load_warnings")
    check(r["all_passed"] and r.get("load_warnings"), r)


# --- render ----------------------------------------------------------------------------------------------------
def image_script():
    s = new_script()
    s["checker"] = GafferImage.Checkerboard()
    s["checker"]["format"].setValue(GafferImage.Format(48, 32))
    s["writer"] = GafferImage.ImageWriter()
    s["writer"]["in"].setInput(s["checker"]["out"])
    s["writer"]["fileName"].setValue(os.path.join(WORK, "own", "own.####.png").replace("\\", "/"))
    return s


@test
def verify_errors_skip_private_internals():
    # Trial on a windowed Gaffer: an ImageWriter's private __contextQuery (a compound output without a value of its
    # own) read as an error of a sound script.
    image_script()
    r = program("verify.py", {"criteria": []})
    EVIDENCE["verify_image_writer"] = r["facts"]["error_scan"]
    check(r["errors"] == [] and r["facts"]["error_scan"]["nodes_with_errors"] == 0, r["errors"])


@test
def render_image_writer_frames():
    image_script()
    output = os.path.join(WORK, "out", "check_####.png")
    started = time.time()
    r = program("render.py", {"node": "writer", "output": output, "frames": [1, 3]}, job="render_job.py")
    returned = time.time() - started
    EVIDENCE["render_writer_start"] = r
    check(SCRIPT["fileName"].getValue() == "", "the open file name changed")
    check(r["kind"] == "render" and len(r["targets"]) == 3 and r["targets"][0].endswith("check_0001.png") and r["start"] == 1 and r["end"] == 3, r)
    check(SCRIPT["writer"]["fileName"].getValue().endswith("own.####.png"), "the open script's output changed")
    marks = wait_job(r)
    EVIDENCE["render_writer_marks"] = marks
    EVIDENCE["render_writer_seconds"] = {"returned": round(returned, 2), "finished": round(time.time() - started, 1)}
    kinds = [k for k, _ in marks]
    check(kinds and kinds[0] == "RENDER" and kinds[-1] == "DONE" and isinstance(marks[0][1]["pid"], int), marks)
    check([v["done"] for k, v in marks if k == "PROGRESS"][-1] == 3, marks)
    check(all(os.path.isfile(t) for t in r["targets"]) and marks[-1][1]["image_size"] == [48, 32], marks[-1])
    check(not os.path.isdir(os.path.join(WORK, "own")), "the node's own output was written")
    message = failing("render.py", {"node": "writer", "output": output, "frames": [1, 3]}, job="render_job.py")
    check("overwrite" in message, message)
    message = failing("render.py", {"node": "checker"}, job="render_job.py")
    check("not a task node" in message, message)
    message = failing("render.py", {"node": "writer", "output": output.replace("####", "x"), "frames": [1, 2]}, job="render_job.py")
    check("frame number" in message, message)
    r = program("render.py", {"node": "writer"}, job="render_job.py")
    check(r["targets"][0].endswith("own.0001.png"), r["targets"])
    check(wait_job(r)[-1][0] == "DONE", "the node's own output")
    SCRIPT["missing"] = GafferImage.ImageReader()
    SCRIPT["missing"]["fileName"].setValue(os.path.join(WORK, "nowhere.exr").replace("\\", "/"))
    SCRIPT["writer"]["in"].setInput(SCRIPT["missing"]["out"])
    r = program("render.py", {"node": "writer", "output": os.path.join(WORK, "failed", "f.png")}, job="render_job.py")
    marks = wait_job(r)
    EVIDENCE["render_failed_marks"] = marks
    check(marks[-1][0] == "FAILED" and "nowhere.exr" in marks[-1][1], marks)


@test
def render_cycles_size():
    s = scene_script()
    del s["bad"], s["badRadius"]
    s["outputs"] = GafferScene.Outputs()
    s["outputs"]["in"].setInput(s["attrs"]["out"])
    s["outputs"].addOutput("beauty", IECoreScene.Output(os.path.join(WORK, "own_beauty.####.exr").replace("\\", "/"), "exr", "rgba", {}))
    s["options"] = GafferScene.StandardOptions()
    s["options"]["in"].setInput(s["outputs"]["out"])
    s["options"]["options"]["render:camera"]["enabled"].setValue(True)
    s["options"]["options"]["render:camera"]["value"].setValue("/group/camera")
    s["render"] = GafferScene.Render()
    s["render"]["in"].setInput(s["options"]["out"])
    s["render"]["renderer"].setValue("Cycles")
    output = os.path.join(WORK, "cycles", "beauty.####.exr")
    r = program("render.py", {"node": "render", "output": output, "frames": 2, "size": [64, 48]}, job="render_job.py")
    EVIDENCE["render_cycles_start"] = r
    check(r["facts"]["renderer"] == "Cycles" and r["targets"][0].endswith("beauty.0002.exr"), r)
    marks = wait_job(r)
    EVIDENCE["render_cycles_marks"] = marks
    check(marks and marks[-1][0] == "DONE" and marks[-1][1]["image_size"] == [64, 48], marks)
    check(not os.path.exists(os.path.join(WORK, "own_beauty.0002.exr")), "the script's own output was rendered")
    message = failing("render.py", {"node": "writer" if "writer" in s else "outputs"}, job="render_job.py")
    check("not a task node" in message, message)
    message = failing("render.py", {"node": "render", "output": output, "frames": 2}, job="render_job.py")
    check("overwrite" in message, message)


def main():
    names = set(sys.argv[1:])
    started = time.time()
    # Relative paths a program might write go to the run's own folder.
    os.chdir(WORK)
    for fn in TESTS:
        if names and fn.__name__ not in names:
            continue
        t = time.time()
        try:
            fn()
            RESULTS.append((fn.__name__, "ok", round(time.time() - t, 1), ""))
        except Exception:  # noqa: BLE001
            RESULTS.append((fn.__name__, "FAIL", round(time.time() - t, 1), traceback.format_exc()[-3000:]))
    for name, status, seconds, detail in RESULTS:
        print("%-34s %-4s %6.1fs %s" % (name, status, seconds, ("\n" + detail) if detail else ""))
    folder = os.environ.get("OPENCODE_GAFFER_EVIDENCE")
    if folder:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "programs_gaffer.json"), "w", encoding="utf-8") as fh:
            json.dump({"gaffer": Gaffer.About.versionString(), "results": [r[:3] for r in RESULTS], "evidence": EVIDENCE},
                      fh, indent=1, default=repr)
    failed = [r for r in RESULTS if r[1] != "ok"]
    print("%d passed, %d failed in %.1f s" % (len(RESULTS) - len(failed), len(failed), time.time() - started))
    os.chdir(tempfile.gettempdir())
    shutil.rmtree(WORK, ignore_errors=True)
    return 1 if failed else 0


if __name__ in ("__main__", "__opencode_test__"):
    code = main()
    sys.stdout.flush()
    os._exit(code)
