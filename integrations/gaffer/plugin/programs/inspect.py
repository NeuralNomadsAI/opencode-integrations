# SPDX-License-Identifier: MIT
# gaffer_inspect (fixed program, read only): what THIS Gaffer really exposes for a node class, a live node or a
# Python symbol, or where words appear among its node classes, plugs and their descriptions, with the Gaffer
# documentation of this version. Everything is read from the running Gaffer (node classes, plugs, Metadata,
# computed scenes and images); nothing in the script is created or changed.
# PARAMS is prepended by the plugin: {"search"?, "type"?, "node"?, "gaffer"?, "match"?, "limit"?}
# The namespace holds Gaffer, GafferScene, GafferImage, GafferDispatch, IECore, imath, script and application.
import difflib
import fnmatch
import importlib
import inspect as _inspect
import math
import os
import pathlib
import sys
import time
import types

import Gaffer
import GafferDispatch
import GafferImage
import GafferScene
import IECore
import imath

P = PARAMS  # noqa: F821
S = script  # noqa: F821
VERSION = Gaffer.About.versionString()
LIMIT = max(1, min(200, int(P.get("limit") or 40)))
MATCH = P.get("match")
NAMES_MAX = 300
VALUE_CHARS = 300
LOCATIONS_MAX = 200
ONLINE = "https://www.gafferhq.org/documentation/%s/" % VERSION
LOCAL = os.path.join(os.environ.get("GAFFER_ROOT", ""), "doc", "gaffer", "html")
HAS_LOCAL = bool(os.environ.get("GAFFER_ROOT")) and os.path.isfile(os.path.join(LOCAL, "index.html"))


def ui_descriptions():
    """Plug and node descriptions are registered by the *UI modules: imported here when Gaffer runs without them."""
    try:
        import GafferDispatchUI  # noqa: F401
        import GafferImageUI  # noqa: F401
        import GafferSceneUI  # noqa: F401
        import GafferUI  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


DESCRIPTIONS = ui_descriptions()


def doc(page):
    """A page of this version's documentation: the local copy when Gaffer ships it, else gafferhq.org."""
    local = os.path.join(LOCAL, *page.split("/"))
    if HAS_LOCAL and os.path.isfile(local):
        return pathlib.Path(local).as_uri()
    return ONLINE + page


out = {"gaffer": {"version": VERSION, "python": "%d.%d" % sys.version_info[:2], "ui": "GafferUI" in sys.modules,
                  "descriptions": DESCRIPTIONS},
       "links": {"docs": doc("index.html"), "nodes": doc("Reference/NodeReference/index.html"),
                 "scripting": doc("WorkingWithThePythonScriptingAPI/index.html"),
                 "substitutions": doc("Reference/ScriptingReference/StringSubstitutionSyntax/index.html")}}


def num(v):
    if isinstance(v, float):
        return round(v, 6) if math.isfinite(v) else str(v)
    return v


def plain(v, depth=0):
    """A JSON-ready, bounded copy of a plug value, an imath or IECore value."""
    if isinstance(v, (bool, int)) or v is None:
        return v
    if isinstance(v, float):
        return num(v)
    if isinstance(v, str):
        return v if len(v) <= VALUE_CHARS else v[:VALUE_CHARS] + "... (%d chars)" % len(v)
    if isinstance(v, (imath.Box3f, imath.Box3d, imath.Box2i, imath.Box2f)):
        return {"min": plain(v.min(), depth + 1), "max": plain(v.max(), depth + 1)}
    if isinstance(v, IECore.Data) and hasattr(v, "value"):
        return plain(v.value, depth)
    if isinstance(v, IECore.CompoundData) or isinstance(v, IECore.CompoundObject):
        return {k: plain(v[k], depth + 1) for k in list(v.keys())[:32]} if depth < 2 else repr(v)[:VALUE_CHARS]
    if isinstance(v, Gaffer.GraphComponent):
        return v.relativeName(S) if S.isAncestorOf(v) else v.fullName()
    if depth < 2 and not isinstance(v, (dict, bytes)):
        try:
            items = list(v)
        except Exception:  # noqa: BLE001
            return repr(v)[:VALUE_CHARS]
        return [plain(x, depth + 1) for x in items[:32]] + (["... (%d)" % len(items)] if len(items) > 32 else [])
    return repr(v)[:VALUE_CHARS]


