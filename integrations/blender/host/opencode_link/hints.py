# SPDX-License-Identifier: GPL-3.0-or-later

"""What is valid where supplied code failed on Blender's API, read from this Blender (RNA, bpy.ops, node types made
in a temporary tree): the closest names, an operator's real signature, a type's properties, an enumeration's items,
a collection's keys (node sockets included), the node types a tree accepts. Computed before the failed run is
undone, so the data the code made is still there. Never raises: no hint rather than a second error.
"""

import ast
import difflib
import re

import bpy

LIST_MAX = 80
CLOSEST = 6

_OPERATOR = re.compile(r'Calling operator "bpy\.ops\.(\w+)\.(\w+)" error, could not be found')
_KEYWORD = re.compile(r'keyword "(\w+)" (unrecognized|missing)')
_ENUM = re.compile(r'enum "([^"]*)" not found in \(([^)]*)\)')
_KEY = re.compile(r'bpy_prop_collection\[key\]: key "([^"]*)" not found')
_INDEX = re.compile(r"bpy_prop_collection\[index\]: index (-?\d+) out of range")
_NODE = re.compile(r"Node type (\w+) undefined")
_POLL = re.compile(r"Operator bpy\.ops\.(\w+)\.(\w+)\.poll\(\) (.*)", re.S)


def explain(error, code, filename):
    """A dict of what is valid at the failure, or None."""
    try:
        text = str(error)
        for rule in (_operator_unknown, _operator_poll, _operator_arguments, _attribute, _enum, _collection, _node_type):
            hint = rule(error, text, code, filename)
            if hint:
                return hint
    except Exception:  # noqa: BLE001 - a hint is a bonus
        return None
    return None


# -- names ----------------------------------------------------------------------------------------------------
def _closest(name, names):
    names = list(dict.fromkeys(names))
    found = difflib.get_close_matches(name, names, n=CLOSEST, cutoff=0.55)
    words = [w for w in re.split(r"[_\W]+|(?<=[a-z])(?=[A-Z])", name.lower()) if len(w) > 2]
    for candidate in names:
        if len(found) >= CLOSEST * 2:
            break
        if candidate not in found and any(w in candidate.lower() for w in words):
            found.append(candidate)
    return found


def _listed(names):
    names = list(names)
    out = {"all": names[:LIST_MAX]}
    if len(names) > LIST_MAX:
        out["more"] = len(names) - LIST_MAX
    return out


def _prop(p):
    d = {"id": p.identifier, "type": p.type}
    if p.type == "ENUM":
        d["enum"] = [e.identifier for e in p.enum_items][:LIST_MAX] or "dynamic"
    elif p.type in ("POINTER", "COLLECTION"):
        d["of"] = p.fixed_type.identifier
    elif getattr(p, "array_length", 0):
        d["array"] = p.array_length
    if p.is_readonly:
        d["readonly"] = True
    if p.description:
        d["desc"] = p.description[:120]
    return d


def _struct(rna, name, members_of=None):
    props = [p for p in rna.properties if p.identifier != "rna_type"]
    funcs = list(rna.functions)
    names = [p.identifier for p in props] + [f.identifier for f in funcs]
    closest = _closest(name, names)
    by_id = {p.identifier: p for p in props}
    hint = {"type": rna.identifier, "missing": name,
            "closest": [(_prop(by_id[c]) if c in by_id else {"id": c, "type": "FUNCTION"}) for c in closest]}
    hint["properties"] = _listed(p.identifier for p in props)
    if funcs:
        hint["functions"] = _listed(f.identifier for f in funcs)
    return hint


# -- rules ----------------------------------------------------------------------------------------------------
def _operator_unknown(error, text, code, filename):
    m = _OPERATOR.search(text)
    if not isinstance(error, AttributeError) or not m:
        return None
    module, name = m.groups()
    modules = sorted(dir(bpy.ops))
    if module not in modules:
        return {"operator": "%s.%s" % (module, name), "missing": "module %s" % module,
                "closest_modules": _closest(module, modules)}
    ops = sorted(dir(getattr(bpy.ops, module)))
    return {"operator": "%s.%s" % (module, name), "missing": name,
            "closest": ["%s.%s" % (module, o) for o in _closest(name, ops)], "in_module": _listed(ops)}


