# SPDX-License-Identifier: MIT
# gaffer_verify (fixed program): check a result by measurement on the open script. criteria: the agent's own
# acceptance checks, read-only expressions measured on computed scenes, images and plug values (at a frame, through
# a Gaffer.Context: the script's frame does not move), each passed or failed with its measured value. errors: the
# outputs of every node under the roots computed (scene hierarchy bounded, images' format and a tile, values), with
# the Gaffer.ProcessException each raised and the plug it arose in. render: a low-resolution Cycles image made by a
# separate background `gaffer python` on a working copy (a crash there never touches this Gaffer), with facts read
# from its pixels. Nothing in the open script is changed.
# PARAMS is prepended by the plugin: {"criteria"?, "frame"?, "errors"?, "roots"?, "render"?, "timeout_s"?}
# The namespace holds Gaffer, GafferScene, GafferImage, GafferDispatch, IECore, imath, script and application.
import ast
import json
import math
import os
import re
import subprocess
import tempfile
import time

import Gaffer
import GafferDispatch
import GafferImage
import GafferScene
import IECore
import imath

P = PARAMS  # noqa: F821
S = script  # noqa: F821
STARTED = time.time()
CRITERIA = P.get("criteria") or []
FRAME = float(P["frame"]) if P.get("frame") is not None else S.context().getFrame()
COMPUTE_BUDGET = 60.0  # seconds spent computing outputs for the error scan, at most
ERRORS_MAX = 60
NODES_MAX = 20000
LOCATIONS_MAX = 2000  # scene locations read per scene output in the error scan
OPS = ("<", "<=", ">", ">=", "==", "!=", "approx", "between", "in", "contains")
THROWAWAY = os.environ.get("OPENCODE_GAFFER_ROLE") in ("verify", "render")
# gaffer_verify saved: the background job defines STEP, a line in its log saying what it does now (the plugin reads
# it to say where a verify that ran out of time stopped). In the open script: nothing.
STEP = globals().get("STEP") or (lambda **what: None)
if not isinstance(CRITERIA, list) or len(CRITERIA) > 50:
    raise ValueError("criteria: a list of at most 50 {expr, op, value, tol?, frame?, label?}")

SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in (
    "abs", "min", "max", "len", "round", "sum", "any", "all", "sorted", "int", "float", "bool", "str", "list",
    "tuple", "set", "dict", "range", "zip", "enumerate", "isinstance", "map", "filter", "reversed")}
# Attribute names that change something (Gaffer, IECore, imath): refused in a criterion.
WRITES = re.compile(r"^(set|create|destroy|delete|remove|insert|append|add|clear|save|load|merge|render|press|move|"
                    r"copy|paste|lock|unlock|allow|bypass|execute|run|revert|undo|redo|define|override|export|"
                    r"reload|mute|unmute|change|rename|layout|cut|write|open|close|install|uninstall|exit|system|"
                    r"dirty|force|update|apply|bake|expand|collapse|hide|show|display|dispatch|serialise|serialize|"
                    r"connect|disconnect|import|acquire|release|register|deregister|emit|push|pop)(?![a-z])")
READS = {"set", "setNames", "setHash", "displayWindow", "displayWindowHash"}
BLOCKED = {"application", "undoStack", "selection", "focus", "system", "exit"}


def read_only_expression(text):
    """An expression that reads: no assignment, no lambda, no private name, no attribute that changes things."""
    tree = ast.parse(text, mode="eval")
    for n in ast.walk(tree):
        if isinstance(n, (ast.NamedExpr, ast.Lambda)):
            raise ValueError("criteria: %r is not a plain read" % text)
        if isinstance(n, ast.Attribute) and (n.attr.startswith("_") or n.attr in BLOCKED or (WRITES.match(n.attr[:1].lower() + n.attr[1:]) and n.attr not in READS)):
            raise ValueError("criteria: %r reaches %s, which is not a read" % (text, n.attr))
        if isinstance(n, ast.Name) and (n.id.startswith("_") or n.id in BLOCKED):
            raise ValueError("criteria: %r names %s" % (text, n.id))
    return compile(tree, "<criterion>", "eval")