def matches(*texts):
    """match: a glob on the name when it has * ? or [, otherwise words all found in name or description."""
    if not MATCH:
        return True
    if any(c in MATCH for c in "*?["):
        return fnmatch.fnmatchcase(texts[0], MATCH)
    hay = " ".join(t or "" for t in texts).lower()
    return all(w in hay for w in MATCH.lower().split())


def described(target):
    try:
        text = Gaffer.Metadata.value(target, "description")
    except Exception:  # noqa: BLE001
        return ""
    return " ".join(str(text).split()) if text else ""


def class_name(cls):
    return "%s.%s" % (cls.__module__.split(".")[0], cls.__name__)


def type_page(cls):
    return doc("Reference/NodeReference/%s/%s.html" % (cls.__module__.split(".")[0], cls.__name__))


def node_modules():
    """The loaded Gaffer modules holding node classes (not the UI, test or private ones)."""
    names = sorted(n for n in sys.modules if n.startswith("Gaffer") and "." not in n and not n.endswith(("UI", "Test")))
    return [(n, sys.modules[n]) for n in names if sys.modules[n] is not None]


def node_classes(module):
    found = []
    for name in dir(module):
        cls = getattr(module, name, None)
        if _inspect.isclass(cls) and issubclass(cls, Gaffer.Node) and not name.startswith("_") \
                and cls.__module__.split(".")[0] == module.__name__:
            found.append(cls)
    return found


def walk_plugs(parent, prefix=""):
    """Every plug under parent, depth first, with its path relative to the node; internal (__) plugs left out."""
    for plug in parent.children(Gaffer.Plug):
        name = plug.getName()
        if name.startswith("__"):
            continue
        path = prefix + name
        yield path, plug
        if len(plug) and not isinstance(plug, (GafferScene.ScenePlug, GafferImage.ImagePlug)):
            yield from walk_plugs(plug, path + ".")


def presets(plug):
    try:
        names = list(Gaffer.NodeAlgo.presets(plug))
    except Exception:  # noqa: BLE001
        return []
    return names[:40]


def plug_template(path, plug):
    d = {"name": path, "type": type(plug).__name__}
    if plug.direction() == Gaffer.Plug.Direction.Out:
        d["direction"] = "out"
    if hasattr(plug, "defaultValue") and not isinstance(plug, (GafferScene.ScenePlug, GafferImage.ImagePlug)):
        try:
            d["default"] = plain(plug.defaultValue())
        except Exception:  # noqa: BLE001
            pass
    for attr in ("minValue", "maxValue"):
        try:
            if getattr(plug, "has" + attr[0].upper() + attr[1:])():
                d.setdefault("range", {})[attr[:3]] = plain(getattr(plug, attr)())
        except Exception:  # noqa: BLE001
            pass
    names = presets(plug)
    if names:
        d["presets"] = names
    text = described(plug)
    if text:
        d["description"] = text[:240]
    return d


def detailed(items, info, key, d, name_of):
    d[key] = [info(i) for i in items[:LIMIT]]
    rest = [name_of(i) for i in items[LIMIT:]]
    if rest:
        d[key + "_more"] = {"count": len(rest), "names": rest[:NAMES_MAX], "details": "pass match (a glob or words) or a larger limit"}


def find_class(name):
    """GafferScene.Sphere, GafferScene::Sphere or Sphere (searched in the loaded modules)."""
    name = name.replace("::", ".")
    if "." in name:
        module_name, _, cls_name = name.rpartition(".")
        try:
            module = sys.modules.get(module_name) or importlib.import_module(module_name)
        except Exception:  # noqa: BLE001
            return None, []
        cls = getattr(module, cls_name, None)
        return (cls if _inspect.isclass(cls) and issubclass(cls, Gaffer.Node) else None), [module]
    hits = [c for _, m in node_modules() for c in node_classes(m) if c.__name__ == name]
    return (hits[0] if hits else None), hits[1:]


