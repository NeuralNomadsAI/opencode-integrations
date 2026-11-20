"""Exercise the actual panel import and shared listener without Houdini or Qt."""
import json
import os
from pathlib import Path
import socket
from string import Template
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET


# Never offer this test's host to the OpenCode services of the computer running it.
os.environ["OPENCODE_HOUDINI_OFFER"] = "0"
hou = types.ModuleType("hou")
hou.nodeEventType = types.SimpleNamespace(**{name: name for name in (
    "BeingDeleted", "ChildCreated", "ChildDeleted", "ChildReordered", "FlagChanged", "InputRewired",
    "NameChanged", "ParmTupleChanged", "PositionChanged", "SelectionChanged", "SpareParmTemplatesChanged")})
hou.isUIAvailable = lambda: False
hou.applicationVersionString = lambda: "22.0.429"
hou.hipFile = Mock()
hou.hipFile.path.return_value = "untitled.hip"
hou.hipFile.isNewFile.return_value = True
hou.hipFile.basename.return_value = "untitled.hip"
sys.modules["hou"] = hou
qt = types.ModuleType("PySide6")
qt.QtCore = types.SimpleNamespace(QObject=object, Signal=lambda *args: Mock(), Slot=lambda *args: lambda fn: fn)
qt.QtGui = types.SimpleNamespace()
class Widget:
    def closeEvent(self, event):
        event.accept()

qt.QtWidgets = types.SimpleNamespace(QWidget=Widget, QPlainTextEdit=object, QComboBox=object)
sys.modules["PySide6"] = qt


def load_panel(root):
    # Execute the installed entrypoint, including its ordinary Python import.
    script = ET.parse(root / "python_panels" / "opencode_houdini.pypanel").find(".//script").text
    namespace = {}
    exec(compile(script, "opencode_houdini.pypanel", "exec"), namespace)
    return namespace


