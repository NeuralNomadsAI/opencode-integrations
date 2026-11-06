from __future__ import annotations

from .host import Host, HostError, HostServer, ScriptState


_host: Host | None = None
_server: HostServer | None = None


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
    "currentState",
    "hostInfo",
    "initialise",
    "reset",
    "scriptHandle",
    "shutdown",
]
