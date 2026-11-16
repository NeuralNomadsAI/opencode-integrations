# SPDX-License-Identifier: MIT
# houdini_verify (fixed program): check a result by measurement on the open scene. criteria: the agent's own
# acceptance checks, read-only expressions measured on cooked geometry, USD stages and parameters (at a frame
# without moving the playbar), each passed or failed with its measured value. errors: every node under the roots
# with errors() or warnings(), after cooking the criteria's nodes and the display (and render) nodes of every
# geometry object and LOP network. render: a low-resolution image (Karma CPU by default, or OpenGL, or a ROP you
# name) made by a separate background hython on a working copy (a crash there never touches this Houdini), with
# facts read from its pixels. Nothing in the open scene is changed: no frame change, no node made.
# PARAMS is prepended by the plugin: {"criteria"?, "frame"?, "errors"?, "roots"?, "render"?, "timeout_s"?}
import ast
import base64
import json
import math
import os
import re
import subprocess
import tempfile
import time

import hou

P = PARAMS  # noqa: F821
STARTED = time.time()
CRITERIA = P.get("criteria") or []
FRAME = float(P["frame"]) if P.get("frame") is not None else hou.frame()
COOK_BUDGET = 60.0  # seconds spent cooking display nodes for the error scan, at most
ERRORS_MAX = 60
NODES_MAX = 200000
OPS = ("<", "<=", ">", ">=", "==", "!=", "approx", "between", "in", "contains")
THROWAWAY = os.environ.get("OPENCODE_HOUDINI_ROLE") in ("verify", "render") and not hou.isUIAvailable()
# houdini_verify saved: the background hython's job defines STEP, a line in its log saying what it does now (the
# plugin reads it to say where a verify that ran out of time stopped). In the open scene: nothing.
STEP = globals().get("STEP") or (lambda **what: None)
if not isinstance(CRITERIA, list) or len(CRITERIA) > 50:
    raise ValueError("criteria: a list of at most 50 {expr, op, value, tol?, frame?, label?}")


def license_name():
    try:
        return str(hou.licenseCategory()).rsplit(".", 1)[-1]
    except Exception:  # noqa: BLE001
        return "unknown"


SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in (
    "abs", "min", "max", "len", "round", "sum", "any", "all", "sorted", "int", "float", "bool", "str", "list",
    "tuple", "set", "dict", "range", "zip", "enumerate", "isinstance", "map", "filter", "reversed")}
# Attribute names that change something (hou and USD): refused in a criterion.
WRITES = re.compile(r"^(set|create|destroy|delete|remove|insert|append|add|clear|save|load|merge|render|press|move|"
                    r"copy|paste|lock|unlock|allow|bypass|hscript|execute|run|revert|undo|define|override|export|"
                    r"reload|mute|unmute|change|rename|layout|cut|write|open|close|install|uninstall|exit|system|"
                    r"cook|dirty|force|update|apply|bake|stash|expand|collapse|hide|show|display)(?![a-z])")
READS = {"renderNode", "displayNode", "isDisplayFlagSet", "isRenderFlagSet"}
BLOCKED = {"ui", "undos", "hipFile", "hda", "session", "qt", "shelves", "hscript", "system", "exit"}


def read_only_expression(text):
    """An expression that reads: no assignment, no lambda, no private name, no attribute that changes things."""
    tree = ast.parse(text, mode="eval")
    for n in ast.walk(tree):
        if isinstance(n, (ast.NamedExpr, ast.Lambda)):
            raise ValueError("criteria: %r is not a plain read" % text)
        if isinstance(n, ast.Attribute) and (n.attr.startswith("_") or n.attr in BLOCKED or (WRITES.match(n.attr[:1].lower() + n.attr[1:]) and n.attr not in READS)):
            raise ValueError("criteria: %r reaches %s, which is not a read" % (text, n.attr))
        if isinstance(n, ast.Name) and n.id.startswith("_"):
            raise ValueError("criteria: %r names %s" % (text, n.id))
    return compile(tree, "<criterion>", "eval")


class Box(object):
    """World bounds of cooked geometry: min, max, size, center (x, y, z tuples)."""

    def __init__(self, bb):
        self.min = tuple(bb.minvec())
        self.max = tuple(bb.maxvec())
        self.size = tuple(bb.sizevec())
        self.center = tuple(bb.center())

    def __repr__(self):
        return "Box(min=%s, max=%s)" % (rounded(self.min), rounded(self.max))


