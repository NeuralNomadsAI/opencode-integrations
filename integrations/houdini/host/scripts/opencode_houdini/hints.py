"""Hints for a failed execute_code run, computed inside Houdini in the except block, while the failing frames live.

explain(error, code, filename) returns a few lines saying what Houdini really offers where the code went wrong, or
None. Each hint reads only: node types and parameters of existing nodes, errors() and warnings(), docstrings and
dir() of hou classes. Expressions from the failing line are never evaluated, except a plain name (or a dotted name
through hou objects and modules) found in the frame and hou.node("<literal path>").
"""

from __future__ import annotations

import difflib
import re
import traceback
from typing import Any, Iterable

MAX_CHARS = 1500
MAX_ITEMS = 40
USER_FILES = ("<opencode execute_code>",)

_RECEIVER = r"(hou\s*\.\s*node\(\s*(['\"])([^'\"]+)\2\s*\)|[A-Za-z_][\w]*(?:\s*\.\s*[A-Za-z_]\w*)*)"
# Groups: 1 receiver (2, 3: a hou.node literal), 4 method, 5 quote, 6 name.
_LOOKUP = re.compile(_RECEIVER + r"\s*\.\s*(parm|parmTuple|node|evalParm|evalParmTuple)\(\s*(['\"])([^'\"]*)\5\s*\)")
_HOU_NODE = re.compile(r"hou\s*\.\s*node\(\s*(['\"])([^'\"]+)\1\s*\)")
# Groups: 1 receiver, 4 quote, 5 type name.
_CREATE = re.compile(_RECEIVER + r"\s*\.\s*createNode\(\s*(['\"])([^'\"]*)\4")
_SWIG = re.compile(r"(?:in method|overloaded function) '(\w+?)_(\w+)'")


def _hou() -> Any:
    try:
        import hou  # noqa: PLC0415 - imported where Houdini runs; tests use a stub
    except ImportError:
        return None
    return hou


def closest(name: str, candidates: Iterable[str], n: int = 5) -> list[str]:
    """The candidates closest to name: difflib matches (case-insensitive), then those containing it."""
    pool = sorted({str(c) for c in candidates if c})
    if not name or not pool:
        return []
    lower = {c.lower(): c for c in pool}
    found = [lower[c] for c in difflib.get_close_matches(name.lower(), list(lower), n=n, cutoff=0.5)]
    needle = name.lower()
    found += [c for c in pool if c not in found and (needle in c.lower() or (len(c) > 2 and c.lower() in needle))]
    return found[:n]


def _bounded(names: Iterable[str], limit: int = MAX_ITEMS) -> str:
    items = list(names)
    shown = ", ".join(items[:limit])
    return shown + (" ... (%d more)" % (len(items) - limit) if len(items) > limit else "")


def _frames(error: BaseException) -> list[tuple[Any, int]]:
    return list(traceback.walk_tb(error.__traceback__))


def _user_frames(frames: list[tuple[Any, int]], filename: str | None) -> list[tuple[Any, int]]:
    names = set(USER_FILES) | ({filename} if filename else set())
    return [(f, n) for f, n in frames if f.f_code.co_filename in names]


def _line(code: str | None, frames: list[tuple[Any, int]], filename: str | None) -> tuple[Any, str]:
    """The innermost user frame and its source line (from the submitted code)."""
    users = _user_frames(frames, filename)
    if not users or not code:
        return (users[-1][0] if users else None), ""
    frame, number = users[-1]
    lines = code.splitlines()
    return frame, (lines[number - 1] if 0 < number <= len(lines) else "")


def _resolve(text: str, frame: Any) -> Any:
    """A receiver from the failing line, only when reading it has no effect: hou.node("<literal>"), a name in the
    frame, or a dotted name whose intermediate objects are hou objects or modules."""
    hou = _hou()
    if hou is None or frame is None:
        return None
    literal = _HOU_NODE.fullmatch(text.strip())
    if literal:
        try:
            return hou.node(literal.group(2))
        except Exception:  # noqa: BLE001
            return None
    parts = [p.strip() for p in text.split(".")]
    scope = dict(frame.f_globals)
    scope.update(frame.f_locals)
    if parts[0] not in scope:
        return None
    value = scope[parts[0]]
    for part in parts[1:]:
        kind = type(value)
        if not (kind.__module__ == "hou" or type(value).__name__ == "module"):
            return None
        try:
            # A hou method is not called: only an attribute that is a plain value or object is followed.
            value = getattr(value, part)
        except Exception:  # noqa: BLE001
            return None
        if callable(value) and type(value).__name__ != "module":
            return None
    return value


def _is_node(value: Any) -> bool:
    hou = _hou()
    return hou is not None and isinstance(value, getattr(hou, "OpNode", getattr(hou, "Node", ())))


