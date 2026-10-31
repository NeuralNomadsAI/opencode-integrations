# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Addon-aware handlers for BlenderKit, Sketchfab, and Bradley Presets."""

from __future__ import annotations

import addon_utils
import json
from pathlib import Path
import urllib.parse
import urllib.request

import bpy

from .shader import assign_material, get_material_info
from .node_utils import serialize_node_tree
from .scene import new_primitive_object


def _http_get_json(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _addon_module(name):
    """Enabled module of an add-on or extension, from any extension repository."""
    for module in bpy.context.preferences.addons.keys():
        if module == name or module.endswith("." + name):
            return module
    return None


def _is_addon_enabled(name):
    return _addon_module(name) is not None


def _find_addon_root(module_name, addon_display_name=None):
    for module in addon_utils.modules():
        if module.__name__ == module_name:
            return Path(module.__file__).resolve().parent
        if addon_display_name and getattr(module, "bl_info", {}).get("name") == addon_display_name:
            return Path(module.__file__).resolve().parent
    return None


def _get_blenderkit_preferences():
    module = _addon_module("blenderkit")
    addon = bpy.context.preferences.addons.get(module) if module else None
    if not addon:
        raise ValueError("BlenderKit addon is not enabled")
    return addon.preferences


def _get_blenderkit_api_headers():
    preferences = _get_blenderkit_preferences()
    api_key = getattr(preferences, "api_key", "")
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _build_blenderkit_query(
    query=None,
    asset_type=None,
    asset_base_id=None,
    asset_id=None,
    free_only=False,
    geometry_nodes_only=False,
    animated=False,
):
    tokens = []
    if query:
        tokens.append(query)
    if asset_base_id:
        tokens.append(f"asset_base_id:{asset_base_id}")
    if asset_id:
        tokens.append(f"id:{asset_id}")
    if asset_type:
        tokens.append(f"asset_type:{asset_type.lower()}")
    if free_only:
        tokens.append("is_free:true")
    if geometry_nodes_only:
        tokens.append("modifiers:nodes")
    if animated:
        tokens.append("animated:true")
    return " ".join(tokens).strip()


def _simplify_blenderkit_result(item):
    author = item.get("author") or {}
    return {
        "name": item.get("name"),
        "asset_type": item.get("assetType"),
        "asset_base_id": item.get("assetBaseId"),
        "asset_id": item.get("id"),
        "display_name": item.get("displayName") or item.get("name"),
        "author": author.get("fullName") or author.get("firstName") or author.get("id"),
        "is_free": item.get("isFree"),
        "verification_status": item.get("verificationStatus"),
        "web_url": item.get("webUrl") or item.get("url"),
        "score": item.get("score"),
        "tags": item.get("tags", [])[:12],
    }


def _fetch_blenderkit_asset_data(
    query=None,
    asset_type=None,
    asset_base_id=None,
    asset_id=None,
    page_size=10,
    page=1,
    free_only=False,
    geometry_nodes_only=False,
    animated=False,
):
    search_query = _build_blenderkit_query(
        query=query,
        asset_type=asset_type,
        asset_base_id=asset_base_id,
        asset_id=asset_id,
        free_only=free_only,
        geometry_nodes_only=geometry_nodes_only,
        animated=animated,
    )
    url = (
        "https://www.blenderkit.com/api/v1/search/?query="
        + urllib.parse.quote_plus(search_query)
        + "&dict_parameters=1"
        + f"&page_size={int(page_size)}&page={int(page)}"
    )
    data = _http_get_json(url, headers=_get_blenderkit_api_headers())
    results = data.get("results", [])
    return data, results


def _iter_blenderkit_datablocks():
    collections = (
        ("OBJECT", bpy.data.objects),
        ("MATERIAL", bpy.data.materials),
        ("NODE_GROUP", bpy.data.node_groups),
        ("WORLD", bpy.data.worlds),
        ("SCENE", bpy.data.scenes),
        ("BRUSH", bpy.data.brushes),
    )
    for asset_type, collection in collections:
        for datablock in collection:
            props = getattr(datablock, "blenderkit", None)
            if props is None:
                continue
            yield asset_type, datablock, props


def _find_blenderkit_matches(asset_base_id=None, asset_id=None):
    matches = []
    for asset_type, datablock, props in _iter_blenderkit_datablocks():
        if asset_base_id and getattr(props, "asset_base_id", "") != asset_base_id:
            continue
        if asset_id and getattr(props, "id", "") != asset_id:
            continue
        if not asset_base_id and not asset_id:
            continue
        matches.append(
            {
                "data_block_type": asset_type,
                "name": datablock.name,
                "asset_base_id": getattr(props, "asset_base_id", ""),
                "asset_id": getattr(props, "id", ""),
            }
        )
    return matches


def _default_transform(value, fallback):
    return tuple(value) if value else fallback


def get_addon_status(addon_name=None):
    """Get a summary of supported addon statuses."""
    supported = {
        "blenderkit": {
            "enabled": _is_addon_enabled("blenderkit"),
            "module_name": _addon_module("blenderkit"),
        },
        "sketchfab": {
            "enabled": _is_addon_enabled("sketchfab"),
            "module_name": "sketchfab",
        },
        "bradley_presets": {
            "enabled": _is_addon_enabled("Bradley-Presets"),
            "module_name": "Bradley-Presets",
        },
    }

    if supported["blenderkit"]["enabled"]:
        preferences = _get_blenderkit_preferences()
        supported["blenderkit"].update(
            {
                "api_key_configured": bool(getattr(preferences, "api_key", "")),
                "global_dir": getattr(preferences, "global_dir", ""),
            }
        )

    if supported["sketchfab"]["enabled"]:
        browser = bpy.context.window_manager.sketchfab_browser
        api = browser.skfb_api
        supported["sketchfab"].update(
            {
                "logged_in": api.is_user_logged(),
                "search_domain": browser.search_domain,
            }
        )

    if supported["bradley_presets"]["enabled"]:
        root = _find_addon_root("Bradley-Presets", addon_display_name="Bradley's Geo Node Presets")
        supported["bradley_presets"].update(
            {
                "addon_root": str(root) if root else None,
                "asset_libraries": [
                    {
                        "name": library.name,
                        "path": bpy.path.abspath(library.path),
                        "import_method": getattr(library, "import_method", None),
                    }
                    for library in bpy.context.preferences.filepaths.asset_libraries
                    if "BRD" in library.name or "Bradley" in library.name
                ],
            }
        )

    if addon_name:
        key = addon_name.lower()
        if key not in supported:
            available = ", ".join(sorted(supported))
            raise ValueError(f"Unsupported addon_name '{addon_name}'. Available: {available}")
        return supported[key]

    return supported


def search_blenderkit_assets(
    query=None,
    asset_type="model",
    page_size=10,
    page=1,
    free_only=False,
    geometry_nodes_only=False,
    animated=False,
):
    """Search BlenderKit assets using its public search API and local auth headers when available."""
    data, results = _fetch_blenderkit_asset_data(
        query=query,
        asset_type=asset_type,
        page_size=page_size,
        page=page,
        free_only=free_only,
        geometry_nodes_only=geometry_nodes_only,
        animated=animated,
    )
    return {
        "query": query,
        "asset_type": asset_type,
        "page": int(page),
        "page_size": int(page_size),
        "count": len(results),
        "results": [_simplify_blenderkit_result(item) for item in results],
        "next": data.get("next"),
        "previous": data.get("previous"),
    }


def import_blenderkit_asset(
    asset_base_id=None,
    asset_id=None,
    query=None,
    asset_type=None,
    target_object=None,
    material_target_slot=0,
    resolution="blend",
    model_location=None,
    model_rotation=None,
    target_collection="",
    nodegroup_mode="",
    node_x=0,
    node_y=0,
):
    """Start importing a BlenderKit asset through the installed addon."""
    if not _is_addon_enabled("blenderkit"):
        raise ValueError("BlenderKit addon is not enabled")

    _, results = _fetch_blenderkit_asset_data(
        query=query,
        asset_type=asset_type,
        asset_base_id=asset_base_id,
        asset_id=asset_id,
        page_size=1,
        page=1,
    )
    if not results:
        raise ValueError("No BlenderKit asset matched the provided search")

    asset_data = results[0]
    if asset_data.get("assetType") == "material" and not target_object:
        raise ValueError("target_object is required when importing a BlenderKit material")

    download_module = __import__(_addon_module("blenderkit") + ".download", fromlist=["start_download"])
    started = download_module.start_download(
        asset_data,
        target_object=target_object or "",
        material_target_slot=int(material_target_slot),
        model_location=_default_transform(model_location, (0, 0, 0)),
        model_rotation=_default_transform(model_rotation, (0, 0, 0)),
        resolution=resolution,
        parent=None,
        target_collection=target_collection or "",
        nodegroup_mode=nodegroup_mode or "",
        node_x=int(node_x),
        node_y=int(node_y),
    )

    matches = _find_blenderkit_matches(
        asset_base_id=asset_data.get("assetBaseId"),
        asset_id=asset_data.get("id"),
    )
    return {
        "started": bool(started),
        "asset": _simplify_blenderkit_result(asset_data),
        "matches": matches,
        "imported": bool(matches),
    }


def get_blenderkit_import_status(asset_base_id=None, asset_id=None):
    """Check whether a BlenderKit asset is already present in the current file."""
    if not asset_base_id and not asset_id:
        raise ValueError("Provide asset_base_id or asset_id")
    matches = _find_blenderkit_matches(asset_base_id=asset_base_id, asset_id=asset_id)
    return {
        "asset_base_id": asset_base_id,
        "asset_id": asset_id,
        "imported": bool(matches),
        "matches": matches,
        "count": len(matches),
    }


def search_sketchfab_models(query=None, page_size=12, page=1, downloadable=True, animated=None):
    """Search Sketchfab's public API for downloadable models."""
    params = {
        "type": "models",
        "q": query or "",
        "count": int(page_size),
        "downloadable": "true" if downloadable else "false",
        "page": int(page),
    }
    if animated is not None:
        params["animated"] = "true" if animated else "false"

    url = "https://api.sketchfab.com/v3/search?" + urllib.parse.urlencode(params)
    data = _http_get_json(url)
    results = []
    for item in data.get("results", []):
        license_info = item.get("license") or {}
        results.append(
            {
                "name": item.get("name"),
                "uid": item.get("uid"),
                "viewer_url": item.get("viewerUrl"),
                "embed_url": item.get("embedUrl"),
                "license": license_info.get("label"),
                "animation_count": item.get("animationCount"),
                "face_count": item.get("faceCount"),
                "is_downloadable": item.get("isDownloadable"),
                "user": (item.get("user") or {}).get("displayName"),
            }
        )

    return {
        "query": query,
        "page": int(page),
        "page_size": int(page_size),
        "count": len(results),
        "results": results,
        "next": data.get("next"),
        "previous": data.get("previous"),
    }


def import_sketchfab_model(uid, model_url=None):
    """Start importing a Sketchfab model through the installed addon."""
    if not _is_addon_enabled("sketchfab"):
        raise ValueError("Sketchfab addon is not enabled")

    browser = bpy.context.window_manager.sketchfab_browser
    api = browser.skfb_api
    if not api.is_user_logged():
        raise ValueError("Sketchfab import requires a logged-in addon session")

    browser.manualImportPath = model_url or f"https://sketchfab.com/3d-models/model-{uid}"
    api.download_model(uid)
    return {
        "started": True,
        "uid": uid,
        "model_url": browser.manualImportPath,
        "logged_in": True,
    }


def _get_bradley_root():
    root = _find_addon_root("Bradley-Presets", addon_display_name="Bradley's Geo Node Presets")
    if not root:
        raise ValueError("Bradley-Presets addon is not enabled")
    return root


def _get_bradley_preset_file():
    root = _get_bradley_root()
    current_version = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
    preferred = root / "Data" / current_version / "preset.blend"
    if preferred.exists():
        return preferred

    matches = sorted((root / "Data").glob("*/preset.blend"))
    if not matches:
        raise ValueError("Bradley preset.blend file was not found")
    return matches[-1]


def _get_bradley_catalog_file():
    root = _get_bradley_root()
    return root / "Data" / "blender_assets.cats.txt"


def _load_bradley_material(material_name):
    filepath = str(_get_bradley_preset_file())
    existing = bpy.data.materials.get(material_name)
    if existing:
        return existing
    with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
        if material_name not in data_from.materials:
            raise ValueError(f"Bradley material not found: {material_name}")
        data_to.materials = [material_name]
    material = bpy.data.materials.get(material_name)
    if not material:
        raise ValueError(f"Failed to import Bradley material: {material_name}")
    return material


def _insert_node_group_in_editor(nodegroup, node_x=0, node_y=0):
    node_type_map = {
        "GeometryNodeTree": "GeometryNodeGroup",
        "ShaderNodeTree": "ShaderNodeGroup",
        "CompositorNodeTree": "CompositorNodeGroup",
    }
    node_type = node_type_map.get(nodegroup.bl_idname)
    if not node_type:
        return False

    for area in bpy.context.screen.areas:
        if area.type != "NODE_EDITOR":
            continue
        space = area.spaces.active
        if space.tree_type != nodegroup.bl_idname:
            continue
        node_tree = space.edit_tree
        if not node_tree:
            continue
        node = node_tree.nodes.new(node_type)
        node.node_tree = nodegroup
        node.location = (node_x, node_y)
        node_tree.nodes.active = node
        return True
    return False


def _load_bradley_nodegroup(nodegroup_name):
    filepath = str(_get_bradley_preset_file())
    existing = bpy.data.node_groups.get(nodegroup_name)
    if existing:
        return existing
    with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
        if nodegroup_name not in data_from.node_groups:
            raise ValueError(f"Bradley node group not found: {nodegroup_name}")
        data_to.node_groups = [nodegroup_name]
    nodegroup = bpy.data.node_groups.get(nodegroup_name)
    if not nodegroup:
        raise ValueError(f"Failed to import Bradley node group: {nodegroup_name}")
    nodegroup.use_fake_user = True
    return nodegroup


def ensure_bradley_asset_library():
    """Ensure the Bradley asset library path is registered in Blender preferences."""
    if not _is_addon_enabled("Bradley-Presets"):
        raise ValueError("Bradley-Presets addon is not enabled")
    result = bpy.ops.bradley.add_asset()
    libraries = [
        {
            "name": library.name,
            "path": bpy.path.abspath(library.path),
            "import_method": getattr(library, "import_method", None),
        }
        for library in bpy.context.preferences.filepaths.asset_libraries
        if "BRD" in library.name or "Bradley" in library.name
    ]
    return {
        "operator_result": list(result),
        "libraries": libraries,
    }


def list_bradley_assets(filter_text=None, asset_kind="ALL", limit=100):
    """List Bradley preset assets available in the local preset library."""
    filepath = str(_get_bradley_preset_file())
    catalog_file = _get_bradley_catalog_file()
    with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
        del data_to
        node_groups = list(data_from.node_groups)
        materials = list(data_from.materials)

    if filter_text:
        needle = filter_text.lower()
        node_groups = [name for name in node_groups if needle in name.lower()]
        materials = [name for name in materials if needle in name.lower()]

    asset_kind = asset_kind.upper()
    if asset_kind not in {"ALL", "NODE_GROUP", "MATERIAL"}:
        raise ValueError("asset_kind must be ALL, NODE_GROUP, or MATERIAL")

    catalogs = []
    if catalog_file.exists():
        for line in catalog_file.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or line.startswith("VERSION"):
                continue
            _, path, label = line.split(":", 2)
            catalogs.append({"path": path, "label": label})

    result = {
        "preset_file": filepath,
        "catalogs": catalogs,
        "node_group_count": len(node_groups),
        "material_count": len(materials),
    }
    if asset_kind in {"ALL", "NODE_GROUP"}:
        result["node_groups"] = node_groups[: int(limit)]
    if asset_kind in {"ALL", "MATERIAL"}:
        result["materials"] = materials[: int(limit)]
    return result


def import_bradley_preset(
    name,
    asset_kind="NODE_GROUP",
    target_object=None,
    material_name=None,
    material_target_slot=0,
    nodegroup_mode="MODIFIER",
    node_x=0,
    node_y=0,
    model_location=None,
    model_rotation=None,
):
    """Import a Bradley node group or material and optionally apply it."""
    asset_kind = asset_kind.upper()
    if asset_kind == "MATERIAL":
        material = _load_bradley_material(name)
        result = {"material": get_material_info(material.name)}
        if target_object:
            result["assignment"] = assign_material(
                object_name=target_object,
                material_name=material.name,
                slot_index=int(material_target_slot),
            )
        return result

    if asset_kind != "NODE_GROUP":
        raise ValueError("asset_kind must be NODE_GROUP or MATERIAL")

    nodegroup = _load_bradley_nodegroup(name)
    result = {
        "name": nodegroup.name,
        "bl_idname": nodegroup.bl_idname,
        "node_tree": serialize_node_tree(nodegroup),
    }

    if nodegroup.bl_idname == "GeometryNodeTree" and target_object:
        obj = bpy.data.objects.get(target_object)
        if not obj:
            raise ValueError(f"Object not found: {target_object}")
        modifier = obj.modifiers.new(name=nodegroup.name, type="NODES")
        modifier.node_group = nodegroup
        result["modifier"] = {"object_name": obj.name, "modifier_name": modifier.name}
        return result

    if nodegroup.bl_idname == "ShaderNodeTree" and material_name:
        material = bpy.data.materials.get(material_name)
        if not material:
            raise ValueError(f"Material not found: {material_name}")
        if not material.use_nodes:
            material.use_nodes = True
        node = material.node_tree.nodes.new("ShaderNodeGroup")
        node.node_tree = nodegroup
        node.location = (node_x, node_y)
        result["material_node"] = {"material_name": material.name, "node_name": node.name}
        return result

    added_to_editor = _insert_node_group_in_editor(nodegroup, node_x=node_x, node_y=node_y)
    result["added_to_editor"] = added_to_editor

    if not added_to_editor and nodegroup.bl_idname == "GeometryNodeTree" and model_location is not None:
        target = new_primitive_object(
            "PLANE",
            name=f"{nodegroup.name}_Target",
            location=model_location,
            rotation=model_rotation,
        )
        modifier = target.modifiers.new(name=nodegroup.name, type="NODES")
        modifier.node_group = nodegroup
        result["modifier"] = {"object_name": target.name, "modifier_name": modifier.name}

    return result
