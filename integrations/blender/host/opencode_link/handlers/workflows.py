# SPDX-License-Identifier: GPL-3.0-or-later

"""Composed recipes: several operations as one OpenCode operation."""

import bpy

from .assets import import_blend_asset, mark_asset
from .geometry_nodes import create_gn_from_template
from .node_utils import serialize_node_tree, set_socket_default_value
from .shader import assign_material, create_shader_material_from_template

_MATERIAL_ARGS = ("name", "template_name", "object_name", "slot_index", "mark_as_asset", "description", "author", "tags")


def apply_library_material_to_object(object_name, filepath, material_name, library_name=None, slot_index=0, link=False):
    """Import a material from a .blend library and assign it to an object."""
    imported_asset = import_blend_asset(
        filepath=filepath,
        asset_type="MATERIAL",
        name=material_name,
        library_name=library_name,
        link=link,
        link_to_scene=False,
    )
    assignment = assign_material(object_name=object_name, material_name=material_name, slot_index=slot_index)
    return {"imported_asset": imported_asset, "assignment": assignment}


def create_material_and_assign(
    name,
    template_name="principled_pbr",
    object_name=None,
    slot_index=0,
    mark_as_asset=False,
    description=None,
    author=None,
    tags=None,
    **template_kwargs,
):
    """Create a templated material, optionally assign it and mark it as an asset."""
    material = create_shader_material_from_template(name=name, template_name=template_name, **template_kwargs)
    result = {"material": material}
    if object_name:
        result["assignment"] = assign_material(
            object_name=object_name, material_name=material["name"], slot_index=slot_index
        )
    if mark_as_asset:
        result["asset"] = mark_asset(
            asset_type="MATERIAL",
            name=material["name"],
            description=description,
            author=author,
            tags=tags,
            generate_preview=False,
        )
    return result


def create_string_to_curves_object(text, object_name=None, tree_name=None, size=1.0, location=None, font_name=None):
    """Create a mesh object whose Geometry Nodes modifier turns a string into filled curves."""
    resolved_object_name = object_name or "GN_Text"
    resolved_tree_name = tree_name or f"{resolved_object_name}_StringToCurves"
    font = None
    if font_name:
        font = bpy.data.fonts.get(font_name)
        if not font:
            raise ValueError(f"Font not found: {font_name}")

    mesh = bpy.data.meshes.new(f"{resolved_object_name}_Mesh")
    obj = bpy.data.objects.new(resolved_object_name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = tuple(location) if location else (0, 0, 0)

    tree = bpy.data.node_groups.new(resolved_tree_name, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    input_node = tree.nodes.new("NodeGroupInput")
    input_node.location = (-700, 0)
    output_node = tree.nodes.new("NodeGroupOutput")
    output_node.location = (500, 0)

    string_to_curves = tree.nodes.new("GeometryNodeStringToCurves")
    string_to_curves.name = "String to Curves"
    string_to_curves.location = (-420, 0)
    set_socket_default_value(string_to_curves.inputs["String"], text)
    set_socket_default_value(string_to_curves.inputs["Size"], float(size))
    if font is not None:
        set_socket_default_value(string_to_curves.inputs["Font"], font)

    realize_instances = tree.nodes.new("GeometryNodeRealizeInstances")
    realize_instances.name = "Realize Text"
    realize_instances.location = (-100, 0)
    fill_curve = tree.nodes.new("GeometryNodeFillCurve")
    fill_curve.name = "Fill Curve"
    fill_curve.location = (150, 0)

    tree.links.new(string_to_curves.outputs["Curve Instances"], realize_instances.inputs["Geometry"])
    tree.links.new(realize_instances.outputs["Geometry"], fill_curve.inputs["Curve"])
    tree.links.new(fill_curve.outputs["Mesh"], output_node.inputs[0])

    modifier = obj.modifiers.new(name=resolved_tree_name, type="NODES")
    modifier.node_group = tree
    return {
        "object_name": obj.name,
        "modifier_name": modifier.name,
        "tree_name": tree.name,
        "text": text,
        "node_tree": serialize_node_tree(tree),
    }


def scatter_library_asset_on_surface(
    surface_object_name,
    filepath,
    asset_name,
    asset_type="OBJECT",
    library_name=None,
    tree_name=None,
    density=100,
    instance_scale=None,
    align_to_normal=True,
    random_rotation=True,
    random_scale_range=None,
    use_realize_instances=False,
    keep_input_geometry=True,
    link=False,
):
    """Import an object or collection from a .blend library and scatter it on a surface."""
    asset_type = asset_type.upper()
    if asset_type not in {"OBJECT", "COLLECTION"}:
        raise ValueError("asset_type must be OBJECT or COLLECTION")
    imported_asset = import_blend_asset(
        filepath=filepath,
        asset_type=asset_type,
        name=asset_name,
        library_name=library_name,
        link=link,
        link_to_scene=False,
    )
    scatter_kwargs = {
        "template_name": "scatter_on_surface",
        "tree_name": tree_name or f"Scatter_{surface_object_name}_{asset_name}",
        "object_name": surface_object_name,
        "density": density,
        "instance_scale": instance_scale,
        "align_to_normal": align_to_normal,
        "random_rotation": random_rotation,
        "random_scale_range": random_scale_range,
        "use_realize_instances": use_realize_instances,
        "keep_input_geometry": keep_input_geometry,
    }
    if asset_type == "OBJECT":
        scatter_kwargs["instance_object_name"] = asset_name
    else:
        scatter_kwargs["instance_collection_name"] = asset_name
    return {"imported_asset": imported_asset, "scatter_setup": create_gn_from_template(**scatter_kwargs)}