def own(rna):
    """A struct's properties by identifier, the struct's own one when a base declares it too (an operator's file
    element redeclares PropertyGroup.name)."""
    found = {}
    for p in rna.properties:
        if p.identifier != "rna_type":
            found[p.identifier] = p
    return list(found.values())


def _signature(rna):
    out = []
    for p in rna.properties:
        if p.identifier == "rna_type":
            continue
        d = _prop(p)
        if p.type == "COLLECTION":
            d["element"] = {"type": p.fixed_type.identifier, "properties": [_prop(e) for e in own(p.fixed_type)],
                            "note": "each element is a dict holding every one of these properties"}
        out.append(d)
    return out


def _operator_rna(error):
    idname = getattr(error, "opencode_operator", None)
    if not idname:
        return None, None
    module, _, name = idname.partition(".")
    try:
        return idname, getattr(getattr(bpy.ops, module), name).get_rna_type()
    except Exception:  # noqa: BLE001
        return idname, None


def _operator_poll(error, text, code, filename):
    m = _POLL.search(text)
    if not isinstance(error, RuntimeError) or not m:
        return None
    idname = "%s.%s" % m.groups()[:2]
    ctx = bpy.context
    seen = {}
    for member in ("window", "screen", "area", "region", "mode", "scene", "view_layer", "active_object", "object", "id"):
        try:
            value = getattr(ctx, member, None)
        except Exception:  # noqa: BLE001
            value = None
        if member == "mode" or value is None:
            seen[member] = value
        elif member in ("area", "region"):
            seen[member] = value.type
        else:
            seen[member] = getattr(value, "name", None) or type(value).__name__
    hint = {"operator": idname, "poll": m.group(3).strip()[:300], "context": seen,
            "give_context": "with bpy.context.temp_override(window=..., area=..., region=..., id=..., active_object=...)"}
    try:
        rna = getattr(getattr(bpy.ops, m.group(1)), m.group(2)).get_rna_type()
        hint["description"] = (rna.description or "")[:300]
    except Exception:  # noqa: BLE001
        pass
    return hint


def _operator_arguments(error, text, code, filename):
    idname, rna = _operator_rna(error)
    if rna is None or not isinstance(error, (TypeError, ValueError)):
        return None
    hint = {"operator": idname, "signature": _signature(rna)}
    keyword = _KEYWORD.search(text)
    if keyword and keyword.group(2) == "unrecognized":
        hint["unknown"] = keyword.group(1)
        hint["closest"] = _closest(keyword.group(1), [p.identifier for p in rna.properties if p.identifier != "rna_type"])
    elif keyword:
        hint["missing"] = keyword.group(1)
    enum = _ENUM.search(text)
    if enum:
        items = [i.strip().strip("'\"") for i in enum.group(2).split(",") if i.strip()]
        hint["invalid"] = enum.group(1)
        hint["closest"] = _closest(enum.group(1), items)
    return hint


def _attribute(error, text, code, filename):
    if not isinstance(error, AttributeError):
        return None
    obj, name = getattr(error, "obj", None), getattr(error, "name", None)
    if not name:
        return None
    if obj is bpy.types:
        return {"missing": "bpy.types.%s" % name, "closest": _closest(name, dir(bpy.types))}
    if obj is bpy.context:
        return {"missing": "bpy.context.%s" % name, "closest": _closest(name, dir(bpy.context))}
    rna = getattr(obj, "bl_rna", None)
    return _struct(rna, name) if rna is not None else None


def _enum(error, text, code, filename):
    m = _ENUM.search(text)
    if not isinstance(error, TypeError) or not m:
        return None
    items = [i.strip().strip("'\"") for i in m.group(2).split(",") if i.strip()]
    return {"invalid": m.group(1), "closest": _closest(m.group(1), items), "items": items[:LIST_MAX]}