class Box(object):
    """Bounds: min, max, size, center (x, y, z tuples)."""

    def __init__(self, box):
        self.min = tuple(box.min())
        self.max = tuple(box.max())
        self.size = tuple(box.size())
        self.center = tuple(box.center())

    def __repr__(self):
        return "Box(min=%s, max=%s)" % (rounded(self.min), rounded(self.max))


def resolve(path):
    text = str(path).strip().strip("/").replace("/", ".")
    return S.descendant(text) if text else None


def need(path):
    n = resolve(path)
    if n is None:
        raise ValueError("no node or plug %r in the script" % path)
    return n


def node_of(path):
    n = need(path)
    if not isinstance(n, Gaffer.Node):
        raise ValueError("%s is a %s, not a node" % (path, type(n).__name__))
    return n


def scene_of(target):
    """A ScenePlug: the plug itself, a node's out, or a Render node's in."""
    if isinstance(target, GafferScene.ScenePlug):
        return target
    n = need(target) if isinstance(target, str) else target
    if isinstance(n, GafferScene.ScenePlug):
        return n
    for name in ("out", "in"):
        if isinstance(n, Gaffer.Node) and name in n and isinstance(n[name], GafferScene.ScenePlug):
            return n[name]
    raise ValueError("%s has no scene output" % (target if isinstance(target, str) else target.relativeName(S)))


def image_of(target):
    n = need(target) if isinstance(target, str) else target
    if isinstance(n, GafferImage.ImagePlug):
        return n
    for name in ("out", "in"):
        if isinstance(n, Gaffer.Node) and name in n and isinstance(n[name], GafferImage.ImagePlug):
            return n[name]
    raise ValueError("%s has no image output" % (target if isinstance(target, str) else target.relativeName(S)))


def plain_data(v):
    if isinstance(v, IECore.Data) and hasattr(v, "value"):
        return v.value
    if isinstance(v, (IECore.CompoundData, IECore.CompoundObject)):
        return {str(k): plain_data(v[k]) for k in v.keys()}
    return v


