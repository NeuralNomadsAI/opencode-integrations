# SPDX-License-Identifier: MIT
# houdini_inspect (fixed program, read only): what THIS Houdini really exposes for a node type, a live node or a
# hou symbol, or where words appear among its node types, parameters and hou API, with the SideFX documentation
# of this version. Everything is read from the running Houdini (node types, parm templates, docstrings, the
# cooked geometry or USD stage); nothing is created.
# PARAMS is prepended by the plugin: {"search"?, "type"?, "category"?, "node"?, "hou"?, "match"?, "limit"?}
import difflib
import fnmatch
import inspect as _inspect
import math
import sys
import time
import types

import hou

P = PARAMS  # noqa: F821
VERSION = hou.applicationVersion()
VER = "%d.%d" % VERSION[:2]
DOCS = "https://www.sidefx.com/docs/houdini%s/" % VER
LIMIT = max(1, min(200, int(P.get("limit") or 40)))
MATCH = P.get("match")
NAMES_MAX = 300
VALUE_CHARS = 300
# Category -> docs folder; categories missing here use their lower-case name.
DOC_DIRS = {"Object": "obj", "Driver": "out", "Sop": "sop", "Lop": "lop", "Top": "top", "Dop": "dop", "Vop": "vop",
            "Cop": "copernicus", "Cop2": "cop2", "Chop": "chop", "Shop": "shop", "TopNet": "top", "ChopNet": "chop",
            "CopNet": "cop2", "VopNet": "vop", "Data": "data"}
PREFERRED = ("Sop", "Lop", "Object", "Driver", "Top", "Dop", "Cop", "Vop", "Chop", "Cop2", "Shop")


def license_name():
    try:
        return str(hou.licenseCategory()).rsplit(".", 1)[-1]
    except Exception:  # noqa: BLE001
        return "unknown"


out = {"houdini": {"version": hou.applicationVersionString(), "license": license_name(),
                   "python": "%d.%d" % sys.version_info[:2], "ui": hou.isUIAvailable()},
       "links": {"docs": DOCS, "hom": DOCS + "hom/hou/index.html", "nodes": DOCS + "nodes/index.html"}}


def num(v):
    if isinstance(v, float):
        return round(v, 6) if math.isfinite(v) else str(v)
    return v


def plain(v, depth=0):
    """A JSON-ready, bounded copy of a parameter value or a hou object."""
    if isinstance(v, (bool, int)) or v is None:
        return v
    if isinstance(v, float):
        return num(v)
    if isinstance(v, str):
        return v if len(v) <= VALUE_CHARS else v[:VALUE_CHARS] + "... (%d chars)" % len(v)
    if hasattr(v, "path") and callable(v.path):
        try:
            return v.path()
        except Exception:  # noqa: BLE001
            pass
    if depth < 2 and isinstance(v, (list, tuple)) or (depth < 2 and hasattr(v, "__iter__") and type(v).__module__ == "hou"):
        try:
            items = list(v)
        except Exception:  # noqa: BLE001
            return repr(v)[:VALUE_CHARS]
        return [plain(x, depth + 1) for x in items[:32]] + (["... (%d)" % len(items)] if len(items) > 32 else [])
    return repr(v)[:VALUE_CHARS]


def matches(*texts):
    """match: a glob on the name when it has * ? or [, otherwise words all found in name, label or help."""
    if not MATCH:
        return True
    if any(c in MATCH for c in "*?["):
        return fnmatch.fnmatchcase(texts[0], MATCH)
    hay = " ".join(t or "" for t in texts).lower()
    return all(w in hay for w in MATCH.lower().split())


def doc_url(node_type):
    """The SideFX page of a node type in this version: nodes/<category>/<[namespace--]name[-version]>.html."""
    category = node_type.category().name()
    scope, ns, name, version = node_type.nameComponents()
    page = (ns + "--" if ns else "") + name + ("-" + version if version else "")
    return "%snodes/%s/%s.html" % (DOCS, DOC_DIRS.get(category, category.lower()), page)


