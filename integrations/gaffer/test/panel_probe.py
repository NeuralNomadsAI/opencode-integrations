"""Exercise the installed Gaffer panel (OpenCodeEditor and its sidecar client) without Gaffer or Qt.

Usage: python -I panel_probe.py <payload root holding python/ and sidecar.cjs>
"""
import gc
import hashlib
import pathlib
import sys
import types
import unittest
import weakref
from unittest.mock import MagicMock, patch


PAYLOAD = pathlib.Path(sys.argv[1]).resolve()
HOST = {"host": "127.0.0.1", "port": 4242, "token": "a" * 64, "protocol": 1}


def widget(*args, **kw):
    return MagicMock()


def weak_method(method):
    # Gaffer.WeakMethod: callbacks never keep an editor alive.
    ref = weakref.WeakMethod(method)
    return lambda *args, **kw: ref()(*args, **kw)


class Editor:
    def __init__(self, topLevelWidget, scriptNode, **kw):
        self.__script = scriptNode
        self.__widget = MagicMock()

    def scriptNode(self):
        return self.__script

    def _qtWidget(self):
        return self.__widget

    @staticmethod
    def registerType(name, cls):
        Editor.registered = (name, cls)


class Api(types.ModuleType):
    """GafferOpenCode as the panel may use it: anything outside the contract fails the probe."""

    CONTRACT = ("hostInfo", "offerInfo", "scriptHandle", "acquirePanel", "releasePanel")

    def __init__(self):
        super().__init__("GafferOpenCode")
        self.calls = []
        self.offers = {}

    def __getattr__(self, name):
        raise AssertionError("the panel used GafferOpenCode." + name)

    def hostInfo(self):
        return dict(HOST)

    def offerInfo(self, script):
        return dict(self.offers[script.handle])

    def scriptHandle(self, script):
        return script.handle

    def acquirePanel(self, channel):
        self.calls.append(("acquire", channel))

    def releasePanel(self, channel):
        self.calls.append(("release", channel))


class Plug:
    def __init__(self, value):
        self.value = value

    def getValue(self):
        return self.value

    def isSame(self, other):
        return self is other


class Script:
    def __init__(self, handle, fileName=""):
        self.handle = handle
        self.plugs = {"fileName": Plug(fileName)}
        self.slots = []

    def __getitem__(self, name):
        return self.plugs[name]

    def getName(self):
        return "ScriptNode"

    def plugSetSignal(self):
        script = self

        class Signal:
            def connect(self, slot, scoped=True):
                script.slots.append(slot)
                return object()

        return Signal()

    def setFileName(self, value):
        self.plugs["fileName"].value = value
        for slot in self.slots:
            slot(self.plugs["fileName"])


class FakeClient:
    def __init__(self):
        self.callbacks = {}
        self.sent = []
        self.closed = []

    def register(self, callback):
        channel = "channel-" + str(len(self.callbacks))
        self.callbacks[channel] = weakref.WeakMethod(callback)
        return channel

    def send(self, channel, method, params):
        self.sent.append((channel, method, params))

    def close(self, channel):
        self.closed.append(channel)

    def emit(self, channel, name, data):
        self.callbacks[channel]()(name, data)


api = Api()
gaffer = types.ModuleType("Gaffer")
gaffer.WeakMethod = weak_method
gafferui = types.ModuleType("GafferUI")
gafferui.Editor = Editor
for name in ("ListContainer", "Label", "MenuButton", "Menu", "Button", "Spacer", "MultiLineTextWidget"):
    setattr(gafferui, name, MagicMock(side_effect=widget))
qt = types.ModuleType("Qt")
qt.QtCore = MagicMock()
qt.QtGui = MagicMock()
sys.modules.update(
    {
        "Gaffer": gaffer,
        "GafferUI": gafferui,
        "GafferOpenCode": api,
        "IECore": MagicMock(),
        "imath": MagicMock(),
        "Qt": qt,
    }
)
sys.path.insert(0, str(PAYLOAD / "python"))
import GafferOpenCodeUI  # noqa: E402

editor_module = sys.modules["GafferOpenCodeUI.OpenCodeEditor"]
client_module = sys.modules["GafferOpenCodeUI.Client"]


def private(editor, name):
    return getattr(editor, "_OpenCodeEditor__" + name)


