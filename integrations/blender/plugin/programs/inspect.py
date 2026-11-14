# SPDX-License-Identifier: GPL-3.0-or-later
# blender_inspect (fixed program, read only): what THIS Blender really exposes for a type, an operator or
# live data, or where words appear in its API, with the official documentation and Python API release notes of
# this version. Everything is read from the running Blender (RNA, bpy.ops, nodes made in a temporary tree).
# PARAMS is prepended by the plugin: {"type"?, "operator"?, "data"?: {"collection", "name", "path"?}, "search"?,
# "tree"?, "match"?, "limit"?}
import difflib
import fnmatch
import math
import re

import bpy

P = PARAMS  # noqa: F821
VER = "%d.%d" % bpy.app.version[:2]
API = "https://docs.blender.org/api/%s/" % VER
LIMIT = max(1, min(200, int(P.get("limit") or 60)))
MATCH = P.get("match")
NAMES_MAX = 400  # names listed beyond the detailed entries, so nothing is cut silently


def matches(ident, name="", desc=""):
    """match: a glob on the identifier when it has * ? or [, otherwise words all found in the identifier, name or
    description (case-insensitive)."""
    if not MATCH:
        return True
    if any(c in MATCH for c in "*?["):
        return fnmatch.fnmatchcase(ident, MATCH)
    hay = " ".join((ident, name or "", desc or "")).lower()
    return all(w in hay for w in MATCH.lower().split())


def detailed(items, info, key, d):
    """The first LIMIT items in detail under key; the others by name, with how to see them."""
    d[key] = [info(i) for i in items[:LIMIT]]
    rest = [i.identifier for i in items[LIMIT:]]
    if rest:
        d[key + "_more"] = {"count": len(rest), "names": rest[:NAMES_MAX],
                            "details": "pass match (a glob or words) or a larger limit"}
out = {
    "blender": bpy.app.version_string,
    "links": {
        "api": API,
        "python_api_release_notes": "https://developer.blender.org/docs/release_notes/%s/python_api/" % VER,
        "manual": "https://docs.blender.org/manual/en/%s/" % VER,
    },
}


def num(v):
    if isinstance(v, float):
        return None if not math.isfinite(v) else round(v, 6)
    return v


def value_of(holder, p):
    try:
        v = getattr(holder, p.identifier)
    except Exception as ex:  # noqa: BLE001
        return {"unreadable": str(ex)[:120]}
    if p.type in ("BOOLEAN", "INT", "FLOAT"):
        return [num(x) for x in v][:32] if getattr(p, "array_length", 0) else num(v)
    if p.type in ("STRING", "ENUM"):
        return sorted(v) if isinstance(v, set) else v
    if p.type == "POINTER":
        if v is None:
            return None
        return {"type": v.bl_rna.identifier, **({"name": v.name} if hasattr(v, "name") else {})}
    if p.type == "COLLECTION":
        items = list(v)
        return {"len": len(items), "items": [
            {"type": i.bl_rna.identifier, **({"name": i.name} if hasattr(i, "name") else {}), **({"identifier": i.identifier} if hasattr(i, "identifier") else {})}
            for i in items[:20]]}
    return None


def prop_info(p, holder=None):
    d = {"id": p.identifier, "type": p.type}
    if p.name and p.name != p.identifier:
        d["name"] = p.name
    if p.description:
        d["desc"] = p.description[:160]
    if p.is_readonly:
        d["readonly"] = True
    if getattr(p, "subtype", "NONE") not in ("NONE", None, ""):
        d["subtype"] = p.subtype
    if p.type == "ENUM":
        d["enum"] = [e.identifier for e in p.enum_items] or "dynamic (depends on the data: read it from live data)"
        if p.is_enum_flag:
            d["flag"] = True
            d["default"] = sorted(p.default_flag)
        else:
            d["default"] = p.default
    elif p.type in ("BOOLEAN", "INT", "FLOAT"):
        if p.array_length:
            d["array"] = p.array_length
            d["default"] = [num(x) for x in p.default_array][:16]
        else:
            d["default"] = num(p.default)
        if p.type != "BOOLEAN":
            d["range"] = [num(p.hard_min), num(p.hard_max)]
    elif p.type == "STRING":
        d["default"] = p.default
    elif p.type in ("POINTER", "COLLECTION"):
        d["of"] = p.fixed_type.identifier
        if p.type == "COLLECTION" and p.srna is not None:
            d["collection_api"] = {"type": p.srna.identifier, "functions": [f.identifier for f in p.srna.functions]}
    if holder is not None:
        d["value"] = value_of(holder, p)
    return d