def hom_url(path):
    """hou.Node.createNode -> hom/hou/Node.html#createNode; hou.node -> hom/hou/node.html."""
    parts = path.split(".")[1:]
    if not parts:
        return DOCS + "hom/hou/index.html"
    if len(parts) == 1:
        return "%shom/hou/%s.html" % (DOCS, parts[0])
    return "%shom/hou/%s.html#%s" % (DOCS, parts[0], parts[1])


SCHEMES = {"XYZW": "xyzw", "RGBA": "rgba", "UVW": "uvw", "MinMax": ["min", "max"], "MaxMin": ["max", "min"],
           "StartEnd": ["start", "end"], "BeginEnd": ["begin", "end"], "XYWH": "xywh"}


def parm_names(t):
    """The real parameter names a template makes (size with XYZW -> sizex, sizey, sizez)."""
    n = t.numComponents() if hasattr(t, "numComponents") else 1
    if n <= 1:
        return [t.name()]
    scheme = str(t.namingScheme()).rsplit(".", 1)[-1]
    suffixes = SCHEMES.get(scheme)
    if suffixes is None:
        return ["%s%d" % (t.name(), i + 1) for i in range(n)]
    return [t.name() + s for s in list(suffixes)[:n]]


def template_info(t, folder=None):
    kind = str(t.type()).rsplit(".", 1)[-1]
    d = {"name": t.name(), "label": t.label(), "type": kind}
    names = parm_names(t)
    if names != [t.name()]:
        d["parms"] = names
    if folder:
        d["folder"] = folder
    try:
        default = t.defaultValue()
        d["default"] = plain(default[0] if isinstance(default, tuple) and len(default) == 1 else default)
    except Exception:  # noqa: BLE001
        pass
    try:
        if t.defaultExpression() and any(t.defaultExpression()):
            d["default_expression"] = [e for e in t.defaultExpression()][:4]
    except Exception:  # noqa: BLE001
        pass
    for attr in ("minValue", "maxValue"):
        try:
            d.setdefault("range", []).append(num(getattr(t, attr)()))
        except Exception:  # noqa: BLE001
            pass
    try:
        items = list(t.menuItems())
        if items:
            labels = list(t.menuLabels())
            d["menu"] = [{"token": i, "label": l} if i != l else i for i, l in zip(items, labels)][:40]
            if len(items) > 40:
                d["menu_more"] = len(items) - 40
        elif t.itemGeneratorScript():
            d["menu"] = "dynamic (read it from a live node: menuItems() of its parm)"
    except Exception:  # noqa: BLE001
        pass
    if kind == "Folder":
        try:
            if "Multiparm" in str(t.folderType()):
                d["multiparm"] = True
                d["instances"] = [c.name() for c in t.parmTemplates()][:20]
        except Exception:  # noqa: BLE001
            pass
    try:
        if t.isHidden():
            d["hidden"] = True
    except Exception:  # noqa: BLE001
        pass
    try:
        help_text = t.help()
        if help_text:
            d["help"] = " ".join(help_text.split())[:160]
    except Exception:  # noqa: BLE001
        pass
    return d


def walk_templates(group_entries, folder=None):
    for t in group_entries:
        kind = str(t.type()).rsplit(".", 1)[-1]
        if kind == "Folder" and "Multiparm" not in str(getattr(t, "folderType", lambda: "")()):
            yield from walk_templates(t.parmTemplates(), ((folder + "/") if folder else "") + t.label())
        elif kind not in ("FolderSet", "Separator", "Label"):
            yield t, folder


def detailed(items, info, key, d, name_of):
    d[key] = [info(i) for i in items[:LIMIT]]
    rest = [name_of(i) for i in items[LIMIT:]]
    if rest:
        d[key + "_more"] = {"count": len(rest), "names": rest[:NAMES_MAX], "details": "pass match (a glob or words) or a larger limit"}


