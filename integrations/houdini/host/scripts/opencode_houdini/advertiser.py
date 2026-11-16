"""Offers this Houdini to the discovered OpenCode services (standard library only).

Runs on its own thread: discovery and RPC calls never block Houdini's main thread. One offer per service,
renewed every 5 seconds; stopping withdraws it from every service, and an offer whose renewals stop expires
within 15 seconds anyway. Results carry service file names and outcomes only, never the daemon password nor
the host token.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
from typing import Any, Callable

RPC_ID = "neuralnomads.houdini"
PROTOCOL = 1
RENEW_SECONDS = 5.0


def _launch_key() -> str | None:
    """A Houdini started by OpenCode offers itself under the key OpenCode chose."""
    key = os.environ.get("OPENCODE_HOUDINI_OFFER_KEY", "")
    return key if re.fullmatch(r"houdini-launch-[0-9a-f]{32}", key) else None


# Stable for the life of the Houdini process: the service recognises a renewed or re-announced offer.
OFFER_KEY = _launch_key() or "houdini-%d-%s" % (os.getpid(), secrets.token_hex(8))


class Advertiser:
    """`snapshot()` returns (port, token, script_handle, label), or a None token while not ready."""

    def __init__(self, snapshot: Callable[[], tuple[Any, Any, Any, Any]], env: dict[str, str] | None = None,
                 interval: float = RENEW_SECONDS) -> None:
        self._snapshot = snapshot
        self._env = env
        self._interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._results: list[dict[str, Any]] = []
        self._thread = threading.Thread(target=self._loop, name="OpenCodeHoudiniAdvertiser", daemon=True)

    def start(self) -> "Advertiser":
        self._thread.start()
        return self

    @property
    def results(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._results)

    def advertise_once(self) -> list[dict[str, Any]] | None:
        port, token, script_handle, label = self._snapshot()
        if token is None:
            return None
        offer = {
            "host": "127.0.0.1",
            "port": port,
            "token": token,
            "script_handle": script_handle,
            "label": label,
            "protocol": PROTOCOL,
            "offer_key": OFFER_KEY,
        }
        from . import opencode_service  # noqa: PLC0415 - the kit's client, vendored into the payload

        try:
            results = opencode_service.advertise_all(RPC_ID, offer, env=self._env)
        except (OSError, ValueError):
            results = []
        with self._lock:
            self._results = results
        return results

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.advertise_once()
            except Exception:  # noqa: BLE001 - the next renewal tries again
                pass
            self._stop.wait(self._interval)

    def stop(self, token: str | None, withdraw: bool = True) -> list[dict[str, Any]]:
        """Stops renewing; withdraws the offer from every service with the token it was announced with."""
        from . import opencode_service  # noqa: PLC0415

        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=opencode_service.TIMEOUT * 2)
        if not withdraw or token is None:
            return []
        try:
            return opencode_service.withdraw_all(RPC_ID, OFFER_KEY, token, env=self._env)
        except (OSError, ValueError):
            return []
