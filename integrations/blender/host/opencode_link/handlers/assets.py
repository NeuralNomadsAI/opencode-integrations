# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Asset browser and library handlers for the OpenCode Blender bundle runtime."""

import os

import bpy


_ASSET_COLLECTIONS = {
    "OBJECT": "objects",
    "MATERIAL": "materials",
    "COLLECTION": "collections",
    "NODE_GROUP": "node_groups",
    "WORLD": "worlds",
}


def _normalize_asset_type(asset_type):
    key = asset_type.upper()
    if key not in _ASSET_COLLECTIONS:
        supported = ", ".join(sorted(_ASSET_COLLECTIONS))
        raise ValueError(f"Unsupported asset_type '{asset_type}'. Supported: {supported}")
    return key


def _get_asset_collection_name(asset_type):
    return _ASSET_COLLECTIONS[_normalize_asset_type(asset_type)]


def _get_asset_collection(asset_type):
    return getattr(bpy.data, _get_asset_collection_name(asset_type))


def _get_data_block(asset_type, name):
    collection = _get_asset_collection(asset_type)
    data_block = collection.get(name)
    if not data_block:
        raise ValueError(f"{asset_type.title()} not found: {name}")
    return data_block


def _get_asset_libraries_map():
    libraries = {}
    for library in bpy.context.preferences.filepaths.asset_libraries:
        libraries[library.name] = bpy.path.abspath(library.path)
    return libraries


def _resolve_library_path(filepath, library_name=None):
    if os.path.isabs(filepath):
        if not os.path.exists(filepath):
            raise ValueError(f"Blend file not found: {filepath}")
        return filepath

    if not library_name:
        raise ValueError("A relative filepath requires library_name")

    libraries = _get_asset_libraries_map()
    if library_name not in libraries:
        available = ", ".join(sorted(libraries))
        raise ValueError(f"Unknown asset library '{library_name}'. Available: {available}")

    resolved_path = os.path.join(libraries[library_name], filepath)
    if not os.path.exists(resolved_path):
        raise ValueError(f"Blend file not found: {resolved_path}")
    return resolved_path


def _serialize_asset_data(data_block, asset_type):
    asset_data = data_block.asset_data
    result = {
        "name": data_block.name,
        "asset_type": asset_type,
        "is_asset": bool(asset_data),
        "users": data_block.users,
        "library_filepath": data_block.library.filepath if getattr(data_block, "library", None) else None,
    }

    if asset_type == "NODE_GROUP":
        result["bl_idname"] = data_block.bl_idname

    if asset_data:
        result.update(
            {
                "description": asset_data.description,
                "author": asset_data.author,
                "catalog_id": str(asset_data.catalog_id),
                "tags": [tag.name for tag in asset_data.tags],
            }
        )

    return result


def list_asset_libraries():
    """List configured asset libraries from Blender preferences."""
    libraries = []
    for name, path in _get_asset_libraries_map().items():
        libraries.append({"name": name, "path": path, "exists": os.path.exists(path)})
    return {"libraries": libraries, "count": len(libraries)}


def list_current_file_assets(asset_types=None, include_unmarked=False):
    """List assets from the current .blend file."""
    selected_types = [_normalize_asset_type(asset_type) for asset_type in (asset_types or _ASSET_COLLECTIONS.keys())]
    assets = []

    for asset_type in selected_types:
        for data_block in _get_asset_collection(asset_type):
            if not include_unmarked and not data_block.asset_data:
                continue
            assets.append(_serialize_asset_data(data_block, asset_type))

    return {"assets": assets, "count": len(assets)}


def get_asset_info(asset_type, name):
    """Get detailed asset metadata for a local data-block."""
    asset_type = _normalize_asset_type(asset_type)
    data_block = _get_data_block(asset_type, name)
    return _serialize_asset_data(data_block, asset_type)


