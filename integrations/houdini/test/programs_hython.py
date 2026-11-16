"""The fixed programs and hints under a real Houdini (opt-in: HOUDINI_TEST_BIN = hython, or Houdini's bin folder).

Run with any Python 3: python programs_hython.py [test names...]. Under system Python it starts hython on itself in
an isolated environment (private preferences, packages and temp folders; never the user's), then, under hython,
builds scenes in a temp folder, runs each program as the plugin sends it (PARAMS, and JOB for verify_saved and
render, prepended; exec with hou, PARAMS and result) and checks the results. Evidence (each program's result) is
written to OPENCODE_HOUDINI_EVIDENCE (default: the run's temp folder) as programs_hython.json.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAMS = os.path.join(HERE, "..", "plugin", "programs")
HOST_SCRIPTS = os.path.join(HERE, "..", "host", "scripts")


def hython_path():
    given = os.environ.get("HOUDINI_TEST_BIN", "")
    if not given:
        return None
    if os.path.isdir(given):
        given = os.path.join(given, "hython.exe" if os.name == "nt" else "hython")
    return given if os.path.isfile(given) else None


def launch():
    hython = hython_path()
    if hython is None:
        print("SKIP: set HOUDINI_TEST_BIN to hython (or Houdini's bin folder)")
        return 0
    root = tempfile.mkdtemp(prefix="oc_houdini_programs_")
    version = subprocess.run([hython, "-c", "import hou;print('%d.%d' % hou.applicationVersion()[:2])"],
                             capture_output=True, text=True, env=isolated(root), timeout=600,
                             creationflags=0x08000000 if os.name == "nt" else 0).stdout.strip().splitlines()
    os.makedirs(os.path.join(root, "prefs", "houdini" + (version[-1] if version else "")), exist_ok=True)
    env = isolated(root)
    env["OPENCODE_HOUDINI_TEST_ROOT"] = root
    started = time.time()
    run = subprocess.run([hython, os.path.abspath(__file__)] + sys.argv[1:], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, timeout=3600, creationflags=0x08000000 if os.name == "nt" else 0)
    text = run.stdout.decode("utf-8", "replace")
    # Loading every HDA's parameters in hython warns about viewer handles that only exist with the UI.
    noise = [l for l in text.splitlines() if l.startswith("oplib:") and "error binding handle" in l]
    sys.stdout.write("\n".join(l for l in text.splitlines() if l not in noise) + "\n")
    if noise:
        print("(%d 'error binding handle' lines from HDA definitions loaded without UI, hidden)" % len(noise))
    if "missing __HVER__" in text:
        print("FAIL: Houdini ignored the private preferences folder")
        return 1
    summary = [l for l in text.splitlines() if l.startswith("OPENCODE_TESTS ")]
    print("hython run: exit %d in %.1f s (root %s)" % (run.returncode, time.time() - started, root))
    if not summary:
        print("FAIL: hython ended before reporting its tests")
        return 1
    # hython can crash in its own exit handlers after the tests reported (exit 139): the report decides.
    return 0 if json.loads(summary[-1].split(" ", 1)[1])["failed"] == 0 else 1


def isolated(root):
    env = {k: v for k, v in os.environ.items() if k != "OPENCODE_HOUDINI_OFFER_KEY" and not k.startswith("HOUDINI_")}
    tmp = os.path.join(root, "tmp")
    for d in ("prefs", "packages", "tmp"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    env.update(HOUDINI_USER_PREF_DIR=os.path.join(root, "prefs", "houdini__HVER__"),
               HOUDINI_PACKAGE_DIR=os.path.join(root, "packages"), HOUDINI_NO_ENV_FILE="1",
               HOUDINI_TEMP_DIR=os.path.join(tmp, "houdini_temp"), TEMP=tmp, TMP=tmp, TMPDIR=tmp)
    if "HOUDINI_TEST_BIN" in os.environ:
        env["HOUDINI_TEST_BIN"] = os.environ["HOUDINI_TEST_BIN"]
    return env


try:
    import hou  # noqa: E402
except ImportError:
    hou = None

if hou is None:
    if __name__ == "__main__":
        sys.exit(launch())
    raise SystemExit(0)

# --- under hython ---------------------------------------------------------------------------------------------
sys.path.insert(0, HOST_SCRIPTS)
from opencode_houdini import hints  # noqa: E402

ROOT = os.environ.get("OPENCODE_HOUDINI_TEST_ROOT") or tempfile.mkdtemp(prefix="oc_houdini_programs_")
WORK = tempfile.mkdtemp(prefix="scenes_", dir=os.environ.get("TEMP"))
EVIDENCE = {}
RESULTS = []


def source(name):
    with open(os.path.join(PROGRAMS, name), encoding="utf-8") as fh:
        return fh.read()


def program(name, params, job=None):
    """As the plugin sends it: PARAMS (and JOB) prepended, run with exec in a namespace holding hou and result."""
    text = "import json\nPARAMS = json.loads(%s)\n" % json.dumps(json.dumps(params))
    if job is not None:
        text += "JOB = %s\n" % json.dumps(source(job))
    text += source(name)
    namespace = {"hou": hou, "__name__": "__opencode__", "result": {}}
    exec(compile(text, "<opencode execute_code>", "exec"), namespace)  # noqa: S102
    return json.loads(json.dumps(namespace.get("result", {}), default=repr))


def verify_saved(params, timeout=600):
    """houdini_verify saved as the plugin runs it: the program starts the background hython and returns at once;
    its result is followed from outside (saved-verify.ts). Returns (result, steps of its log, seconds to start)."""
    started_at = time.time()
    start = program("verify_saved.py", params, job="verify.py")["started"]
    start_s = time.time() - started_at
    deadline = time.time() + timeout
    while time.time() < deadline and not os.path.isfile(start["result"]):
        time.sleep(0.2)
    with open(start["log"], encoding="utf-8", errors="replace") as fh:
        steps = [json.loads(l[14:]) for l in fh.read().splitlines() if l.startswith("OPENCODE_STEP ")]
    with open(start["result"], encoding="utf-8") as fh:
        result = json.load(fh)
    if "error" in result:
        raise AssertionError(result["error"])
    result["read"] = {"file": start["file"], "bytes": start["bytes"], "seconds": round(time.time() - started_at, 1)}
    shutil.rmtree(start["work"], ignore_errors=True)
    return result, steps, start_s


def failing(name, params, job=None):
    try:
        program(name, params, job)
    except Exception as exc:  # noqa: BLE001
        return "%s: %s" % (type(exc).__name__, exc)
    raise AssertionError("%s with %s did not fail" % (name, params))


def hint_of(code):
    """execute_code's except block: the code fails inside exec, explain runs while its frames live."""
    namespace = {"hou": hou, "__name__": "__opencode__", "result": {}}
    try:
        exec(compile(code, "<opencode execute_code>", "exec"), namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        return hints.explain(exc, code, None), "%s: %s" % (type(exc).__name__, exc)
    raise AssertionError("the code did not fail")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def reset_scene():
    hou.hipFile.clear(suppress_save_prompt=True)


def sop_scene():
    """/obj/geo1: box (size 2, 1, 1) -> wrangle (adds point attribute 'mask'); camera; an erroring wrangle."""
    reset_scene()
    geo = hou.node("/obj").createNode("geo", "geo1")
    box = geo.createNode("box", "box1")
    box.parmTuple("size").set((2, 1, 1))
    wrangle = geo.createNode("attribwrangle", "mask")
    wrangle.setInput(0, box)
    wrangle.parm("snippet").set("f@mask = @P.x > 0;")
    wrangle.setDisplayFlag(True)
    wrangle.setRenderFlag(True)
    bad = geo.createNode("attribwrangle", "broken")
    bad.setInput(0, box)
    bad.parm("snippet").set("@P.y += undefined_fn(1);")
    cam = hou.node("/obj").createNode("cam", "cam1")
    cam.parmTuple("t").set((4, 3, 6))
    cam.parmTuple("r").set((-22, 33, 0))
    cam.parmTuple("res").set((320, 240))
    light = hou.node("/obj").createNode("hlight::2.0", "key") if "hlight::2.0" in hou.objNodeTypeCategory().nodeTypes() else None
    if light is not None:
        light.parmTuple("t").set((3, 5, 4))
    return geo


# --- inspect ---------------------------------------------------------------------------------------------------
@test
def inspect_type():
    r = program("inspect.py", {"type": "Sop/box"})
    EVIDENCE["inspect_type"] = r
    t = r["type"]
    check(r["houdini"]["version"] == hou.applicationVersionString(), "version")
    check(t["type"] == "Sop/box" and t["inputs"] == {"min": 0, "max": 1}, t)
    check(t["doc"] == "https://www.sidefx.com/docs/houdini%d.%d/nodes/sop/box.html" % hou.applicationVersion()[:2], t["doc"])
    size = next(p for p in t["parm_templates"] if p["name"] == "size")
    check(size["parms"] == ["sizex", "sizey", "sizez"], size)
    kind = next(p for p in t["parm_templates"] if p["name"] == "type")
    check(isinstance(kind["menu"], list) and kind["menu"], kind)
    r = program("inspect.py", {"type": "boxx"})
    check("error" in r["type"] and any(c.endswith("/box") for c in r["type"]["closest"]), r["type"])
    r = program("inspect.py", {"type": "box", "category": "Sop", "match": "size"})
    check([p["name"] for p in r["type"]["parm_templates"]] == ["size"], r["type"]["parm_templates"])
    labs = [n for n in hou.sopNodeTypeCategory().nodeTypes() if n.startswith("labs::")]
    if labs:
        r = program("inspect.py", {"type": "Sop/" + labs[0]})
        EVIDENCE["inspect_hda"] = {k: v for k, v in r["type"].items() if k != "parm_templates"}
        check(r["type"]["hda"]["library"].lower().endswith(".hda"), r["type"].get("hda"))
        check("labs--" in r["type"]["doc"], r["type"]["doc"])


@test
def inspect_node():
    sop_scene()
    r = program("inspect.py", {"node": "/obj/geo1/mask"})
    EVIDENCE["inspect_node_sop"] = r
    n = r["node"]
    check(n["geometry"]["points"] == 8 and n["geometry"]["prims"] == 6, n["geometry"])
    check(any(a.startswith("mask:") for a in n["geometry"]["attributes"]["point"]), n["geometry"]["attributes"])
    check(n["geometry"]["bbox"]["size"] == [2.0, 1.0, 1.0], n["geometry"]["bbox"])
    check(any(p["name"] == "snippet" for p in n["parms_changed"]), n["parms_changed"])
    check(n["inputs"][0]["from"] == "/obj/geo1/box1", n["inputs"])
    hou.node("/obj/geo1/broken").cook(force=False) if False else None
    r = program("inspect.py", {"node": "/obj/geo1/broken"})
    check(r["node"].get("errors"), r["node"])
    r = program("inspect.py", {"node": "/obj/geo1"})
    check(r["node"]["display_node"] == "/obj/geo1/mask" and len(r["node"]["children"]) == 3, r["node"])
    stage = hou.node("/stage")
    cube = stage.createNode("cube", "cube1")
    r = program("inspect.py", {"node": cube.path()})
    EVIDENCE["inspect_node_lop"] = r
    check(any(p["path"] == "/cube1" and p["type"] == "Cube" for p in r["node"]["stage"]["listed"]), r["node"]["stage"])
    r = program("inspect.py", {"node": "/obj/geo1/maks"})
    check("mask" in r["node"]["closest"], r["node"])


@test
def inspect_hou_and_search():
    r = program("inspect.py", {"hou": "hou.OpNode.createNode"})
    check("createNode(self, node_type_name" in r["hou"]["doc"], r["hou"]["doc"][:200])
    check(r["hou"]["url"].endswith("hom/hou/OpNode.html#createNode"), r["hou"]["url"])
    r = program("inspect.py", {"hou": "hou.Node.parm"})
    check("hou.OpNode.parm" in r["hou"].get("defined_on", []), r["hou"])
    started = time.time()
    r = program("inspect.py", {"search": "scatter", "limit": 15})
    first = time.time() - started
    EVIDENCE["inspect_search"] = r
    paths = [h["path"] for h in r["search"]["results"]]
    check(any(p.startswith("Sop/scatter") for p in paths[:5]), paths)
    r = program("inspect.py", {"search": "scatter", "category": "Lop", "limit": 5})
    check(all(h["path"].startswith("Lop/") for h in r["search"]["results"] if h["kind"] == "node type"), r["search"]["results"])
    started = time.time()
    r = program("inspect.py", {"search": "sizex"})
    check(any(h["kind"] == "parm" and h["path"] == "sizex" for h in r["search"]["results"]), r["search"]["results"][:5])
    EVIDENCE["inspect_search_seconds"] = {"first": round(first, 2), "cached": round(time.time() - started, 2)}
    r = program("inspect.py", {"search": "create node child"})
    check(any(h["path"] in ("hou.Node.createNode", "hou.OpNode.createNode") for h in r["search"]["results"]), [h["path"] for h in r["search"]["results"]])


# --- hints -----------------------------------------------------------------------------------------------------
@test
def hint_node_type():
    sop_scene()
    hint, error = hint_of("geo = hou.node('/obj/geo1')\ngeo.createNode('boxx')\n")
    EVIDENCE["hint_node_type"] = [error, hint]
    check(hint and "box" in hint and "/obj/geo1" in hint, hint)
    hint, _ = hint_of("hou.node('/obj').createNode('box')\n")
    EVIDENCE["hint_wrong_category"] = hint
    check(hint and "Sop" in hint and "geo" in hint, hint)


@test
def hint_parm():
    sop_scene()
    hint, error = hint_of("box = hou.node('/obj/geo1/box1')\nbox.parm('sizexx').set(2)\n")
    EVIDENCE["hint_parm"] = [error, hint]
    check(hint and "sizex" in hint and "sizexx" in hint, hint)
    hint, _ = hint_of("hou.node('/obj/geo1/box1').parm('size').set(2)\n")
    EVIDENCE["hint_parm_tuple"] = hint
    check(hint and "parmTuple('size')" in hint, hint)
    hint, _ = hint_of("hou.node('/obj/geo1/boxx').parm('sizex').set(2)\n")
    EVIDENCE["hint_missing_node"] = hint
    check(hint and "box1" in hint, hint)


@test
def hint_cook_type_attribute():
    sop_scene()
    hint, error = hint_of("hou.node('/obj/geo1/broken').cook(force=True)\n")
    EVIDENCE["hint_cook"] = [error, hint]
    check(hint and "undefined_fn" in hint, hint)
    hint, error = hint_of("hou.node('/obj/geo1').createNode(12)\n")
    EVIDENCE["hint_type_error"] = [error, hint]
    check(hint and "createNode(" in hint, hint)
    hint, error = hint_of("hou.node('/obj/geo1/box1').parmm('sizex')\n")
    EVIDENCE["hint_attribute"] = [error, hint]
    check(hint and "parm" in hint, hint)
    hint, _ = hint_of("hou.nodee('/obj')\n")
    check(hint and "node" in hint, hint)


# --- verify ----------------------------------------------------------------------------------------------------
def fingerprint():
    nodes = hou.node("/").allSubChildren(recurse_in_locked_nodes=False)
    return sorted("%s|%s" % (n.path(), n.type().nameWithCategory()) for n in nodes), hou.frame(), hou.hipFile.path()


def verify_scene():
    geo = sop_scene()
    geo.node("box1").parm("ty").setExpression("$F")
    bad = hou.node("/obj").createNode("geo", "bad")
    broken = bad.createNode("attribwrangle", "broken")
    broken.setInput(0, bad.createNode("sphere"))
    broken.parm("snippet").set("@P.y += undefined_fn(1);")
    broken.setDisplayFlag(True)
    hou.node("/stage").createNode("cube", "cube1")
    hou.setFrame(1)


CRITERIA = [
    {"expr": "npoints('/obj/geo1')", "op": "==", "value": 8, "label": "box points"},
    {"expr": "bbox('/obj/geo1/box1').size", "op": "approx", "value": [2, 1, 1], "tol": 1e-5},
    {"expr": "attrib_names('/obj/geo1')", "op": "contains", "value": "mask"},
    {"expr": "sum(attrib_values('/obj/geo1', 'mask'))", "op": "==", "value": 4},
    {"expr": "parm('/obj/geo1/box1/sizex')", "op": "==", "value": 2},
    {"expr": "parm('/obj/geo1/box1/size')", "op": "==", "value": [2, 1, 1]},
    {"expr": "bbox('/obj/geo1/box1').center[1]", "op": "approx", "value": 10, "frame": 10, "label": "ty follows $F at frame 10"},
    {"expr": "usd_prims('/stage/cube1', 'Cube')", "op": "contains", "value": "/cube1"},
    {"expr": "usd_attr('/stage/cube1', '/cube1', 'size')", "op": "approx", "value": 2},
    {"expr": "nprims('/obj/geo1')", "op": ">", "value": 100, "label": "deliberately failing"},
    {"expr": "npoints('/obj/nothing')", "op": "==", "value": 0, "label": "missing node"},
]


@test
def verify_criteria_and_errors():
    verify_scene()
    before = fingerprint()
    r = program("verify.py", {"criteria": CRITERIA})
    EVIDENCE["verify"] = r
    check(fingerprint() == before, "the scene or frame changed")
    by = {c["label"]: c for c in r["criteria"]}
    check(r["passed"] == len(CRITERIA) - 2 and r["failed"] == 2, [(c["label"], c["passed"], c.get("measured"), c.get("error")) for c in r["criteria"]])
    check(by["deliberately failing"]["measured"] == 6 and "no node" in by["missing node"]["error"], by)
    check(by["ty follows $F at frame 10"]["passed"] and hou.frame() == 1, by["ty follows $F at frame 10"])
    broken = [e for e in r["errors"] if e["node"] == "/obj/bad/broken"]
    check(broken and "undefined_fn" in broken[0]["errors"][0], r["errors"])
    check(r["houdini"]["license"] and r["facts"]["error_scan"]["nodes_with_errors"] >= 1, r["facts"])
    message = failing("verify.py", {"criteria": [{"expr": "hou.node('/obj').createNode('geo')", "op": "==", "value": 1}]})
    check("not a read" in message, message)
    message = failing("verify.py", {"criteria": [{"expr": "hou.hipFile.save()", "op": "==", "value": 1}]})
    check("not a read" in message, message)


@test
def verify_render():
    verify_scene()
    before = fingerprint()
    started = time.time()
    r = program("verify.py", {"errors": False, "render": {"size": 160}})
    karma_s = time.time() - started
    EVIDENCE["verify_render_karma"] = dict(r, image_base64=len(r.get("image_base64") or ""))
    check(fingerprint() == before, "the scene changed")
    image = r["facts"]["render"]["image"]
    check(r.get("image_base64") and image["size"][0] == 160 and not image["black"], r["facts"]["render"])
    started = time.time()
    try:
        r = program("verify.py", {"errors": False, "render": {"size": 160, "renderer": "opengl"}})
        EVIDENCE["verify_render_opengl"] = dict(r, image_base64=len(r.get("image_base64") or ""))
        check(r.get("image_base64"), r["facts"])
    except Exception as exc:  # noqa: BLE001
        EVIDENCE["verify_render_opengl"] = {"failed": str(exc)[:2000]}
    EVIDENCE["verify_render_seconds"] = {"karma": round(karma_s, 1), "opengl": round(time.time() - started, 1)}


# --- save and verify saved -------------------------------------------------------------------------------------
@test
def save_and_verify_saved():
    verify_scene()
    message = failing("save.py", {})
    check("never saved" in message, message)
    first = os.path.join(WORK, "shot.hipnc")
    r = program("save.py", {"path": first})
    EVIDENCE["save"] = r
    check(os.path.normcase(r["open_file"]) == os.path.normcase(first) and r["bytes"] > 1000, r)
    copy = os.path.join(WORK, "copies", "shot_copy.hipnc")
    r = program("save.py", {"path": copy, "copy": True})
    EVIDENCE["save_copy"] = r
    check(os.path.isfile(copy) and os.path.normcase(r["open_file"]) == os.path.normcase(first), r)
    message = failing("save.py", {"path": copy, "copy": True})
    check("overwrite" in message, message)
    plain = os.path.join(WORK, "plain.hip")
    r = program("save.py", {"path": plain, "copy": True})
    EVIDENCE["save_hip_extension"] = r
    check(os.path.isfile(plain), r)
    if hou.isApprentice():
        check("non-commercial" in r.get("license_note", ""), r)
    saved_children = len(hou.node("/obj").children())
    r = program("save.py", {})
    check(os.path.normcase(r["saved"]) == os.path.normcase(first), r)
    hou.node("/obj").createNode("geo", "unsaved")  # in the open scene only
    criteria = [{"expr": "len(node('/obj').children())", "op": "==", "value": saved_children, "label": "objects in the file"},
                {"expr": "npoints('/obj/geo1')", "op": "==", "value": 8}]
    live = program("verify.py", {"criteria": criteria, "errors": False})
    check(live["failed"] == 1, live["criteria"])
    r, steps, start_s = verify_saved({"saved": True, "criteria": criteria})
    EVIDENCE["verify_saved"] = r
    check(r["all_passed"] and r["read"]["file"] and r["read"]["bytes"] > 1000, r)
    check(any(e["node"] == "/obj/bad/broken" for e in r["errors"]), r["errors"])
    # Started, not waited for, on the main thread; its log says each step.
    check(start_s < 3, start_s)
    check([s["step"] for s in steps][:4] == ["load", "loaded", "criterion", "criterion"] and steps[-1]["step"] == "done", steps)
    r2, _, _ = verify_saved({"saved": copy, "criteria": criteria[:1], "render": {"size": 128}})
    EVIDENCE["verify_saved_render"] = dict(r2, image_base64=len(r2.get("image_base64") or ""))
    check(r2["all_passed"] and r2.get("image_base64"), r2)
    EVIDENCE["verify_saved_seconds"] = {"criteria_and_errors": r["read"]["seconds"], "with_render": r2["read"]["seconds"]}


@test
def verify_saved_says_what_was_read_from_cache():
    # Sol ex03 retrial: a Vellum scene with its 48 cached frames. verify saved says which geometry it read from the
    # cache files and which it cooked (a solver read directly simulates), and its error scan cooks no solver inside.
    reset_scene()
    geo = hou.node("/obj").createNode("geo", "sim")
    grid = geo.createNode("grid")
    solver = geo.createNode("solver", "solver1")
    solver.setInput(0, grid)
    cached = geo.createNode("file", "cached")
    cached.parm("file").set("$HIP/cache/sim.$F4.bgeo.sc")
    cached.setDisplayFlag(True)
    cached.setRenderFlag(True)
    hip = os.path.join(WORK, "sim", "sim.hipnc")
    os.makedirs(os.path.dirname(hip), exist_ok=True)
    hou.hipFile.save(hip)
    rop = hou.node("/out").createNode("geometry", "cache")
    rop.parm("soppath").set(solver.path())
    rop.parm("sopoutput").set("$HIP/cache/sim.$F4.bgeo.sc")
    rop.parm("trange").set(1)
    rop.parmTuple("f").set((1, 5, 1))
    rop.render()
    hou.hipFile.save(hip)
    criteria = [
        {"label": "5 cached frames", "expr": "[len(node('/obj/sim/cached').geometryAtFrame(f).points()) for f in range(1, 6)]",
         "op": "==", "value": [100] * 5},
        {"label": "the solver itself", "expr": "npoints('/obj/sim/solver1')", "op": "==", "value": 100, "frame": 3},
    ]
    r, steps, _ = verify_saved({"saved": True, "criteria": criteria, "frame": 5, "roots": ["/obj/sim"]})
    EVIDENCE["verify_saved_read"] = {"read": r["facts"].get("read"), "cooked": r["facts"]["error_scan"]["cooked"],
                                     "criteria": [(c["label"], c["passed"], c["seconds"]) for c in r["criteria"]]}
    check(r["all_passed"], r["criteria"])
    read = {entry["node"]: entry for entry in r["facts"]["read"]}
    check(read["/obj/sim/cached"]["from"] == "files on disk" and read["/obj/sim/cached"]["files"] == "$HIP/cache/sim.$F4.bgeo.sc", read)
    check(read["/obj/sim/solver1"]["from"].startswith("cooked: simulated"), read)
    check(all(c["seconds"] >= 0 for c in r["criteria"]), r["criteria"])
    check(not any(p.startswith("/obj/sim/solver1/") for p in r["facts"]["error_scan"]["cooked"]), r["facts"]["error_scan"])
    check([s.get("label") for s in steps if s["step"] == "criterion"] == ["5 cached frames", "the solver itself"], steps)


# --- render and PDG --------------------------------------------------------------------------------------------
def wait_job(started, timeout=600):
    """Follow the log as the plugin's task tracker does: markers, then the process's end."""
    deadline = time.time() + timeout
    marks = []
    while time.time() < deadline:
        with open(started["log"], encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        marks = [l for l in lines if l.startswith("OPENCODE_")]
        if any(l.startswith(("OPENCODE_DONE ", "OPENCODE_FAILED ")) for l in marks):
            break
        time.sleep(0.5)
    parsed = [(l.split(" ", 1)[0][9:], json.loads(l.split(" ", 1)[1])) for l in marks]
    return parsed


def render_scene():
    verify_scene()
    hou.node("/obj/bad").destroy()
    karma = hou.node("/out").createNode("karma", "karma1")
    karma.parm("camera").set("/obj/cam1")
    karma.parm("samplesperpixel").set(2)
    gl = hou.node("/out").createNode("opengl", "gl1")
    gl.parm("camera").set("/obj/cam1")
    top = hou.node("/obj").createNode("topnet", "tasks")
    wedge = top.createNode("wedge", "wedge1")
    wedge.parm("wedgecount").set(3)
    script = top.createNode("pythonscript", "write")
    script.setInput(0, wedge)
    folder = os.path.join(WORK, "pdg").replace("\\", "/")
    script.parm("script").set(
        "import os\nos.makedirs(%r, exist_ok=True)\npath = %r + '/item_%%d.txt' %% work_item.index\n"
        "open(path, 'w').write('ok')\nwork_item.addResultData(path, 'file/text', 0)\n" % (folder, folder))
    script.setDisplayFlag(True)
    hou.hipFile.save(os.path.join(WORK, "render_scene.hipnc").replace("\\", "/"))
    return folder


@test
def render_karma_frames():
    render_scene()
    name = hou.hipFile.path()
    output = os.path.join(WORK, "out", "shot_####.png")
    started = time.time()
    r = program("render.py", {"rop": "/out/karma1", "output": output, "frames": [1, 5], "size": [160, 120]}, job="render_job.py")
    returned = time.time() - started
    EVIDENCE["render_karma_start"] = r
    check(hou.hipFile.path() == name, "the open file name changed")
    check(r["kind"] == "render" and len(r["targets"]) == 5 and r["targets"][0].endswith("shot_0001.png"), r)
    marks = wait_job(r)
    EVIDENCE["render_karma_marks"] = marks
    EVIDENCE["render_karma_seconds"] = {"returned": round(returned, 2), "finished": round(time.time() - started, 1)}
    kinds = [k for k, _ in marks]
    check(kinds[0] == "RENDER" and kinds[-1] == "DONE", marks)
    check([v["done"] for k, v in marks if k == "PROGRESS"][-1] == 5, marks)
    check(all(os.path.isfile(t) for t in r["targets"]), r["targets"])
    from PIL import Image
    check(Image.open(r["targets"][0]).size == (160, 120), "size not applied")
    message = failing("render.py", {"rop": "/out/karma1", "output": output, "frames": [1, 3]}, job="render_job.py")
    check("overwrite" in message, message)


@test
def render_apprentice_limits():
    """What the license really allows: a 4K Karma still, its written size, and the watermark corner."""
    render_scene()
    output = os.path.join(WORK, "big", "big.png")
    r = program("render.py", {"rop": "/out/karma1", "output": output, "frames": 1, "size": [3840, 2160]}, job="render_job.py")
    marks = wait_job(r)
    check(marks[-1][0] == "DONE", marks)
    if hou.isApprentice():
        check(marks[-1][1].get("size_note") and marks[-1][1]["image_size"] == [1920, 1080], marks[-1])
    from PIL import Image
    image = Image.open(output).convert("RGB")
    w, h = image.size
    px = image.load()
    lit = [(x, y) for y in range(int(h * 0.8), h, 2) for x in range(int(w * 0.6), w, 2) if sum(px[x, y]) > 60]
    EVIDENCE["apprentice_limits"] = {"license": str(hou.licenseCategory()), "asked": [3840, 2160], "written": [w, h],
                                     "corner_lit_box": [min(p[0] for p in lit), min(p[1] for p in lit), max(p[0] for p in lit), max(p[1] for p in lit)] if lit else None}


@test
def render_opengl():
    render_scene()
    output = os.path.join(WORK, "gl", "gl_####.png")
    r = program("render.py", {"rop": "/out/gl1", "output": output, "frames": [1, 2], "size": [160, 120]}, job="render_job.py")
    marks = wait_job(r)
    EVIDENCE["render_opengl_marks"] = marks
    check(marks and marks[-1][0] in ("DONE", "FAILED"), marks)
    EVIDENCE["render_opengl_files"] = [os.path.isfile(t) for t in r["targets"]]


@test
def render_opengl_without_hq_lighting():
    # Sol ex07: an OpenGL ROP with High Quality Lighting off crashed its render hython (and, rendered in the open
    # Houdini, Houdini itself) in the Vulkan renderer. The start says so; the job's log holds the crash.
    render_scene()
    hou.node("/out/gl1").parm("hqlighting").set(0)
    output = os.path.join(WORK, "gl_nohq", "frame.png")
    r = program("render.py", {"rop": "/out/gl1", "output": output, "frames": 1}, job="render_job.py")
    EVIDENCE["render_opengl_nohq_start"] = r.get("facts")
    check("hqlighting" in r["facts"].get("crash_risk", ""), r["facts"])
    deadline = time.time() + 120
    text = ""
    while time.time() < deadline and "TRACEBACK END" not in text and "OPENCODE_DONE" not in text:
        time.sleep(0.5)
        with open(r["log"], encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    current = text[text.find("CURRENT THREAD"):] if "CURRENT THREAD" in text else ""
    EVIDENCE["render_opengl_nohq_crash"] = [line.split("[")[1].split("]")[0] for line in current.splitlines() if "[" in line][:4]
    check("Segmentation fault" in text, text[-600:])
    hou.node("/out/gl1").parm("hqlighting").set(1)
    r = program("render.py", {"rop": "/out/gl1", "output": os.path.join(WORK, "gl_hq", "frame.png"), "frames": 1},
                job="render_job.py")
    check("crash_risk" not in r["facts"], r["facts"])
    check(wait_job(r)[-1][0] == "DONE", "the same ROP with High Quality Lighting on")


@test
def render_pdg():
    folder = render_scene()
    r = program("render.py", {"top": "/obj/tasks"}, job="render_job.py")
    EVIDENCE["render_pdg_start"] = r
    check(r["kind"] == "pdg" and r["node"] == "/obj/tasks/write", r)
    marks = wait_job(r)
    EVIDENCE["render_pdg_marks"] = marks
    check(marks[-1][0] == "DONE", marks)
    check(sorted(os.listdir(folder)) == ["item_0.txt", "item_1.txt", "item_2.txt"], os.listdir(folder) if os.path.isdir(folder) else folder)
    message = failing("render.py", {"rop": "/obj/geo1"}, job="render_job.py")
    check("not a ROP" in message, message)


# --- the installed part itself: Host, its listener and execute_code over the wire, in a real Houdini -----------

def served():
    """A Host and its listener (never offered: OPENCODE_HOUDINI_OFFER is not involved, nothing is advertised)."""
    from opencode_houdini.host import Host, HostServer  # noqa: PLC0415

    reset_scene()
    host = Host()
    return host, HostServer(host)


def ask(server, method, params):
    """One request over the wire, as the plugin sends it (from any thread)."""
    import socket  # noqa: PLC0415

    with socket.create_connection(("127.0.0.1", server.port), timeout=120) as client:
        request = {"id": "t", "token": server.token, "method": method, "params": params}
        client.sendall((json.dumps(request) + "\n").encode())
        with client.makefile("rb") as response:
            return json.loads(response.readline())


def call(server, method, params):
    """A request from another thread while this, the main thread, serves it (hython's pump)."""
    import threading  # noqa: PLC0415

    reply = []
    worker = threading.Thread(target=lambda: reply.append(ask(server, method, params)))
    worker.start()
    server.host.ui.pump(120, stop=lambda: not worker.is_alive())
    worker.join()
    return reply[0]


@test
def host_execute_code_reloading_the_open_file():
    # Sol ex03 retrial: execute_code loaded the open (saved) file again and moved the frame; the main thread stayed
    # busy for 14 minutes and every later call, houdini_verify saved: true included, waited behind it.
    host, server = served()
    try:
        geo = hou.node("/obj").createNode("geo", "reloaded")
        geo.createNode("box").setDisplayFlag(True)
        hip = os.path.join(WORK, "reload", "reload.hipnc").replace("\\", "/")
        os.makedirs(os.path.dirname(hip), exist_ok=True)
        hou.hipFile.save(hip)
        handle = call(server, "hello", {})["result"]["script_handle"]
        started = time.time()
        reply = call(server, "execute_code", {"script_handle": handle, "code":
                     "hou.hipFile.load(hou.hipFile.path(), suppress_save_prompt=True)\nhou.setFrame(1)\nhou.setFrame(4)\n"
                     "result = {'path': hou.hipFile.path(), 'frame': hou.frame()}"})
        EVIDENCE["host_reload"] = {"seconds": round(time.time() - started, 1), "reply": reply}
        check(time.time() - started < 30, EVIDENCE["host_reload"])
        check("result" in reply, reply)
    finally:
        server.close()
        host.close()


@test
def host_execute_code_undo_and_hints():
    host, server = served()
    try:
        handle = call(server, "hello", {})["result"]["script_handle"]
        hello = call(server, "hello", {})["result"]
        check(hello["process"]["pid"] == os.getpid() and hello["process"]["temp_dir"], hello["process"])
        check({"execute_code", "status", "release"} <= set(hello["capabilities"]["methods"]), hello["capabilities"])
        ok = call(server, "execute_code", {"script_handle": handle, "code":
                  "geo = hou.node('/obj').createNode('geo', 'kept')\ngeo.createNode('box')\nresult = {'path': geo.path()}"})["result"]
        EVIDENCE["host_execute_ok"] = ok
        check(ok["status"] == "executed" and ok["changed"] and ok["undo_step"] == "OpenCode: code", ok)
        # A run that changes the scene, then fails: undone, the graph as it was.
        failed = call(server, "execute_code", {"script_handle": handle, "code":
                      "g = hou.node('/obj').createNode('geo', 'lost')\ng.createNode('sphere')\nraise ValueError('late')"})["result"]
        EVIDENCE["host_execute_failed"] = failed
        check(failed["status"] == "failed" and failed["restored"] is True, failed)
        check(hou.node("/obj/lost") is None and hou.node("/obj/kept") is not None, [n.name() for n in hou.node("/obj").children()])
        check(not host.state.quarantined, host.state.quarantine)
        # Houdini's own knowledge in the failure.
        typo = call(server, "execute_code", {"script_handle": handle, "code": "hou.node('/obj/kept').createNode('boxx')"})["result"]
        EVIDENCE["host_hint_type"] = typo.get("hint")
        check("closest box" in (typo.get("hint") or ""), typo)
        parm = call(server, "execute_code", {"script_handle": handle, "code": "hou.node('/obj/kept/box1').parm('sizexx').set(2)"})["result"]
        EVIDENCE["host_hint_parm"] = parm.get("hint")
        check("sizex" in (parm.get("hint") or ""), parm)
        # A read-only program run (inspect) changes nothing and asks for no undo group.
        text = "import json\nPARAMS = json.loads(%s)\n%s" % (json.dumps(json.dumps({"node": "/obj/kept/box1"})), source("inspect.py"))
        inspected = call(server, "execute_code", {"script_handle": handle, "code": text, "read_only": True})["result"]
        check(inspected["status"] == "executed" and inspected["changed"] is False and "undo_step" not in inspected, {k: v for k, v in inspected.items() if k != "result"})
    finally:
        server.close()
        host.close()


@test
def host_status_off_the_main_thread_and_copies_are_not_saves():
    import threading  # noqa: PLC0415

    host, server = served()
    try:
        handle = call(server, "hello", {})["result"]["script_handle"]
        box = os.path.join(WORK, "status.hipnc")
        call(server, "execute_code", {"script_handle": handle, "code":
             "hou.node('/obj').createNode('geo').createNode('box')\nhou.hipFile.save(%r)" % box})
        handle = call(server, "hello", {})["result"]["script_handle"]
        done, seen = [], []
        worker = threading.Thread(target=lambda: done.append(ask(server, "execute_code", {
            "script_handle": handle, "code": "import time\ntime.sleep(2)", "read_only": True})))
        asker = threading.Thread(target=lambda: (time.sleep(0.6), seen.append(ask(server, "status", {})["result"])))
        worker.start()
        asker.start()
        host.ui.pump(10, stop=lambda: not worker.is_alive() and not asker.is_alive())
        status = seen[0]
        EVIDENCE["host_status"] = status
        check(status["busy"] is True and status["running"] == "execute_code", status)
        check(done and done[0]["result"]["status"] == "executed", done)
        # The save program's copy (mwrite -n) fires Houdini's save events: neither a save nor a change.
        handle = call(server, "hello", {})["result"]["script_handle"]
        copy = os.path.join(WORK, "status_copy.hipnc")
        text = "import json\nPARAMS = json.loads(%s)\n%s" % (json.dumps(json.dumps({"path": copy, "copy": True})), source("save.py"))
        copied = call(server, "execute_code", {"script_handle": handle, "code": text, "read_only": True})["result"]
        EVIDENCE["host_copy"] = {k: v for k, v in copied.items() if k not in ("stdout", "stderr")}
        check(copied["status"] == "executed" and os.path.isfile(copy), copied)
        check("saved" not in copied and copied["changed"] is False and "warnings" not in copied, copied)
        # A real save of the open file is one.
        saved = call(server, "execute_code", {"script_handle": handle, "code": "import time\ntime.sleep(1.1)\nhou.hipFile.save()"})["result"]
        check(saved.get("saved") is True, saved)
    finally:
        server.close()
        host.close()


def main():
    names = set(sys.argv[1:])
    started = time.time()
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
        print("%-28s %-4s %6.1fs %s" % (name, status, seconds, ("\n" + detail) if detail else ""))
    folder = os.environ.get("OPENCODE_HOUDINI_EVIDENCE") or ROOT
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "programs_hython.json"), "w", encoding="utf-8") as fh:
        json.dump({"houdini": hou.applicationVersionString(), "license": str(hou.licenseCategory()),
                   "results": [r[:3] for r in RESULTS], "evidence": EVIDENCE}, fh, indent=1, default=repr)
    failed = [r for r in RESULTS if r[1] != "ok"]
    print("%d passed, %d failed in %.1f s; evidence in %s" % (len(RESULTS) - len(failed), len(failed), time.time() - started, folder))
    print("OPENCODE_TESTS " + json.dumps({"passed": len(RESULTS) - len(failed), "failed": len(failed)}))
    shutil.rmtree(WORK, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)  # hython can hang on exit after renders; the results are written