def find_types(name, category=None):
    cats = hou.nodeTypeCategories()
    if "/" in name and category is None:
        category, name = name.split("/", 1)
    if category is not None:
        cat = cats.get(category) or next((c for k, c in cats.items() if k.lower() == str(category).lower()), None)
        if cat is None:
            raise ValueError("category %r: one of %s" % (category, sorted(k for k, c in cats.items() if c.nodeTypes())))
        chosen = [cat]
    else:
        order = {k: i for i, k in enumerate(PREFERRED)}
        chosen = sorted(cats.values(), key=lambda c: order.get(c.name(), 99))
    found = []
    for cat in chosen:
        t = cat.nodeTypes().get(name)
        if t is None:
            # A name without namespace or version: the type it resolves to (the newest one preferred).
            try:
                resolved = hou.preferredNodeType("%s/%s" % (cat.name(), name))
            except Exception:  # noqa: BLE001
                resolved = None
            t = resolved
        if t is not None:
            found.append(t)
    return found, chosen


def type_info(t):
    d = {"type": t.nameWithCategory(), "description": t.description(), "doc": doc_url(t),
         "inputs": {"min": t.minNumInputs(), "max": t.maxNumInputs()}, "outputs": {"max": t.maxNumOutputs()},
         "generator": t.isGenerator()}
    child = t.childTypeCategory()
    if child is not None:
        d["contains"] = child.name()
    try:
        versions = list(t.namespaceOrder())
        if len(versions) > 1:
            d["versions"] = versions[:12]
    except Exception:  # noqa: BLE001
        pass
    definition = t.definition()
    if definition is not None:
        d["hda"] = {"library": definition.libraryFilePath(), "version": definition.version(),
                    "embedded": definition.libraryFilePath() == "Embedded", "installed": definition.isInstalled()}
        try:
            labels = definition.sections().get("DialogScript") is not None
            d["hda"]["sections"] = sorted(definition.sections())[:30] if labels else []
        except Exception:  # noqa: BLE001
            pass
        try:
            comment = definition.comment()
            if comment:
                d["hda"]["comment"] = comment[:200]
        except Exception:  # noqa: BLE001
            pass
    templates = [(tt, f) for tt, f in walk_templates(t.parmTemplateGroup().entries()) if matches(tt.name(), tt.label(), "")]
    detailed(templates, lambda x: template_info(*x), "parm_templates", d, lambda x: x[0].name())
    if MATCH and not templates:
        d["match_found_nothing"] = {"match": MATCH, "parms": [tt.name() for tt, _ in walk_templates(t.parmTemplateGroup().entries())][:NAMES_MAX]}
    return d


if P.get("type"):
    wanted = str(P["type"])
    found, cats = find_types(wanted, P.get("category"))
    if not found:
        bare = wanted.split("/", 1)[-1]
        close = []
        for cat in cats:
            names = list(cat.nodeTypes())
            close += ["%s/%s" % (cat.name(), n) for n in difflib.get_close_matches(bare, names, n=4, cutoff=0.6)]
            close += ["%s/%s" % (cat.name(), n) for n, nt in cat.nodeTypes().items()
                      if nt.description().lower() == bare.lower() and "%s/%s" % (cat.name(), n) not in close]
        out["type"] = {"error": "no node type %r in this Houdini%s" % (wanted, " (category %s)" % P["category"] if P.get("category") else ""),
                       "closest": close[:20], "details": "pass Category/name, e.g. Sop/box, or search words"}
    else:
        out["type"] = type_info(found[0])
        if len(found) > 1:
            out["type"]["also_in"] = [t.nameWithCategory() for t in found[1:]]


