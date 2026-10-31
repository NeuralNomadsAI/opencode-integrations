# SPDX-License-Identifier: GPL-3.0-or-later

"""Advertises this Blender session to the discovered OpenCode services (standard library only).

Runs on its own thread: discovery and RPC calls never block Blender's main
thread. One offer per service, renewed every 5 seconds; stopping withdraws it
from every service, and an offer whose renewals stop expires within 15
seconds anyway. Results carry service file names and outcomes only, never
the daemon password nor the host token.
"""

import os
import secrets
import threading

from . import opencode_service

RPC_ID = "neuralnomads.blender"
PROTOCOL = 1
RENEW_SECONDS = 5.0
LABEL_MAX = 120

# Stable for the life of the Blender process (HST-13).
OFFER_KEY = "blender-%d-%s" % (os.getpid(), secrets.token_hex(4))


def offer_label(document):
    name = (document or "Untitled").replace("/", " ").replace("\\", " ")
    name = "".join(char for char in name if ord(char) >= 32 and char != "\x7f").strip() or "Untitled"
    return ("Blender: %s" % name)[:LABEL_MAX]


class Advertiser(object):
    """`snapshot()` returns (port, token, script_handle, label) read from the main thread's state."""

    def __init__(self, snapshot, service_name="", config_dir="", env=None, interval=RENEW_SECONDS):
        self._snapshot = snapshot
        self._service_name = service_name or None
        self._config_dir = config_dir or None
        self._env = env
        self._interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._results = []
        self._thread = threading.Thread(target=self._loop, name="OpenCodeLinkAdvertiser", daemon=True)

    def start(self):
        self._thread.start()
        return self

    @property
    def results(self):
        with self._lock:
            return list(self._results)

    def advertise_once(self):
        port, token, script_handle, label = self._snapshot()
        if token is None:
            return []
        offer = {
            "host": "127.0.0.1",
            "port": port,
            "token": token,
            "script_handle": script_handle,
            "label": label,
            "protocol": PROTOCOL,
            "offer_key": OFFER_KEY,
        }
        try:
            results = opencode_service.advertise_all(
                RPC_ID, offer, service_name=self._service_name, config_dir_setting=self._config_dir, env=self._env
            )
        except (OSError, ValueError):
            results = []
        with self._lock:
            self._results = results
        return results

    def _loop(self):
        while not self._stop.is_set():
            self.advertise_once()
            self._stop.wait(self._interval)

    def stop(self, token, withdraw=True):
        """Stops renewing; withdraws the offer from every service with the token it was announced with."""
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=opencode_service.TIMEOUT * 2)
        if not withdraw or token is None:
            return []
        try:
            return opencode_service.withdraw_all(
                RPC_ID, OFFER_KEY, token, service_name=self._service_name,
                config_dir_setting=self._config_dir, env=self._env,
            )
        except (OSError, ValueError):
            return []
