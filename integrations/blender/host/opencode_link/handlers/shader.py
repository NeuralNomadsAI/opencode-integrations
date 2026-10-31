# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Shader editor and material handlers for the OpenCode Blender bundle runtime."""

import bpy

from .captures import capture_area, find_area
from .node_utils import resolve_socket, serialize_node, serialize_node_tree, set_socket_default_value


def _get_object(object_name):
    obj = bpy.data.objects.get(object_name)
    if not obj:
        raise ValueError(f"Object not found: {object_name}")
    return obj


def _get_material(material_name):
    material = bpy.data.materials.get(material_name)
    if not material:
        raise ValueError(f"Material not found: {material_name}")
    return material


def _get_shader_group(group_name):
    group = bpy.data.node_groups.get(group_name)
    if not group:
        raise ValueError(f"Shader node group not found: {group_name}")
    if group.bl_idname != "ShaderNodeTree":
        raise ValueError(f"'{group_name}' is not a ShaderNodeTree")
    return group


def _resolve_shader_tree(material_name=None, group_name=None):
    if bool(material_name) == bool(group_name):
        raise ValueError("Provide exactly one of material_name or group_name")

    if material_name:
        material = _get_material(material_name)
        if not material.use_nodes:
            material.use_nodes = True
        return material.node_tree, {"material_name": material.name}

    group = _get_shader_group(group_name)
    return group, {"group_name": group.name}


def _material_users(material):
    users = []
    for obj in bpy.data.objects:
        for index, slot in enumerate(obj.material_slots):
            if slot.material == material:
                users.append({"object_name": obj.name, "slot_index": index})
    return users


def _material_summary(material):
    node_tree = material.node_tree if material.use_nodes else None
    return {
        "name": material.name,
        "use_nodes": material.use_nodes,
        "users": material.users,
        "is_asset": bool(material.asset_data),
        "slot_users": _material_users(material),
        "node_count": len(node_tree.nodes) if node_tree else 0,
        "link_count": len(node_tree.links) if node_tree else 0,
    }


def list_materials(filter_text=None, only_node_materials=False):
    """List materials in the current Blender file."""
    materials = []
    for material in bpy.data.materials:
        if filter_text and filter_text.lower() not in material.name.lower():
            continue
        if only_node_materials and not material.use_nodes:
            continue
        materials.append(_material_summary(material))

    return {"materials": materials, "count": len(materials)}


def get_material_info(name):
    """Get detailed information about a material and its shader graph."""
    material = _get_material(name)
    info = _material_summary(material)
    if material.use_nodes and material.node_tree:
        info["node_tree"] = serialize_node_tree(material.node_tree)
    return info


def create_material(name, use_nodes=True):
    """Create a new material."""
    if bpy.data.materials.get(name):
        raise ValueError(f"Material already exists: {name}")

    material = bpy.data.materials.new(name)
    material.use_nodes = use_nodes
    return _material_summary(material)


def delete_material(name):
    """Delete a material from the file."""
    material = _get_material(name)
    users = _material_users(material)

    try:
        bpy.data.materials.remove(material, do_unlink=True)
    except TypeError:
        bpy.data.materials.remove(material)

    return {"name": name, "deleted": True, "slot_users": users}


def assign_material(object_name, material_name, slot_index=0):
    """Assign a material to an object material slot."""
    obj = _get_object(object_name)
    material = _get_material(material_name)

    if not obj.data or not hasattr(obj.data, "materials"):
        raise ValueError(f"Object '{object_name}' does not support material slots")

    slot_index = int(slot_index)
    if slot_index < 0:
        raise ValueError("slot_index must be >= 0")

    while len(obj.material_slots) <= slot_index:
        obj.data.materials.append(material)

    obj.material_slots[slot_index].material = material

    return {"object_name": obj.name, "material_name": material.name, "slot_index": slot_index}


