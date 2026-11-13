"""Writes host/ops.json, the edit operations of the add-on's typed layer as its validator accepts them (thin_plan.py,
read without Blender), so the plugin validates the same fields, the operation table of the plugin's README, and the
description of the blender_apply tool, which is what the model reads: every operation with its fields.
--check only compares and exits 1 when any of them is stale.
Usage: python -I -B integrations/blender/host/ops_json.py [--check]"""

import importlib
import json
import os
import sys
import tomllib
import types

HOST = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HOST, "opencode_link")
TARGET = os.path.join(HOST, "ops.json")

# thin_plan and limits are free of bpy; a bare package (no __init__ run) imports them alone.
package = types.ModuleType("ocl")
package.__path__ = [SOURCE]
sys.modules["ocl"] = package
plan = importlib.import_module("ocl.thin_plan")
limits = importlib.import_module("ocl.limits")

with open(os.path.join(SOURCE, "blender_manifest.toml"), "rb") as handle:
    version = tomllib.load(handle)["version"]


def field(spec):
    kind = spec["kind"]
    out = {"enum": list(kind["enum"])} if isinstance(kind, dict) else {"kind": kind}
    if spec["required"]:
        out["required"] = True
    if spec["default"] is not None:
        out["default"] = spec["default"]
    return out


document = {
    "addon": "opencode_link",
    "version": version,
    "protocol": 1,
    "method": "apply",
    "wire": "params.operations: [{op, args}] with every op below (an edit batch); unknown ops and fields are refused",
    "null": "any field given null counts as absent (its default applies; a required one is missing)",
    "limits": {"operations": limits.value("operations"), "name_bytes": limits.value("name_bytes"),
               "max_frame": limits.value("max_frame")},
    "kinds": plan.kinds(),
    "ops": {op: dict({"description": description, "args": {key: field(spec) for key, spec in fields.items()}},
                     **({"rules": list(plan.RULES[op])} if op in plan.RULES else {}))
            for op, (fields, description) in plan.SPEC.items()},
}
text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def cell(value):
    return str(value).replace("|", "\\|")


def table():
    """The README's operation table (between its ops markers), from the same document, its columns aligned as the
    repository's Markdown formatter writes them."""
    rows = [["Operation", "Arguments (`*` required, `=` default)", "Description and rules"]]
    for op, spec in document["ops"].items():
        args = []
        for key, arg in spec["args"].items():
            kind = "|".join(arg["enum"]) if "enum" in arg else arg["kind"]
            args.append("`%s`%s %s%s" % (key, "*" if arg.get("required") else "", kind,
                                          " =%s" % json.dumps(arg["default"]) if "default" in arg else ""))
        rules = "".join(" Rule: %s." % rule for rule in spec.get("rules", ()))
        rows.append(["`%s`" % op, cell("<br>".join(args)), cell(spec["description"]) + cell(rules)])
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    line = lambda row: "| " + " | ".join(value.ljust(widths[column]) for column, value in enumerate(row)) + " |"  # noqa: E731
    return "\n".join([line(rows[0]), "| " + " | ".join("-" * width for width in widths) + " |"] + [line(row) for row in rows[1:]])


# What the model reads before calling blender_apply: the conventions, the shapes of the structured fields, then every
# operation with its fields (`?` optional, `=` default, `a|b` one of), its description and its rules.
INTRO = (
    "Change the scene with up to %d typed operations as ONE undo step. Everything is checked first (names, bones, "
    "aliases, expect, if_revision); a failure while applying restores what the batch changed. Result: what changed "
    "(before/after), created names, new revision. Without if_revision the batch applies to the document as it is now.\n"
    "Each operation is {op: '<name>', ...its fields}. Refs are object names; an object created or copied earlier in the "
    "batch is \"$alias\" (its \"as\"); a rename or delete is visible to the later operations. Colours are linear RGB."
)
SHAPES = (
    "Shapes: rotation {euler_deg:[x,y,z], order?=XYZ} or {quaternion:[w,x,y,z]}, in the holder's own rotation mode; "
    "target {point:[x,y,z]} | {object, bone?} | {direction:[x,y,z]}; goal {point:[x,y,z]} | {object, bone?}, with "
    "rotation? (the end bone's world orientation); paths [location|rotation|scale]; path an RNA path such as "
    "constraints[\"Grab\"].influence or key_blocks[\"Blink\"].value; frames [first, last]; "
    "nodes [{name, type? (a new node: ShaderNodeTexNoise, ShaderNodeValToRGB, ShaderNodeMix...), location?:[x,y], "
    "label?, inputs?:{socket: value}, props?:{attribute: value}, ramp?:[[position, [r,g,b,a]], ...]}]; "
    "links [{from:[node, output], to:[node, input]}]."
)


def signature(op, spec):
    parts = []
    for key, arg in spec["args"].items():
        text = key + ("" if arg.get("required") else "?")
        if "enum" in arg:
            text += ":" + "|".join(arg["enum"])
        if "default" in arg:
            text += "=" + (arg["default"] if isinstance(arg["default"], str) else json.dumps(arg["default"]))
        parts.append(text)
    rules = "".join(" Rule: %s." % rule for rule in spec.get("rules", ()))
    return "%s {%s}: %s%s" % (op, ", ".join(parts), spec["description"], rules)


description = "\n".join([INTRO % limits.value("operations"), SHAPES] + [signature(op, spec) for op, spec in document["ops"].items()])
APPLY = os.path.join(HOST, "..", "plugin", "methods", "tools", "blender_apply.json")
manifest = open(APPLY, encoding="utf-8").read()
head_line = '  "description": '
start = manifest.index(head_line)
end = manifest.index('",\n', start) + 1
new_manifest = manifest[:start] + head_line + json.dumps(description, ensure_ascii=False) + manifest[end:]

README = os.path.join(HOST, "..", "plugin", "README.md")
START, END = "<!-- ops:start (host/ops_json.py) -->", "<!-- ops:end -->"
readme = open(README, encoding="utf-8").read()
if START not in readme or END not in readme:
    sys.exit("README.md lacks the ops markers")
head, rest = readme.split(START, 1)
new_readme = head + START + "\n" + table() + "\n" + END + rest.split(END, 1)[1]
if "--check" in sys.argv:
    current = open(TARGET, encoding="utf-8").read() if os.path.exists(TARGET) else ""
    fresh = current == text and readme == new_readme and manifest == new_manifest
    print("ops.json, README table and blender_apply description %s (%d ops)" % ("up to date" if fresh else "STALE",
                                                                                len(document["ops"])))
    sys.exit(0 if fresh else 1)
with open(TARGET, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(text)
with open(README, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(new_readme)
with open(APPLY, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(new_manifest)
print("wrote ops.json, the README table and the blender_apply description: %d ops, version %s" % (len(document["ops"]), version))
