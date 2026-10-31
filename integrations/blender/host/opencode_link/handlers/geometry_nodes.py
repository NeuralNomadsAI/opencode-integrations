# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Geometry Nodes handlers for the OpenCode Blender bundle runtime."""

import bpy

from .node_utils import (
    modifier_input_set,
    resolve_socket,
    serialize_node,
    serialize_node_tree,
    set_socket_default_value,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_tree(tree_name):
    tree = bpy.data.node_groups.get(tree_name)
    if not tree:
        raise ValueError(f"Node tree not found: {tree_name}")
    if tree.bl_idname != "GeometryNodeTree":
        raise ValueError(f"'{tree_name}' is not a GeometryNodeTree")
    return tree


def _get_object(object_name):
    obj = bpy.data.objects.get(object_name)
    if not obj:
        raise ValueError(f"Object not found: {object_name}")
    return obj


def _attach_tree_to_object(tree, object_name, modifier_name=None):
    obj = _get_object(object_name)
    modifier = obj.modifiers.get(modifier_name) if modifier_name else None
    if modifier and modifier.type != "NODES":
        raise ValueError(f"Modifier '{modifier_name}' exists on '{object_name}' but is not a Geometry Nodes modifier")
    if modifier is None:
        modifier = obj.modifiers.new(name=modifier_name or tree.name, type="NODES")
    modifier.node_group = tree
    return {"object_name": obj.name, "modifier_name": modifier.name}


def _get_assigned_modifiers(tree):
    assigned = []
    for obj in bpy.data.objects:
        for modifier in obj.modifiers:
            if modifier.type == "NODES" and modifier.node_group == tree:
                assigned.append({"object_name": obj.name, "modifier_name": modifier.name})
    return assigned


# ---------------------------------------------------------------------------
# Tree listing / introspection
# ---------------------------------------------------------------------------
def list_geometry_node_trees():
    """List all geometry node trees in the file."""
    trees = []
    for node_group in bpy.data.node_groups:
        if node_group.bl_idname == "GeometryNodeTree":
            assigned_modifiers = _get_assigned_modifiers(node_group)
            trees.append({
                "name": node_group.name,
                "node_count": len(node_group.nodes),
                "link_count": len(node_group.links),
                "users": node_group.users,
                "is_asset": bool(node_group.asset_data),
                "assigned_modifiers": assigned_modifiers,
            })
    return {"trees": trees}


def list_geometry_nodes_modifiers(object_name=None):
    """List Geometry Nodes modifiers in the scene or on one object."""
    objects = [_get_object(object_name)] if object_name else bpy.data.objects
    entries = []

    for obj in objects:
        modifiers = []
        for modifier in obj.modifiers:
            if modifier.type != "NODES":
                continue
            modifiers.append(
                {
                    "modifier_name": modifier.name,
                    "tree_name": modifier.node_group.name if modifier.node_group else None,
                }
            )
        if modifiers or object_name:
            entries.append({"object_name": obj.name, "modifiers": modifiers})

    return {"objects": entries, "count": sum(len(entry["modifiers"]) for entry in entries)}


def get_geometry_node_tree(tree_name):
    """Get full introspection of a geometry node tree."""
    tree = _get_tree(tree_name)
    info = serialize_node_tree(tree)
    info["assigned_modifiers"] = _get_assigned_modifiers(tree)
    return info


# ---------------------------------------------------------------------------
# Tree creation
# ---------------------------------------------------------------------------
def create_geometry_node_tree(name, object_name=None):
    """Create a new GN tree. Optionally assign to an object as a modifier."""
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")

    input_node = tree.nodes.new("NodeGroupInput")
    input_node.location = (-300, 0)
    output_node = tree.nodes.new("NodeGroupOutput")
    output_node.location = (300, 0)

    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    tree.links.new(input_node.outputs[0], output_node.inputs[0])

    result = {"tree_name": tree.name}

    if object_name:
        result.update(_attach_tree_to_object(tree, object_name, modifier_name=name))

    return result


def attach_geometry_node_tree(object_name, tree_name, modifier_name=None):
    """Attach an existing Geometry Nodes tree to an object."""
    tree = _get_tree(tree_name)
    result = {"tree_name": tree.name}
    result.update(_attach_tree_to_object(tree, object_name, modifier_name=modifier_name))
    return result


# ---------------------------------------------------------------------------
# Node operations
# ---------------------------------------------------------------------------
def add_geometry_node(tree_name, node_type, name=None, location=None):
    """Add a node to a GN tree. Returns the created node info."""
    tree = _get_tree(tree_name)

    node = tree.nodes.new(node_type)
    if name:
        node.name = name
        node.label = name
    if location:
        node.location = tuple(location)

    return serialize_node(node)


def remove_geometry_node(tree_name, node_name):
    """Remove a node from a GN tree."""
    tree = _get_tree(tree_name)
    node = tree.nodes.get(node_name)
    if not node:
        raise ValueError(f"Node not found: {node_name}")
    tree.nodes.remove(node)
    return {"removed": node_name}


def rename_geometry_node(tree_name, node_name, new_name):
    """Rename a node in a Geometry Nodes tree."""
    tree = _get_tree(tree_name)
    node = tree.nodes.get(node_name)
    if not node:
        raise ValueError(f"Node not found: {node_name}")

    previous_name = node.name
    previous_label = node.label
    node.name = new_name
    if not previous_label or previous_label == previous_name:
        node.label = new_name

    return {"old_name": previous_name, "new_name": node.name, "label": node.label}


def set_geometry_node_location(tree_name, node_name, location):
    """Move a node to a new [x, y] editor position."""
    tree = _get_tree(tree_name)
    node = tree.nodes.get(node_name)
    if not node:
        raise ValueError(f"Node not found: {node_name}")

    if len(location) != 2:
        raise ValueError("location must be [x, y]")

    node.location = tuple(location)
    return {"node": node.name, "location": list(node.location)}


def connect_geometry_nodes(tree_name, from_node, from_socket, to_node, to_socket):
    """Connect two nodes in a GN tree."""
    tree = _get_tree(tree_name)

    source = tree.nodes.get(from_node)
    destination = tree.nodes.get(to_node)
    if not source:
        raise ValueError(f"Source node not found: {from_node}")
    if not destination:
        raise ValueError(f"Destination node not found: {to_node}")

    output_socket = resolve_socket(source.outputs, from_socket, "Output", from_node)
    input_socket = resolve_socket(destination.inputs, to_socket, "Input", to_node)

    tree.links.new(output_socket, input_socket)
    return {
        "from": f"{from_node}.{output_socket.name}",
        "to": f"{to_node}.{input_socket.name}",
    }


def disconnect_geometry_nodes(tree_name, from_node, from_socket, to_node, to_socket):
    """Remove a specific link between two nodes."""
    tree = _get_tree(tree_name)

    for link in tree.links:
        if (
            link.from_node.name == from_node
            and link.from_socket.name == from_socket
            and link.to_node.name == to_node
            and link.to_socket.name == to_socket
        ):
            tree.links.remove(link)
            return {"disconnected": True}

    raise ValueError(f"Link not found: {from_node}.{from_socket} -> {to_node}.{to_socket}")


# ---------------------------------------------------------------------------
# Node input values
# ---------------------------------------------------------------------------
def set_node_input(tree_name, node_name, input_name, value):
    """Set a default value on a node input socket."""
    tree = _get_tree(tree_name)
    node = tree.nodes.get(node_name)
    if not node:
        raise ValueError(f"Node not found: {node_name}")

    socket = resolve_socket(node.inputs, input_name, "Input", node_name)
    assigned_value = set_socket_default_value(socket, value)

    return {
        "node": node_name,
        "input": socket.name,
        "identifier": getattr(socket, "identifier", socket.name),
        "value": assigned_value,
    }


def set_modifier_input(object_name, modifier_name, input_name, value):
    """Set an exposed input on a Geometry Nodes modifier."""
    obj = _get_object(object_name)

    modifier = obj.modifiers.get(modifier_name)
    if not modifier or modifier.type != "NODES":
        raise ValueError(f"GN modifier '{modifier_name}' not found on '{object_name}'")

    if not modifier.node_group:
        raise ValueError(f"Modifier '{modifier_name}' has no node group")

    for item in modifier.node_group.interface.items_tree:
        if (
            item.item_type == "SOCKET"
            and item.in_out == "INPUT"
            and (item.name == input_name or item.identifier == input_name)
        ):
            modifier_input_set(modifier, item.identifier, value)
            return {
                "object": object_name,
                "modifier": modifier_name,
                "input": item.name,
                "identifier": item.identifier,
                "value": value,
            }

    raise ValueError(f"Input '{input_name}' not found on modifier '{modifier_name}'")


# ---------------------------------------------------------------------------
# Interface sockets
# ---------------------------------------------------------------------------
def add_node_tree_socket(tree_name, name, in_out="INPUT", socket_type="NodeSocketFloat"):
    """Add an interface socket (exposed input or output) to a GN tree."""
    tree = _get_tree(tree_name)
    tree.interface.new_socket(name, in_out=in_out, socket_type=socket_type)
    return {"tree": tree_name, "socket": name, "direction": in_out, "type": socket_type}


# ---------------------------------------------------------------------------
# Node type discovery
# ---------------------------------------------------------------------------
def list_available_node_types(filter=None):
    """List available geometry node types. Optionally filter by keyword."""
    node_types = []
    prefixes = (
        "GeometryNode",
        "ShaderNodeMath", "ShaderNodeVectorMath", "ShaderNodeMapRange",
        "ShaderNodeClamp", "ShaderNodeMix", "ShaderNodeValToRGB",
        "ShaderNodeTexNoise", "ShaderNodeTexVoronoi", "ShaderNodeTexWave",
        "ShaderNodeTexWhiteNoise", "ShaderNodeTexGradient",
        "FunctionNode",
    )
    utility_extras = [
        ("NodeGroupInput", "Group Input", "Input node for the node group"),
        ("NodeGroupOutput", "Group Output", "Output node for the node group"),
    ]

    for attr_name in dir(bpy.types):
        if not attr_name.startswith(prefixes):
            continue
        if attr_name == "GeometryNode":
            continue
        cls = getattr(bpy.types, attr_name, None)
        if not cls or not hasattr(cls, "bl_rna"):
            continue
        rna = cls.bl_rna
        entry = {
            "bl_idname": attr_name,
            "name": rna.name,
            "description": rna.description or "",
        }
        if filter and filter.lower() not in entry["name"].lower() and filter.lower() not in entry["bl_idname"].lower():
            continue
        node_types.append(entry)

    for bl_idname, name, description in utility_extras:
        if filter and filter.lower() not in name.lower() and filter.lower() not in bl_idname.lower():
            continue
        node_types.append({"bl_idname": bl_idname, "name": name, "description": description})

    return {"node_types": node_types, "count": len(node_types)}


# ---------------------------------------------------------------------------
# GN Templates - high-level presets for common setups
# ---------------------------------------------------------------------------
_GN_TEMPLATES = {
    "scatter_on_surface": {
        "description": "Scatter instances on a mesh surface (grass, rocks, trees, etc.). "
        "Creates: Distribute Points on Faces -> Instance on Points with a primitive as instance.",
        "parameters": {
            "density": "Point density (default 100)",
            "instance_type": "CONE, CUBE, SPHERE, CYLINDER, or ICO_SPHERE (default CONE)",
            "instance_scale": "[x, y, z] scale of each instance (default [0.01, 0.01, 0.15])",
            "align_to_normal": "Align instances to surface normal (default true)",
            "random_rotation": "Add random Z rotation (default true)",
            "random_scale_range": "[min, max] random scale variation (default [0.7, 1.3])",
        },
    },
    "deform_with_noise": {
        "description": "Deform a mesh with noise texture on Set Position. "
        "Creates: Noise Texture -> Vector Math (Scale) -> Set Position.",
        "parameters": {
            "noise_scale": "Noise texture scale (default 5.0)",
            "strength": "Deformation strength (default 0.5)",
        },
    },
    "subdivide_and_displace": {
        "description": "Subdivide mesh then displace with noise. "
        "Creates: Subdivide Mesh -> Set Position with noise displacement.",
        "parameters": {
            "subdivisions": "Number of subdivisions (default 3)",
            "noise_scale": "Noise scale (default 4.0)",
            "strength": "Displacement strength (default 0.3)",
        },
    },
}


def list_gn_templates():
    """List available Geometry Nodes templates with descriptions and parameters."""
    result = {}
    for name, info in _GN_TEMPLATES.items():
        result[name] = {
            "description": info["description"],
            "parameters": info["parameters"],
        }
    return {"templates": result, "count": len(result)}


def create_gn_from_template(template_name, tree_name, object_name=None, **kwargs):
    """Create a complete Geometry Nodes setup from a template."""
    if template_name not in _GN_TEMPLATES:
        available = ", ".join(_GN_TEMPLATES.keys())
        raise ValueError(f"Unknown template: {template_name}. Available: {available}")

    if template_name == "scatter_on_surface":
        return _template_scatter_on_surface(tree_name, object_name, **kwargs)
    if template_name == "deform_with_noise":
        return _template_deform_with_noise(tree_name, object_name, **kwargs)
    if template_name == "subdivide_and_displace":
        return _template_subdivide_and_displace(tree_name, object_name, **kwargs)
    raise ValueError(f"Unhandled template: {template_name}")


def _template_scatter_on_surface(
    tree_name,
    object_name=None,
    density=100,
    instance_type="CONE",
    instance_object_name=None,
    instance_collection_name=None,
    instance_scale=None,
    align_to_normal=True,
    random_rotation=True,
    random_scale_range=None,
    use_realize_instances=False,
    keep_input_geometry=False,
):
    """Scatter instances on a mesh surface."""
    if instance_scale is None:
        instance_scale = [0.01, 0.01, 0.15]
    if random_scale_range is None:
        random_scale_range = [0.7, 1.3]

    if instance_object_name and instance_collection_name:
        raise ValueError("Provide either instance_object_name or instance_collection_name, not both")

    tree = bpy.data.node_groups.new(tree_name, "GeometryNodeTree")

    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Density", in_out="INPUT", socket_type="NodeSocketFloat")

    input_node = tree.nodes.new("NodeGroupInput")
    input_node.location = (-600, 0)
    output_node = tree.nodes.new("NodeGroupOutput")
    output_node.location = (600, 0)

    distribute = tree.nodes.new("GeometryNodeDistributePointsOnFaces")
    distribute.name = "Distribute Points"
    distribute.location = (-200, 0)

    instance_on = tree.nodes.new("GeometryNodeInstanceOnPoints")
    instance_on.name = "Instance on Points"
    instance_on.location = (200, 0)

    if instance_object_name:
        instance_source = tree.nodes.new("GeometryNodeObjectInfo")
        instance_source.name = "Instance Object"
        instance_source.location = (0, -320)
        source_object = _get_object(instance_object_name)
        instance_source.inputs["Object"].default_value = source_object
        instance_source.inputs["As Instance"].default_value = True
        instance_output_socket = instance_source.outputs["Geometry"]
    elif instance_collection_name:
        collection = bpy.data.collections.get(instance_collection_name)
        if not collection:
            raise ValueError(f"Collection not found: {instance_collection_name}")
        instance_source = tree.nodes.new("GeometryNodeCollectionInfo")
        instance_source.name = "Instance Collection"
        instance_source.location = (0, -320)
        instance_source.inputs["Collection"].default_value = collection
        instance_source.inputs["Separate Children"].default_value = True
        instance_source.inputs["Reset Children"].default_value = False
        instance_output_socket = instance_source.outputs["Instances"]
    else:
        primitive_types = {
            "CONE": "GeometryNodeMeshCone",
            "CUBE": "GeometryNodeMeshCube",
            "SPHERE": "GeometryNodeMeshUVSphere",
            "CYLINDER": "GeometryNodeMeshCylinder",
            "ICO_SPHERE": "GeometryNodeMeshIcoSphere",
        }
        instance_source = tree.nodes.new(primitive_types.get(instance_type.upper(), "GeometryNodeMeshCone"))
        instance_source.name = "Instance Shape"
        instance_source.location = (0, -320)
        instance_output_socket = instance_source.outputs[0]

    scale_node = tree.nodes.new("GeometryNodeScaleInstances")
    scale_node.name = "Scale Instances"
    scale_node.location = (400, 0)

    current_output_socket = scale_node.outputs["Instances"]

    tree.links.new(input_node.outputs[0], distribute.inputs["Mesh"])
    tree.links.new(input_node.outputs[1], distribute.inputs["Density"])
    tree.links.new(distribute.outputs["Points"], instance_on.inputs["Points"])
    tree.links.new(instance_output_socket, instance_on.inputs["Instance"])
    tree.links.new(instance_on.outputs["Instances"], scale_node.inputs["Instances"])

    if align_to_normal:
        tree.links.new(distribute.outputs["Rotation"], instance_on.inputs["Rotation"])

    distribute.inputs["Density"].default_value = float(density)

    if random_scale_range and len(random_scale_range) == 2:
        random_scale = tree.nodes.new("FunctionNodeRandomValue")
        random_scale.name = "Random Scale"
        random_scale.location = (200, -300)
        random_scale.data_type = "FLOAT_VECTOR"
        min_scale, max_scale = random_scale_range
        scale_x, scale_y, scale_z = instance_scale
        random_scale.inputs[0].default_value = [scale_x * min_scale, scale_y * min_scale, scale_z * min_scale]
        random_scale.inputs[1].default_value = [scale_x * max_scale, scale_y * max_scale, scale_z * max_scale]
        tree.links.new(random_scale.outputs[0], scale_node.inputs["Scale"])
    else:
        scale_node.inputs["Scale"].default_value = instance_scale

    if random_rotation:
        random_rotation_node = tree.nodes.new("FunctionNodeRandomValue")
        random_rotation_node.name = "Random Rotation"
        random_rotation_node.location = (520, -280)
        random_rotation_node.data_type = "FLOAT_VECTOR"
        random_rotation_node.inputs[0].default_value = [0.0, 0.0, 0.0]
        random_rotation_node.inputs[1].default_value = [0.0, 0.0, 6.283185307179586]

        rotate_instances = tree.nodes.new("GeometryNodeRotateInstances")
        rotate_instances.name = "Rotate Instances"
        rotate_instances.location = (620, 0)
        tree.links.new(current_output_socket, rotate_instances.inputs["Instances"])
        tree.links.new(random_rotation_node.outputs[0], rotate_instances.inputs["Rotation"])
        current_output_socket = rotate_instances.outputs["Instances"]

    if use_realize_instances:
        realize_instances = tree.nodes.new("GeometryNodeRealizeInstances")
        realize_instances.name = "Realize Instances"
        realize_instances.location = (820, 0)
        tree.links.new(current_output_socket, realize_instances.inputs["Geometry"])
        current_output_socket = realize_instances.outputs["Geometry"]

    if keep_input_geometry:
        join_geometry = tree.nodes.new("GeometryNodeJoinGeometry")
        join_geometry.name = "Join Geometry"
        join_geometry.location = (1000, 0)
        tree.links.new(input_node.outputs["Geometry"], join_geometry.inputs["Geometry"])
        tree.links.new(current_output_socket, join_geometry.inputs["Geometry"])
        current_output_socket = join_geometry.outputs["Geometry"]

    tree.links.new(current_output_socket, output_node.inputs[0])

    result = {"tree_name": tree.name, "nodes": len(tree.nodes), "links": len(tree.links)}
    if object_name:
        attach_result = _attach_tree_to_object(tree, object_name, modifier_name=tree_name)
        modifier = _get_object(object_name).modifiers[attach_result["modifier_name"]]
        for item in tree.interface.items_tree:
            if item.item_type == "SOCKET" and item.name == "Density":
                modifier_input_set(modifier, item.identifier, float(density))
        result.update(attach_result)

    return result


def _template_deform_with_noise(tree_name, object_name=None, noise_scale=5.0, strength=0.5):
    """Deform mesh with noise texture."""
    tree = bpy.data.node_groups.new(tree_name, "GeometryNodeTree")

    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    input_node = tree.nodes.new("NodeGroupInput")
    input_node.location = (-400, 0)
    output_node = tree.nodes.new("NodeGroupOutput")
    output_node.location = (400, 0)

    set_position = tree.nodes.new("GeometryNodeSetPosition")
    set_position.location = (200, 0)

    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.location = (-100, -150)
    noise.inputs["Scale"].default_value = noise_scale

    vector_math = tree.nodes.new("ShaderNodeVectorMath")
    vector_math.operation = "SCALE"
    vector_math.location = (50, -150)
    vector_math.inputs["Scale"].default_value = strength

    position = tree.nodes.new("GeometryNodeInputPosition")
    position.location = (-300, -150)

    tree.links.new(input_node.outputs[0], set_position.inputs["Geometry"])
    tree.links.new(position.outputs["Position"], noise.inputs["Vector"])
    tree.links.new(noise.outputs["Color"], vector_math.inputs[0])
    tree.links.new(vector_math.outputs["Vector"], set_position.inputs["Offset"])
    tree.links.new(set_position.outputs["Geometry"], output_node.inputs[0])

    result = {"tree_name": tree.name, "nodes": len(tree.nodes), "links": len(tree.links)}
    if object_name:
        result.update(_attach_tree_to_object(tree, object_name, modifier_name=tree_name))
    return result


def _template_subdivide_and_displace(
    tree_name,
    object_name=None,
    subdivisions=3,
    noise_scale=4.0,
    strength=0.3,
):
    """Subdivide then displace with noise."""
    tree = bpy.data.node_groups.new(tree_name, "GeometryNodeTree")

    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    input_node = tree.nodes.new("NodeGroupInput")
    input_node.location = (-500, 0)
    output_node = tree.nodes.new("NodeGroupOutput")
    output_node.location = (500, 0)

    subdivide = tree.nodes.new("GeometryNodeSubdivideMesh")
    subdivide.location = (-200, 0)
    subdivide.inputs["Level"].default_value = subdivisions

    set_position = tree.nodes.new("GeometryNodeSetPosition")
    set_position.location = (200, 0)

    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.location = (-100, -200)
    noise.inputs["Scale"].default_value = noise_scale

    vector_math = tree.nodes.new("ShaderNodeVectorMath")
    vector_math.operation = "SCALE"
    vector_math.location = (50, -200)
    vector_math.inputs["Scale"].default_value = strength

    position = tree.nodes.new("GeometryNodeInputPosition")
    position.location = (-300, -200)

    tree.links.new(input_node.outputs[0], subdivide.inputs["Mesh"])
    tree.links.new(subdivide.outputs["Mesh"], set_position.inputs["Geometry"])
    tree.links.new(position.outputs["Position"], noise.inputs["Vector"])
    tree.links.new(noise.outputs["Color"], vector_math.inputs[0])
    tree.links.new(vector_math.outputs["Vector"], set_position.inputs["Offset"])
    tree.links.new(set_position.outputs["Geometry"], output_node.inputs[0])

    result = {"tree_name": tree.name, "nodes": len(tree.nodes), "links": len(tree.links)}
    if object_name:
        result.update(_attach_tree_to_object(tree, object_name, modifier_name=tree_name))
    return result