def _collection(error, text, code, filename):
    key = _KEY.search(text)
    index = _INDEX.search(text)
    if not (isinstance(error, KeyError) and key) and not (isinstance(error, IndexError) and index):
        return None
    wanted = key.group(1) if key else int(index.group(1))
    found = _failing_value(error, code, filename,
                           lambda node: isinstance(node, ast.Subscript) and _constant(node.slice) == wanted,
                           lambda node: node.value)
    if found is None:
        return None
    path, collection = found
    if not isinstance(collection, bpy.types.bpy_prop_collection):
        return None
    keys, labels = [], []
    for item in collection:
        name = getattr(item, "name", None) or getattr(item, "identifier", None)
        if name is None:
            continue
        ident = getattr(item, "identifier", None)
        keys.append(name)
        if ident and ident != name:
            keys.append(ident)
        labels.append("%s (identifier %s)" % (name, ident) if ident and ident != name else name)
    hint = {"collection": path, "length": len(collection), "keys": _listed(labels)}
    if key:
        hint["missing"] = wanted
        hint["closest"] = _closest(wanted, keys)
    owner = getattr(collection, "id_data", None)
    if owner is not None:
        hint["of"] = "%s %r" % (type(owner).__name__, owner.name)
    return hint


def _node_type(error, text, code, filename):
    m = _NODE.search(text)
    if not isinstance(error, RuntimeError) or not m:
        return None
    wanted = m.group(1)
    names = [n for n in dir(bpy.types) if _is_node(getattr(bpy.types, n, None))]
    hint = {"missing": wanted}
    found = _failing_value(
        error, code, filename,
        lambda node: (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "new"
                      and node.args and _constant(node.args[0]) == wanted),
        lambda node: node.func.value)
    tree_type = None
    if found is not None and getattr(found[1], "id_data", None) is not None:
        tree_type = found[1].id_data.bl_idname
        hint["tree_type"] = tree_type
    candidates = _closest(wanted, names)
    if tree_type:
        hint["closest_creatable"] = _creatable(tree_type, candidates)
    else:
        hint["closest"] = candidates
    return hint


# -- helpers ----------------------------------------------------------------------------------------------------
def _is_node(cls):
    rna = getattr(cls, "bl_rna", None)
    while rna is not None:
        if rna.identifier == "Node":
            return True
        rna = rna.base
    return False


def _creatable(tree_type, candidates):
    out = []
    with bpy.data.temp_data() as data:
        tree = data.node_groups.new("hints", tree_type)
        for name in candidates:
            try:
                node = tree.nodes.new(name)
            except Exception:  # noqa: BLE001
                continue
            out.append({"type": name, "name": node.name,
                        "inputs": [s.name for s in node.inputs][:24], "outputs": [s.name for s in node.outputs][:24]})
            tree.nodes.remove(node)
    return out


def _constant(node):
    return node.value if isinstance(node, ast.Constant) else None


def _failing_value(error, code, filename, match, part):
    """The value of `part(node)` for the first AST node of the failing line that `match`es, evaluated from the
    failing frame's names with attribute reads and constant subscripts only (no call). (source, value) or None."""
    if not isinstance(code, str):
        return None
    frame, line = None, None
    tb = error.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == (filename or "<opencode execute_code>"):
            frame, line = tb.tb_frame, tb.tb_lineno
        tb = tb.tb_next
    if frame is None:
        return None
    scope = dict(frame.f_globals)
    scope.update(frame.f_locals)
    for node in ast.walk(ast.parse(code)):
        if getattr(node, "lineno", None) is None or not node.lineno <= line <= node.end_lineno or not match(node):
            continue
        target = part(node)
        try:
            return ast.unparse(target), _value(target, scope)
        except Exception:  # noqa: BLE001 - not a plain read: try the next candidate
            continue
    return None


def _value(node, scope):
    if isinstance(node, ast.Name):
        if node.id not in scope:
            raise LookupError(node.id)
        return scope[node.id]
    if isinstance(node, ast.Attribute):
        return getattr(_value(node.value, scope), node.attr)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return _value(node.value, scope)[node.slice.value]
    raise LookupError(type(node).__name__)