# --- live node ----------------------------------------------------------------------------------------------
def parm_entry(p):
    d = {"name": p.name()}
    try:
        d["value"] = plain(p.eval())
    except Exception as ex:  # noqa: BLE001
        d["error"] = str(ex)[:160]
    try:
        raw = p.unexpandedString()
        if raw != d.get("value"):
            d["raw"] = plain(raw)
    except Exception:  # noqa: BLE001
        pass
    try:
        d["expression"] = p.expression()
        d["language"] = str(p.expressionLanguage()).rsplit(".", 1)[-1]
    except Exception:  # noqa: BLE001
        pass
    try:
        keys = p.keyframes()
        if keys:
            d["keyframes"] = len(keys)
    except Exception:  # noqa: BLE001
        pass
    try:
        ref = p.getReferencedParm()
        if ref is not None and ref.path() != p.path():
            d["references"] = ref.path()
    except Exception:  # noqa: BLE001
        pass
    try:
        if p.isLocked():
            d["locked"] = True
        if p.isDisabled():
            d["disabled"] = True
    except Exception:  # noqa: BLE001
        pass
    try:
        items = p.menuItems()
        if items:
            d["menu"] = list(items)[:30]
    except Exception:  # noqa: BLE001
        pass
    return d


FLAGS = ("isDisplayFlagSet", "isRenderFlagSet", "isBypassed", "isTemplateFlagSet", "isHardLocked", "isSoftLocked",
         "isLockedHDA", "isEditable", "isTimeDependent")


def flags_of(node):
    d = {}
    for name in FLAGS:
        fn = getattr(node, name, None)
        if fn is None:
            continue
        try:
            d[name[2:] if name.startswith("is") else name] = bool(fn())
        except Exception:  # noqa: BLE001
            pass
    return d


def messages(node):
    d = {}
    for key in ("errors", "warnings", "messages"):
        try:
            values = [" ".join(m.split())[:400] for m in getattr(node, key)()]
        except Exception:  # noqa: BLE001
            values = []
        if values:
            d[key] = values[:10]
    return d


def geometry_summary(geo):
    if geo is None:
        return {"none": "no geometry (did it cook?)"}
    d = {"points": len(geo.iterPoints()) if hasattr(geo, "iterPoints") else len(geo.points()),
         "prims": geo.intrinsicValue("primitivecount"), "vertices": geo.intrinsicValue("vertexcount")}
    try:
        box = geo.boundingBox()
        if d["points"]:
            d["bbox"] = {"min": [num(x) for x in box.minvec()], "max": [num(x) for x in box.maxvec()],
                         "size": [num(x) for x in box.sizevec()], "center": [num(x) for x in box.center()]}
    except Exception:  # noqa: BLE001
        pass
    attribs = {}
    for cls, fn in (("point", "pointAttribs"), ("prim", "primAttribs"), ("vertex", "vertexAttribs"), ("detail", "globalAttribs")):
        try:
            values = ["%s:%s%s" % (a.name(), str(a.dataType()).rsplit(".", 1)[-1].lower(), a.size() if a.size() > 1 else "")
                      for a in getattr(geo, fn)()]
        except Exception:  # noqa: BLE001
            values = []
        if values:
            attribs[cls] = values[:60]
    d["attributes"] = attribs
    groups = {}
    for cls, fn in (("point", "pointGroups"), ("prim", "primGroups"), ("edge", "edgeGroups"), ("vertex", "vertexGroups")):
        try:
            names = [g.name() for g in getattr(geo, fn)()]
        except Exception:  # noqa: BLE001
            names = []
        if names:
            groups[cls] = names[:40]
    if groups:
        d["groups"] = groups
    try:
        kinds = {}
        for prim in geo.prims()[:20000]:
            key = prim.type().name()
            kinds[key] = kinds.get(key, 0) + 1
        d["prim_types"] = dict(sorted(kinds.items(), key=lambda kv: -kv[1])[:12])
    except Exception:  # noqa: BLE001
        pass
    return d


