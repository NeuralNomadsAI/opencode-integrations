"""Run with `gaffer python real_host.py` in an isolated environment."""
import hashlib
import json
import pathlib
import sys
import types
import socket
import threading
sys.path.insert(0, str(pathlib.Path(sys.argv[0]).resolve().parents[2] / "host" / "python"))
import Gaffer
from GafferOpenCode.Host import HostError, ScriptState

script = Gaffer.ScriptNode("PrivateSmoke")
source = Gaffer.Node("source")
source.addChild(Gaffer.IntPlug("value", defaultValue=1))
script.addChild(source)
state = ScriptState(script)
try:
    hello = state.hello()
    canonical = json.dumps(hello["capabilities"], sort_keys=True, separators=(",", ":"))
    assert hello["capability_hash"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert hello["host_version"] == "0.2.0"
    context = state.context({"limit": 10, "include": ["values"]})
    card = next(card for card in context["entities"] if card["name"] == "source")
    intent = {"session_id": "private-smoke", "script_handle": state.handle,
              "if_revision": state.revision, "command_id": "private-rename",
              "operations": [{"op": "node.rename", "target": card["ref"], "name": "renamed"}],
              "expect": [{"subject": card["ref"], "predicate": "name_equals", "value": "renamed"}]}
    result = state.apply(intent, 0, False)
    assert source.getName() == "renamed"
    assert state.apply(intent, 0, False) == result
    try:
        state.apply(dict(intent, command_id="stale"), 0, False)
        raise AssertionError("Stale revision accepted")
    except HostError as error:
        assert error.code == "revision_conflict", error.code
    script.undo()
    assert source.getName() == "source"
    state.flush()
    view = {"op": "ui.view", "target": card["ref"]}
    rename = {"op": "node.rename", "target": card["ref"], "name": "mixed"}
    for operations, expect in [([rename, view], []), ([view, view], []),
                                ([view], [{"subject": card["ref"], "predicate": "exists"}])]:
        try:
            state.apply(dict(intent, if_revision=state.revision, command_id="mixed-view",
                             operations=operations, expect=expect), 0, False)
            raise AssertionError("Mixed or postconditioned Viewer intent accepted")
        except HostError as error:
            assert error.code == "invalid_intent", error.code
        assert source.getName() == "source"
        assert script.getFocus() is None
    viewIntent = dict(intent, if_revision=state.revision, command_id="sole-view", operations=[view], expect=[])
    viewResult = state.apply(viewIntent, 0, False)
    assert viewResult["side_effect"] and viewResult["undoable"] is False
    assert script.getFocus().isSame(source)
    assert not script.undoAvailable()
    assert state.apply(viewIntent, 0, False) == viewResult
    print("GAFFER_REAL_OK " + json.dumps({"app_version": hello["app_version"], "host_version": hello["host_version"], "undo": hello["capabilities"]["undo"]}))
finally:
    state.close()

# Construct the retained panel under offscreen Qt. Opening it does not launch the
# sidecar or advertise: the explicit Connect button is the consent boundary.
import GafferOpenCode
from GafferOpenCodeUI.OpenCodeEditor import OpenCodeEditor
root = Gaffer.ApplicationRoot("PrivatePanel")
root["scripts"].addChild(script)
GafferOpenCode.initialise(types.SimpleNamespace(root=lambda: root))
editor = OpenCodeEditor(script)
assert editor._OpenCodeEditor__channel is None
assert editor._OpenCodeEditor__client is None
assert editor._OpenCodeEditor__connectButton.getText() == "Connect"
assert GafferOpenCode.scriptHandle(script) not in editor._OpenCodeEditor__connectParams["script_key"]
print("GAFFER_PANEL_OK")

import GafferUI
info = GafferOpenCode.hostInfo()
handle = GafferOpenCode.scriptHandle(script)
assert info["host"] == "127.0.0.1"
assert len(bytes.fromhex(info["token"])) == 32
loop = GafferUI.EventLoop.mainEventLoop()
wire = []
def probe():
    try:
        for valid in (False, True):
            with socket.create_connection((info["host"], info["port"]), timeout=5) as client:
                request = {"id": "real-probe", "token": info["token"] if valid else "0" * 64,
                           "method": "hello", "params": {"script_handle": handle}}
                client.sendall((json.dumps(request) + "\n").encode())
                response = json.loads(client.makefile("rb").readline())
                assert response["ok"] == valid
                if valid:
                    assert response["result"]["script_handle"] == handle
                    assert response["result"]["host_version"] == "0.2.0"
        with socket.create_connection((info["host"], info["port"]), timeout=5) as client:
            client.sendall((json.dumps({"id": "bad-handle", "token": info["token"], "method": "hello", "params": {"script_handle": "gone"}}) + "\n").encode())
            response = json.loads(client.makefile("rb").readline())
            assert response["error"]["code"] == "stale_handle"
        wire.append("ok")
    except Exception as error:
        wire.append(type(error).__name__)
    finally:
        GafferUI.EventLoop.executeOnUIThread(loop.stop)
threading.Thread(target=probe, daemon=True).start()
loop.start()
assert wire == ["ok"], wire
print("GAFFER_WIRE_OK")