def func_info(f):
    params, returns = [], []
    for a in f.parameters:
        item = {"id": a.identifier, "type": a.type + ((":" + a.fixed_type.identifier) if a.type in ("POINTER", "COLLECTION") else "")}
        if a.type == "ENUM":
            item["enum"] = [e.identifier for e in a.enum_items]
        (returns if a.is_output else params).append(item if a.is_output else {**item, "required": a.is_required})
    return {"id": f.identifier, "desc": (f.description or "")[:160], "params": params, **({"returns": returns} if returns else {})}


def struct_info(rna, holder=None):
    d = {"type": rna.identifier, "base": rna.base.identifier if rna.base else None, "doc": "%sbpy.types.%s.html" % (API, rna.identifier)}
    if rna.description:
        d["desc"] = rna.description[:200]
    props = [p for p in rna.properties if p.identifier != "rna_type" and matches(p.identifier, p.name, p.description)]
    detailed(props, lambda p: prop_info(p, holder), "properties", d)
    funcs = [f for f in rna.functions if matches(f.identifier, "", f.description)]
    detailed(funcs, func_info, "functions", d)
    subs = sorted({t.bl_rna.identifier for t in (getattr(bpy.types, n, None) for n in dir(bpy.types))
                   if hasattr(t, "bl_rna") and t.bl_rna.base is not None and t.bl_rna.base.identifier == rna.identifier})
    if subs:
        d["subtypes"] = subs[:NAMES_MAX]
    if MATCH and not props and not funcs:
        d["match_found_nothing"] = {"match": MATCH, "properties": [p.identifier for p in rna.properties][:NAMES_MAX]}
    return d


def element_info(p):
    """The element type of an operator's collection (or pointer) property: what each item holds."""
    rna = p.fixed_type
    own = {e.identifier: e for e in rna.properties if e.identifier != "rna_type"}  # the struct's own one wins
    return {"type": rna.identifier, "properties": [prop_info(e) for e in own.values()],
            **({"note": "each element: a dict holding every one of these properties"} if p.type == "COLLECTION" else {})}


def modifier_inputs(mod):
    """Geometry Nodes modifier inputs: interface sockets and the access that actually works here."""
    group = getattr(mod, "node_group", None)
    if group is None:
        return None
    rows = []
    for item in group.interface.items_tree:
        if getattr(item, "item_type", None) != "SOCKET" or item.in_out != "INPUT":
            continue
        ident = item.identifier
        row = {"name": item.name, "identifier": ident, "socket_type": item.socket_type, "access": {}}
        # Older releases keep inputs as ID properties of the modifier; newer ones under mod.properties.inputs.
        tries = (
            ("mod[%r]" % ident, lambda i=ident: mod[i]),
            ("mod.properties.inputs.%s.value" % ident, lambda i=ident: getattr(mod.properties.inputs, i).value),
            ("mod.properties.inputs[%r]['value']" % ident, lambda i=ident: mod.properties.inputs[i]["value"]),
        )
        for label, read in tries:
            try:
                v = read()
                shown = [num(x) for x in v] if hasattr(v, "__len__") and not isinstance(v, str) else (v.name if hasattr(v, "name") else num(v))
                row["access"][label] = {"reads": True, "value": shown}
                row.setdefault("set_with", "%s = <value>" % label)
            except Exception as ex:  # noqa: BLE001
                row["access"][label] = {"reads": False, "error": ("%s: %s" % (type(ex).__name__, ex))[:120]}
        try:
            row["fields"] = list(mod.properties.inputs[ident].keys())
        except Exception:  # noqa: BLE001
            pass
        rows.append(row)
    return rows


# --- nodes: their real sockets and their own properties, not Node's generic ones ---------------------------
NODE_PROPS = {p.identifier for p in bpy.types.Node.bl_rna.properties}
TREES = (("ShaderNode", "ShaderNodeTree"), ("GeometryNode", "GeometryNodeTree"), ("FunctionNode", "GeometryNodeTree"),
         ("CompositorNode", "CompositorNodeTree"), ("TextureNode", "TextureNodeTree"))


def is_node_type(rna):
    while rna is not None:
        if rna.identifier == "Node":
            return True
        rna = rna.base
    return False