def instance(cls):
    """A node of the class, made outside the script: node metadata (descriptions) is read through an instance."""
    try:
        return cls(), None
    except Exception as ex:  # noqa: BLE001
        return None, ("%s: %s" % (type(ex).__name__, ex))[:300]


def type_info(cls):
    node, error = instance(cls)
    d = {"type": class_name(cls), "description": described(node) if node is not None else "", "doc": type_page(cls),
         "bases": [class_name(b) for b in cls.__mro__[1:] if _inspect.isclass(b) and issubclass(b, Gaffer.Node)][:8]}
    if issubclass(cls, GafferDispatch.TaskNode):
        d["task"] = True
    if node is None:
        d["plugs_error"] = error
        return d
    plugs = [(p, x) for p, x in walk_plugs(node) if matches(p, described(x))]
    detailed(plugs, lambda x: plug_template(*x), "plugs", d, lambda x: x[0])
    if MATCH and not plugs:
        d["match_found_nothing"] = {"match": MATCH, "plugs": [p for p, _ in walk_plugs(node)][:NAMES_MAX]}
    return d


if P.get("type"):
    wanted = str(P["type"])
    cls, also = find_class(wanted)
    if cls is None:
        bare = wanted.replace("::", ".").rpartition(".")[2]
        everything = {class_name(c): c.__name__ for _, m in node_modules() for c in node_classes(m)}
        close = [full for full, short in everything.items() if short in difflib.get_close_matches(bare, list(everything.values()), n=6, cutoff=0.6)]
        out["type"] = {"error": "no node class %r in this Gaffer" % wanted, "closest": sorted(close)[:20],
                       "loaded_modules": [n for n, _ in node_modules()], "details": "pass Module.Class, e.g. GafferScene.Sphere, or search words"}
    else:
        out["type"] = type_info(cls)
        if also and _inspect.isclass(also[0]):
            out["type"]["also_in"] = [class_name(c) for c in also]


# --- live node ----------------------------------------------------------------------------------------------
def resolve(path):
    """A node or plug of the script: Box.sphere, Box/sphere or /Box/sphere."""
    text = str(path).strip().strip("/").replace("/", ".")
    return S.descendant(text) if text else S


def plug_entry(path, plug):
    d = {"name": path, "type": type(plug).__name__}
    source = plug.getInput()
    if source is not None:
        d["from"] = plain(source)
    if isinstance(plug, Gaffer.ValuePlug) and not isinstance(plug, (GafferScene.ScenePlug, GafferImage.ImagePlug, Gaffer.ObjectPlug)) \
            and not len(plug):
        try:
            d["value"] = plain(plug.getValue())
        except Exception as ex:  # noqa: BLE001
            d["error"] = ("%s: %s" % (type(ex).__name__, " ".join(str(ex).split())))[:300]
    targets = [o for o in plug.outputs() if o.node() is not None and not o.node().isSame(plug.node())]
    if targets:
        d["to"] = [plain(o) for o in targets[:12]]
    return d


