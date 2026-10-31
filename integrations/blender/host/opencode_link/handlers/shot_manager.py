# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Shot Manager handlers for the OpenCode Blender bundle runtime."""

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false

import bpy  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_sm_props():
    """Get Shot Manager general properties. Raises if SM not active."""
    if not hasattr(bpy.context.scene, "sm_general_props"):
        raise RuntimeError("Shot Manager is not active in this scene")
    return bpy.context.scene.sm_general_props


def _get_shots():
    """Get the shot list from Shot Manager."""
    props = _get_sm_props()
    if not props.node_tree:
        return []
    return list(props.node_tree.shots)


def _normalize_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bpy.types.ID):
        return value.name

    if hasattr(value, "to_list"):
        try:
            return list(value.to_list())
        except Exception:
            pass

    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}

    try:
        return [_normalize_value(item) for item in list(value)]
    except Exception:
        return str(value)


def _lookup_prop_safe(shot, prop_id, full=False):
    if not hasattr(shot, "lookup_prop"):
        return None

    try:
        return shot.lookup_prop(prop_id, full=full)
    except Exception:
        return None


def _find_shot(shot_name):
    for shot in _get_shots():
        label = _lookup_prop_safe(shot, "label")
        if shot.name == shot_name or label == shot_name:
            return shot
    return None


def _describe_shot_property(shot, prop):
    icon = "ERROR"
    source = None
    value = prop.error_message if hasattr(prop, "error_message") else None

    resolved = _lookup_prop_safe(shot, prop.name, full=True)
    if resolved is not None:
        icon, source, _prop_type, value = resolved

    return {
        "override": bool(getattr(prop, "override", False)),
        "rna_path": getattr(prop, "rna_path", ""),
        "rna_type": getattr(prop, "rna_type", ""),
        "source_icon": icon,
        "source_name": getattr(source, "parent_id", None) or getattr(source, "name", None),
        "value": _normalize_value(value),
        "error": getattr(prop, "error_message", "") or None,
    }


def _get_window_override_context():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return None

    preferred_areas = ("PROPERTIES", "VIEW_3D", "DOPESHEET_EDITOR", "OUTLINER")

    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue

        area = None
        for area_type in preferred_areas:
            area = next((candidate for candidate in screen.areas if candidate.type == area_type), None)
            if area is not None:
                break

        if area is None and screen.areas:
            area = screen.areas[0]

        if area is None:
            continue

        override = {
            "window": window,
            "screen": screen,
            "area": area,
            "scene": bpy.context.scene,
        }

        region = next((candidate for candidate in area.regions if candidate.type == "WINDOW"), None)
        if region is not None:
            override["region"] = region

        return override

    return None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def get_shot_manager_status():
    """Check if Shot Manager Pro is active and return basic info."""
    active = hasattr(bpy.context.scene, "sm_general_props")
    if not active:
        return {"active": False, "message": "Shot Manager is not installed or not active"}

    props = bpy.context.scene.sm_general_props
    shot_count = len(_get_shots()) if props.node_tree else 0

    return {
        "active": True,
        "shot_count": shot_count,
        "redirect_output": props.redirect_output,
        "alternative_root": props.alternative_root,
    }


# ---------------------------------------------------------------------------
# Shot listing
# ---------------------------------------------------------------------------
def get_shot_list():
    """Get all shots with basic info."""
    shots = _get_shots()
    result = []
    for shot in shots:
        result.append({
            "name": shot.name,
            "enabled": shot.enable,
            "start_frame": shot.start_frame_render,
            "end_frame": shot.end_frame_render,
            "still_mode": shot.still_mode,
        })
    return {"shots": result, "count": len(result)}