def menu_items(socket):
    """A menu socket's items: Blender names the valid values when given an invalid one (temporary nodes only)."""
    try:
        socket.default_value = "\x01"
    except TypeError as ex:
        text = str(ex)
        if "(" in text:
            return [x.strip().strip("'\"") for x in text[text.rfind("(") + 1:text.rfind(")")].split(",") if x.strip()]
    except Exception:  # noqa: BLE001
        pass
    return None


def socket_info(s, temporary, values=True):
    d = {"name": s.name, "identifier": s.identifier, "type": s.type, "bl_idname": s.bl_idname}
    if not getattr(s, "enabled", True):
        d["enabled"] = False
    if getattr(s, "hide_value", False):
        d["hide_value"] = True
    p = s.bl_rna.properties.get("default_value")
    if p is not None:
        if p.type in ("FLOAT", "INT") and p.subtype not in ("NONE", ""):
            d["subtype"] = p.subtype
        if p.type in ("FLOAT", "INT"):
            d["range"] = [num(p.soft_min), num(p.soft_max)]
        if values:
            try:
                v = s.default_value
                d["default" if temporary else "value"] = [num(x) for x in v] if hasattr(v, "__len__") and not isinstance(v, str) else (v.name if hasattr(v, "name") else num(v))
            except Exception:  # noqa: BLE001
                pass
        if p.type == "ENUM" and temporary:
            items = [e.identifier for e in p.enum_items] or menu_items(s)
            if items:
                d["menu"] = items
    if not temporary and s.is_linked:
        d["linked"] = [("%s.%s" % (l.from_node.name, l.from_socket.identifier)) if l.to_socket == s else ("%s.%s" % (l.to_node.name, l.to_socket.identifier)) for l in s.links][:8]
    return d


def tree_types():
    """The node tree types of this Blender (NodeTree's subtypes)."""
    return sorted(n for n in dir(bpy.types) if n != "NodeTree" and is_type_of(getattr(bpy.types, n, None), "NodeTree"))


def is_type_of(cls, base):
    rna = getattr(cls, "bl_rna", None)
    while rna is not None:
        if rna.identifier == base:
            return True
        rna = rna.base
    return False


def chosen_tree(value):
    """tree: a NodeTree type, or a word its name starts with (shader, geometry, compositing, texture)."""
    trees = tree_types()
    if value in trees:
        return value
    found = [t for t in trees if t.lower().startswith(str(value).lower()[:6])]
    if len(found) == 1:
        return found[0]
    raise ValueError("tree %r: one of %s" % (value, trees))


def creatable(tree_type, wanted):
    """Node types closest to `wanted` that this Blender creates in a tree of tree_type, with their sockets' names."""
    names = [n for n in dir(bpy.types) if is_type_of(getattr(bpy.types, n, None), "Node")]
    words = [w for w in re.split(r"(?<=[a-z])(?=[A-Z])", wanted) if len(w) > 2 and w not in ("Node", "Shader", "Geometry", "Compositor", "Function", "Texture")]
    candidates = difflib.get_close_matches(wanted, names, n=8, cutoff=0.5)
    candidates += [n for n in names if n not in candidates and any(w.lower() in n.lower() for w in words)]
    out = []
    with bpy.data.temp_data() as td:
        tree = td.node_groups.new("inspect", tree_type)
        for name in candidates:
            try:
                node = tree.nodes.new(name)
            except Exception:  # noqa: BLE001
                continue
            out.append({"type": name, "label": node.name, "inputs": [s.name for s in node.inputs][:24],
                        "outputs": [s.name for s in node.outputs][:24]})
            tree.nodes.remove(node)
            if len(out) >= 12:
                break
    return out


def node_info(rna, live=None, tree_type=None):
    """Own properties (with values for a live node) and real sockets of a node type or a node."""
    d = {"type": rna.identifier, "base": rna.base.identifier if rna.base else None, "doc": "%sbpy.types.%s.html" % (API, rna.identifier)}
    if rna.description:
        d["desc"] = rna.description[:200]
    own = [p for p in rna.properties if p.identifier not in NODE_PROPS and matches(p.identifier, p.name, p.description)]
    detailed(own, lambda p: prop_info(p, live), "properties", d)
    tree_type = tree_type or next((tree for prefix, tree in TREES if rna.identifier.startswith(prefix)), "GeometryNodeTree")
    with bpy.data.temp_data() as td:
        tree = td.node_groups.new("inspect", live.id_data.bl_idname if live is not None else tree_type)
        try:
            node = tree.nodes.new(rna.identifier)
        except Exception as ex:  # noqa: BLE001
            node = None
            d["sockets_error"] = "cannot create %s in a %s: %s" % (rna.identifier, tree.bl_idname, str(ex)[:160])
            d["creatable_in_%s" % tree.bl_idname] = creatable(tree.bl_idname, rna.identifier)
        menus = {}
        if node is not None:
            if live is None:
                d["tree_type"] = tree.bl_idname
                d["inputs"] = [socket_info(s, True) for s in node.inputs]
                d["outputs"] = [socket_info(s, True, values=False) for s in node.outputs]
            else:
                menus = {s.identifier: socket_info(s, True).get("menu") for s in node.inputs if s.type == "MENU"}
    if live is not None:
        d["name"] = live.name
        d["tree"] = live.id_data.name
        d["inputs"] = [dict(socket_info(s, False), **({"menu": menus[s.identifier]} if menus.get(s.identifier) else {})) for s in live.inputs]
        d["outputs"] = [socket_info(s, False, values=False) for s in live.outputs]
    return d