def need(path):
    n = hou.node(path)
    if n is None:
        raise ValueError("no node %r" % path)
    return n


def sop_of(path):
    n = need(path)
    if n.type().category().name() == "Object":
        shown = n.displayNode() if hasattr(n, "displayNode") else None
        if shown is None:
            raise ValueError("%s has no display SOP" % path)
        return shown
    if not hasattr(n, "geometry"):
        raise ValueError("%s is a %s, not a SOP or a geometry object" % (path, n.type().nameWithCategory()))
    return n


COOKED = []
READ = []  # nodes the criteria named (node(path)): where their geometry came from is said in facts.read
SOLVERS = ("dopnet", "dopimport", "dopimportfield", "dopimportrecords")


def source_of(n):
    """Where a SOP's geometry comes from: files on disk (a File SOP, or a File Cache that loads from disk), or
    cooked, simulated by an upstream solver from its start frame when no cache is read on the way."""
    kind = n.type().name()
    loads = kind == "file" or (kind.startswith("filecache") and n.parm("loadfromdisk") is not None and n.evalParm("loadfromdisk"))
    if loads:
        p = n.parm("file") or n.parm("sopoutput")
        return {"from": "files on disk", **({"files": p.unexpandedString()} if p is not None else {})}
    solvers = []
    try:
        for a in n.inputAncestors():
            name = a.type().name()
            if name == "file" or (name.startswith("filecache") and a.parm("loadfromdisk") is not None and a.evalParm("loadfromdisk")):
                continue
            if "solver" in name or name in SOLVERS:
                solvers.append(a.path())
    except Exception:  # noqa: BLE001
        pass
    if solvers and (kind in SOLVERS or "solver" in kind or n.isTimeDependent()):
        return {"from": "cooked: simulated from the start frame (no cache read)", "solvers": solvers[:4]}
    if "solver" in kind or kind in SOLVERS:
        return {"from": "cooked: simulated from the start frame (no cache read)", "solvers": [n.path()]}
    return {"from": "cooked"}


def read_sources():
    found = []
    for path in READ + [p for p in COOKED if p not in READ]:
        n = hou.node(path)
        if n is None or n.type().category().name() != "Sop":
            continue
        # A node a criterion only named (its parms, its inputs) gave no geometry: not listed.
        if path not in COOKED and not n.cookCount():
            continue
        try:
            found.append(dict({"node": path}, **source_of(n)))
        except Exception:  # noqa: BLE001
            continue
        if len(found) >= 20:
            break
    return found


def node_read(path):
    n = need(path)
    if path not in READ:
        READ.append(n.path())
    return n