def get_shot_details(shot_name):
    """Get detailed info about a specific shot."""
    shot = _find_shot(shot_name)
    if shot is None:
        raise ValueError(f"Shot not found: {shot_name}")

    details = {
        "name": shot.name,
        "label": _lookup_prop_safe(shot, "label") or getattr(shot, "label", ""),
        "suffix": _lookup_prop_safe(shot, "suffix") or getattr(shot, "suffix", ""),
        "enabled": bool(getattr(shot, "enable", True)),
        "start_frame": _normalize_value(_lookup_prop_safe(shot, "Start")),
        "end_frame": _normalize_value(_lookup_prop_safe(shot, "End")),
        "render_start_frame": getattr(shot, "start_frame_render", None),
        "render_end_frame": getattr(shot, "end_frame_render", None),
        "still_mode": _normalize_value(_lookup_prop_safe(shot, "still_mode")),
        "is_default": bool(getattr(shot, "is_default", False)),
        "source_id": getattr(shot, "source_id", None),
        "notes": getattr(shot, "notes", "") if hasattr(shot, "notes") else "",
        "notes_file": str(getattr(shot, "notes_file", "")) if hasattr(shot, "notes_file") else "",
        "rendered": bool(getattr(shot, "rendered", False)),
        "rendering": bool(getattr(shot, "rendering", False)),
    }

    camera = _lookup_prop_safe(shot, "Camera")
    if camera not in (None, "ERROR", "None"):
        details["camera"] = _normalize_value(camera)

    primary_layer = _lookup_prop_safe(shot, "primary")
    if primary_layer not in (None, "ERROR", "None"):
        details["primary_layer"] = _normalize_value(primary_layer)

    if hasattr(shot, "properties"):
        details["overrides"] = {
            prop.name: _describe_shot_property(shot, prop)
            for prop in shot.properties
        }

    return details


# ---------------------------------------------------------------------------
# Shot creation / modification
# ---------------------------------------------------------------------------
def create_shot(name=None, start_frame=None, end_frame=None, camera=None):
    """Create a new shot in Shot Manager.

    Bypasses bpy.ops.sm.add_shot to avoid poll() context failures when called
    from the bridge socket handler (the operator requires node_tree to exist
    and a full UI context).  Instead we manipulate SM's data structures
    directly and call its internal state-sync helpers.
    """
    _get_sm_props()
    scene = bpy.context.scene
    props = scene.sm_general_props

    # Import SM internals for proper state management
    module = next(
        (name for name in bpy.context.preferences.addons.keys() if name == "shot_manager" or name.endswith(".shot_manager")),
        None,
    )
    try:
        sm_globals = __import__(module + ".globals", fromlist=["skip_update"]) if module else None
        skip_update = sm_globals.skip_update
        get_all_shot_grps = sm_globals.get_all_shot_grps
        shot_change = sm_globals.shot_change
        update_visibility_set = sm_globals.update_visibility_set
    except (ImportError, AttributeError):
        raise RuntimeError("Cannot import Shot Manager internals. Ensure the Shot Manager extension is enabled.")

    # --- Ensure a ShotList node tree exists ---
    if not props.node_tree:
        props.node_tree = bpy.data.node_groups.new("Shot List", "ShotList")

    shot_grp = props.node_tree.shots

    # --- Helpers ---
    def _next_id():
        """Generate the next unused numeric string ID across all shot lists."""
        ids = set()
        for grp in get_all_shot_grps():
            for s in grp:
                try:
                    ids.add(int(s.name))
                except ValueError:
                    pass
        n = 0
        while n in ids:
            n += 1
        return str(n)

    def _unique_label(label):
        """Return *label* if unique, otherwise append .001 / .002 / etc."""
        all_labels = []
        for grp in get_all_shot_grps():
            all_labels.extend(s.label for s in grp)
        if all_labels.count(label) <= 1:
            return label
        i = 1
        while True:
            candidate = f"{label}.{str(i).zfill(3)}"
            if candidate not in all_labels:
                return candidate
            i += 1

    def _add_sub_prop(shot, rna_path, label, override=True):
        """Add a sub-property (SM_shot_custom_props) to *shot*."""
        sub = shot.properties.add()
        sub.name = label
        skip_update[0] = False
        sub.rna_path = rna_path
        skip_update[0] = True
        sub.override = override
        sub.parent_id = shot.name
        try:
            val = scene.path_resolve(rna_path)
            if val is not None:
                setattr(sub, sub.rna_type, val)
        except Exception:
            pass
        return sub

    was_skip = skip_update[0]
    skip_update[0] = True

    try:
        # --- Create the Default shot if the list is empty ---
        if len(shot_grp) == 0:
            default = shot_grp.add()
            default.is_default = True
            default.name = _next_id()
            default.source_id = default.name
            default.source_tree = props.node_tree
            default.label = "Default"
            default.icon = "HOME"
            for rna_path, label in (
                ("frame_start", "Start"),
                ("frame_end", "End"),
                ("camera", "Camera"),
            ):
                _add_sub_prop(default, rna_path, label, override=True)

        # --- Create the new regular shot ---
        default_shot = shot_grp[0]
        active_index = props.node_tree.list_index
        new_index = len(shot_grp)

        new = shot_grp.add()
        new.source_tree = props.node_tree
        new.source_id = default_shot.name
        new.still_mode = default_shot.still_mode
        new.name = _next_id()

        shot_label = name if name else "New_Shot"
        new.label = shot_label
        new.label = _unique_label(shot_label)

        # Start / End sub-properties (like make_default_props)
        _add_sub_prop(new, "frame_start", "Start")
        _add_sub_prop(new, "frame_end", "End")

        # Position after active shot
        shot_grp.move(new_index, active_index + 1)
        props.node_tree.list_index = active_index + 1

        # Camera sub-property (added during structure creation)
        if camera:
            cam_obj = bpy.data.objects.get(camera)
            if cam_obj and cam_obj.type == "CAMERA":
                sub = _add_sub_prop(new, "camera", "Camera")
                sub.rna_object = cam_obj

    finally:
        skip_update[0] = was_skip

    # Finalize SM internal state -- let shot_change sync everything first
    skip_update[0] = False
    try:
        shot_change(None, bpy.context)
        update_visibility_set(bpy.context)
    except Exception:
        pass

    # Apply caller-provided values AFTER shot_change so they are not
    # overwritten by SM's internal sync.  Setting sub-properties with
    # skip_update off lets SM's own update callbacks propagate the
    # values to start_frame_render / end_frame_render and the scene.
    #
    # IMPORTANT: End must be set BEFORE Start because Blender enforces
    # frame_start <= frame_end.  If the new start exceeds the current
    # scene.frame_end the value gets clamped.
    def _set_sub_prop_value(shot, prop_name, value):
        for p in shot.properties:
            if p.name == prop_name and p.rna_type:
                setattr(p, p.rna_type, value)
                return True
        return False

    if end_frame is not None:
        _set_sub_prop_value(new, "End", end_frame)
        new.end_frame_render = end_frame

    if start_frame is not None:
        _set_sub_prop_value(new, "Start", start_frame)
        new.start_frame_render = start_frame

    return {
        "name": new.label,
        "id": new.name,
        "start_frame": new.start_frame_render,
        "end_frame": new.end_frame_render,
    }


