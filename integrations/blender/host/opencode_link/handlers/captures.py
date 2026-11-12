# SPDX-License-Identifier: GPL-3.0-or-later

"""Area captures. The image goes back to the plugin as data, which returns it as an attachment."""

import base64
import os
import shutil
import tempfile

import bpy
import imbuf


def refresh_display():
    """The interface draws after a change only at Blender's next turn: draw now, so a screenshot shows the
    current document (two requests served in one turn would otherwise capture the state before the first)."""
    if bpy.app.background:
        return False
    layer = bpy.context.view_layer
    if layer is not None:
        layer.update()
    windows = bpy.context.window_manager.windows
    if not windows:
        return False
    with bpy.context.temp_override(window=windows[0]):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    return True


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
    try:
        refresh_display()
        with bpy.context.temp_override(area=area):
            bpy.ops.screen.screenshot_area(filepath=path)
        return encode_png(path, max_size)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def encode_png(path, max_size):
    """A PNG file scaled to max_size pixels at most, as image data for an attachment.

    Scaled with imbuf, outside bpy.data: a temporary image datablock would change the document and
    advance its revision at Blender's next evaluation, after the read has returned."""
    image = imbuf.load(path)
    try:
        width, height = image.size
        limit = max(16, int(max_size))
        if max(width, height) > limit:
            scale = limit / max(width, height)
            width, height = max(1, int(width * scale)), max(1, int(height * scale))
            image.resize((width, height), method="BILINEAR")
            imbuf.write(image, filepath=path)
    finally:
        image.free()
    with open(path, "rb") as handle:
        data = base64.b64encode(handle.read()).decode("ascii")
    from .. import resources

    return {"width": width, "height": height, "resources": resources.snapshot(gpu=False),
            "image": {"mime": "image/png", "data": data}}
