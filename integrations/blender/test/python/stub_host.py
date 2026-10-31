"""The add-on's own wire and core modules served without Blender, for the conformance checks.

A stub application stands in for bpy: hello, the listener, the token check and the
execute_code gate are the add-on's code. Prints one JSON line with the endpoint (tests only),
then serves until stdin closes.
Usage: python -I stub_host.py <host folder> <allow code execution: 0|1>
"""

import contextlib
import json
import sys
import threading

sys.path.insert(0, sys.argv[1])

from opencode_link.core import HostCore  # noqa: E402
from opencode_link.wire import Listener  # noqa: E402


class StubApp(object):
    background = True

    def __init__(self, allow):
        self._allow = allow
        self._lock = threading.Lock()
        self._revision = 0

    def app_version(self):
        return "5.2.0 stub"

    def script_handle(self):
        return "stub-document"

    def revision(self):
        return self._revision

    def bump_revision(self):
        self._revision += 1

    def document(self):
        return "stub.blend"

    def mode(self):
        return "OBJECT"

    def code_execution_allowed(self):
        return self._allow

    def on_main(self, fn):
        with self._lock:
            return fn()

    @contextlib.contextmanager
    def quiet(self):
        yield

    def undo_push(self, message):
        pass

    def undo(self):
        pass

    def flush(self):
        pass

    def fingerprint(self):
        return ()

    def check_expect(self, clause):
        return None

    def created_refs(self, op, result):
        return []

    def handler(self, name):
        raise KeyError(name)

    def exec_namespace(self):
        return {"__name__": "__opencode_execute__"}


core = HostCore(StubApp(sys.argv[2] == "1"), folder=sys.argv[1] + "/opencode_link")
listener = Listener(core.handle)
print(json.dumps({"port": listener.port, "token": listener.token}), flush=True)
sys.stdin.read()
listener.close()