def _missing_path(path: str) -> str:
    """For a hou.node(path) that found nothing: the deepest existing ancestor and its closest children."""
    hou = _hou()
    parts = [p for p in path.split("/") if p]
    for depth in range(len(parts) - 1, -1, -1):
        parent = hou.node("/" + "/".join(parts[:depth]))
        if parent is not None:
            names = [c.name() for c in parent.children()]
            near = closest(parts[depth], names)
            return "hou.node(%r) is None: %s has no child %r%s; its children: %s" % (
                path, parent.path(), parts[depth], (" (closest: %s)" % ", ".join(near)) if near else "",
                _bounded(names) or "none")
    return "hou.node(%r) is None" % path


def _parm_hint(line: str, frame: Any, attribute: str) -> str | None:
    """A .parm("x") (or parmTuple, node, hou.node) that gave None before .attribute."""
    hits = []
    for match in _LOOKUP.finditer(line):
        receiver, method, name = match.group(1), match.group(4), match.group(6)
        rest = line[match.end():].lstrip()
        if attribute and not rest.startswith("." + attribute) and not rest.startswith(". " + attribute):
            hits.append((False, receiver, method, name))
        else:
            hits.insert(0, (True, receiver, method, name))
    for _, receiver, method, name in hits:
        literal = _HOU_NODE.fullmatch(receiver.strip())
        node = _resolve(receiver, frame)
        if literal and node is None:
            return _missing_path(literal.group(2))
        if not _is_node(node):
            continue
        if method == "node":
            try:
                target = node.node(name)
            except Exception:  # noqa: BLE001
                target = None
            if target is not None:
                continue
            names = [c.name() for c in node.children()]
            near = closest(name.split("/")[-1], names)
            return "%s.node(%r) is None%s; children of %s: %s" % (
                receiver, name, (" (closest: %s)" % ", ".join(near)) if near else "", node.path(), _bounded(names) or "none")
        if method in ("parm", "evalParm"):
            if node.parm(name) is not None:
                continue
            names = [p.name() for p in node.parms()]
            tuples = [t.name() for t in node.parmTuples()]
            near = closest(name, names)
            lines = ["%s has no parameter %r (%s.%s returned None)%s" % (
                node.path(), name, receiver, method, (": closest %s" % ", ".join(near)) if near else "")]
            if name in tuples:
                lines.append("%r is a parameter tuple: use parmTuple(%r) or one of %s" % (
                    name, name, ", ".join(p.name() for p in node.parmTuple(name))))
            lines.append("parameters of %s (%s): %s" % (node.path(), node.type().nameWithCategory(), _bounded(names)))
            return "\n".join(lines)
        if method in ("parmTuple", "evalParmTuple"):
            if node.parmTuple(name) is not None:
                continue
            tuples = [t.name() for t in node.parmTuples()]
            near = closest(name, tuples)
            lines = ["%s has no parameter tuple %r%s" % (node.path(), name, (": closest %s" % ", ".join(near)) if near else "")]
            if node.parm(name) is not None:
                lines.append("%r is a single parameter: use parm(%r)" % (name, name))
            lines.append("parameter tuples of %s: %s" % (node.path(), _bounded(tuples)))
            return "\n".join(lines)
    for match in _HOU_NODE.finditer(line):
        hou = _hou()
        if hou.node(match.group(2)) is None:
            return _missing_path(match.group(2))
    return None


def _category_hint(parent: Any, wanted: str) -> str:
    hou = _hou()
    category = parent.childTypeCategory()
    if category is None:
        return "%s holds no nodes (it is not a network)" % parent.path()
    types = category.nodeTypes()
    near = closest(wanted, types.keys())
    by_label = [name for name, t in types.items() if t.description().lower() == wanted.lower()]
    lines = ["%s (%s network) has no node type %r%s" % (
        parent.path(), category.name(), wanted, (": closest %s" % ", ".join(dict.fromkeys(by_label + near))) if near or by_label else "")]
    elsewhere = [name for name, other in hou.nodeTypeCategories().items() if other != category and wanted in other.nodeTypes()]
    if elsewhere:
        lines.append("%r is a type of %s; %s takes %s types%s" % (
            wanted, "/".join(elsewhere), parent.path(), category.name(),
            " (SOPs go inside a geo object: hou.node('/obj').createNode('geo'))" if "Sop" in elsewhere and category.name() == "Object" else ""))
    return "\n".join(lines)


def _create_hint(error: BaseException, frames: list[tuple[Any, int]], line: str, frame: Any) -> str | None:
    for f, _ in reversed(frames):
        if f.f_code.co_name == "createNode" and _is_node(f.f_locals.get("self")):
            wanted = f.f_locals.get("node_type_name")
            if wanted is None:
                args = f.f_locals.get("args") or ()
                wanted = args[0] if args else None
            if isinstance(wanted, str):
                return _category_hint(f.f_locals["self"], wanted)
    match = _CREATE.search(line)
    if match:
        parent = _resolve(match.group(1), frame)
        if _is_node(parent):
            return _category_hint(parent, match.group(5))
    return None


