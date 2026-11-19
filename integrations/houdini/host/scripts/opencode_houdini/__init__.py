"""OpenCode Link for Houdini: the installed part.

Started with Houdini's interface by the package's pythonrc.py (start_with_houdini): it listens on 127.0.0.1 and
offers this Houdini to the OpenCode services every 5 seconds, so any conversation (CodeNomad, the terminal, the
OpenCode Link panel) can attach it. OPENCODE_HOUDINI_OFFER=0 in Houdini's environment keeps it from offering
itself. Background hython (verification, renders) never starts it by itself.
"""

from __future__ import annotations

import atexit
import os

from .host import Host, HostError, HostServer, ScriptState


_host: Host | None = None
_server: HostServer | None = None
_advertiser = None
_panel_owners: set[str] = set()


def _snapshot():
    host, server = _host, _server
    if host is None or server is None or not server.running:
        return None, None, None, None
    return server.port, server.token, host.state.handle, host.label


def initialise(application=None, advertise: bool | None = None) -> None:
    """Starts the host once per Houdini process; with advertise (by default unless OPENCODE_HOUDINI_OFFER=0 or a
    background role), offers it to OpenCode."""
    global _host, _server, _advertiser
    if _host is not None:
        return
    host = Host()
    server = HostServer(host)
    _host = host
    _server = server
    if advertise is None:
        advertise = os.environ.get("OPENCODE_HOUDINI_OFFER", "1") != "0" and not os.environ.get("OPENCODE_HOUDINI_ROLE")
    if advertise:
        from .advertiser import Advertiser  # noqa: PLC0415

        _advertiser = Advertiser(_snapshot).start()


def start_with_houdini() -> None:
    """pythonrc.py: in Houdini's interface, start once its event loop runs (never in hython)."""
    import hou  # noqa: PLC0415

    if not hou.isUIAvailable() or os.environ.get("OPENCODE_HOUDINI_ROLE"):
        return

    def once() -> None:
        try:
            hou.ui.removeEventLoopCallback(once)
        except Exception:  # noqa: BLE001
            pass
        initialise()

    hou.ui.addEventLoopCallback(once)


def pump(seconds: float = 0.05, stop=None) -> None:
    """hython: serve the queued requests on the main thread (Houdini's interface serves them by itself)."""
    if _host is not None:
        _host.ui.pump(seconds, stop)


def shutdown() -> None:
    global _host, _server, _advertiser
    advertiser, server = _advertiser, _server
    token = server.token if server is not None else None
    if advertiser is not None:
        advertiser.stop(token)
    if server is not None:
        server.close()
    if _host is not None:
        _host.close()
    _advertiser = None
    _server = None
    _host = None
    _panel_owners.clear()


def acquirePanel(channel: str) -> None:
    """A panel uses the host (started with Houdini; started here when Houdini was started without it)."""
    initialise()
    _panel_owners.add(channel)


def releasePanel(channel: str) -> None:
    """A panel closed: the host and its offer stay, for the other conversations attached to this Houdini."""
    _panel_owners.discard(channel)


atexit.register(shutdown)


def reset() -> None:
    initialise()
    _host.reset()


def hostInfo() -> dict:
    if _server is None:
        raise RuntimeError("OpenCode Houdini host has not been initialised")
    return _server.info()


def offerInfo() -> dict:
    """What the panel needs to find this Houdini's offer among the offered hosts."""
    from .advertiser import OFFER_KEY  # noqa: PLC0415

    if _host is None:
        raise RuntimeError("OpenCode Houdini host has not been initialised")
    return {"label": _host.label, "offer_key": OFFER_KEY, "advertising": _advertiser is not None}


def panelServices() -> list:
    return _advertiser.results if _advertiser is not None else []


def scriptHandle(script=None) -> str:
    if _host is None:
        raise RuntimeError("OpenCode Houdini host has not been initialised")
    return _host.script_handle(script)


def currentState() -> ScriptState:
    if _host is None:
        raise RuntimeError("OpenCode Houdini host has not been initialised")
    return _host.state


__all__ = [
    "Host",
    "HostError",
    "HostServer",
    "ScriptState",
    "acquirePanel",
    "currentState",
    "hostInfo",
    "initialise",
    "offerInfo",
    "pump",
    "reset",
    "releasePanel",
    "scriptHandle",
    "shutdown",
    "start_with_houdini",
]
