# SPDX-License-Identifier: GPL-3.0-or-later

"""Registration and preferences of OpenCode Link: enabled, the add-on offers this Blender session to OpenCode
(code execution included, each run asked in OpenCode); disabled, it offers nothing. Its state is shown in its
preferences; Blender's console output is captured as reports for OpenCode (console.py)."""

import os
import sys
import threading
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty

from . import advertiser as advertiser_module
from . import console
from .blender_app import BlenderApp
from .core import HostCore, require_app_version
from .process import state as process_state
from .wire import Listener
from . import limits

DRAIN_INTERVAL = limits.value("drain_interval_s")
# A reload or a relaunch's quit waits this long after its request, so the reply leaves before the listener closes.
RELOAD_DELAY = limits.value("reload_delay_s")
QUIT_DELAY = limits.value("quit_delay_s")


class _Session(object):
    """The live host: one adapter and core per Blender process, a listener and advertiser while enabled."""

    def __init__(self):
        self.app = BlenderApp(code_execution_allowed=lambda: True)
        self.app.reload_request = schedule_reload
        self.app.relaunch_request = schedule_quit
        self.core = HostCore(self.app)
        self.listener = None
        self.advertiser = None

    def snapshot(self):
        listener = self.listener
        # Nothing is offered before the open document is known: never an "Untitled" first offer.
        if listener is None or not listener.running or not self.app.document_known():
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
        # The update callback runs on the main thread: the first offer carries the open file's name.
        self.app.refresh_document()
        self.listener = Listener(self.core.handle)
        self.advertiser = advertiser_module.Advertiser(
            self.snapshot, service_name=getattr(prefs, "service_name", "").strip(),
            config_dir=getattr(prefs, "config_dir", "").strip()
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
    """Background mode: serve queued requests on the main thread for `seconds` (no timers run there).

    The live module is looked up on each turn: after a hot reload, the reloaded add-on serves."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not (stop and stop()):
        live = sys.modules.get(__name__)
        if getattr(live, "_quit_requested", False):
            return  # a relaunch quits this Blender: the script ends, and Blender with it
        current = getattr(live, "_session", None)
        if current is not None:
            current.app.drain()
        at = getattr(live, "_reload_at", None)
        if at is not None and time.monotonic() >= at:
            live.run_pending_reload()
        quit_at = getattr(live, "_quit_at", None)
        if quit_at is not None and time.monotonic() >= quit_at:
            live.run_pending_quit()
        time.sleep(0.01)


_reload_at = None
_quit_at = None
_quit_requested = False


def schedule_quit():
    """Main thread: quit Blender shortly after a relaunch's reply (its replacement is already starting)."""
    global _quit_at
    _quit_at = time.monotonic() + QUIT_DELAY
    if not bpy.app.background:
        bpy.app.timers.register(run_pending_quit, first_interval=QUIT_DELAY)


def run_pending_quit():
    """Quit without a prompt: the relaunch saved what it had to (a recovery copy at least)."""
    global _quit_at, _quit_requested
    if _quit_at is None:
        return None
    _quit_at = None
    _quit_requested = True
    if _session is not None:
        _session.stop()
    try:
        if _session is not None:
            _session.app.with_window(lambda: bpy.ops.wm.quit_blender())
        else:
            bpy.ops.wm.quit_blender()
    except RuntimeError:
        pass  # background mode: the pump returns and the script ends
    return None


def schedule_reload():
    """Main thread: reload OpenCode Link from disk shortly after this request's reply."""
    global _reload_at
    _reload_at = time.monotonic() + RELOAD_DELAY
    if not bpy.app.background:
        bpy.app.timers.register(run_pending_reload, first_interval=RELOAD_DELAY)


def run_pending_reload():
    """Disable the add-on, drop its modules and enable it again from disk, which offers the session again:
    a new listener, token and script handle (references are invalidated), the same offer key."""
    global _reload_at
    if _reload_at is None:
        return None
    _reload_at = None
    import addon_utils

    if _session is not None and _session.core.busy():
        # An apply is still running (a network operation): try again shortly.
        _reload_at = time.monotonic() + RELOAD_DELAY
        return RELOAD_DELAY
    if _session is not None:
        process_state().revision = _session.app.revision()
    addon_utils.disable(__package__, default_set=False, handle_error=None)
    for name in [name for name in sys.modules if name == __package__ or name.startswith(__package__ + ".")]:
        del sys.modules[name]
    addon_utils.enable(__package__, default_set=False, handle_error=None)
    return None  # a one-shot timer


def _service_changed(prefs, _context):
    # A new target needs a new handshake: offer the session afresh (new token); conversations attached
    # to it follow (same host and document).
    if _session is not None and _session.listener is not None:
        _session.stop()
        _session.start(prefs)


class OpenCodeLinkPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

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

    def draw(self, context):
        del context
        layout = self.layout
        draw_status(layout)
        layout.prop(self, "service_name")
        layout.prop(self, "config_dir")
        draw_code_note(layout)
        if _session is not None and _session.core.quarantined:
            layout.operator(OPENCODE_OT_link_reset.bl_idname, icon="FILE_REFRESH")


def draw_code_note(layout):
    layout.label(text="OpenCode can run Python here: it can change or delete anything Blender can reach.", icon="ERROR")


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
            _session.app.reset_native()
            _session.app.bump_revision()
        return {"FINISHED"}


@persistent
def _on_change(*_args):
    if _session is not None:
        _session.app.changed()


@persistent
def _on_save(*_args):
    if _session is not None:
        _session.app.saved()


@persistent
def _on_history(*_args):
    if _session is not None:
        _session.app.history_changed()


@persistent
def _on_load(*_args):
    if _session is not None:
        _session.app.loaded()
        _session.core.quarantined = False


_HANDLERS = (
    ("depsgraph_update_post", _on_change),
    ("undo_post", _on_history),
    ("redo_post", _on_history),
    ("load_post", _on_load),
    ("save_post", _on_save),
)
_CLASSES = (OpenCodeLinkPreferences, OPENCODE_OT_link_reset)


def _remove_handlers():
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)


def register():
    global _session
    require_app_version(bpy.app.version)
    # Process-wide and kept across hot reloads; a failure leaves Blender's streams as they were.
    console.capture().install()
    registered = []
    try:
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        session = _Session()
        for name, handler in _HANDLERS:
            getattr(bpy.app.handlers, name).append(handler)
        _session = session
    except BaseException:
        # Never leave the add-on half registered: undo what this call registered.
        console.capture().uninstall()
        _remove_handlers()
        _session = None
        for cls in reversed(registered):
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
        raise
    # Enabled means offered: once Blender has started (data access is restricted while add-ons
    # register), or at once in background mode (no timers run there).
    if bpy.app.background:
        _start_offer()
    else:
        bpy.app.timers.register(_start_offer, first_interval=0.1, persistent=True)


def _start_offer():
    # A Blender that OpenCode Link started to render (render.render_animation) is never offered.
    if os.environ.get("OPENCODE_LINK_ROLE") == "render":
        return None
    if _session is not None:
        _session.start(preferences())
    return None  # a one-shot timer


def unregister():
    global _session
    console.capture().uninstall()
    _remove_handlers()
    if _session is not None:
        _session.stop()
        _session = None
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