def world_bound(scene, location):
    box, m = scene.bound(location), scene.fullTransform(location)
    if box.isEmpty():
        return Box(box)
    lo, hi = box.min(), box.max()
    corners = [imath.V3f(x, y, z) * m for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    world = imath.Box3f()
    for c in corners:
        world.extendBy(c)
    return Box(world)


def image_stats(target):
    image = image_of(target)
    fmt = image.format()
    stats = GafferImage.ImageStats()
    stats["in"].setInput(image)
    stats["area"].setValue(fmt.getDisplayWindow())
    return {"format": (fmt.width(), fmt.height()), "channels": [str(c) for c in image.channelNames()],
            "average": tuple(stats["average"].getValue()), "min": tuple(stats["min"].getValue()), "max": tuple(stats["max"].getValue())}


def namespace(frame):
    def plug(path):
        p = need(path)
        if not isinstance(p, Gaffer.ValuePlug):
            raise ValueError("%s is a %s, not a plug with a value" % (path, type(p).__name__))
        value = p.getValue()
        return Box(value) if isinstance(value, (imath.Box3f, imath.Box2f, imath.Box2i)) else plain_data(value)

    def located(fn):
        return lambda scene, location="/": fn(scene_of(scene), location)

    return {"__builtins__": SAFE_BUILTINS, "Gaffer": Gaffer, "GafferScene": GafferScene, "GafferImage": GafferImage,
            "IECore": IECore, "imath": imath, "math": math, "frame": frame, "script": S, "node": node_of, "plug": plug,
            "scene": scene_of, "exists": located(lambda s, l: s.exists(l)), "bound": located(lambda s, l: Box(s.bound(l))),
            "world_bound": located(world_bound), "translate": located(lambda s, l: tuple(s.fullTransform(l).translation())),
            "attributes": located(lambda s, l: plain_data(s.attributes(l))),
            "child_names": located(lambda s, l: [str(n) for n in s.childNames(l)]),
            "object": located(lambda s, l: s.object(l)), "object_type": located(lambda s, l: s.object(l).typeName()),
            "primitive_variables": located(lambda s, l: sorted(s.object(l).keys()) if hasattr(s.object(l), "keys") else []),
            "set_names": lambda scene: sorted(str(n) for n in scene_of(scene).setNames()),
            "set_members": lambda scene, name: sorted(scene_of(scene).set(name).value.paths()),
            "globals": lambda scene: plain_data(scene_of(scene).globals()), "image": image_stats,
            "errors": lambda path: [e["error"] for e in node_errors(node_of(path))]}


def rounded(value):
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else str(value)
    if isinstance(value, (bool, int, str)) or value is None:
        return value if not isinstance(value, str) or len(value) <= 300 else value[:300] + "..."
    if isinstance(value, Box):
        return {"min": rounded(value.min), "max": rounded(value.max), "size": rounded(value.size)}
    if isinstance(value, (imath.Box3f, imath.Box2f, imath.Box2i)):
        return rounded(Box(value))
    if isinstance(value, Gaffer.GraphComponent):
        return value.relativeName(S) if S.isAncestorOf(value) else value.fullName()
    if isinstance(value, IECore.Object):
        data = plain_data(value)
        return rounded(data) if data is not value else value.typeName()
    if isinstance(value, dict):
        return {str(k): rounded(v) for k, v in list(value.items())[:64]}
    try:
        return [rounded(v) for v in list(value)[:64]]
    except TypeError:
        return repr(value)[:200]


def compare(measured, op, value, tol):
    def numbers(x):
        if isinstance(x, (str, Box, dict)):
            return None
        try:
            return [float(v) for v in x]
        except (TypeError, ValueError):
            return None

    if op == "in":
        return measured in value
    if op == "contains":
        return value in measured
    if op == "between":
        lo, hi = value
        seq = numbers(measured)
        return all(lo <= v <= hi for v in seq) if seq is not None else lo <= measured <= hi
    seq = numbers(measured)
    if seq is not None and (op not in ("==", "!=") or isinstance(value, (list, tuple))):
        targets = numbers(value) if isinstance(value, (list, tuple)) else [value] * len(seq)
        if targets is None or len(targets) != len(seq):
            raise ValueError("value: %d numbers to compare with %s" % (len(seq), rounded(measured)))
        if op == "!=":
            return any(m != t for m, t in zip(seq, targets))
        return all(compare(m, op, t, tol) for m, t in zip(seq, targets))
    if op == "approx":
        return abs(measured - value) <= tol
    return {"<": lambda: measured < value, "<=": lambda: measured <= value, ">": lambda: measured > value,
            ">=": lambda: measured >= value, "==": lambda: measured == value, "!=": lambda: measured != value}[op]()


def at_frame(frame):
    context = Gaffer.Context(S.context())
    context.setFrame(frame)
    return context


# --- the error scan: compute every output, report the ProcessExceptions ------------------------------------------
def error_text(ex):
    """A ProcessException's text without its Python traceback: the plug, then the exception that ended it."""
    text = str(ex)
    if "Traceback (most recent call last)" in text:
        head = text.split(":", 1)[0].strip()
        last = [line for line in text.splitlines() if line.strip()][-1].strip()
        text = "%s : %s" % (head, last)
    return " ".join(text.split())[:400]


def compute(plug):
    if isinstance(plug, GafferScene.ScenePlug):
        plug.globals()
        names = list(plug.setNames())
        for name in names[:40]:
            plug.set(name)
        queue, read = ["/"], 0
        while queue and read < LOCATIONS_MAX:
            location = queue.pop(0)
            read += 1
            plug.bound(location)
            if location != "/":
                plug.transform(location)
                plug.attributes(location)
                plug.object(location)
            queue += [location.rstrip("/") + "/" + str(n) for n in plug.childNames(location)]
        return
    if isinstance(plug, GafferImage.ImagePlug):
        plug.format()
        window = plug.dataWindow()
        channels = plug.channelNames()
        plug.metadata()
        if channels and not GafferImage.BufferAlgo.empty(window):
            plug.channelData(channels[0], GafferImage.ImagePlug.tileOrigin(window.min()))
        return
    if isinstance(plug, GafferDispatch.TaskNode.TaskPlug):
        return
    # A compound ValuePlug has no value of its own: its children do.
    if isinstance(plug, Gaffer.ValuePlug) and hasattr(plug, "getValue"):
        plug.getValue()
        return
    for child in plug.children(Gaffer.Plug):
        compute(child)


def output_plugs(node):
    # A filter's output is only computed for a scene location, inside the node it filters.
    return [p for p in node.children(Gaffer.Plug) if p.direction() == Gaffer.Plug.Direction.Out
            and not p.getName().startswith("__") and not isinstance(p, GafferScene.FilterPlug)]


NEEDS_CONTEXT = []  # outputs computed only within a context the scan does not make (a loop's, a filter's)


def node_errors(node):
    found = []
    for plug in output_plugs(node):
        try:
            compute(plug)
        except Exception as ex:  # noqa: BLE001
            text = error_text(ex)
            if "Context has no variable named" in text:
                NEEDS_CONTEXT.append(plug.relativeName(S))
                continue
            entry = {"plug": plug.relativeName(S), "error": text}
            origin = ex.plug() if isinstance(ex, Gaffer.ProcessException) and hasattr(ex, "plug") else None
            if origin is not None:
                entry["arose_in"] = origin.relativeName(S) if S.isAncestorOf(origin) else origin.fullName()
                entry["origin_node"] = origin.node().relativeName(S) if origin.node() is not None and S.isAncestorOf(origin.node()) else None
            found.append(entry)
    return found


def all_nodes(root):
    # A node's private internals ("__" names, such as an ImageWriter's __contextQuery) are its own: its outputs say
    # what they lead to.
    stack = [root]
    while stack:
        n = stack.pop(0)
        yield n
        stack += [c for c in n.children(Gaffer.Node) if not c.getName().startswith("__")]


def scan_errors():
    found, computed, skipped, origins = [], 0, [], []
    with_errors = total = 0
    for root in roots:
        for n in all_nodes(root):
            if n.isSame(S):
                continue
            total += 1
            if total > NODES_MAX:
                break
            if time.time() - STARTED > COMPUTE_BUDGET:
                skipped.append(n.relativeName(S))
                continue
            STEP(step="cook", node=n.relativeName(S))  # the plugin's step name: "computing <node> for the error scan"
            computed += len(output_plugs(n))
            errors = node_errors(n)
            if not errors:
                continue
            with_errors += 1
            name = n.relativeName(S)
            for e in errors:
                origin = e.pop("origin_node", None) or name
                if origin not in origins:
                    origins.append(origin)
            here = any(e.get("arose_in", e["plug"]).startswith(name + ".") for e in errors)
            if len(found) < ERRORS_MAX:
                found.append({"node": name, "type": "%s.%s" % (type(n).__module__.split(".")[0], type(n).__name__),
                              "errors": errors[:4], "arises_here": here})
    # Where an error arises first, then the nodes it reaches downstream.
    found.sort(key=lambda e: 0 if e["arises_here"] else 1)
    return found, {"nodes_with_errors": with_errors, "errors_arise_in": origins[:40], "nodes_read": min(total, NODES_MAX),
                   "outputs_computed": computed,
                   **({"outputs_needing_context": NEEDS_CONTEXT[:20]} if NEEDS_CONTEXT else {}),
                   **({"not_computed": skipped[:40], "not_computed_count": len(skipped)} if skipped else {})}


rows = []
for i, c in enumerate(CRITERIA):
    if not isinstance(c, dict) or "expr" not in c or c.get("op") not in OPS or "value" not in c:
        raise ValueError("criteria[%d]: {expr, op: one of %s, value, tol?, frame?, label?}" % (i, " ".join(OPS)))
    code = read_only_expression(str(c["expr"]))
    frame = float(c["frame"]) if c.get("frame") is not None else FRAME
    row = {"label": c.get("label") or c["expr"], "check": "%s %s %s" % (c["expr"], c["op"], json.dumps(c["value"])), "frame": rounded(frame)}
    STEP(step="criterion", index=i + 1, of=len(CRITERIA), label=row["label"][:120], frame=rounded(frame))
    measuring = time.time()
    try:
        with at_frame(frame):
            measured = eval(code, namespace(frame))  # noqa: S307 - a checked read-only expression
            row["measured"] = rounded(measured)
            row["passed"] = bool(compare(measured, c["op"], c["value"], float(c.get("tol", 1e-4))))
    except Exception as ex:  # noqa: BLE001
        row["passed"] = False
        row["error"] = ("%s: %s" % (type(ex).__name__, error_text(ex)))[:400]
    row["seconds"] = round(time.time() - measuring, 2)
    rows.append(row)

facts = {"script": S["fileName"].getValue() or None, "frame": rounded(S.context().getFrame()),
         "fps": rounded(S["framesPerSecond"].getValue()),
         "frame_range": [S["frameRange"]["start"].getValue(), S["frameRange"]["end"].getValue()]}
if FRAME != S.context().getFrame():
    facts["measured_at"] = rounded(FRAME)

roots = [S if r in ("", "/") else node_of(r) for r in (P.get("roots") or ["/"])]
error_rows = []
if P.get("errors", True):
    STEP(step="errors", roots=["/" if r.isSame(S) else r.relativeName(S) for r in roots])
    with at_frame(FRAME):
        error_rows, facts["error_scan"] = scan_errors()
    if len(error_rows) >= ERRORS_MAX:
        facts["error_scan"]["listed"] = ERRORS_MAX


# --- render: a low-resolution Cycles image from a separate gaffer python -------------------------------------
RENDER_SCRIPT = r'''
import base64, os, time
import Gaffer, GafferImage, GafferScene, IECore, IECoreScene, imath
o = OPTIONS
facts = {"renderer": "Cycles"}
out = {"facts": facts}
try:
    import GafferCycles
except ImportError as ex:
    raise RuntimeError("render: Cycles is not available in this Gaffer (%s)" % ex)
def find(path):
    found = script.descendant(str(path).strip().strip("/").replace("/", "."))
    if found is None:
        raise ValueError("render.scene: no node or plug %r" % path)
    return found
def scene_plug(target):
    if isinstance(target, GafferScene.ScenePlug):
        return target
    for name in ("in", "out") if isinstance(target, GafferScene.Render) else ("out",):
        if name in target and isinstance(target[name], GafferScene.ScenePlug):
            return target[name]
    raise ValueError("render.scene: %s has no scene output" % target.relativeName(script))
def nodes(root):
    for n in root.children(Gaffer.Node):
        yield n
        yield from nodes(n)
if o.get("scene"):
    scene = scene_plug(find(o["scene"]))
    facts["source"] = scene.relativeName(script)
else:
    renders = [n for n in nodes(script) if isinstance(n, GafferScene.Render) and n["in"].getInput() is not None]
    if renders:
        scene = renders[0]["in"]
        facts["source"] = "the scene of %s" % renders[0].relativeName(script)
    else:
        ends = [n["out"] for n in script.children(Gaffer.Node) if "out" in n and isinstance(n["out"], GafferScene.ScenePlug) and not n["out"].outputs()]
        if len(ends) != 1:
            raise ValueError("render: no Render node, and %d scene outputs lead nowhere (%s): give render.scene"
                             % (len(ends), ", ".join(e.relativeName(script) for e in ends[:8])))
        scene = ends[0]
        facts["source"] = scene.relativeName(script)
frame = o["frame"]
context = Gaffer.Context(script.context())
context.setFrame(frame)
with context:
    g = scene.globals()
    asked = g["option:render:resolution"].value if "option:render:resolution" in g else imath.V2i(4, 3)
    k = o["size"] / float(max(asked.x, asked.y))
    w, h = max(16, int(round(asked.x * k))), max(16, int(round(asked.y * k)))
    camera = o.get("camera") or (g["option:render:camera"].value if "option:render:camera" in g else "")
    if not camera:
        cams = sorted(scene.set("__cameras").value.paths())
        camera = cams[0] if cams else ""
    if camera and not scene.exists(camera):
        raise ValueError("render.camera: no location %s in the scene" % camera)
facts["camera"] = camera or "none in the scene: Gaffer's default camera, at the origin looking down -Z"
facts["resolution"] = [w, h]
script["opencodeVerifyOptions"] = GafferScene.StandardOptions()
opts = script["opencodeVerifyOptions"]
opts["in"].setInput(scene)
for name, value in (("render:resolution", imath.V2i(w, h)), ("render:resolutionMultiplier", 1.0)) + ((("render:camera", camera),) if camera else ()):
    opts["options"][name]["enabled"].setValue(True)
    opts["options"][name]["value"].setValue(value)
script["opencodeVerifyNoOutputs"] = GafferScene.DeleteOutputs()
script["opencodeVerifyNoOutputs"]["in"].setInput(opts["out"])
script["opencodeVerifyNoOutputs"]["names"].setValue("*")
script["opencodeVerifyOutputs"] = GafferScene.Outputs()
outputs = script["opencodeVerifyOutputs"]
outputs["in"].setInput(script["opencodeVerifyNoOutputs"]["out"])
exr = o["exr"]
outputs.addOutput("opencodeVerify", IECoreScene.Output(exr, "exr", "rgba", {}))
script["opencodeVerifyCycles"] = GafferCycles.CyclesOptions()
cycles = script["opencodeVerifyCycles"]
cycles["in"].setInput(outputs["out"])
cycles["options"]["cycles:session:samples"]["enabled"].setValue(True)
cycles["options"]["cycles:session:samples"]["value"].setValue(o["samples"])
script["opencodeVerifyRender"] = GafferScene.Render()
render = script["opencodeVerifyRender"]
render["in"].setInput(cycles["out"])
render["renderer"].setValue("Cycles")
started = time.time()
with context:
    render["task"].execute()
facts["seconds"] = round(time.time() - started, 2)
if not os.path.isfile(exr):
    raise RuntimeError("the Cycles render wrote no image")
script["opencodeVerifyRead"] = GafferImage.ImageReader()
reader = script["opencodeVerifyRead"]
reader["fileName"].setValue(exr)
image = GafferImage.ImageAlgo.image(reader["out"])
names = image.keys()
r, gr, b = (image[c] if c in names else None for c in ("R", "G", "B"))
alpha = image["A"] if "A" in names else None
n = len(r)
lum = [0.2126 * r[i] + 0.7152 * gr[i] + 0.0722 * b[i] for i in range(n)]
mean = sum(lum) / n
std = (sum((x - mean) ** 2 for x in lum) / n) ** 0.5
facts["image"] = {"size": [w, h], "mean_luminance": round(mean, 4), "max_luminance": round(max(lum), 4),
                  "luminance_std": round(std, 4), "mean_alpha": round(sum(alpha) / n, 4) if alpha is not None else None,
                  "transparent_fraction": round(sum(1 for a in alpha if a < 0.01) / n, 4) if alpha is not None else None,
                  "black": max(lum) < 0.02, "uniform": std < 0.002, "values": "linear (scene-referred)"}
facts["frame"] = frame
script["opencodeVerifyWrite"] = GafferImage.ImageWriter()
writer = script["opencodeVerifyWrite"]
writer["in"].setInput(reader["out"])
writer["fileName"].setValue(o["png"])
with context:
    writer["task"].execute()
with open(o["png"], "rb") as fh:
    out["image_base64"] = base64.b64encode(fh.read()).decode("ascii")
'''


def render_options(spec):
    spec = {} if spec is True else dict(spec)
    unknown = set(spec) - {"scene", "camera", "size", "frame", "samples"}
    if unknown:
        raise ValueError("render: unknown keys %s; give {scene?, camera?, size?, frame?, samples?}" % sorted(unknown))
    size = int(spec.get("size", 320))
    if not 64 <= size <= 1024:
        raise ValueError("render.size: 64 to 1024 pixels (longest side)")
    samples = int(spec.get("samples", 16))
    if not 1 <= samples <= 1024:
        raise ValueError("render.samples: 1 to 1024")
    return {"scene": spec.get("scene"), "camera": spec.get("camera"), "size": size, "samples": samples,
            "frame": float(spec["frame"]) if spec.get("frame") is not None else FRAME}


def launcher(job):
    """`gaffer python <job>`, started with this Gaffer's own launcher and environment, without a console window."""
    exe = os.path.join(os.environ.get("GAFFER_ROOT") or "", "bin", "gaffer.cmd" if os.name == "nt" else "gaffer")
    if not os.path.isfile(exe):
        raise RuntimeError("no Gaffer launcher at %s" % exe)
    if os.name == "nt":
        return '"%s" /d /s /c ""%s" python "%s""' % (os.environ.get("ComSpec") or "cmd.exe", exe, job)
    return [exe, "python", job]


def child_env(role):
    env = {k: v for k, v in os.environ.items() if k != "OPENCODE_GAFFER_OFFER_KEY"}
    env["OPENCODE_GAFFER_ROLE"] = role  # never offered to OpenCode
    return env


def render_image(options):
    work = tempfile.mkdtemp(prefix="opencode_verify_render_")
    options["png"] = os.path.join(work, "verify.png").replace("\\", "/")
    options["exr"] = os.path.join(work, "verify.exr").replace("\\", "/")

    def clean():
        for name in os.listdir(work):
            try:
                os.remove(os.path.join(work, name))
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass

    if THROWAWAY:
        # Already a background gaffer python on its own copy (gaffer_verify saved): render here.
        scope = {"OPTIONS": options, "script": S, "__name__": "__opencode_render__"}
        try:
            exec(compile(RENDER_SCRIPT, "<verify render>", "exec"), scope)  # noqa: S102
            got = scope["out"]
        finally:
            clean()
        got["facts"]["by"] = "this background gaffer python (on the file read)"
        return got
    started = time.time()
    copy = os.path.join(work, os.path.basename(S["fileName"].getValue()) or "untitled.gfr").replace("\\", "/")
    S.serialiseToFile(copy)
    result_path = os.path.join(work, "result.json")
    job = os.path.join(work, "render.py")
    with open(job, "w", encoding="utf-8") as fh:
        fh.write("import json, os, sys, traceback\nimport Gaffer\n")
        fh.write("OPTIONS = json.loads(%r)\n" % json.dumps(options))
        fh.write("try:\n    script = Gaffer.ScriptNode()\n    script['fileName'].setValue(%r)\n" % copy)
        fh.write("    script.load(continueOnError=True)\n")
        fh.write("    exec(compile(%r, '<verify render>', 'exec'))\n" % RENDER_SCRIPT)
        fh.write("except Exception:\n    out = {'error': traceback.format_exc()[-2000:]}\n")
        fh.write("with open(%r, 'w', encoding='utf-8') as fh:\n    json.dump(out, fh)\n" % result_path)
        fh.write("sys.stdout.flush()\nos._exit(0)\n")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "env": child_env("verify"), "cwd": work}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # no console window
    try:
        run = subprocess.run(launcher(job), timeout=int(P.get("timeout_s") or 300), **kwargs)
        if not os.path.isfile(result_path):
            tail = run.stdout.decode("utf-8", "replace")[-1500:] if run.stdout else ""
            raise RuntimeError("the render gaffer python gave no result (exit %s):\n%s" % (run.returncode, tail))
        with open(result_path, encoding="utf-8") as fh:
            got = json.load(fh)
    finally:
        clean()
    if "error" in got:
        raise RuntimeError("render: " + got["error"])
    got["facts"]["by"] = "a background gaffer python on a working copy of the open script (the open file keeps its name)"
    got["facts"]["total_seconds"] = round(time.time() - started, 1)
    return got


image = None
if P.get("render"):
    STEP(step="render")
    rendered = render_image(render_options(P["render"]))
    facts["render"] = rendered["facts"]
    image = rendered.get("image_base64")
facts["seconds"] = round(time.time() - STARTED, 2)
passed = sum(1 for r in rows if r["passed"])
result = {"gaffer": {"version": Gaffer.About.versionString()},
          "passed": passed, "failed": len(rows) - passed, "all_passed": passed == len(rows),
          "failed_labels": [r["label"] for r in rows if not r["passed"]], "criteria": rows, "errors": error_rows,
          "facts": facts, **({"image_base64": image} if image else {})}
