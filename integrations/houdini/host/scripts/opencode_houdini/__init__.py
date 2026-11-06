from __future__ import annotations

import atexit

from .host import Host, HostError, HostServer, ScriptState


_host: Host | None = None
_server: HostServer | None = None
_panel_owners: set[str] = set()


def initialise(application=None) -> None:
    global _host, _server
    if _host is not None:
        return
    host = Host()
    server = HostServer(host)
    _host = host
    _server = server


def shutdown() -> None:
    global _host, _server
    if _server is not None:
        _server.close()
    if _host is not None:
        _host.close()
    _server = None
    _host = None
    _panel_owners.clear()


def acquirePanel(channel: str) -> None:
    initialise()
    _panel_owners.add(channel)


def releasePanel(channel: str) -> None:
    if channel not in _panel_owners:
        return
    _panel_owners.remove(channel)
    if not _panel_owners:
        shutdown()


# Process exit also cleans up any remaining consenting panels.
atexit.register(shutdown)


def reset() -> None:
    initialise()
    _host.reset()


def hostInfo() -> dict:
    if _server is None:
        raise RuntimeError("OpenCode Houdini host has not been initialised")
    return _server.info()


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
    "reset",
    "releasePanel",
    "scriptHandle",
    "shutdown",
]