def namespace(frame):
    at_current = abs(frame - hou.frame()) < 1e-9

    def geo(path):
        n = sop_of(path)
        g = n.geometry() if at_current else n.geometryAtFrame(frame)
        if n.path() not in COOKED:
            COOKED.append(n.path())
        if g is None:
            raise ValueError("%s has no cooked geometry: %s" % (n.path(), "; ".join(n.errors())[:300] or "no error reported"))
        return g

    def bbox(path):
        return Box(geo(path).boundingBox())

    def stage(path):
        n = need(path)
        if not hasattr(n, "stage"):
            raise ValueError("%s is a %s, not a LOP" % (path, n.type().nameWithCategory()))
        s = n.stage()
        if n.path() not in COOKED:
            COOKED.append(n.path())
        if s is None:
            raise ValueError("%s has no stage: %s" % (path, "; ".join(n.errors())[:300] or "no error reported"))
        return s

    def prim(path, prim_path):
        p = stage(path).GetPrimAtPath(prim_path)
        if not p or not p.IsValid():
            raise ValueError("%s: no prim %s" % (path, prim_path))
        return p

    def usd_attr(path, prim_path, name):
        from pxr import Usd
        a = prim(path, prim_path).GetAttribute(name)
        if not a or not a.IsValid():
            raise ValueError("%s %s: no attribute %r" % (path, prim_path, name))
        value = a.Get(Usd.TimeCode(frame))
        return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value

    def usd_prims(path, type_name=None):
        return [str(p.GetPath()) for p in stage(path).Traverse() if type_name is None or p.GetTypeName() == type_name]

    def parm(path):
        p = hou.parm(path)
        if p is not None:
            return p.eval() if at_current else p.evalAtFrame(frame)
        t = hou.parmTuple(path)
        if t is None:
            raise ValueError("no parm or parm tuple %r" % path)
        return tuple(t.eval() if at_current else t.evalAtFrame(frame))

    def attrib_names(path, cls="point"):
        fn = {"point": "pointAttribs", "prim": "primAttribs", "vertex": "vertexAttribs", "detail": "globalAttribs"}[cls]
        return [a.name() for a in getattr(geo(path), fn)()]

    def attrib_values(path, name, cls="point"):
        g = geo(path)
        fn = {"point": "pointFloatAttribValues", "prim": "primFloatAttribValues", "vertex": "vertexFloatAttribValues"}
        if cls == "detail":
            return g.attribValue(name)
        attrib = {"point": g.findPointAttrib, "prim": g.findPrimAttrib, "vertex": g.findVertexAttrib}[cls](name)
        if attrib is None:
            raise ValueError("%s: no %s attribute %r" % (path, cls, name))
        if attrib.dataType() in (hou.attribData.Float, hou.attribData.Int):
            values = list(getattr(g, fn[cls])(name)) if attrib.dataType() == hou.attribData.Float else \
                list(getattr(g, fn[cls].replace("Float", "Int"))(name))
            size = attrib.size()
            return values if size == 1 else [tuple(values[i:i + size]) for i in range(0, len(values), size)]
        return list(getattr(g, fn[cls].replace("Float", "String"))(name))

    return {"__builtins__": SAFE_BUILTINS, "hou": hou, "math": math, "frame": frame, "node": node_read, "geo": geo,
            "bbox": bbox, "npoints": lambda path: geo(path).intrinsicValue("pointcount"),
            "nprims": lambda path: geo(path).intrinsicValue("primitivecount"), "stage": stage, "prim": prim,
            "usd_attr": usd_attr, "usd_prims": usd_prims, "parm": parm, "attrib_names": attrib_names,
            "attrib_values": attrib_values, "errors": lambda path: list(need(path).errors()),
            "warnings": lambda path: list(need(path).warnings())}


def rounded(value):
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else str(value)
    if isinstance(value, (bool, int, str)) or value is None:
        return value if not isinstance(value, str) or len(value) <= 300 else value[:300] + "..."
    if isinstance(value, Box):
        return {"min": rounded(value.min), "max": rounded(value.max), "size": rounded(value.size)}
    if hasattr(value, "path") and callable(value.path):
        try:
            return value.path()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, dict):
        return {str(k): rounded(v) for k, v in list(value.items())[:64]}
    try:
        return [rounded(v) for v in list(value)[:64]]
    except TypeError:
        return repr(value)[:200]


def compare(measured, op, value, tol):
    def numbers(x):
        if isinstance(x, (str, Box)):
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
        measured = eval(code, namespace(frame))  # noqa: S307 - a checked read-only expression
        row["measured"] = rounded(measured)
        row["passed"] = bool(compare(measured, c["op"], c["value"], float(c.get("tol", 1e-4))))
    except Exception as ex:  # noqa: BLE001
        row["passed"] = False
        row["error"] = ("%s: %s" % (type(ex).__name__, ex))[:400]
    row["seconds"] = round(time.time() - measuring, 2)
    rows.append(row)

facts = {"hip": None if hou.hipFile.isNewFile() else hou.hipFile.path(), "frame": rounded(hou.frame()), "fps": rounded(hou.fps()),
         "frame_range": [rounded(float(hou.expandString(v))) for v in ("$FSTART", "$FEND")]}


def cook_targets():
    """Display (and render) SOPs of every geometry object, display LOPs of every LOP network, under the roots."""
    found = []
    for root in roots:
        for n in [root] + list(root.allSubChildren(recurse_in_locked_nodes=False)):
            category = n.childTypeCategory()
            if category is None or not n.children():
                continue
            # A network inside a network of its kind (a SOP subnet, a solver asset's inside): its parent's display
            # node cooks what is shown; cooking its own could run a whole simulation up to this frame.
            if n.type().category() == category:
                continue
            if category.name() in ("Sop", "Lop"):
                for getter in ("displayNode", "renderNode"):
                    fn = getattr(n, getter, None)
                    try:
                        target = fn() if fn else None
                    except Exception:  # noqa: BLE001
                        target = None
                    if target is not None and target.path() not in found:
                        found.append(target.path())
    return found


