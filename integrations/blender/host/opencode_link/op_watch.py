# SPDX-License-Identifier: GPL-3.0-or-later

"""The operator calls of supplied code (execute_code), watched while it runs.

Every `bpy.ops` callable the code creates is wrapped: a failing call names its operator, so the error's hint reads
the operator's real signature. Two calls that close Blender 5.3 from a script are prepared first, as Blender's
interactive path prepares them:

- a mask stroke (`sculpt.brush_stroke` with the Mask brush or brush_toggle='MASK') on a multiresolution object.
  The operator's interactive path creates the grid mask layer before painting; its script path does not, and the
  stroke writes into grids never allocated (a heap corruption: Blender closes on the next mode switch or redraw).
  The layer is created first, as the interactive path does: inverting the mask twice keeps its values.
- a sculpt-mode operator in a Blender without interface: its sculpt undo step reads the undo stack, which a
  background Blender makes at its first undo step only. One step is pushed first when none was yet (an
  execute_code that changes the document already pushed its barrier step).

Where `bpy.ops` creates its callables otherwise (another Blender version), nothing is wrapped.
"""

import contextlib

import bpy

# Sculpt-mode operators that record no sculpt undo step.
_NO_SCULPT_UNDO = frozenset(("sculpt.sculptmode_toggle",))
_undo_stack = {"made": False}
UNDO_STEP = "OpenCode: undo stack"
UNDO_NOTE = ("OpenCode: pushed a first undo step before %s: a Blender without interface makes its undo stack then, "
             "and a sculpt-mode operator closes it without one.")
MASK_LAYER_NOTE = ("OpenCode: created the multiresolution mask layer of %r before the scripted mask stroke "
                   "(Blender's script path of sculpt.brush_stroke skips it and closes Blender).")


def undo_stack_made():
    """An undo step was pushed: from then on this Blender has its undo stack."""
    _undo_stack["made"] = True


class _Watch(object):
    def __init__(self, create):
        self.create = create  # the original factory: the guards' own calls are not watched
        self.notes = []
        self.prepared = set()

    def before(self, idname, args, kwargs):
        module = idname.partition(".")[0]
        if module not in ("sculpt", "paint"):
            return
        obj = getattr(bpy.context, "active_object", None)
        sculpting = obj is not None and getattr(obj, "mode", None) == "SCULPT"
        if not sculpting:
            return
        if bpy.app.background and idname not in _NO_SCULPT_UNDO and not _undo_stack["made"]:
            self.create("ed", "undo_push")(message=UNDO_STEP)
            undo_stack_made()
            self.notes.append(UNDO_NOTE % idname)
        if idname == "sculpt.brush_stroke" and not _invoked(args) and _masks(kwargs) and _multires(obj):
            key = obj.as_pointer()
            if key in self.prepared:
                return
            invert = self.create("paint", "mask_flood_fill")
            invert(mode="INVERT")
            invert(mode="INVERT")
            self.prepared.add(key)
            self.notes.append(MASK_LAYER_NOTE % obj.name)


def _invoked(args):
    # An INVOKE_* call goes through the interactive path, which prepares the layer itself.
    return bool(args) and isinstance(args[0], str) and args[0].startswith("INVOKE")


def _masks(kwargs):
    if kwargs.get("brush_toggle") == "MASK":
        return True
    paint = getattr(bpy.context.tool_settings, "sculpt", None)
    brush = getattr(paint, "brush", None)
    return getattr(brush, "sculpt_brush_type", None) == "MASK"


def _multires(obj):
    return any(m.type == "MULTIRES" and m.show_viewport for m in getattr(obj, "modifiers", ()))


class _Operator(object):
    """A bpy.ops callable: the same call, poll, get_rna_type and repr, watched."""

    __slots__ = ("_op", "_idname", "_watch")

    def __init__(self, op, idname, watch):
        self._op = op
        self._idname = idname
        self._watch = watch

    def __call__(self, *args, **kwargs):
        try:
            self._watch.before(self._idname, args, kwargs)
            return self._op(*args, **kwargs)
        except Exception as error:
            try:
                error.opencode_operator = self._idname  # read by hints.explain
            except Exception:  # noqa: BLE001 - an exception without attributes keeps its message
                pass
            raise

    def __getattr__(self, name):
        return getattr(self._op, name)

    def __repr__(self):
        return repr(self._op)


@contextlib.contextmanager
def watching():
    """Wraps the bpy.ops callables created inside the block; yields the watch (its notes)."""
    ops = getattr(bpy, "ops", None)
    create = getattr(ops, "_op_create_function", None)
    if create is None:
        yield _Watch(None)
        return
    watch = _Watch(create)
    ops._op_create_function = lambda module, func: _Operator(create(module, func), "%s.%s" % (module, func), watch)
    try:
        yield watch
    finally:
        ops._op_create_function = create
