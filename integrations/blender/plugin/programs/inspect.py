# SPDX-License-Identifier: GPL-3.0-or-later
# blender_inspect (fixed program, read only): what THIS Blender really exposes for a type, an operator or
# live data, with the official documentation and Python API release notes of this version.
# PARAMS is prepended by the plugin: {"type"?, "operator"?, "data"?: {"collection", "name", "path"?}, "match"?, "limit"?}
import fnmatch
import math

import bpy

P = PARAMS  # noqa: F821
VER = "%d.%d" % bpy.app.version[:2]
API = "https://docs.blender.org/api/%s/" % VER
LIMIT = int(P.get("limit") or 60)
MATCH = P.get("match")
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
    props = [p for p in rna.properties if p.identifier != "rna_type" and (not MATCH or fnmatch.fnmatchcase(p.identifier, MATCH))]
    d["properties"] = [prop_info(p, holder) for p in props[:LIMIT]]
    if len(props) > LIMIT:
        d["properties_truncated"] = len(props)
    funcs = [f for f in rna.functions if not MATCH or fnmatch.fnmatchcase(f.identifier, MATCH)]
    d["functions"] = [func_info(f) for f in funcs[:LIMIT]]
    subs = sorted({t.bl_rna.identifier for t in (getattr(bpy.types, n, None) for n in dir(bpy.types))
                   if hasattr(t, "bl_rna") and t.bl_rna.base is not None and t.bl_rna.base.identifier == rna.identifier})
    if subs:
        d["subtypes"] = subs[:80]
    return d


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


def node_info(rna, live=None):
    """Own properties (with values for a live node) and real sockets of a node type or a node."""
    d = {"type": rna.identifier, "base": rna.base.identifier if rna.base else None, "doc": "%sbpy.types.%s.html" % (API, rna.identifier)}
    if rna.description:
        d["desc"] = rna.description[:200]
    own = [p for p in rna.properties if p.identifier not in NODE_PROPS and (not MATCH or fnmatch.fnmatchcase(p.identifier, MATCH))]
    d["properties"] = [prop_info(p, live) for p in own[:LIMIT]]
    tree_type = next((tree for prefix, tree in TREES if rna.identifier.startswith(prefix)), "GeometryNodeTree")
    with bpy.data.temp_data() as td:
        tree = td.node_groups.new("inspect", live.id_data.bl_idname if live is not None else tree_type)
        try:
            node = tree.nodes.new(rna.identifier)
        except Exception as ex:  # noqa: BLE001
            node = None
            d["sockets_error"] = "cannot create %s in a %s: %s" % (rna.identifier, tree.bl_idname, str(ex)[:160])
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


if P.get("type"):
    t = getattr(bpy.types, P["type"], None)
    if t is None or not hasattr(t, "bl_rna"):
        close = sorted(n for n in dir(bpy.types) if P["type"].lower() in n.lower())[:30]
        out["type"] = {"error": "no type %r in this Blender" % P["type"], "similar": close}
    elif is_node_type(t.bl_rna) and t.bl_rna.identifier not in ("Node", "ShaderNode", "GeometryNode", "CompositorNode", "FunctionNode", "TextureNode", "NodeInternal"):
        out["type"] = node_info(t.bl_rna)
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
        mod_ops = getattr(bpy.ops, module, None)
        out["operator"] = {"error": "no operator %r in this Blender" % P["operator"], "in_module": sorted(dir(mod_ops))[:80] if mod_ops else []}
    else:
        try:
            poll = op.poll()
        except Exception as ex:  # noqa: BLE001
            poll = "unknown: %s" % ex
        out["operator"] = {
            "id": P["operator"], "name": rna.name, "desc": (rna.description or "")[:300],
            "doc": "%sbpy.ops.%s.html#bpy.ops.%s.%s" % (API, module, module, name),
            "poll_without_ui_context": poll,
            "properties": [prop_info(p) for p in rna.properties if p.identifier != "rna_type"][:LIMIT],
        }

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

result = out