def stage_summary(stage):
    if stage is None:
        return {"none": "no stage (did it cook?)"}
    prims = []
    total = 0
    types_count = {}
    for prim in stage.Traverse():
        total += 1
        kind = prim.GetTypeName() or ""
        types_count[kind or "(untyped)"] = types_count.get(kind or "(untyped)", 0) + 1
        if len(prims) < max(LIMIT, 60) and matches(str(prim.GetPath()), kind, ""):
            prims.append({"path": str(prim.GetPath()), "type": kind, **({"inactive": True} if not prim.IsActive() else {})})
    d = {"prims": total, "types": dict(sorted(types_count.items(), key=lambda kv: -kv[1])[:20]), "listed": prims}
    if total > len(prims):
        d["listed_more"] = total - len(prims)
    try:
        default = stage.GetDefaultPrim()
        d["default_prim"] = str(default.GetPath()) if default else None
        d["time_codes"] = [num(stage.GetStartTimeCode()), num(stage.GetEndTimeCode())]
        d["up_axis"] = stage.GetMetadata("upAxis")
        d["layers"] = len(stage.GetLayerStack())
    except Exception:  # noqa: BLE001
        pass
    return d


def node_info(node):
    t = node.type()
    d = {"path": node.path(), "type": t.nameWithCategory(), "description": t.description(), "doc": doc_url(t),
         "flags": flags_of(node)}
    try:
        d["inputs"] = [{"index": i, "from": c.path() if c is not None else None,
                        **({"label": node.inputLabels()[i]} if i < len(node.inputLabels()) else {})}
                       for i, c in enumerate(node.inputs())]
        d["outputs"] = [c.path() for c in node.outputs()][:40]
    except Exception:  # noqa: BLE001
        pass
    d.update(messages(node))
    parms = [p for p in node.parms() if matches(p.name(), p.description(), "")]
    changed = [p for p in parms if not p.isAtDefault()]
    default = [p for p in parms if p.isAtDefault()]
    detailed(changed, parm_entry, "parms_changed", d, lambda p: p.name())
    if default:
        d["parms_at_default"] = {"count": len(default), "names": [p.name() for p in default][:NAMES_MAX],
                                 "details": "pass match to read them with their values"}
        if MATCH:
            d["parms_at_default"]["values"] = [parm_entry(p) for p in default[:LIMIT]]
    children = node.children()
    if children:
        d["children"] = [{"name": c.name(), "type": c.type().name(), **{k: v for k, v in flags_of(c).items() if v and k in ("DisplayFlagSet", "RenderFlagSet", "Bypassed")},
                          **({"errors": len(c.errors())} if c.errors() else {})} for c in children[:LIMIT]]
        if len(children) > LIMIT:
            d["children_more"] = {"count": len(children) - LIMIT, "names": [c.name() for c in children[LIMIT:LIMIT + NAMES_MAX]]}
        for getter in ("displayNode", "renderNode"):
            fn = getattr(node, getter, None)
            try:
                target = fn() if fn else None
            except Exception:  # noqa: BLE001
                target = None
            if target is not None:
                d[getter.replace("Node", "_node")] = target.path()
    category = t.category().name()
    try:
        if category == "Sop":
            d["geometry"] = geometry_summary(node.geometry())
        elif category == "Object" and getattr(node, "displayNode", None) and node.displayNode() is not None:
            d["geometry"] = dict(geometry_summary(node.displayNode().geometry()), of=node.displayNode().path())
            d["transform"] = {"world_translate": [num(x) for x in node.worldTransform().extractTranslates()]}
        elif category == "Lop":
            d["stage"] = stage_summary(node.stage())
        elif category == "Top" and hasattr(node, "getPDGNode"):
            pdg_node = node.getPDGNode()
            if pdg_node is not None:
                states = {}
                for item in pdg_node.workItems:
                    key = str(item.state).rsplit(".", 1)[-1]
                    states[key] = states.get(key, 0) + 1
                d["work_items"] = states
        elif category == "Driver":
            for name in ("picture", "sopoutput", "lopoutput", "vm_picture", "outputimage", "copoutput", "filename"):
                p = node.parm(name)
                if p is not None:
                    d.setdefault("output", {})[name] = p.eval()
            for name in ("trange", "f1", "f2", "f3"):
                p = node.parm(name)
                if p is not None:
                    d.setdefault("frame_range", {})[name] = plain(p.eval())
    except Exception as ex:  # noqa: BLE001
        d["read_error"] = ("%s: %s" % (type(ex).__name__, ex))[:400]
    d.update({k: v for k, v in messages(node).items() if k not in d})
    return d