def scan_errors():
    cooked, skipped = [], []
    for path in COOKED + [p for p in cook_targets() if p not in COOKED]:
        if time.time() - STARTED > COOK_BUDGET:
            skipped.append(path)
            continue
        STEP(step="cook", node=path)
        n = hou.node(path)
        try:
            n.cook()
            cooked.append(path)
        except hou.OperationFailed:
            cooked.append(path)  # its errors() say why
        except Exception as ex:  # noqa: BLE001
            skipped.append("%s (%s)" % (path, str(ex)[:120]))
    found, counted, total = [], {"errors": 0, "warnings": 0}, 0
    for root in roots:
        for n in [root] + list(root.allSubChildren(recurse_in_locked_nodes=False)):
            total += 1
            if total > NODES_MAX:
                break
            try:
                errors, warnings = n.errors(), n.warnings()
            except Exception:  # noqa: BLE001
                continue
            if errors:
                counted["errors"] += 1
            elif warnings:
                counted["warnings"] += 1
            else:
                continue
            if len(found) < ERRORS_MAX:
                entry = {"node": n.path(), "type": n.type().nameWithCategory()}
                if errors:
                    entry["errors"] = [" ".join(m.split())[:400] for m in errors[:4]]
                if warnings:
                    entry["warnings"] = [" ".join(m.split())[:400] for m in warnings[:4]]
                found.append(entry)
    found.sort(key=lambda e: 0 if "errors" in e else 1)
    return found, {"nodes_with_errors": counted["errors"], "nodes_with_warnings_only": counted["warnings"],
                   "nodes_read": min(total, NODES_MAX), "cooked": cooked[:40], **({"cooked_more": len(cooked) - 40} if len(cooked) > 40 else {}),
                   **({"not_cooked": skipped[:40]} if skipped else {})}


roots = [need(r) for r in (P.get("roots") or ["/"])]
sources = read_sources()
if sources:
    facts["read"] = sources
error_rows = []
if P.get("errors", True):
    STEP(step="errors", roots=[r.path() for r in roots])
    error_rows, facts["error_scan"] = scan_errors()
    if len(error_rows) >= ERRORS_MAX:
        facts["error_scan"]["listed"] = ERRORS_MAX


