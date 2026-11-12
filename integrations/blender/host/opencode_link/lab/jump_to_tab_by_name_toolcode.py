# Copied from Blender Lab blender_mcp v1.0.3 (commit 2cea8d566dde07fbac28a61d698909d69724e853),
# mcp/blmcp/tools/jump_to_tab_by_name_toolcode.py with its includes expanded. Do not edit by hand.
# Copyright Blender Authors, GPL-3.0-or-later (see the SPDX lines below).

# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for switching workspace tabs by name.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import NamedTuple


class Params(NamedTuple):
    name: str


class Result(NamedTuple):
    status: str
    workspace: str | None = None
    message: str | None = None
    available_workspaces: list[str] | None = None


def main(params: Params) -> Result:
    import bpy  # pylint: disable=import-error,no-name-in-module

    if bpy.app.background:
        return Result(status="error", message="Not available in background mode")
    if bpy.context.window is None:
        return Result(status="error", message="No active window")

    ws = bpy.data.workspaces.get(params.name)
    if ws is None:
        return Result(
            status="error",
            message="Workspace {!r} not found".format(params.name),
            available_workspaces=[w.name for w in bpy.data.workspaces],
        )

    bpy.context.window.workspace = ws
    return Result(status="ok", workspace=ws.name)