TREE = chosen_tree(P["tree"]) if P.get("tree") else None

if P.get("type"):
    t = getattr(bpy.types, P["type"], None)
    if t is None or not hasattr(t, "bl_rna"):
        names = dir(bpy.types)
        close = difflib.get_close_matches(P["type"], names, n=10, cutoff=0.5)
        close += sorted(n for n in names if P["type"].lower() in n.lower() and n not in close)
        out["type"] = {"error": "no type %r in this Blender" % P["type"], "similar": close[:40]}
        if TREE:
            out["type"]["creatable_in_%s" % TREE] = creatable(TREE, P["type"])
    elif is_node_type(t.bl_rna) and t.bl_rna.identifier not in ("Node", "ShaderNode", "GeometryNode", "CompositorNode", "FunctionNode", "TextureNode", "NodeInternal"):
        out["type"] = node_info(t.bl_rna, tree_type=TREE)
    else:
        out["type"] = struct_info(t.bl_rna)

if P.get("operator"):
    module, _, name = P["operator"].partition(".")
    op = getattr(getattr(bpy.ops, module, None), name, None) if name else None
    try:
        rna = op.get_rna_type() if op is not None else None
    except Exception:  # noqa: BLE001
        rna = None
    if rna is None:
        modules = sorted(dir(bpy.ops))
        mod_ops = getattr(bpy.ops, module, None) if module in modules else None
        in_module = sorted(dir(mod_ops)) if mod_ops else []
        out["operator"] = {"error": "no operator %r in this Blender" % P["operator"],
                           "closest": ["%s.%s" % (module, n) for n in difflib.get_close_matches(name, in_module, n=8, cutoff=0.5)],
                           "in_module": in_module[:NAMES_MAX]}
        if mod_ops is None:
            out["operator"]["closest_modules"] = difflib.get_close_matches(module, modules, n=6, cutoff=0.5)
    else:
        try:
            poll = op.poll()
        except Exception as ex:  # noqa: BLE001
            poll = "unknown: %s" % ex
        report = {"in_this_context": poll}
        if poll is False:
            # Blender checks poll before running: the call fails at once, with the reason it gives, if any.
            try:
                op()
            except Exception as ex:  # noqa: BLE001
                report["blender_says"] = str(ex)[:300]
            ctx = bpy.context
            report["context"] = {"window": ctx.window is not None, "area": ctx.area.type if ctx.area else None,
                                 "region": ctx.region.type if ctx.region else None, "mode": ctx.mode,
                                 "active_object": ctx.active_object.name if ctx.active_object else None,
                                 "id": getattr(ctx, "id", None) is not None}
            # What would let it run, found by the add-on trying poll() only (hints.context_help).
            import sys
            helper = next((m for k, m in list(sys.modules.items()) if k.endswith("opencode_link.hints")), None)
            found = helper.context_help(module, name) if helper is not None and hasattr(helper, "context_help") else {}
            report.update(found or {"give_context": "bpy.context.temp_override(window=..., area=..., region=..., id=..., active_object=...)"})
        props = [p for p in rna.properties if p.identifier != "rna_type"]
        info = {
            "id": P["operator"], "name": rna.name, "desc": (rna.description or "")[:300],
            "doc": "%sbpy.ops.%s.html#bpy.ops.%s.%s" % (API, module, module, name),
            "poll": report,
        }
        detailed(props, lambda p: dict(prop_info(p), **({"element": element_info(p)} if p.type in ("COLLECTION", "POINTER") else {})),
                 "properties", info)
        out["operator"] = info