# --- render: a low-resolution image from a separate hython ---------------------------------------------------
RENDER_SCRIPT = r'''
import base64, json, os, time, hou
o = OPTIONS
facts = {"renderer": None}
out = {"facts": facts}
def first_camera():
    cams = [n for n in hou.node("/obj").allSubChildren() if n.type().name() in ("cam", "camera") or n.type().name().startswith("cam::")]
    return cams[0] if cams else None
def res_for(cam_res):
    w, h = cam_res
    k = o["size"] / float(max(w, h))
    return max(16, int(round(w * k))), max(16, int(round(h * k)))
def set_parm(node, name, value):
    p = node.parm(name) if not isinstance(value, (tuple, list)) else node.parmTuple(name)
    if p is None:
        return False
    p.set(value)
    return True
png = o["png"]
frame = o["frame"]
rop = hou.node(o["rop"]) if o.get("rop") else None
if o.get("rop") and rop is None:
    raise ValueError("render.rop: no node %r" % o["rop"])
lop = hou.node(o["lop"]) if o.get("lop") else None
cam = hou.node(o["camera"]) if o.get("camera") else None
if o.get("camera") and cam is None:
    raise ValueError("render.camera: no node %r" % o["camera"])
if rop is None:
    if lop is None and cam is None:
        cam = first_camera()
        if cam is None:
            stage_net = hou.node("/stage")
            shown = stage_net.displayNode() if stage_net is not None and stage_net.children() else None
            if shown is None:
                raise ValueError("render: no camera in /obj and no LOP display node in /stage: give render.camera, lop or rop")
            lop = shown
    if lop is not None:
        rop = hou.node("/out").createNode("usdrender", "opencode_verify")
        rop.parm("loppath").set(lop.path())
        set_parm(rop, "renderer", "BRAY_HdKarma")
        set_parm(rop, "outputimage", png)
        set_parm(rop, "override_res", "specific")
        set_parm(rop, "res_user", (o["size"], int(o["size"] * 0.75)))
        if o.get("renderer") == "opengl":
            rop.destroy()
            rop = hou.node("/out").createNode("opengl", "opencode_verify")
            set_parm(rop, "opsource", "lop") or set_parm(rop, "opsource", 1)
            rop.parm("loppath").set(lop.path())
        facts["source"] = "LOP %s" % lop.path()
    else:
        kind = "opengl" if o.get("renderer") == "opengl" else "karma"
        rop = hou.node("/out").createNode(kind, "opencode_verify")
        rop.parm("camera").set(cam.path())
        facts["source"] = "camera %s" % cam.path()
        facts["camera"] = cam.path()
made = rop.name() == "opencode_verify"
kind = rop.type().name().split("::")[0]
facts["rop"] = o.get("rop") or "a temporary %s ROP" % kind
facts["renderer"] = kind
cam_node = hou.node(rop.parm("camera").eval()) if rop.parm("camera") and rop.parm("camera").eval() else None
cam_res = tuple(cam_node.parmTuple("res").eval()) if cam_node is not None and cam_node.parmTuple("res") else (4, 3)
w, h = res_for(cam_res)
if kind == "opengl":
    set_parm(rop, "tres", True); set_parm(rop, "res", (w, h)); set_parm(rop, "picture", png)
elif kind == "karma":
    set_parm(rop, "resolutionx", w); set_parm(rop, "resolutiony", h); set_parm(rop, "picture", png)
    set_parm(rop, "samplesperpixel", min(rop.evalParm("samplesperpixel") if rop.parm("samplesperpixel") else 9, 9))
elif kind == "usdrender":
    set_parm(rop, "outputimage", png); set_parm(rop, "override_res", "specific"); set_parm(rop, "res_user", (w, h))
elif kind == "ifd":
    set_parm(rop, "vm_picture", png); set_parm(rop, "override_camerares", True)
    set_parm(rop, "res_fraction", "specific"); set_parm(rop, "res_override", (w, h))
else:
    raise ValueError("render.rop: %s is a %s ROP; verify renders opengl, karma, usdrender or ifd (Mantra) ROPs" % (rop.path(), kind))
for name in ("trange",):
    set_parm(rop, name, 1)
started = time.time()
try:
    rop.render(frame_range=(frame, frame), verbose=False, output_progress=False)
except hou.OperationFailed as ex:
    facts["render_error"] = str(ex)[:500]
facts["seconds"] = round(time.time() - started, 2)
messages = list(rop.errors()) + ["warning: " + m for m in rop.warnings()]
if messages:
    facts["rop_messages"] = [" ".join(m.split())[:300] for m in messages[:6]]
if not os.path.isfile(png):
    raise RuntimeError("the %s render wrote no image%s" % (kind, (": " + "; ".join(facts.get("rop_messages", [])))[:600]))
from PIL import Image
im = Image.open(png).convert("RGBA")
iw, ih = im.size
px = list(im.getdata())
n = len(px)
lum = [(0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0 for r, g, b, a in px]
alpha = [a / 255.0 for r, g, b, a in px]
mean = sum(lum) / n
std = (sum((x - mean) ** 2 for x in lum) / n) ** 0.5
# The Apprentice watermark sits in the bottom-right corner: measured apart, so a black scene still reads black.
corner = [lum[y * iw + x] for y in range(int(ih * 0.85), ih) for x in range(int(iw * 0.7), iw)]
body = [lum[y * iw + x] for y in range(ih) for x in range(iw) if not (y >= int(ih * 0.85) and x >= int(iw * 0.7))]
facts["image"] = {"size": [iw, ih], "mean_luminance": round(mean, 4), "max_luminance": round(max(lum), 4),
                  "luminance_std": round(std, 4), "mean_alpha": round(sum(alpha) / n, 4),
                  "transparent_fraction": round(sum(1 for a in alpha if a < 0.01) / n, 4),
                  "black": max(body) < 0.02, "uniform": (sum((x - sum(body) / len(body)) ** 2 for x in body) / len(body)) ** 0.5 < 0.002,
                  "corner_max_luminance": round(max(corner), 4)}
facts["frame"] = frame
with open(png, "rb") as fh:
    out["image_base64"] = base64.b64encode(fh.read()).decode("ascii")
'''


def render_options(spec):
    spec = {} if spec is True else dict(spec)
    unknown = set(spec) - {"rop", "camera", "lop", "renderer", "size", "frame"}
    if unknown:
        raise ValueError("render: unknown keys %s; give {rop?, camera?, lop?, renderer?: karma or opengl, size?, frame?}" % sorted(unknown))
    size = int(spec.get("size", 320))
    if not 64 <= size <= 1024:
        raise ValueError("render.size: 64 to 1024 pixels (longest side)")
    if spec.get("renderer") not in (None, "karma", "opengl"):
        raise ValueError("render.renderer: karma or opengl")
    return {"rop": spec.get("rop"), "camera": spec.get("camera"), "lop": spec.get("lop"), "renderer": spec.get("renderer"),
            "size": size, "frame": float(spec["frame"]) if spec.get("frame") is not None else FRAME}


