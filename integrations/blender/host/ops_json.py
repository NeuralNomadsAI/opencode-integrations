"""Writes host/ops.json, the edit operations of the add-on's typed layer as its validator accepts them (thin_plan.py,
read without Blender), so the plugin validates the same fields, and the operation table of the plugin's README.
--check only compares and exits 1 when either is stale.
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


README = os.path.join(HOST, "..", "plugin", "README.md")
START, END = "<!-- ops:start (host/ops_json.py) -->", "<!-- ops:end -->"
readme = open(README, encoding="utf-8").read()
if START not in readme or END not in readme:
    sys.exit("README.md lacks the ops markers")
head, rest = readme.split(START, 1)
new_readme = head + START + "\n" + table() + "\n" + END + rest.split(END, 1)[1]
if "--check" in sys.argv:
    current = open(TARGET, encoding="utf-8").read() if os.path.exists(TARGET) else ""
    fresh = current == text and readme == new_readme
    print("ops.json and README table %s (%d ops)" % ("up to date" if fresh else "STALE", len(document["ops"])))
    sys.exit(0 if fresh else 1)
with open(TARGET, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(text)
with open(README, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(new_readme)
print("wrote ops.json and the README table: %d ops, version %s" % (len(document["ops"]), version))
