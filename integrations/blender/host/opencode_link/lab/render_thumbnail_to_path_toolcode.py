# Copied from Blender Lab blender_mcp v1.0.3 (commit 2cea8d566dde07fbac28a61d698909d69724e853),
# mcp/blmcp/tools/render_thumbnail_to_path_toolcode.py with its includes expanded. Do not edit by hand.
# Copyright Blender Authors, GPL-3.0-or-later (see the SPDX lines below).

# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for low-quality thumbnail rendering.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

import contextlib
import typing
from collections.abc import Callable
from typing import NamedTuple

# Thumbnail render settings. Small resolution and low samples
# for a fast preview that is still useful for visual inspection.
# The longest dimension is clamped to this value, preserving aspect ratio.
_THUMB_DIMS_MAX = 320
_THUMB_SIMPLIFY_SUBDIV = 1
_THUMB_CYCLES_SAMPLES = 16
_THUMB_EEVEE_SAMPLES = 16


class Params(NamedTuple):
    output_path: str


class Result(NamedTuple):
    status: str
    filepath: str | None = None
    message: str | None = None


# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Inline `_backup_attrs_and_assign_multi`.

__all__ = ()

import contextlib
from typing import Generator


@contextlib.contextmanager
def _backup_attrs_and_assign_multi(
        *obj_attrs: tuple[object, dict[str, object]],
) -> Generator[None, None, None]:
    """
    Context manager that saves and assigns attributes on multiple objects,
    restoring all originals on exit.

    Each argument is a ``(obj, {attr: value, ...})`` pair.
    """
    saved: list[tuple[object, dict[str, object]]] = []
    try:
        for obj, attrs in obj_attrs:
            saved.append((obj, {attr: getattr(obj, attr) for attr in attrs}))
            for attr, value in attrs.items():
                setattr(obj, attr, value)
        yield
    finally:
        for obj, attrs in saved:
            for attr, value in attrs.items():
                setattr(obj, attr, value)


# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Inline `_deferred_tool_check_for_file_output`.

__all__ = ()

import os
from collections.abc import Callable


def _deferred_tool_check_for_file_output(
        job_type: str,
        output_path: str,
        restore_attrs: list[tuple[object, str, object]] | None = None,
) -> Callable[[], dict[str, object] | None]:
    """
    Return a deferred checker for a background job that writes a file.

    The returned callable follows the deferred tool convention
    (see ``deferred_tool.py``): returns ``None`` while the job is
    running, or a result ``dict`` once it completes.

    When *restore_attrs* is provided, each ``(obj, attr, value)`` tuple
    is restored via ``setattr`` after the job completes.
    """
    import bpy  # pylint: disable=import-error,no-name-in-module

    def check_is_finished() -> dict[str, object] | None:
        if bpy.app.is_job_running(job_type):
            return None
        if restore_attrs is not None:
            for obj, attr, value in restore_attrs:
                setattr(obj, attr, value)
        if os.path.exists(output_path):
            return {"status": "ok", "filepath": output_path}
        return {"status": "error", "message": "Job completed but output file was not created"}

    return check_is_finished


def main(params: Params) -> Result | Callable[[], dict[str, object] | None]:
    import os
    import bpy  # pylint: disable=import-error,no-name-in-module

    use_deferred = not bpy.app.background

    # Resolve the output path inside the MCP scratch directory.
    output_path = os.path.join(bpy.app.tempdir, "blender_mcp", os.path.basename(params.output_path))

    scene = bpy.context.scene
    rd = scene.render

    # Compute thumbnail resolution from originals, preserving aspect ratio.
    res_x = rd.resolution_x
    res_y = rd.resolution_y
    if res_x >= res_y:
        thumb_x = _THUMB_DIMS_MAX
        thumb_y = max(int(res_y * _THUMB_DIMS_MAX / res_x), 1)
    else:
        thumb_y = _THUMB_DIMS_MAX
        thumb_x = max(int(res_x * _THUMB_DIMS_MAX / res_y), 1)

    # NOTE: `filepath` is excluded from `_backup_attrs_and_assign_multi`
    # because `write_still` reads it after the render completes. With
    # `INVOKE_DEFAULT` the context manager would restore it before the
    # file is written. Instead, filepath is saved/restored manually.
    orig_filepath = rd.filepath
    rd.filepath = output_path

    obj_attrs: list[tuple[object, dict[str, object]]] = [
        (rd, {
            "resolution_x": thumb_x,
            "resolution_y": thumb_y,
            "resolution_percentage": 100,
            "use_simplify": True,
            "simplify_subdivision_render": _THUMB_SIMPLIFY_SUBDIV,
        }),
    ]
    if rd.engine == "CYCLES":
        obj_attrs.append((scene.cycles, {"samples": _THUMB_CYCLES_SAMPLES}))
    elif rd.engine == "BLENDER_EEVEE_NEXT":
        obj_attrs.append((scene.eevee, {"taa_render_samples": _THUMB_EEVEE_SAMPLES}))

    render_args = ('INVOKE_DEFAULT',) if use_deferred else ()

    with _backup_attrs_and_assign_multi(*obj_attrs):
        try:
            bpy.ops.render.render(*render_args, write_still=True)
        except RuntimeError as ex:
            rd.filepath = orig_filepath
            return Result(status="error", message=str(ex))

    if use_deferred:
        return _deferred_tool_check_for_file_output(
            'RENDER', output_path, restore_attrs=[(rd, "filepath", orig_filepath)],
        )

    rd.filepath = orig_filepath
    return Result(status="ok", filepath=output_path)