def hython():
    exe = os.path.join(hou.getenv("HFS") or "", "bin", "hython.exe" if os.name == "nt" else "hython")
    if not os.path.isfile(exe):
        raise RuntimeError("no hython at %s" % exe)
    return exe


def child_env(role):
    env = {k: v for k, v in os.environ.items() if k != "OPENCODE_HOUDINI_OFFER_KEY"}
    env["OPENCODE_HOUDINI_ROLE"] = role  # never offered to OpenCode
    return env


def render_image(options):
    work = tempfile.mkdtemp(prefix="opencode_verify_render_")
    options["png"] = os.path.join(work, "verify.png").replace("\\", "/")
    if THROWAWAY:
        # Already a background hython on its own copy (houdini_verify saved): render here.
        scope = {"OPTIONS": options, "__name__": "__opencode_render__"}
        try:
            exec(compile(RENDER_SCRIPT, "<verify render>", "exec"), scope)  # noqa: S102
            got = scope["out"]
        finally:
            try:
                os.remove(options["png"])
                os.rmdir(work)
            except OSError:
                pass
        got["facts"]["by"] = "this background hython (on the file read)"
        return got
    started = time.time()
    copy = os.path.join(work, "working_copy" + (os.path.splitext(hou.hipFile.path())[1] or ".hipnc")).replace("\\", "/")
    out_err = hou.hscript('mwrite -n "%s"' % copy)
    if not os.path.isfile(copy):
        raise RuntimeError("the working copy was not written: %s" % " ".join(out_err).strip()[:300])
    result_path = os.path.join(work, "result.json")
    script = os.path.join(work, "render.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("import json, sys, traceback, hou\n")
        fh.write("OPTIONS = json.loads(%r)\n" % json.dumps(options))
        fh.write("try:\n    hou.hipFile.load(%r, suppress_save_prompt=True, ignore_load_warnings=True)\n" % copy)
        fh.write("    exec(compile(%r, '<verify render>', 'exec'))\n" % RENDER_SCRIPT)
        fh.write("except Exception:\n    out = {'error': traceback.format_exc()[-2000:]}\n")
        fh.write("with open(%r, 'w', encoding='utf-8') as fh:\n    json.dump(out, fh)\n" % result_path)
        fh.write("sys.stdout.flush()\nimport os\nos._exit(0)\n")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "env": child_env("verify")}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # no console window
    try:
        run = subprocess.run([hython(), script], timeout=int(P.get("timeout_s") or 300), **kwargs)
        if not os.path.isfile(result_path):
            tail = run.stdout.decode("utf-8", "replace")[-1500:] if run.stdout else ""
            raise RuntimeError("the render hython gave no result (exit %s):\n%s" % (run.returncode, tail))
        with open(result_path, encoding="utf-8") as fh:
            got = json.load(fh)
    finally:
        for name in os.listdir(work):
            try:
                os.remove(os.path.join(work, name))
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass
    if "error" in got:
        raise RuntimeError("render: " + got["error"])
    got["facts"]["by"] = "a background hython on a working copy of the open scene (written with mwrite -n: the open file keeps its name)"
    got["facts"]["total_seconds"] = round(time.time() - started, 1)
    return got


image = None
if P.get("render"):
    STEP(step="render")
    rendered = render_image(render_options(P["render"]))
    facts["render"] = rendered["facts"]
    image = rendered.get("image_base64")
    if license_name() in ("Apprentice", "ApprenticeHD", "Education"):
        facts["render"]["license_note"] = ("%s: every image carries the Houdini logo in its bottom-right corner "
                                           "(read apart: corner_max_luminance)" % license_name())
facts["seconds"] = round(time.time() - STARTED, 2)
passed = sum(1 for r in rows if r["passed"])
result = {"houdini": {"version": hou.applicationVersionString(), "license": license_name()},
          "passed": passed, "failed": len(rows) - passed, "all_passed": passed == len(rows),
          "failed_labels": [r["label"] for r in rows if not r["passed"]], "criteria": rows, "errors": error_rows,
          "facts": facts, **({"image_base64": image} if image else {})}
