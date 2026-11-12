# Copied from Blender Lab blender_mcp v1.0.3 (commit 2cea8d566dde07fbac28a61d698909d69724e853),
# mcp/blmcp/tools/render_viewport_to_path_toolcode.py with its includes expanded. Do not edit by hand.
# Copyright Blender Authors, GPL-3.0-or-later (see the SPDX lines below).

# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for rendering the current scene.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from collections.abc import Callable
from typing import NamedTuple


class Params(NamedTuple):
    output_path: str


class Result(NamedTuple):
    status: str
    filepath: str | None = None
    message: str | None = None


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

    # NOTE: `filepath` is set manually (not via context manager) because
    # `write_still` reads it after the render completes.
    # With `INVOKE_DEFAULT` a context manager would restore it too early.
    rd = scene.render
    orig_filepath = rd.filepath
    rd.filepath = output_path

    render_args = ('INVOKE_DEFAULT',) if use_deferred else ()

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
