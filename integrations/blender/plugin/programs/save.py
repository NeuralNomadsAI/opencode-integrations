# SPDX-License-Identifier: GPL-3.0-or-later
# blender_save (fixed program): save the open file, or save it under a path (as the open file, or as a copy).
# PARAMS: {"path"?, "copy"?, "overwrite"?}
import os

import bpy

P = PARAMS  # noqa: F821
current = bpy.data.filepath
target = P.get("path")
if target:
    if not os.path.isabs(target):
        if not current:
            raise ValueError("path %r is relative and the file was never saved: give an absolute path" % target)
        target = os.path.join(os.path.dirname(current), target)
    target = os.path.normpath(target)
    if not target.lower().endswith(".blend"):
        raise ValueError("path must end with .blend")
    if os.path.exists(target) and os.path.normcase(target) != os.path.normcase(current) and not P.get("overwrite"):
        raise ValueError("%s exists: pass overwrite true or name another file" % target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
elif not current:
    raise ValueError("the file was never saved: pass path")


def with_window(fn):
    wm = bpy.context.window_manager
    if wm is not None and wm.windows and not bpy.app.background:
        with bpy.context.temp_override(window=wm.windows[0]):
            return fn()
    return fn()


if target:
    with_window(lambda: bpy.ops.wm.save_as_mainfile(filepath=target, copy=bool(P.get("copy")), check_existing=False))
else:
    with_window(lambda: bpy.ops.wm.save_mainfile())
written = target or current
result = {"saved": written, "bytes": os.path.getsize(written), "open_file": bpy.data.filepath,
          **({"copy": True} if P.get("copy") else {})}