if P.get("node"):
    node = hou.node(str(P["node"]))
    if node is None:
        path = str(P["node"])
        parts = [p for p in path.split("/") if p]
        parent, depth = None, len(parts)
        while depth > 0 and parent is None:
            depth -= 1
            parent = hou.node("/" + "/".join(parts[:depth]))
        names = [c.name() for c in parent.children()] if parent is not None else []
        missing = parts[depth] if depth < len(parts) else ""
        out["node"] = {"error": "no node %r" % path, "deepest_existing": parent.path() if parent is not None else None,
                       "closest": difflib.get_close_matches(missing, names, n=6, cutoff=0.5), "children": names[:NAMES_MAX]}
    else:
        out["node"] = node_info(node)


# --- hou symbol ---------------------------------------------------------------------------------------------
def doc_head(obj, lines=40):
    text = (getattr(obj, "__doc__", None) or "").strip("\n")
    rows = text.splitlines()
    return "\n".join(rows[:lines]) + ("\n... (%d more lines)" % (len(rows) - lines) if len(rows) > lines else "")


if P.get("hou"):
    path = str(P["hou"]).strip()
    if not path.startswith("hou"):
        path = "hou." + path
    target, reached, missing = hou, ["hou"], None
    for part in path.split(".")[1:]:
        nxt = getattr(target, part, None)
        if nxt is None:
            missing = part
            break
        target = nxt
        reached.append(part)
    if missing is not None:
        names = [n for n in dir(target) if not n.startswith("_")]
        out["hou"] = {"error": "%s has no %r" % (".".join(reached), missing),
                      "closest": [".".join(reached + [n]) for n in difflib.get_close_matches(missing, names, n=8, cutoff=0.5)]}
        # Methods moved between classes (hou.Node -> hou.OpNode): where else the name exists.
        owners = [n for n in dir(hou) if _inspect.isclass(getattr(hou, n, None)) and missing in vars(getattr(hou, n))]
        if owners:
            out["hou"]["defined_on"] = ["hou.%s.%s" % (o, missing) for o in owners[:12]]
    else:
        info = {"path": path, "kind": "class" if _inspect.isclass(target) else "module" if isinstance(target, types.ModuleType)
                else "callable" if callable(target) else type(target).__name__, "doc": doc_head(target), "url": hom_url(path)}
        if _inspect.isclass(target):
            own = [n for n in vars(target) if not n.startswith("_") and n != "thisown"]
            info["bases"] = [b.__name__ for b in target.__mro__[1:] if b.__module__ == "hou"]
            info["methods"] = sorted(n for n in own if matches(n, (getattr(getattr(target, n, None), "__doc__", "") or "")[:200]))[:NAMES_MAX]
            inherited = sorted(n for n in dir(target) if not n.startswith("_") and n not in own and n != "thisown")
            if inherited:
                info["inherited"] = inherited[:NAMES_MAX]
        elif not callable(target) and not isinstance(target, types.ModuleType):
            info["value"] = plain(target)
        out["hou"] = info


# --- search -------------------------------------------------------------------------------------------------
def parm_index():
    """(category, type, parm, label) for every parm template of every node type, kept for this Houdini session."""
    cache = sys.modules.get("_opencode_houdini_inspect_cache")
    if cache is None:
        cache = types.ModuleType("_opencode_houdini_inspect_cache")
        sys.modules[cache.__name__] = cache
    if getattr(cache, "parms", None) is None:
        rows = []
        for cname, cat in hou.nodeTypeCategories().items():
            for tname, t in cat.nodeTypes().items():
                try:
                    entries = t.parmTemplateGroup().entriesWithoutFolders()
                except Exception:  # noqa: BLE001
                    continue
                for e in entries:
                    # The template (size) and the parameters it makes (sizex, sizey, sizez).
                    for name in dict.fromkeys([e.name()] + parm_names(e)):
                        rows.append((cname, tname, name, e.label()))
        cache.parms = rows
    return cache.parms