class PanelTests(unittest.TestCase):
    def test_installed_import(self):
        config = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        env = {}
        for item in config["env"]:
            for name, value in item.items():
                if isinstance(value, dict):
                    self.assertEqual(value["method"], "prepend")
                    value = value["value"]
                env[name] = Template(value).substitute(env)
        root = Path(env["NEURALNOMADS_HOUDINI_ROOT"]).resolve()
        self.assertEqual(Path(Template(config["path"]).substitute(env)).resolve(), root)
        # -I excludes cwd and ambient PYTHONPATH: only the generated package's path is added.
        if env.get("PYTHONPATH"):
            sys.path.insert(0, env["PYTHONPATH"])
        namespace = load_panel(root)
        import opencode_houdini as integration
        from opencode_houdini import panel
        self.assertEqual(Path(panel.__file__).resolve(), root / "scripts" / "opencode_houdini" / "panel.py")
        self.assertIs(namespace["create_interface"], panel.create_interface)
        self.assertIsNone(integration._host)
        self.assertEqual(integration.host.host_version(), config["neuralnomadsHostVersion"])
        # The panel still resolves its bundled sidecar from the stable installed copy.
        process = Mock()
        qt.QtCore.QProcess = types.SimpleNamespace(ProcessState=types.SimpleNamespace(NotRunning=0))
        process.state.return_value = 0
        client = panel.JsonlClient.__new__(panel.JsonlClient)
        client._process = process
        client._targets = {"test": {}}
        client._problem = ""
        client._launch_at = 0
        qt.QtCore.QProcessEnvironment = Mock()
        with patch.object(panel, "panel_launcher", return_value=(sys.executable, True)):
            client.start()
        process.setArguments.assert_called_once_with([str(root / "sidecar.cjs")])
        process.start.assert_called_once()
        qt.QtCore.QProcessEnvironment.systemEnvironment.return_value.insert.assert_called_once_with("BUN_BE_BUN", "1")
        self.assertTrue((root / "sidecar.cjs").is_file())

    def test_shared_host_lifetime(self):
        root = Path(__file__).resolve().parents[1] / "host"
        sys.path.insert(0, str(root / "scripts"))
        with patch("atexit.register") as register:
            interfaces = [load_panel(root), load_panel(root)]
        import opencode_houdini as integration
        from opencode_houdini import panel
        register.assert_called_once_with(integration.shutdown)
        state = Mock(handle="hip:two-panels", epoch="test-epoch", revision=1, _closed=False)
        state.hello.return_value = {"script_handle": state.handle, "revision": 1}
        # Only the HOM scene boundary is stubbed; Host, HostServer and panel methods are real.
        with patch.object(integration.host, "ScriptState", return_value=state):
            first = panel.OpenCodePanel.__new__(panel.OpenCodePanel)
            second = panel.OpenCodePanel.__new__(panel.OpenCodePanel)
            client = Mock()
            for index, widget in enumerate((first, second)):
                widget._disposed = False
                widget._channel = str(index)
                widget._client = client
                widget._status = Mock()
                widget._send = Mock()
                widget._prompt = Mock()
                widget._append = Mock()
                widget._target = None
                integration.acquirePanel(widget._channel)
                with patch.object(panel, "create_interface", return_value=widget):
                    interfaces[index]["create_interface"] = panel.create_interface
                    self.assertIs(interfaces[index]["onCreateInterface"](), widget)
                widget._sync()
                widget._event(widget._channel, "session", {"id": "session-" + str(index)})
            endpoint = integration.hostInfo()
            host, server = integration._host, integration._server

            def assert_live():
                self.assertIs(integration._host, host)
                self.assertEqual(integration.hostInfo(), endpoint)
                self.assertTrue(second._connected)
                replies = []

                def ask():
                    with socket.create_connection((endpoint["host"], endpoint["port"]), timeout=5) as connection:
                        request = {"id": "probe", "token": endpoint["token"], "method": "hello", "params": {}}
                        connection.sendall((json.dumps(request) + "\n").encode())
                        with connection.makefile("rb") as response:
                            replies.append(json.loads(response.readline()))

                # The request runs on this, the main thread, which serves it as hython's does.
                asker = threading.Thread(target=ask)
                asker.start()
                integration.pump(5.0, stop=lambda: not asker.is_alive())
                asker.join()
                result = replies[0]
                self.assertTrue(result["ok"])
                self.assertEqual(result["result"]["script_handle"], state.handle)

            try:
                first._new_session()
                interfaces[0]["onDestroyInterface"]()
                client.send.assert_called_with(first._channel, "close", {})
                self.assertEqual(integration._panel_owners, {second._channel})
                sends = client.send.call_count
                interfaces[0]["onDestroyInterface"]()
                first._destroyed()
                first._sync()
                self.assertEqual(client.send.call_count, sends)
                assert_live()
                # The last panel closing leaves the host with Houdini: its offer serves the other conversations.
                client.send.side_effect = RuntimeError("sidecar unavailable")
                interfaces[1]["onDestroyInterface"]()
                self.assertEqual(integration._panel_owners, set())
                self.assertIsNone(interfaces[1]["panel"])
                self.assertEqual(client.eventReceived.disconnect.call_count, 2)
                self.assertEqual(client.diagnostic.disconnect.call_count, 2)
                self.assertTrue(server.running)
                self.assertEqual(integration.hostInfo(), endpoint)
                state.close.assert_not_called()
                client.send.side_effect = None
                # A new panel uses the same host, authority unchanged.
                third = panel.OpenCodePanel.__new__(panel.OpenCodePanel)
                third.__dict__.update(second.__dict__, _disposed=False, _channel="third", _target=None)
                integration.acquirePanel(third._channel)
                third._sync()
                self.assertIs(integration._server, server)
                params = client.send.call_args.args[2]
                self.assertEqual(params["offer_label"], "Houdini: untitled.hip")
                self.assertEqual(params["directory"], os.getcwd())
                third._destroyed()
                self.assertTrue(server.running)
            finally:
                integration.shutdown()
            # Houdini exiting (atexit) stops the host and invalidates its token.
            self.assertFalse(server.running)
            self.assertNotEqual(server.token, endpoint["token"])
            self.assertIsNone(integration._host)
            with self.assertRaises(OSError):
                socket.create_connection((endpoint["host"], endpoint["port"]), timeout=2)
            state.close.assert_called_once()
            integration.shutdown()  # Explicit cleanup and process exit are idempotent.


if __name__ == "__main__":
    suite = unittest.TestSuite([PanelTests(sys.argv[1])])
    result = unittest.TextTestRunner().run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