def modify_shot(shot_name, name=None, start_frame=None, end_frame=None, enabled=None, still_mode=None):
    """Modify an existing shot's properties."""
    shots = _get_shots()
    for shot in shots:
        if shot.name == shot_name:
            if name is not None:
                shot.name = name
            if start_frame is not None:
                shot.start_frame_render = start_frame
            if end_frame is not None:
                shot.end_frame_render = end_frame
            if enabled is not None:
                shot.enable = enabled
            if still_mode is not None:
                shot.still_mode = still_mode

            return {
                "name": shot.name,
                "start_frame": shot.start_frame_render,
                "end_frame": shot.end_frame_render,
                "enabled": shot.enable,
            }

    raise ValueError(f"Shot not found: {shot_name}")


def enable_disable_shots(shot_names, enabled):
    """Enable or disable multiple shots at once."""
    shots = _get_shots()
    modified = []
    for shot in shots:
        if shot.name in shot_names:
            shot.enable = enabled
            modified.append(shot.name)

    return {"modified": modified, "enabled": enabled}


# ---------------------------------------------------------------------------
# Render path
# ---------------------------------------------------------------------------
def set_shot_manager_render_path(path, redirect=True):
    """Set the Shot Manager alternative root output path."""
    props = _get_sm_props()
    props.redirect_output = redirect
    props.alternative_root = path
    return {
        "redirect_output": props.redirect_output,
        "alternative_root": props.alternative_root,
    }


# ---------------------------------------------------------------------------
# Batch render
# ---------------------------------------------------------------------------
def launch_batch_render():
    """Launch a batch render of all enabled shots."""
    props = _get_sm_props()

    if not bpy.data.is_saved:
        return {"status": "error", "message": "Project must be saved before batch render can start"}

    total_queue_count = int(getattr(props, "total_queue_count", 0) or 0)
    if total_queue_count <= 0:
        return {"status": "error", "message": "No shots have been queued for render"}

    override = _get_window_override_context()
    if override is None:
        return {
            "status": "error",
            "message": "No Blender window context available to start Shot Manager batch render",
        }

    try:
        with bpy.context.temp_override(**override):
            result = bpy.ops.wm.render_sm()

        result_flags = sorted(result) if isinstance(result, set) else [str(result)]
        if "RUNNING_MODAL" in result_flags:
            status = "started"
        elif "FINISHED" in result_flags:
            status = "finished"
        elif "CANCELLED" in result_flags:
            status = "cancelled"
        else:
            status = "unknown"

        return {
            "status": status,
            "message": "Batch render started" if status == "started" else "Shot Manager render operator returned",
            "result": result_flags,
            "queued_shots": total_queue_count,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
