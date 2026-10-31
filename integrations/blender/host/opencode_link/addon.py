# SPDX-License-Identifier: GPL-3.0-or-later

"""Registration, preferences and the consent switch of OpenCode Link."""

import threading
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty

from . import advertiser as advertiser_module
from .blender_app import BlenderApp
from .core import HostCore
from .wire import Listener

DRAIN_INTERVAL = 0.02


class _Session(object):
    """The live host: one adapter and core per Blender process, a listener and advertiser while consenting."""

    def __init__(self):
        self.app = BlenderApp(code_execution_allowed=lambda: bool(getattr(preferences(), "allow_code_execution", False)))
        self.core = HostCore(self.app)
        self.listener = None
        self.advertiser = None

    def snapshot(self):
        listener = self.listener
        if listener is None or not listener.running:
            return 0, None, self.app.script_handle(), ""
        return (
            listener.port,
            listener.token,
            self.app.script_handle(),
            advertiser_module.offer_label(self.app.document()),
        )

    def start(self, prefs):
        if self.listener is not None:
            return
        self.listener = Listener(self.core.handle)
        self.advertiser = advertiser_module.Advertiser(
            self.snapshot, service_name=prefs.service_name.strip(), config_dir=prefs.config_dir.strip()
        ).start()
        if not bpy.app.background and not bpy.app.timers.is_registered(_drain):
            bpy.app.timers.register(_drain, first_interval=DRAIN_INTERVAL, persistent=True)

    def stop(self, withdraw=True):
        listener, advertiser = self.listener, self.advertiser
        self.listener = self.advertiser = None
        token = listener.token if listener is not None else None
        if listener is not None:
            # Closing invalidates the token: the offer's hello fails from now on.
            listener.close()
        if advertiser is not None:
            # Withdrawal runs off the main thread; lease expiry covers a lost withdraw.
            threading.Thread(target=advertiser.stop, args=(token, withdraw), daemon=True).start()
        if bpy.app.timers.is_registered(_drain):
            bpy.app.timers.unregister(_drain)


_session = None


def session():
    return _session


def preferences():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _drain():
    if _session is not None:
        _session.app.drain()
    return DRAIN_INTERVAL


def pump(seconds, stop=None):
    """Background mode: serve queued requests on the main thread for `seconds` (no timers run there)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not (stop and stop()):
        if _session is not None:
            _session.app.drain()
        time.sleep(0.01)


def _offer_changed(prefs, _context):
    if _session is None:
        return
    if prefs.offer_session:
        _session.start(prefs)
    else:
        _session.stop()


def _service_changed(prefs, _context):
    # A new target takes effect on the next consent.
    if _session is not None and _session.listener is not None and prefs.offer_session:
        _session.stop()
        _session.start(prefs)


class OpenCodeLinkPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    offer_session: BoolProperty(
        name="Offer this session to OpenCode",
        description="Let OpenCode services on this computer attach this Blender session. Off at every start",
        default=False,
        update=_offer_changed,
    )
    service_name: StringProperty(
        name="OpenCode service",
        description="Name of one OpenCode service to offer the session to; empty offers it to every service found",
        default="",
        update=_service_changed,
    )
    config_dir: StringProperty(
        name="OpenCode configuration folder",
        description="Only for a service started with OPENCODE_CONFIG_DIR: its global configuration folder",
        default="",
        subtype="DIR_PATH",
        update=_service_changed,
    )
    allow_code_execution: BoolProperty(
        name="Allow code execution",
        description=(
            "Dangerous: lets OpenCode run Python code in this Blender when its plugin option allowCodeExecution "
            "is also on; every run still asks for permission"
        ),
        default=False,
    )

    def draw(self, context):
        del context
        layout = self.layout
        layout.prop(self, "offer_session")
        draw_status(layout)
        layout.prop(self, "service_name")
        layout.prop(self, "config_dir")
        box = layout.box()
        box.prop(self, "allow_code_execution")
        box.label(text="Code execution can change or delete anything Blender can reach.", icon="ERROR")
        if _session is not None and _session.core.quarantined:
            layout.operator(OPENCODE_OT_link_reset.bl_idname, icon="FILE_REFRESH")


def draw_status(layout):
    if _session is None or _session.listener is None:
        layout.label(text="Not offered to OpenCode.", icon="UNLINKED")
        return
    results = _session.advertiser.results if _session.advertiser else []
    reached = sum(1 for result in results if result["outcome"] in ("accepted", "renewed"))
    if not results:
        layout.label(text="Looking for OpenCode services…", icon="TIME")
    else:
        layout.label(text="Offered to %d of %d OpenCode service(s)." % (reached, len(results)), icon="LINKED")
    if _session.core.quarantined:
        layout.label(text="Quarantined: a failed change could not be undone.", icon="ERROR")


class OPENCODE_OT_link_reset(bpy.types.Operator):
    bl_idname = "opencode_link.reset"
    bl_label = "Reset OpenCode Link"
    bl_description = "Accept changes from OpenCode again after checking the scene"

    def execute(self, _context):
        if _session is not None:
            _session.core.quarantined = False
        return {"FINISHED"}


class VIEW3D_PT_opencode_link(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "OpenCode"
    bl_label = "OpenCode Link"

    def draw(self, _context):
        prefs = preferences()
        if prefs is None:
            return
        self.layout.prop(prefs, "offer_session")
        draw_status(self.layout)
        if _session is not None and _session.core.quarantined:
            self.layout.operator(OPENCODE_OT_link_reset.bl_idname, icon="FILE_REFRESH")


@persistent
def _on_change(*_args):
    if _session is not None:
        _session.app.changed()


@persistent
def _on_load(*_args):
    if _session is not None:
        _session.app.loaded()
        _session.core.quarantined = False


_HANDLERS = (
    ("depsgraph_update_post", _on_change),
    ("undo_post", _on_change),
    ("redo_post", _on_change),
    ("load_post", _on_load),
)
_CLASSES = (OpenCodeLinkPreferences, OPENCODE_OT_link_reset, VIEW3D_PT_opencode_link)


def register():
    global _session
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    prefs = preferences()
    if prefs is not None and prefs.offer_session:
        # Consent never survives a restart (saved preferences included).
        prefs["offer_session"] = False
    _session = _Session()
    for name, handler in _HANDLERS:
        getattr(bpy.app.handlers, name).append(handler)


def unregister():
    global _session
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    if _session is not None:
        _session.stop()
        _session = None
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