def mark_asset(asset_type, name, description=None, author=None, tags=None, catalog_id=None, generate_preview=True):
    """Mark a local data-block as an asset and update its metadata."""
    asset_type = _normalize_asset_type(asset_type)
    data_block = _get_data_block(asset_type, name)

    if not data_block.asset_data:
        data_block.asset_mark()

    asset_data = data_block.asset_data
    if description is not None:
        asset_data.description = description
    if author is not None:
        asset_data.author = author
    if catalog_id is not None:
        asset_data.catalog_id = catalog_id

    if tags is not None:
        existing_tags = {tag.name: tag for tag in asset_data.tags}
        for tag in list(asset_data.tags):
            asset_data.tags.remove(tag)
        for tag_name in tags:
            if tag_name not in existing_tags:
                asset_data.tags.new(tag_name)
            else:
                asset_data.tags.new(tag_name)

    if generate_preview:
        try:
            data_block.asset_generate_preview()
        except Exception:
            pass

    return _serialize_asset_data(data_block, asset_type)


def clear_asset(asset_type, name):
    """Clear asset metadata from a local data-block."""
    asset_type = _normalize_asset_type(asset_type)
    data_block = _get_data_block(asset_type, name)
    if not data_block.asset_data:
        return {"name": data_block.name, "asset_type": asset_type, "cleared": False}

    data_block.asset_clear()
    return {"name": data_block.name, "asset_type": asset_type, "cleared": True}


def list_library_blend_files(library_name=None, recursive=True, limit=200):
    """List .blend files found inside configured asset libraries."""
    libraries = _get_asset_libraries_map()
    targets = [(library_name, libraries[library_name])] if library_name else list(libraries.items())
    if library_name and library_name not in libraries:
        available = ", ".join(sorted(libraries))
        raise ValueError(f"Unknown asset library '{library_name}'. Available: {available}")

    results = []
    for current_library_name, root_path in targets:
        if not os.path.exists(root_path):
            continue

        if recursive:
            for current_root, _, filenames in os.walk(root_path):
                for filename in filenames:
                    if not filename.lower().endswith(".blend"):
                        continue
                    absolute_path = os.path.join(current_root, filename)
                    results.append(
                        {
                            "library_name": current_library_name,
                            "filepath": absolute_path,
                            "relative_path": os.path.relpath(absolute_path, root_path),
                        }
                    )
                    if len(results) >= limit:
                        return {"blend_files": results, "count": len(results), "truncated": True}
        else:
            for filename in os.listdir(root_path):
                if not filename.lower().endswith(".blend"):
                    continue
                absolute_path = os.path.join(root_path, filename)
                results.append(
                    {
                        "library_name": current_library_name,
                        "filepath": absolute_path,
                        "relative_path": os.path.relpath(absolute_path, root_path),
                    }
                )
                if len(results) >= limit:
                    return {"blend_files": results, "count": len(results), "truncated": True}

    return {"blend_files": results, "count": len(results), "truncated": False}


def list_blend_file_assets(filepath, library_name=None, asset_types=None):
    """Inspect a .blend library file and list its importable datablocks."""
    resolved_path = _resolve_library_path(filepath, library_name=library_name)
    selected_types = [_normalize_asset_type(asset_type) for asset_type in (asset_types or _ASSET_COLLECTIONS.keys())]

    with bpy.data.libraries.load(resolved_path, link=False) as (data_from, _):
        assets = {}
        for asset_type in selected_types:
            collection_name = _get_asset_collection_name(asset_type)
            assets[collection_name] = list(getattr(data_from, collection_name))

    return {"filepath": resolved_path, "assets": assets}


def import_blend_asset(filepath, asset_type, name, library_name=None, link=False, link_to_scene=True):
    """Import one datablock from a .blend library file into the current session."""
    asset_type = _normalize_asset_type(asset_type)
    resolved_path = _resolve_library_path(filepath, library_name=library_name)
    collection_name = _get_asset_collection_name(asset_type)

    with bpy.data.libraries.load(resolved_path, link=link) as (data_from, data_to):
        available_names = list(getattr(data_from, collection_name))
        if name not in available_names:
            raise ValueError(f"{asset_type.title()} '{name}' not found in {resolved_path}")
        setattr(data_to, collection_name, [name])

    imported = _get_data_block(asset_type, name)

    if link_to_scene and asset_type == "OBJECT" and imported.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(imported)
    if link_to_scene and asset_type == "COLLECTION" and imported.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(imported)

    result = _serialize_asset_data(imported, asset_type)
    result.update({"filepath": resolved_path, "linked": link, "linked_to_scene": bool(link_to_scene)})
    return result
