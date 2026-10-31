# SPDX-License-Identifier: GPL-3.0-or-later

"""OpenCode Link: lets OpenCode act on this Blender session through an authenticated loopback host.

The Blender-specific modules load on registration only, so the protocol
modules (wire, core, advertiser, opencode_service) also import without bpy.
"""


def register():
    from . import addon

    addon.register()


def unregister():
    from . import addon

    addon.unregister()