def _cook_hint(frames: list[tuple[Any, int]], line: str, frame: Any) -> str | None:
    hou = _hou()
    nodes = []
    for f, _ in reversed(frames):
        value = f.f_locals.get("self")
        if _is_node(value):
            nodes.append(value)
            break
    if not nodes:
        match = re.search(_RECEIVER + r"\s*\.\s*(cook|geometry|stage|render|cookWorkItems)\(", line)
        node = _resolve(match.group(1), frame) if match else None
        if _is_node(node):
            nodes.append(node)
    if not nodes and frame is not None:
        nodes = [v for v in frame.f_locals.values() if _is_node(v)][:8]
    seen, lines = set(), []
    networks = []
    for node in nodes:
        try:
            parent = node.parent()
        except Exception:  # noqa: BLE001
            continue
        if parent is not None and parent.path() not in seen:
            seen.add(parent.path())
            networks.append(parent)
    candidates = list(nodes)
    for node in nodes:
        try:
            candidates += list(node.inputAncestors())[:200]
        except Exception:  # noqa: BLE001
            pass
    for net in networks:
        candidates += list(net.children())[:500]
    done = set()
    for node in candidates:
        try:
            key = node.path()
            if key in done:
                continue
            done.add(key)
            errors, warnings = node.errors(), node.warnings()
        except Exception:  # noqa: BLE001
            continue
        if errors or warnings:
            text = "; ".join(" ".join(m.split()) for m in list(errors)[:2]) or ""
            if warnings and not errors:
                text = "warning: " + "; ".join(" ".join(m.split()) for m in list(warnings)[:2])
            lines.append("%s (%s): %s" % (key, node.type().name(), text[:300]))
        if len(lines) >= 8:
            break
    if not lines:
        return None
    return "nodes in error or warning:\n" + "\n".join(lines)


def _signature(function: Any, label: str) -> str | None:
    doc = getattr(function, "__doc__", None) or ""
    lines = [l.rstrip() for l in doc.strip("\n").splitlines()]
    head = []
    for l in lines:
        if not l.strip() and head and len(head) > 1:
            break
        if l.strip():
            head.append(l.strip())
        if len(head) >= 6:
            break
    return ("%s:\n%s" % (label, "\n".join(head))) if head else None


def _type_error_hint(error: BaseException, frames: list[tuple[Any, int]]) -> str | None:
    hou = _hou()
    match = _SWIG.search(str(error))
    if match:
        cls = getattr(hou, match.group(1), None)
        fn = getattr(cls, match.group(2), None) if cls is not None else getattr(hou, match.group(2), None)
        if fn is not None:
            return _signature(fn, "hou.%s.%s" % (match.group(1), match.group(2)))
    for f, _ in reversed(frames):
        name = f.f_code.co_filename.replace("\\", "/")
        if not name.endswith("/hou.py"):
            continue
        owner = f.f_locals.get("self")
        fn = getattr(type(owner), f.f_code.co_name, None) if owner is not None else getattr(hou, f.f_code.co_name, None)
        label = ("hou.%s.%s" % (type(owner).__name__, f.f_code.co_name)) if owner is not None else "hou.%s" % f.f_code.co_name
        if fn is not None:
            return _signature(fn, label)
    return None


def _attribute_hint(error: AttributeError) -> str | None:
    hou = _hou()
    obj, name = getattr(error, "obj", None), getattr(error, "name", None)
    if obj is None or not name:
        return None
    if obj is hou:
        near = closest(name, [n for n in dir(hou) if not n.startswith("_")])
        return ("hou has no %r: closest %s" % (name, ", ".join(near))) if near else None
    if type(obj).__module__ != "hou":
        return None
    names = [n for n in dir(type(obj)) if not n.startswith("_")]
    near = closest(name, names)
    lines = ["hou.%s has no %r%s" % (type(obj).__name__, name, (": closest %s" % ", ".join(near)) if near else "")]
    if not near:
        lines.append("its methods: %s" % _bounded(names, 60))
    return "\n".join(lines)


def explain(error: BaseException, code: str | None = None, filename: str | None = None) -> str | None:
    """A few lines on what Houdini really offers where the run failed, or None."""
    hou = _hou()
    if hou is None:
        return None
    frames = _frames(error)
    frame, line = _line(code, frames, filename)
    message = str(error)
    hint = None
    operation_failed = getattr(hou, "OperationFailed", ())
    if isinstance(error, operation_failed) and "Invalid node type name" in message:
        hint = _create_hint(error, frames, line, frame)
    elif isinstance(error, AttributeError) and message.startswith("'NoneType' object has no attribute"):
        hint = _parm_hint(line, frame, getattr(error, "name", None) or message.rsplit("'", 2)[-2])
    elif isinstance(error, AttributeError):
        hint = _attribute_hint(error)
    elif isinstance(error, TypeError):
        hint = _type_error_hint(error, frames)
    if hint is None and (
        (isinstance(error, operation_failed) and "cook" in message.lower())
        or (isinstance(error, getattr(hou, "Error", ())) and any(f.f_code.co_name in ("cook", "cookWorkItems", "render") for f, _ in frames))
    ):
        hint = _cook_hint(frames, line, frame)
    if not hint:
        return None
    return hint if len(hint) <= MAX_CHARS else hint[: MAX_CHARS - 4] + " ..."