def list_shader_node_groups(filter_text=None):
    """List shader node groups in the current file."""
    groups = []
    for node_group in bpy.data.node_groups:
        if node_group.bl_idname != "ShaderNodeTree":
            continue
        if filter_text and filter_text.lower() not in node_group.name.lower():
            continue
        groups.append(
            {
                "name": node_group.name,
                "node_count": len(node_group.nodes),
                "link_count": len(node_group.links),
                "users": node_group.users,
                "is_asset": bool(node_group.asset_data),
            }
        )
    return {"groups": groups, "count": len(groups)}


def get_shader_node_group_info(name):
    """Get detailed info for a shader node group."""
    group = _get_shader_group(name)
    return serialize_node_tree(group)


def list_shader_node_types(filter=None):
    """List available shader node types. Optionally filter by keyword."""
    node_types = []
    utility_extras = [
        ("NodeGroupInput", "Group Input", "Input node for the node group"),
        ("NodeGroupOutput", "Group Output", "Output node for the node group"),
        ("NodeFrame", "Frame", "Visual frame for organizing nodes"),
        ("NodeReroute", "Reroute", "Reroute connection lines"),
    ]

    for attr_name in dir(bpy.types):
        if not attr_name.startswith("ShaderNode"):
            continue
        if attr_name == "ShaderNode":
            continue
        cls = getattr(bpy.types, attr_name, None)
        if not cls or not hasattr(cls, "bl_rna"):
            continue

        entry = {
            "bl_idname": attr_name,
            "name": cls.bl_rna.name,
            "description": cls.bl_rna.description or "",
        }
        if filter and filter.lower() not in entry["name"].lower() and filter.lower() not in entry["bl_idname"].lower():
            continue
        node_types.append(entry)

    for bl_idname, name, description in utility_extras:
        if filter and filter.lower() not in name.lower() and filter.lower() not in bl_idname.lower():
            continue
        node_types.append({"bl_idname": bl_idname, "name": name, "description": description})

    return {"node_types": node_types, "count": len(node_types)}


def add_shader_node_group_socket(group_name, name, in_out="INPUT", socket_type="NodeSocketFloat"):
    """Add an interface socket to a shader node group."""
    group = _get_shader_group(group_name)
    group.interface.new_socket(name, in_out=in_out, socket_type=socket_type)
    return {"group_name": group.name, "socket": name, "direction": in_out, "type": socket_type}


def add_shader_node(node_type, material_name=None, group_name=None, name=None, location=None):
    """Add a node to a material or shader node group."""
    tree, owner = _resolve_shader_tree(material_name=material_name, group_name=group_name)
    node = tree.nodes.new(node_type)

    if name:
        node.name = name
        node.label = name
    if location:
        node.location = tuple(location)

    result = serialize_node(node)
    result.update(owner)
    return result


def remove_shader_node(node_name, material_name=None, group_name=None):
    """Remove a node from a material or shader node group."""
    tree, owner = _resolve_shader_tree(material_name=material_name, group_name=group_name)
    node = tree.nodes.get(node_name)
    if not node:
        raise ValueError(f"Node not found: {node_name}")

    tree.nodes.remove(node)
    result = {"removed": node_name}
    result.update(owner)
    return result


def connect_shader_nodes(from_node, from_socket, to_node, to_socket, material_name=None, group_name=None):
    """Connect two shader nodes."""
    tree, owner = _resolve_shader_tree(material_name=material_name, group_name=group_name)
    source = tree.nodes.get(from_node)
    destination = tree.nodes.get(to_node)
    if not source:
        raise ValueError(f"Source node not found: {from_node}")
    if not destination:
        raise ValueError(f"Destination node not found: {to_node}")

    output_socket = resolve_socket(source.outputs, from_socket, "Output", from_node)
    input_socket = resolve_socket(destination.inputs, to_socket, "Input", to_node)
    tree.links.new(output_socket, input_socket)

    result = {"from": f"{from_node}.{output_socket.name}", "to": f"{to_node}.{input_socket.name}"}
    result.update(owner)
    return result


