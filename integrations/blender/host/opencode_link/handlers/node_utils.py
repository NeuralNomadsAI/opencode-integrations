# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Helpers for serializing and editing Blender node trees."""

import bpy


_COMMON_NODE_PROPERTIES = (
    "operation",
    "data_type",
    "domain",
    "mode",
    "input_type",
    "blend_type",
    "distribution",
    "space",
    "transform_space",
    "interpolation_type",
    "mapping",
    "clamp",
    "use_clamp",
)


def serialize_value(value):
    """Convert Blender RNA values into JSON-safe values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, bpy.types.ID):
        return {"name": value.name, "id_type": value.__class__.__name__}

    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]

    try:
        return [serialize_value(item) for item in value]
    except TypeError:
        return value


def serialize_socket(socket):
    """Serialize a node socket to a JSON-safe dict."""
    info = {
        "name": socket.name,
        "type": socket.type,
        "bl_idname": socket.bl_idname,
        "identifier": getattr(socket, "identifier", socket.name),
        "is_linked": socket.is_linked,
        "enabled": getattr(socket, "enabled", True),
        "hide": getattr(socket, "hide", False),
        "hide_value": getattr(socket, "hide_value", False),
    }

    if hasattr(socket, "default_value"):
        try:
            info["default_value"] = serialize_value(socket.default_value)
        except Exception:
            info["default_value"] = None

    return info


def _serialize_node_properties(node):
    properties = {}
    for property_name in _COMMON_NODE_PROPERTIES:
        if not hasattr(node, property_name):
            continue
        try:
            properties[property_name] = serialize_value(getattr(node, property_name))
        except Exception:
            continue
    return properties


def serialize_node(node):
    """Serialize a node to a JSON-safe dict."""
    return {
        "name": node.name,
        "type": node.type,
        "bl_idname": node.bl_idname,
        "label": node.label,
        "location": serialize_value(node.location),
        "dimensions": serialize_value(node.dimensions),
        "width": node.width,
        "height": node.height,
        "parent": node.parent.name if node.parent else None,
        "mute": getattr(node, "mute", False),
        "hide": getattr(node, "hide", False),
        "select": getattr(node, "select", False),
        "properties": _serialize_node_properties(node),
        "inputs": [serialize_socket(socket) for socket in node.inputs],
        "outputs": [serialize_socket(socket) for socket in node.outputs],
    }


def serialize_links(tree):
    """Serialize all links in a node tree."""
    links = []
    for link in tree.links:
        links.append(
            {
                "from_node": link.from_node.name,
                "from_socket": link.from_socket.name,
                "to_node": link.to_node.name,
                "to_socket": link.to_socket.name,
            }
        )
    return links


def serialize_interface_items(tree):
    """Serialize the interface sockets and panels of a node group."""
    interface = []
    tree_interface = getattr(tree, "interface", None)
    if not tree_interface:
        return interface

    for item in tree_interface.items_tree:
        entry = {
            "name": item.name,
            "item_type": item.item_type,
            "description": getattr(item, "description", ""),
            "parent": item.parent.name if getattr(item, "parent", None) else None,
        }
        if item.item_type == "SOCKET":
            entry.update(
                {
                    "socket_type": item.socket_type,
                    "identifier": item.identifier,
                    "in_out": item.in_out,
                }
            )
        interface.append(entry)

    return interface


def serialize_node_tree(tree):
    """Serialize a whole node tree."""
    interface_items = serialize_interface_items(tree)
    return {
        "name": tree.name,
        "bl_idname": tree.bl_idname,
        "nodes": [serialize_node(node) for node in tree.nodes],
        "links": serialize_links(tree),
        "interface_items": interface_items,
        "interface_inputs": [item for item in interface_items if item.get("item_type") == "SOCKET" and item.get("in_out") == "INPUT"],
        "interface_outputs": [item for item in interface_items if item.get("item_type") == "SOCKET" and item.get("in_out") == "OUTPUT"],
        "node_count": len(tree.nodes),
        "link_count": len(tree.links),
        "is_asset": bool(getattr(tree, "asset_data", None)),
        "users": tree.users,
    }


def resolve_socket(sockets, query, socket_kind, node_name):
    """Resolve a socket from its index, display name, or identifier."""
    if isinstance(query, int):
        try:
            return sockets[query]
        except Exception as exc:
            raise ValueError(f"{socket_kind} socket index {query} not found on '{node_name}'") from exc

    if isinstance(query, str):
        socket = sockets.get(query)
        if socket:
            return socket

        for socket in sockets:
            identifier = getattr(socket, "identifier", None)
            if socket.name == query or identifier == query:
                return socket

    raise ValueError(f"{socket_kind} socket '{query}' not found on '{node_name}'")


def set_socket_default_value(socket, value):
    """Set a socket default value with light coercion for vectors/colors."""
    if not hasattr(socket, "default_value"):
        raise ValueError(f"Socket '{socket.name}' has no default_value")

    try:
        socket.default_value = value
    except Exception:
        if isinstance(value, (list, tuple)):
            socket.default_value = type(socket.default_value)(value)
        else:
            raise

    return serialize_value(socket.default_value)


def modifier_input_get(modifier, identifier):
    """A Geometry Nodes modifier input: properties.inputs.<identifier>.value since Blender 5.2."""
    properties = getattr(modifier, "properties", None)
    if properties is not None and hasattr(properties, "inputs"):
        return serialize_value(getattr(properties.inputs, identifier).value)
    return serialize_value(modifier[identifier])


def modifier_input_set(modifier, identifier, value):
    properties = getattr(modifier, "properties", None)
    if properties is not None and hasattr(properties, "inputs"):
        getattr(properties.inputs, identifier).value = value
    else:
        modifier[identifier] = value