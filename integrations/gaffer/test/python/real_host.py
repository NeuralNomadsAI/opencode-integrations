"""Run with `gaffer python real_host.py` in an isolated environment (real-host.test.ts sets it up).

The installed part in a real Gaffer, its interface's event loop running offscreen: the typed apply, the panel's
construction, the wire, hello and status off the main thread, execute_code (undo step, rollback, hints, quarantine and
its release), the busy answer while the main thread runs another request, and each script's offer and label.
"""
import hashlib
import json
import pathlib
import socket
import sys
import threading
import time
import types

sys.path.insert(0, str(pathlib.Path(sys.argv[0]).resolve().parents[2] / "host" / "python"))
import Gaffer
import GafferScene
from GafferOpenCode.Host import HostError, ScriptState

HOST_VERSION = json.loads((pathlib.Path(sys.argv[0]).resolve().parents[2] / "host" / "manifest.json").read_text())["version"]

# --- the typed apply on a ScriptState --------------------------------------------------------------------------
script = Gaffer.ScriptNode("PrivateSmoke")
source = Gaffer.Node("source")
source.addChild(Gaffer.IntPlug("value", defaultValue=1))
script.addChild(source)
state = ScriptState(script)
try:
    hello = state.hello()
    canonical = json.dumps(hello["capabilities"], sort_keys=True, separators=(",", ":"))
    assert hello["capability_hash"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert hello["host_version"] == HOST_VERSION
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

# --- the installed part as Gaffer's interface starts it, and the panel ------------------------------------------
# Opening the panel requests its own conversation immediately, without changing the script's offers.
import GafferOpenCode
from GafferOpenCodeUI.OpenCodeEditor import OpenCodeEditor
root = Gaffer.ApplicationRoot("PrivatePanel")
root["scripts"].addChild(script)
second = Gaffer.ScriptNode()
root["scripts"].addChild(second)
third = Gaffer.ScriptNode()
root["scripts"].addChild(third)
GafferOpenCode.initialise(types.SimpleNamespace(root=lambda: root), advertise=False)
host = GafferOpenCode.currentHost()
editor = OpenCodeEditor(script)
assert editor._OpenCodeEditor__channel is not None
assert editor._OpenCodeEditor__client is not None
assert editor._OpenCodeEditor__conversationButton.getText() == "New conversation"
print("GAFFER_PANEL_OK")

# Each script is its own offer, labelled with its file (numbered when two scripts share it), under its own key.
offers = {handle: (label, key) for handle, label, key in host.offers()}
assert len(offers) == 3, offers
assert len({key for _, key in offers.values()}) == 3, offers
labels = sorted(label for label, _ in offers.values())
assert labels[0] == "Gaffer: PrivateSmoke" and len(set(labels)) == 3, labels
# The label follows the fileName plug (nothing is written); two scripts with the same file are numbered.
second["fileName"].setValue("D:/shots/ex03.gfr")
third["fileName"].setValue("D:/shots/ex03.gfr")
assert GafferOpenCode.offerInfo(second)["label"] == "Gaffer: ex03.gfr", GafferOpenCode.offerInfo(second)
assert GafferOpenCode.offerInfo(third)["label"] == "Gaffer: ex03.gfr (2)", GafferOpenCode.offerInfo(third)
print("GAFFER_OFFERS_OK " + json.dumps(sorted(label for _, label, _ in host.offers())))

import GafferUI
info = GafferOpenCode.hostInfo()
handle = GafferOpenCode.scriptHandle(script)
assert info["host"] == "127.0.0.1"
assert len(bytes.fromhex(info["token"])) == 32
loop = GafferUI.EventLoop.mainEventLoop()


def ask(method, params, token=None, timeout=60):
    with socket.create_connection((info["host"], info["port"]), timeout=timeout) as client:
        request = {"id": "probe", "token": token or info["token"], "method": method, "params": params}
        client.sendall((json.dumps(request) + "\n").encode())
        return json.loads(client.makefile("rb").readline())


checks = []
evidence = {}


def probe():
    try:
        assert ask("hello", {"script_handle": handle}, token="0" * 64)["ok"] is False
        hello = ask("hello", {"script_handle": handle})["result"]
        assert hello["script_handle"] == handle and hello["host_version"] == HOST_VERSION
        assert hello["process"]["pid"] > 0 and hello["process"]["temp_dir"]
        assert {"execute_code", "release", "status"} <= set(hello["capabilities"]["methods"])
        assert ask("hello", {})["result"]["process"] == hello["process"]
        assert ask("hello", {"script_handle": "gone"})["error"]["code"] == "stale_handle"
        # A refusal keeps the host's message, its data and its own code (data.reason).
        error = ask("context", {"script_handle": handle, "session_id": "private-smoke", "if_revision": 10 ** 6})["error"]
        assert error["code"] == "stale_revision" and error["data"]["reason"] == "revision_conflict", error
        checks.append("wire")

        # execute_code: one undo step; a failure after a change is undone, with Gaffer's hint.
        ok = ask("execute_code", {"script_handle": handle, "code": "script['box'] = GafferScene.Cube()\nresult = {'n': len(script.children(Gaffer.Node))}"})["result"]
        assert ok["status"] == "executed" and ok["changed"] and ok["undo_step"] == "OpenCode: code", ok
        bad = ask("execute_code", {"script_handle": handle, "code": "script['plane'] = GafferScene.Plane()\nscript['box']['dimensionss'].setValue(imath.V3f(2))"})["result"]
        assert bad["status"] == "failed" and bad["restored"] is True, bad
        assert "dimensions" in bad["hint"], bad
        typed = ask("execute_code", {"script_handle": handle, "code": "script['box']['dimensions'].setValue('big')"})["result"]
        assert "V3fPlug.setValue takes a imath.V3f" in typed["hint"], typed
        computed = ask("execute_code", {"script_handle": handle, "code": "script['e'] = Gaffer.Expression()\nscript['e'].setExpression('parent[\"box\"][\"dimensions\"][\"x\"] = 1 / 0', 'python')\nscript['box']['out'].bound('/cube')"})["result"]
        assert computed["restored"] is True and "Its python expression" in computed["hint"], computed
        read = ask("execute_code", {"script_handle": handle, "read_only": True, "code": "result = {'nodes': [n.getName() for n in script.children(Gaffer.Node)]}"})["result"]
        assert read["changed"] is False and "plane" not in read["result"]["nodes"] and "e" not in read["result"]["nodes"], read
        evidence["hints"] = {"child": bad["hint"], "type": typed["hint"], "compute": computed["hint"]}
        checks.append("code")

        # A change outside the undo step: the rollback cannot give the script back, which is quarantined until the
        # agent read it and releases it with that read's revision.
        leak = ask("execute_code", {"script_handle": handle, "code": "with Gaffer.UndoScope(script, Gaffer.UndoScope.State.Disabled):\n    script['leak'] = Gaffer.Node()\nscript['tracked'] = Gaffer.Node()\nraise ValueError('late')"})["result"]
        assert leak["restored"] is False and leak["quarantine"]["cause"].startswith("failed code"), leak
        refused = ask("execute_code", {"script_handle": handle, "code": "script['more'] = Gaffer.Node()"})
        assert refused["error"]["code"] == "quarantined", refused
        assert ask("release", {"script_handle": handle, "revision": 1})["error"]["code"] == "stale_revision"
        revision = ask("context", {"script_handle": handle, "limit": 1})["result"]["revision"]
        assert ask("release", {"script_handle": handle, "revision": revision})["result"]["released"] is True
        assert ask("execute_code", {"script_handle": handle, "code": "script['more'] = Gaffer.Node()"})["result"]["status"] == "executed"
        checks.append("quarantine")

        # The main thread busy: status answers at once, another request is refused within 10 s, saying what runs.
        long = []
        runner = threading.Thread(target=lambda: long.append(ask("execute_code", {"script_handle": handle, "read_only": True, "code": "import time\ntime.sleep(14)\nresult = 1"})))
        runner.start()
        time.sleep(1.5)
        status = ask("status", {})["result"]
        assert status["busy"] and status["running"] == "execute_code", status
        started = time.monotonic()
        busy = ask("context", {"script_handle": handle, "limit": 1})
        waited = time.monotonic() - started
        assert busy["ok"] is False and busy["error"]["code"] == "busy" and "execute_code" in busy["error"]["message"], busy
        assert 9 < waited < 12, waited
        runner.join(30)
        assert long and long[0]["result"]["status"] == "executed", long
        assert ask("hello", {"script_handle": handle})["ok"]
        evidence["busy"] = {"status": status, "seconds": round(waited, 1), "message": busy["error"]["message"]}
        checks.append("gate")
    except Exception as error:
        import traceback
        checks.append("{}: {}".format(type(error).__name__, error))
        traceback.print_exc()
    finally:
        GafferUI.EventLoop.executeOnUIThread(loop.stop)


threading.Thread(target=probe, daemon=True).start()
loop.start()
GafferOpenCode.shutdown()
print("GAFFER_EVIDENCE " + json.dumps(evidence))
assert checks == ["wire", "code", "quarantine", "gate"], checks
print("GAFFER_WIRE_OK")
print("GAFFER_CODE_OK")
print("GAFFER_GATE_OK")