def scene_summary(scene):
    d = {}
    started = time.time()
    try:
        d["bound"] = plain(scene.bound("/"))
        d["sets"] = {}
        for name in list(scene.setNames())[:40]:
            d["sets"][str(name)] = scene.set(name).value.size()
        g = scene.globals()
        d["globals"] = sorted(str(k) for k in g.keys())[:60]
        listed, total, queue = [], 0, ["/"]
        while queue and time.time() - started < 10:
            location = queue.pop(0)
            names = [str(n) for n in scene.childNames(location)]
            for n in names:
                child = location.rstrip("/") + "/" + n
                total += 1
                if len(listed) < max(LIMIT, LOCATIONS_MAX // 4) and matches(child, ""):
                    entry = {"path": child}
                    obj = scene.object(child)
                    if not isinstance(obj, IECore.NullObject):
                        entry["object"] = obj.typeName()
                    attributes = scene.attributes(child)
                    if len(attributes):
                        entry["attributes"] = sorted(attributes.keys())[:20]
                    listed.append(entry)
                if total < LOCATIONS_MAX:
                    queue.append(child)
        d["locations"] = listed
        d["locations_counted"] = total
        if queue:
            d["locations_more"] = "not all read: pass match (a glob on location paths) or read the node with gaffer_verify"
    except Exception as ex:  # noqa: BLE001
        d["error"] = ("%s: %s" % (type(ex).__name__, " ".join(str(ex).split())))[:600]
    return d


def image_summary(image):
    d = {}
    try:
        fmt = image.format()
        d["format"] = [fmt.width(), fmt.height()]
        d["data_window"] = plain(image.dataWindow())
        d["channels"] = [str(c) for c in image.channelNames()][:40]
        d["metadata"] = sorted(str(k) for k in image.metadata().keys())[:40]
    except Exception as ex:  # noqa: BLE001
        d["error"] = ("%s: %s" % (type(ex).__name__, " ".join(str(ex).split())))[:600]
    return d


def node_info(node):
    cls = type(node)
    d = {"path": "/" if node.isSame(S) else node.relativeName(S), "type": class_name(cls), "description": described(node),
         "doc": type_page(cls)}
    plugs = [(p, x) for p, x in walk_plugs(node) if matches(p, described(x))]
    inputs = [(p, x) for p, x in plugs if x.direction() == Gaffer.Plug.Direction.In]
    outputs = [(p, x) for p, x in plugs if x.direction() == Gaffer.Plug.Direction.Out and x.parent().isSame(node)]
    changed = [(p, x) for p, x in inputs if x.getInput() is not None or (isinstance(x, Gaffer.ValuePlug) and not len(x) and not x.isSetToDefault())]
    changed_paths = {p for p, _ in changed}
    default = [(p, x) for p, x in inputs if p not in changed_paths and not len(x)]
    detailed(changed, lambda x: plug_entry(*x), "plugs_changed", d, lambda x: x[0])
    if default:
        d["plugs_at_default"] = {"count": len(default), "names": [p for p, _ in default][:NAMES_MAX],
                                 "details": "pass match to read them with their values"}
        if MATCH:
            d["plugs_at_default"]["values"] = [plug_entry(*x) for x in default[:LIMIT]]
    d["outputs"] = [plug_entry(*x) for x in outputs[:LIMIT]]
    errors = []
    for _, plug in outputs:
        try:
            if isinstance(plug, GafferScene.ScenePlug):
                d.setdefault("scene", {})[plug.getName()] = scene_summary(plug)
                if "error" in d["scene"][plug.getName()]:
                    errors.append({"plug": plug.getName(), "error": d["scene"][plug.getName()]["error"]})
            elif isinstance(plug, GafferImage.ImagePlug):
                d.setdefault("image", {})[plug.getName()] = image_summary(plug)
                if "error" in d["image"][plug.getName()]:
                    errors.append({"plug": plug.getName(), "error": d["image"][plug.getName()]["error"]})
        except Exception as ex:  # noqa: BLE001
            errors.append({"plug": plug.getName(), "error": ("%s: %s" % (type(ex).__name__, ex))[:600]})
    errors += [{"plug": e["name"], "error": e["error"]} for e in [plug_entry(p, x) for p, x in outputs] if "error" in e]
    if errors:
        d["errors"] = errors[:10]
    if isinstance(node, GafferDispatch.TaskNode):
        d["task"] = {"pre_tasks": [plain(p.getInput()) for p in node["preTasks"] if p.getInput() is not None],
                     **({"fileName": node["fileName"].getValue()} if "fileName" in node else {})}
    children = node.children(Gaffer.Node)
    if children:
        d["children"] = [{"name": c.getName(), "type": class_name(type(c))} for c in children[:LIMIT]]
        if len(children) > LIMIT:
            d["children_more"] = {"count": len(children) - LIMIT, "names": [c.getName() for c in children[LIMIT:LIMIT + NAMES_MAX]]}
    return d


if P.get("node"):
    path = str(P["node"])
    target = resolve(path)
    if isinstance(target, Gaffer.Plug):
        target = target.node()
    if target is None:
        parts = [p for p in path.strip("/").replace("/", ".").split(".") if p]
        parent, depth = None, len(parts)
        while depth > 0 and parent is None:
            depth -= 1
            parent = S.descendant(".".join(parts[:depth])) if depth else S
        names = [c.getName() for c in parent.children(Gaffer.Node)] if parent is not None else []
        missing = parts[depth] if depth < len(parts) else ""
        out["node"] = {"error": "no node %r in the script" % path, "deepest_existing": parent.relativeName(S) if parent is not None and parent is not S else "",
                       "closest": difflib.get_close_matches(missing, names, n=6, cutoff=0.5), "children": names[:NAMES_MAX]}
    else:
        out["node"] = node_info(target)


# --- Python symbol ------------------------------------------------------------------------------------------
def doc_head(obj, lines=40):
    text = (getattr(obj, "__doc__", None) or "").strip("\n")
    rows = text.splitlines()
    return "\n".join(rows[:lines]) + ("\n... (%d more lines)" % (len(rows) - lines) if len(rows) > lines else "")


if P.get("gaffer"):
    path = str(P["gaffer"]).strip()
    parts = path.split(".")
    try:
        target = sys.modules.get(parts[0]) or importlib.import_module(parts[0])
    except Exception:  # noqa: BLE001
        target = None
    if target is None or not (parts[0].startswith(("Gaffer", "IECore")) or parts[0] == "imath"):
        out["gaffer"] = {"error": "%r is not a Gaffer, IECore or imath module here" % parts[0],
                         "modules": [n for n, _ in node_modules()] + ["IECore", "IECoreScene", "IECoreImage", "imath"]}
    else:
        reached, missing = [parts[0]], None
        for part in parts[1:]:
            nxt = getattr(target, part, None)
            if nxt is None:
                missing = part
                break
            target = nxt
            reached.append(part)
        if missing is not None:
            names = [n for n in dir(target) if not n.startswith("_")]
            out["gaffer"] = {"error": "%s has no %r" % (".".join(reached), missing),
                             "closest": [".".join(reached + [n]) for n in difflib.get_close_matches(missing, names, n=8, cutoff=0.5)]}
        else:
            info = {"path": path, "kind": "class" if _inspect.isclass(target) else "module" if isinstance(target, types.ModuleType)
                    else "callable" if callable(target) else type(target).__name__, "doc": doc_head(target)}
            if _inspect.isclass(target):
                own = [n for n in vars(target) if not n.startswith("_")]
                info["bases"] = [class_name(b) for b in target.__mro__[1:] if b.__module__.split(".")[0].startswith(("Gaffer", "IECore"))][:12]
                info["methods"] = sorted(n for n in own if matches(n, (getattr(getattr(target, n, None), "__doc__", "") or "")[:200]))[:NAMES_MAX]
                inherited = sorted(n for n in dir(target) if not n.startswith("_") and n not in own)
                if inherited:
                    info["inherited"] = inherited[:NAMES_MAX]
                if issubclass(target, Gaffer.Node):
                    info["doc_page"] = type_page(target)
                    node, _ = instance(target)
                    text = described(node) if node is not None else ""
                    if text:
                        info["description"] = text
            elif not callable(target) and not isinstance(target, types.ModuleType):
                info["value"] = plain(target)
            out["gaffer"] = info


# --- search -------------------------------------------------------------------------------------------------
def plug_index():
    """(class, plug path, description) for every plug of every node class of the loaded modules, kept for this
    Gaffer session (rebuilt when another module is loaded)."""
    cache = sys.modules.get("_opencode_gaffer_inspect_cache")
    if cache is None:
        cache = types.ModuleType("_opencode_gaffer_inspect_cache")
        sys.modules[cache.__name__] = cache
    key = (tuple(n for n, _ in node_modules()), DESCRIPTIONS)
    if getattr(cache, "key", None) != key:
        rows, kinds = [], []
        for _, module in node_modules():
            for cls in node_classes(module):
                node, _ = instance(cls)
                kinds.append((cls, described(node) if node is not None else ""))
                if node is None:
                    continue
                for path, plug in walk_plugs(node):
                    if path != "user":
                        rows.append((class_name(cls), path, described(plug)))
        cache.key, cache.plugs, cache.kinds = key, rows, kinds
    return cache.plugs, cache.kinds


def search(text):
    words = [w.lower() for w in str(text).split() if w]
    if not words:
        raise ValueError("search: give one or more words")
    started = time.time()

    def score(name, desc):
        name = name.lower()
        hay = " ".join((name, (desc or "").lower()))
        if not all(w in hay for w in words):
            return 0
        return sum(3 if w in name else 1 for w in words) + (2 if name in words else 0)

    plugs, kinds = plug_index()
    hits = []
    for cls, desc in kinds:
        s = score(class_name(cls), desc)
        if s:
            hits.append((s + 3, {"kind": "node type", "path": class_name(cls), "description": desc[:200], "doc": type_page(cls)}))
    by_plug = {}
    for owner, path, desc in plugs:
        s = score(path.rsplit(".", 1)[-1], desc[:300])
        if s:
            # One entry per plug name and meaning: the classes sharing a description are listed together.
            entry = by_plug.setdefault((path, desc), [s, []])
            entry[1].append(owner)
    for (path, desc), (s, owners) in by_plug.items():
        hits.append((s - 1, {"kind": "plug", "path": path, "description": desc[:200], "on": owners[:8],
                             **({"on_count": len(owners)} if len(owners) > 8 else {})}))
    for module_name in ("Gaffer", "GafferScene", "GafferImage", "GafferDispatch"):
        module = sys.modules[module_name]
        for name in dir(module):
            obj = getattr(module, name, None)
            if name.startswith("_") or (_inspect.isclass(obj) and issubclass(obj, Gaffer.Node)):
                continue
            first = " ".join((getattr(obj, "__doc__", None) or "").split()[:30])
            s = score(name, first)
            if s:
                hits.append((s - 0.5, {"kind": "class" if _inspect.isclass(obj) else "function" if callable(obj) else "value",
                                       "path": "%s.%s" % (module_name, name), "doc": first[:160]}))
            if _inspect.isclass(obj):
                for m in vars(obj):
                    if m.startswith("_"):
                        continue
                    mdoc = (getattr(getattr(obj, m, None), "__doc__", None) or "").strip()
                    s = score(m, " ".join(mdoc.split()[:40]))
                    if s:
                        hits.append((s - 0.75, {"kind": "method", "path": "%s.%s.%s" % (module_name, name, m),
                                                "signature": (mdoc.splitlines()[0] if mdoc else "")[:200]}))
    hits.sort(key=lambda h: (-h[0], len(h[1]["path"]), h[1]["path"]))
    found = {"words": words, "found": len(hits), "results": [h for _, h in hits[:LIMIT]], "seconds": round(time.time() - started, 2),
             "modules": [n for n, _ in node_modules()]}
    if len(hits) > LIMIT:
        found["more"] = {"count": len(hits) - LIMIT, "paths": [h[1]["path"] for h in hits[LIMIT:LIMIT + NAMES_MAX]],
                         "details": "add words, or inspect a type, node or gaffer path"}
    return found


if P.get("search"):
    out["search"] = search(P["search"])

if not any(P.get(k) for k in ("search", "type", "node", "gaffer")):
    raise ValueError("give search (words), type (e.g. GafferScene.Sphere), node (a node path, e.g. Group.sphere) "
                     "or gaffer (e.g. Gaffer.Node.setName)")

result = out
