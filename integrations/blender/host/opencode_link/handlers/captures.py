# SPDX-License-Identifier: GPL-3.0-or-later

"""Area captures. The image goes back to the plugin as data, which returns it as an attachment."""

import base64
import os
import shutil
import tempfile

import bpy


def find_area(area_type):
    screen = bpy.context.screen
    if bpy.app.background or screen is None:
        raise RuntimeError("Captures need the Blender interface; this Blender runs in the background.")
    for area in screen.areas:
        if area.type == area_type:
            return area
    labels = {"VIEW_3D": "3D Viewport", "NODE_EDITOR": "Node Editor"}
    raise RuntimeError(f"No {labels.get(area_type, area_type)} area is open in the current screen.")


def capture_area(area, max_size, fit_all=False):
    if fit_all:
        region = next((candidate for candidate in area.regions if candidate.type == "WINDOW"), None)
        if region is not None:
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.node.view_all()
    # A private folder: the capture never lands in the shared temporary folder.
    folder = tempfile.mkdtemp(prefix="opencode-link-")
    path = os.path.join(folder, "capture.png")
    image = None
    try:
        with bpy.context.temp_override(area=area):
            bpy.ops.screen.screenshot_area(filepath=path)
        image = bpy.data.images.load(path)
        width, height = image.size
        limit = max(16, int(max_size))
        if max(width, height) > limit:
            scale = limit / max(width, height)
            width, height = max(1, int(width * scale)), max(1, int(height * scale))
            image.scale(width, height)
            image.save()
        with open(path, "rb") as handle:
            data = base64.b64encode(handle.read()).decode("ascii")
    finally:
        if image is not None:
            bpy.data.images.remove(image)
        shutil.rmtree(folder, ignore_errors=True)
    return {"width": width, "height": height, "image": {"mime": "image/png", "data": data}}
