# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Material conversion handlers for VRScene -> Blender workflows."""

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false

from __future__ import annotations

from pathlib import Path

import bpy  # type: ignore[import-not-found]

from .vrscene import analyze_vrscene_file as analyze_vrscene_file_impl
from .vrscene import convert_vrscene_file as convert_vrscene_file_impl
from .vrscene import convert_vrscene_folder as convert_vrscene_folder_impl


def _serialize_socket(item):
    data = {
        "name": item.name,
        "socket_type": item.socket_type,
        "identifier": item.identifier,
        "in_out": item.in_out,
    }
    if hasattr(item, "default_value"):
        try:
            data["default_value"] = list(item.default_value)
        except TypeError:
            data["default_value"] = item.default_value
    return data


def list_shader_node_groups(filter_text=None):
    result = []
    filter_lower = filter_text.lower() if filter_text else None
    for node_group in bpy.data.node_groups:
        if node_group.bl_idname != "ShaderNodeTree":
            continue
        if filter_lower and filter_lower not in node_group.name.lower():
            continue
        result.append(
            {
                "name": node_group.name,
                "node_count": len(node_group.nodes),
                "link_count": len(node_group.links),
                "inputs": [
                    item.name
                    for item in node_group.interface.items_tree
                    if item.item_type == "SOCKET" and item.in_out == "INPUT"
                ],
                "outputs": [
                    item.name
                    for item in node_group.interface.items_tree
                    if item.item_type == "SOCKET" and item.in_out == "OUTPUT"
                ],
            }
        )
    return {"node_groups": result}


def get_shader_node_group_info(name):
    node_group = bpy.data.node_groups.get(name)
    if not node_group:
        raise ValueError(f"Node group not found: {name}")
    if node_group.bl_idname != "ShaderNodeTree":
        raise ValueError(f"Node group is not a ShaderNodeTree: {name}")

    inputs = []
    outputs = []
    for item in node_group.interface.items_tree:
        if item.item_type != "SOCKET":
            continue
        serialized = _serialize_socket(item)
        if item.in_out == "INPUT":
            inputs.append(serialized)
        else:
            outputs.append(serialized)

    links = []
    for link in node_group.links:
        links.append(
            {
                "from_node": link.from_node.name,
                "from_socket": link.from_socket.name,
                "to_node": link.to_node.name,
                "to_socket": link.to_socket.name,
            }
        )

    nodes = []
    for node in node_group.nodes:
        nodes.append(
            {
                "name": node.name,
                "bl_idname": node.bl_idname,
                "label": node.label,
                "location": [node.location.x, node.location.y],
                "inputs": [socket.name for socket in node.inputs],
                "outputs": [socket.name for socket in node.outputs],
            }
        )

    return {
        "name": node_group.name,
        "type": node_group.bl_idname,
        "inputs": inputs,
        "outputs": outputs,
        "nodes": nodes,
        "links": links,
    }


def analyze_vrscene_file(filepath):
    path = Path(filepath)
    if not path.exists():
        raise ValueError(f"VRScene file not found: {filepath}")
    return analyze_vrscene_file_impl(str(path))


def convert_vrscene_file(
    filepath,
    mapping_group_name=None,
    group_socket_map=None,
    replace_existing=False,
    use_fake_user=True,
):
    path = Path(filepath)
    if not path.exists():
        raise ValueError(f"VRScene file not found: {filepath}")
    return convert_vrscene_file_impl(
        str(path),
        mapping_group_name=mapping_group_name,
        group_socket_map=group_socket_map,
        replace_existing=replace_existing,
        use_fake_user=use_fake_user,
    )


def convert_vrscene_folder(
    folder_path,
    output_blend_path=None,
    mapping_group_name=None,
    group_socket_map=None,
    replace_existing=False,
    use_fake_user=True,
):
    path = Path(folder_path)
    if not path.exists():
        raise ValueError(f"Folder not found: {folder_path}")
    return convert_vrscene_folder_impl(
        str(path),
        output_blend_path=output_blend_path,
        mapping_group_name=mapping_group_name,
        group_socket_map=group_socket_map,
        replace_existing=replace_existing,
        use_fake_user=use_fake_user,
    )