def search(text):
    words = [w.lower() for w in str(text).split() if w]
    if not words:
        raise ValueError("search: give one or more words")
    started = time.time()
    category = P.get("category")

    def score(name, label, desc):
        name, label = name.lower(), (label or "").lower()
        hay = " ".join((name, label, (desc or "").lower()))
        if not all(w in hay for w in words):
            return 0
        return sum(3 if w in name else 2 if w in label else 1 for w in words) + (2 if name in words else 0)

    hits = []
    for cname, cat in hou.nodeTypeCategories().items():
        if category and cname.lower() != str(category).lower():
            continue
        for tname, t in cat.nodeTypes().items():
            try:
                hidden = t.hidden() if hasattr(t, "hidden") else False
            except Exception:  # noqa: BLE001
                hidden = False
            s = score(tname, t.description(), "")
            if s:
                # Node types first, the hidden and versioned variants after the plain one.
                hits.append((s + 3 - (0.5 if hidden or "::" in tname else 0), {"kind": "node type", "path": "%s/%s" % (cname, tname),
                                                                                "description": t.description(), "doc": doc_url(t)}))
    by_parm = {}
    for cname, tname, pname, label in parm_index():
        if category and cname.lower() != str(category).lower():
            continue
        s = score(pname, label, "")
        if s:
            entry = by_parm.setdefault(pname, [s, [], []])
            entry[0] = max(entry[0], s)
            if label not in entry[1]:
                entry[1].append(label)
            entry[2].append("%s/%s" % (cname, tname))
    for pname, (s, labels, owners) in by_parm.items():
        hits.append((s - 1, {"kind": "parm", "path": pname, "labels": labels[:4], "on": owners[:8],
                             **({"on_count": len(owners)} if len(owners) > 8 else {})}))
    if not category:
        for name in dir(hou):
            if name.startswith("_"):
                continue
            obj = getattr(hou, name, None)
            doc = (getattr(obj, "__doc__", None) or "").strip()
            first = " ".join(doc.splitlines()[2:4]) if doc.startswith("hou.") else " ".join(doc.splitlines()[:2])
            s = score(name, "", first[:200])
            if s:
                hits.append((s, {"kind": "hou class" if _inspect.isclass(obj) else "hou function" if callable(obj) else "hou value",
                                 "path": "hou." + name, "doc": first[:160], "url": hom_url("hou." + name)}))
            if _inspect.isclass(obj):
                for m in vars(obj):
                    if m.startswith("_") or m == "thisown":
                        continue
                    mdoc = (getattr(getattr(obj, m, None), "__doc__", None) or "").strip()
                    sig = mdoc.splitlines()[0] if mdoc else ""
                    s = score(m, "", " ".join(mdoc.split()[:40]))
                    if s:
                        hits.append((s - 0.25, {"kind": "hou method", "path": "hou.%s.%s" % (name, m), "signature": sig[:200],
                                                "url": hom_url("hou.%s.%s" % (name, m))}))
    hits.sort(key=lambda h: (-h[0], len(h[1]["path"]), h[1]["path"]))
    found = {"words": words, "found": len(hits), "results": [h for _, h in hits[:LIMIT]], "seconds": round(time.time() - started, 2)}
    if len(hits) > LIMIT:
        found["more"] = {"count": len(hits) - LIMIT, "paths": [h[1]["path"] for h in hits[LIMIT:LIMIT + NAMES_MAX]],
                         "details": "add words or category, or inspect a type, node or hou path"}
    return found


if P.get("search"):
    out["search"] = search(P["search"])

if not any(P.get(k) for k in ("search", "type", "node", "hou")):
    raise ValueError("give search (words), type (e.g. Sop/box), node (a node path) or hou (e.g. hou.OpNode.createNode)")

result = out