class PanelTests(unittest.TestCase):
    def setUp(self):
        api.calls.clear()
        self.client = FakeClient()
        patcher = patch.object(editor_module.ClientProcess, "acquire", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_installed_client_runs_node_sidecar(self):
        self.assertEqual(Editor.registered, ("OpenCode for Gaffer", GafferOpenCodeUI.OpenCodeEditor))
        process = qt.QtCore.QProcess.return_value
        process.state.return_value = qt.QtCore.QProcess.NotRunning
        with patch.dict("os.environ", {"NEURALNOMADS_GAFFER_NODE": ""}), patch.object(
            client_module.shutil, "which", return_value="/usr/bin/node"
        ) as which:
            client_module.ClientProcess()
        which.assert_called_with("node")
        process.setProgram.assert_called_with("/usr/bin/node")
        process.setArguments.assert_called_with([str(PAYLOAD / "sidecar.cjs")])
        process.start.assert_called()
        self.assertTrue((PAYLOAD / "sidecar.cjs").is_file())
        # NEURALNOMADS_GAFFER_NODE names Node explicitly.
        with patch.dict("os.environ", {"NEURALNOMADS_GAFFER_NODE": "/opt/node22/bin/node"}):
            client_module.ClientProcess()
        process.setProgram.assert_called_with("/opt/node22/bin/node")

    def test_connect_targets_this_scripts_offer_and_closing_never_withdraws(self):
        shot = pathlib.Path(PAYLOAD / "shots" / "shot.gfr")
        script = Script("gaffer:shot", str(shot))
        api.offers[script.handle] = {"label": "Gaffer: shot.gfr", "offer_key": "gaffer-shot", "advertising": True}
        editor = GafferOpenCodeUI.OpenCodeEditor(script)
        # Opening the panel attaches nothing: Gaffer already offers the script by itself.
        self.assertEqual(api.calls, [])
        self.assertEqual(self.client.sent, [])

        private(editor, "toggleConnection")()
        channel, method, params = self.client.sent[-1]
        self.assertEqual(api.calls, [("acquire", channel)])
        self.assertEqual(method, "connect")
        key = "document:" + hashlib.sha256(str(shot.resolve()).encode("utf-8")).hexdigest()
        self.assertEqual(
            params,
            {
                **HOST,
                "script_handle": "gaffer:shot",
                "offer_label": "Gaffer: shot.gfr",
                "script_key": key,
                "script_name": "shot.gfr",
                "directory": str(shot.resolve().parent),
            },
        )
        self.client.emit(channel, "session", {"id": "ses_shot", "resumed": True})
        self.client.emit(channel, "status", {"state": "idle"})
        prompt = private(editor, "prompt")
        prompt.getText.return_value = "Add a sphere"
        private(editor, "send")(None)
        self.assertEqual(self.client.sent[-1], (channel, "prompt", {"text": "Add a sphere"}))

        # New starts another conversation for the same script, on the same offer.
        private(editor, "newSession")(None)
        self.assertEqual(self.client.sent[-1], (channel, "new_session", params))

        # Disconnect releases the panel's conversation only.
        private(editor, "toggleConnection")()
        self.assertEqual(self.client.sent[-1], (channel, "close", {}))
        self.assertEqual(api.calls[-1], ("release", channel))
        # A reply to the closed connection is ignored.
        self.client.emit(channel, "session", {"id": "ses_late"})
        self.assertFalse(private(editor, "connected"))

        # Connect again asks for the same script's conversation: the sidecar resumes the session marked with it.
        private(editor, "toggleConnection")()
        self.assertEqual(self.client.sent[-1], (channel, "connect", params))

        # Save As: another file is another script's conversation.
        moved = PAYLOAD / "shots" / "shot_v2.gfr"
        api.offers[script.handle]["label"] = "Gaffer: shot_v2.gfr"
        script.setFileName(str(moved))
        self.assertEqual(self.client.sent[-1], (channel, "close", {}))
        self.assertEqual(api.calls[-1], ("release", channel))
        private(editor, "toggleConnection")()
        moved_params = self.client.sent[-1][2]
        self.assertEqual(moved_params["offer_label"], "Gaffer: shot_v2.gfr")
        self.assertNotEqual(moved_params["script_key"], key)

        # Closing the editor (its last reference gone) releases its conversation and the panel registration.
        del editor, prompt
        gc.collect()
        self.assertEqual(self.client.closed, [channel])
        self.assertEqual(api.calls[-1], ("release", channel))
        self.assertEqual({call[0] for call in api.calls}, {"acquire", "release"})

    def test_each_script_has_its_own_offer_and_conversation(self):
        first = Script("gaffer:one")
        second = Script("gaffer:two")
        api.offers[first.handle] = {"label": "Gaffer: untitled", "offer_key": "gaffer-one", "advertising": True}
        api.offers[second.handle] = {"label": "Gaffer: untitled (2)", "offer_key": "gaffer-two", "advertising": True}
        editors = [GafferOpenCodeUI.OpenCodeEditor(first), GafferOpenCodeUI.OpenCodeEditor(second)]
        for editor in editors:
            private(editor, "toggleConnection")()
        (a, _, one), (b, _, two) = self.client.sent
        self.assertNotEqual(a, b)
        self.assertEqual((one["offer_label"], one["script_key"]), ("Gaffer: untitled", "untitled:gaffer:one"))
        self.assertEqual((two["offer_label"], two["script_key"]), ("Gaffer: untitled (2)", "untitled:gaffer:two"))
        # A second panel on the same script takes back the same conversation.
        third = GafferOpenCodeUI.OpenCodeEditor(first)
        private(third, "toggleConnection")()
        self.assertEqual(self.client.sent[-1][2]["script_key"], "untitled:gaffer:one")
        # Closing one panel leaves the others (and every offer) alone.
        del third
        gc.collect()
        self.assertEqual(self.client.closed, ["channel-2"])
        self.assertEqual(api.calls[-1], ("release", "channel-2"))
        self.assertEqual([call for call in api.calls if call[0] == "release"], [("release", "channel-2")])

    def test_a_script_not_offered_is_said_without_connecting(self):
        script = Script("gaffer:private")
        api.offers[script.handle] = {"label": "Gaffer: untitled", "offer_key": "gaffer-private", "advertising": False}
        editor = GafferOpenCodeUI.OpenCodeEditor(script)
        append = MagicMock()
        setattr(editor, "_OpenCodeEditor__append", append)
        private(editor, "toggleConnection")()
        self.assertEqual(self.client.sent, [])
        self.assertEqual(api.calls, [])
        append.assert_called_once_with("error", "OpenCode Link does not offer this script to OpenCode.")
        self.assertFalse(private(editor, "active"))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PanelTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