if P.get("data"):
    spec = P["data"]
    coll = getattr(bpy.data, spec.get("collection", ""), None)
    if not isinstance(coll, bpy.types.bpy_prop_collection):
        out["data"] = {"error": "bpy.data has no collection %r" % spec.get("collection")}
    else:
        holder = coll.get(spec.get("name", ""))
        if holder is None:
            out["data"] = {"error": "no %s %r" % (spec["collection"], spec.get("name")), "names": [i.name for i in coll][:40]}
        else:
            try:
                target = holder.path_resolve(spec["path"]) if spec.get("path") else holder
            except Exception as ex:  # noqa: BLE001
                target = None
                out["data"] = {"error": "path %r: %s" % (spec["path"], ex)}
            if target is not None:
                if not hasattr(target, "bl_rna"):
                    out["data"] = {"path": spec.get("path"), "value": repr(target)[:500]}
                elif isinstance(target, bpy.types.Node):
                    out["data"] = node_info(target.bl_rna, target)
                else:
                    info = struct_info(target.bl_rna, target)
                    if getattr(target, "type", None) == "NODES" and isinstance(target, bpy.types.Modifier):
                        info["geometry_nodes_inputs"] = modifier_inputs(target)
                    out["data"] = info

def search(text):
    """Where words appear in this Blender's API: types, their own properties and functions, operators. Every word
    is found in the identifier, name or description; identifier matches rank first. A type lists the properties
    that lead to it (reached_by)."""
    words = [w.lower() for w in str(text).split() if w]
    if not words:
        raise ValueError("search: give one or more words")

    def score(ident, name, desc):
        ident, name = ident.lower(), (name or "").lower()
        hay = " ".join((ident, name, (desc or "").lower()))
        if not all(w in hay for w in words):
            return 0
        return sum(3 if w in ident else 2 if w in name else 1 for w in words)

    hits, reached = [], {}
    for type_name in dir(bpy.types):
        rna = getattr(getattr(bpy.types, type_name, None), "bl_rna", None)
        if rna is None or rna.identifier != type_name:
            continue
        inherited = {p.identifier for p in rna.base.properties} if rna.base else set()
        inherited_f = {f.identifier for f in rna.base.functions} if rna.base else set()
        s = score(type_name, rna.name, rna.description)
        if s:
            hits.append((s + 1, {"kind": "type", "path": type_name, "desc": (rna.description or "")[:120]}))
        for p in rna.properties:
            if p.identifier == "rna_type" or p.identifier in inherited:
                continue
            if p.type in ("POINTER", "COLLECTION"):
                reached.setdefault(p.fixed_type.identifier, []).append("%s.%s" % (type_name, p.identifier))
            s = score(p.identifier, p.name, p.description)
            if s:
                kind = p.type + (":" + p.fixed_type.identifier if p.type in ("POINTER", "COLLECTION") else "")
                hits.append((s, {"kind": "property", "path": "%s.%s" % (type_name, p.identifier), "type": kind,
                                 "desc": (p.description or "")[:120]}))
        for f in rna.functions:
            if f.identifier in inherited_f:
                continue
            s = score(f.identifier, "", f.description)
            if s:
                hits.append((s, {"kind": "function", "path": "%s.%s()" % (type_name, f.identifier),
                                 "desc": (f.description or "")[:120]}))
    for module in dir(bpy.ops):
        for name in dir(getattr(bpy.ops, module)):
            try:
                rna = getattr(getattr(bpy.ops, module), name).get_rna_type()
            except Exception:  # noqa: BLE001
                continue
            s = score("%s.%s" % (module, name), rna.name, rna.description)
            if s:
                hits.append((s, {"kind": "operator", "path": "bpy.ops.%s.%s" % (module, name),
                                 "desc": (rna.description or "")[:120]}))
    hits.sort(key=lambda h: (-h[0], len(h[1]["path"]), h[1]["path"]))
    results = []
    for _, hit in hits[:LIMIT]:
        if hit["kind"] == "type" and reached.get(hit["path"]):
            hit["reached_by"] = reached[hit["path"]][:8]
        results.append(hit)
    found = {"words": words, "found": len(hits), "results": results}
    if len(hits) > LIMIT:
        found["more"] = {"count": len(hits) - LIMIT, "paths": [h[1]["path"] for h in hits[LIMIT:LIMIT + NAMES_MAX]],
                         "details": "add words, or inspect a path's type or operator"}
    return found


if P.get("search"):
    out["search"] = search(P["search"])

result = out