def disconnect_shader_nodes(from_node, from_socket, to_node, to_socket, material_name=None, group_name=None):
    """Disconnect two shader nodes."""
    tree, owner = _resolve_shader_tree(material_name=material_name, group_name=group_name)
    for link in tree.links:
        if (
            link.from_node.name == from_node
            and link.from_socket.name == from_socket
            and link.to_node.name == to_node
            and link.to_socket.name == to_socket
        ):
            tree.links.remove(link)
            result = {"disconnected": True}
            result.update(owner)
            return result

    raise ValueError(f"Link not found: {from_node}.{from_socket} -> {to_node}.{to_socket}")


def set_shader_node_input(node_name, input_name, value, material_name=None, group_name=None):
    """Set the default value of a shader node input."""
    tree, owner = _resolve_shader_tree(material_name=material_name, group_name=group_name)
    node = tree.nodes.get(node_name)
    if not node:
        raise ValueError(f"Node not found: {node_name}")

    socket = resolve_socket(node.inputs, input_name, "Input", node_name)
    assigned_value = set_socket_default_value(socket, value)

    result = {
        "node": node.name,
        "input": socket.name,
        "identifier": getattr(socket, "identifier", socket.name),
        "value": assigned_value,
    }
    result.update(owner)
    return result


_SHADER_TEMPLATES = {
    "principled_pbr": "Principled BSDF connected to Material Output.",
    "emission": "Emission shader connected to Material Output.",
    "glass": "Glass BSDF connected to Material Output.",
}


def create_shader_material_from_template(
    name,
    template_name="principled_pbr",
    base_color=None,
    roughness=None,
    metallic=None,
    transmission=None,
    emission_color=None,
    emission_strength=None,
    alpha=None,
):
    """Create a new material from a small high-level shader template."""
    if template_name not in _SHADER_TEMPLATES:
        available = ", ".join(sorted(_SHADER_TEMPLATES))
        raise ValueError(f"Unknown shader template: {template_name}. Available: {available}")

    material = bpy.data.materials.get(name)
    if material:
        raise ValueError(f"Material already exists: {name}")

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (260, 0)

    if template_name == "principled_pbr":
        shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
        shader.location = (-120, 0)
        tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])

        if base_color is not None:
            set_socket_default_value(shader.inputs["Base Color"], base_color)
        if roughness is not None:
            set_socket_default_value(shader.inputs["Roughness"], roughness)
        if metallic is not None:
            set_socket_default_value(shader.inputs["Metallic"], metallic)
        if transmission is not None and "Transmission Weight" in shader.inputs:
            set_socket_default_value(shader.inputs["Transmission Weight"], transmission)
        if alpha is not None:
            set_socket_default_value(shader.inputs["Alpha"], alpha)
    elif template_name == "emission":
        shader = tree.nodes.new("ShaderNodeEmission")
        shader.location = (-120, 0)
        tree.links.new(shader.outputs["Emission"], output.inputs["Surface"])

        if emission_color is not None:
            set_socket_default_value(shader.inputs["Color"], emission_color)
        if emission_strength is not None:
            set_socket_default_value(shader.inputs["Strength"], emission_strength)
    else:
        shader = tree.nodes.new("ShaderNodeBsdfGlass")
        shader.location = (-120, 0)
        tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])

        if roughness is not None:
            set_socket_default_value(shader.inputs["Roughness"], roughness)
        if base_color is not None:
            set_socket_default_value(shader.inputs["Color"], base_color)

    result = get_material_info(material.name)
    result["template_name"] = template_name
    result["available_templates"] = sorted(_SHADER_TEMPLATES)
    return result


def get_shader_editor_screenshot(material_name=None, group_name=None, max_size=1200, fit_all=True):
    """Capture the Shader Editor showing a material or a shader node group."""
    area = find_area("NODE_EDITOR")
    tree, owner = _resolve_shader_tree(material_name=material_name, group_name=group_name)
    space = area.spaces.active
    if hasattr(space, "ui_type"):
        space.ui_type = "ShaderNodeTree"
    if hasattr(space, "tree_type"):
        space.tree_type = "ShaderNodeTree"
    if hasattr(space, "node_tree"):
        space.node_tree = tree
    result = capture_area(area, max_size, fit_all=fit_all)
    result.update(owner)
    return result
