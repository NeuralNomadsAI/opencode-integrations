"""Exercise the actual panel import and shared listener without Houdini or Qt."""
import json
from pathlib import Path
import socket
from string import Template
import sys
import types
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET


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
qt.QtWidgets = types.SimpleNamespace(QWidget=object, QPlainTextEdit=object)
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
        root = Path(env["NEURALNOMADS_HOUDINI_ROOT"])
        self.assertEqual(Template(config["path"]).substitute(env), str(root).replace("\\", "/"))
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
        with patch.object(panel.shutil, "which", return_value=sys.executable):
            client.start()
        process.setArguments.assert_called_once_with([str(root / "sidecar.cjs")])
        process.start.assert_called_once()
        self.assertTrue((root / "sidecar.cjs").is_file())

    def test_shared_host_lifetime(self):
        root = Path(__file__).resolve().parents[1] / "host"
        sys.path.insert(0, str(root / "scripts"))
        with patch("atexit.register") as register:
            load_panel(root)
        import opencode_houdini as integration
        from opencode_houdini import panel
        register.assert_called_once_with(integration.shutdown)
        state = Mock(handle="hip:two-panels", _closed=False)
        state.hello.return_value = {"script_handle": state.handle, "revision": 1}
        # Only the HOM scene boundary is stubbed; Host, HostServer and panel methods are real.
        with patch.object(integration.host, "ScriptState", return_value=state):
            first = panel.OpenCodePanel.__new__(panel.OpenCodePanel)
            second = panel.OpenCodePanel.__new__(panel.OpenCodePanel)
            client = Mock()
            for index, widget in enumerate((first, second)):
                widget._channel = str(index)
                widget._client = client
                widget._status = Mock()
                widget._send = Mock()
                widget._prompt = Mock()
                widget._session = Mock()
                widget._append = Mock()
                widget._connect()
                widget._event(widget._channel, "session", {"id": "session-" + str(index)})
            endpoint = integration.hostInfo()
            host, server = integration._host, integration._server

            def assert_live():
                self.assertIs(integration._host, host)
                self.assertEqual(integration.hostInfo(), endpoint)
                self.assertTrue(second._connected)
                with socket.create_connection((endpoint["host"], endpoint["port"]), timeout=2) as connection:
                    request = {"id": "probe", "token": endpoint["token"], "method": "hello", "params": {}}
                    connection.sendall((json.dumps(request) + "\n").encode())
                    with connection.makefile("rb") as response:
                        result = json.loads(response.readline())
                self.assertTrue(result["ok"])
                self.assertEqual(result["result"]["script_handle"], state.handle)

            try:
                first._disconnect()
                client.send.assert_called_with(first._channel, "close", {})
                self.assertFalse(first._connected)
                assert_live()
                first._connect()
                first._new_session()
                first._destroyed()
                client.send.assert_called_with(first._channel, "close", {})
                assert_live()
                # Even the last panel closing leaves the process-owned host usable by attached clients.
                second._destroyed()
                assert_live()
                state.close.assert_not_called()
            finally:
                integration.shutdown()
            self.assertFalse(server.running)
            self.assertNotEqual(server.token, endpoint["token"])
            self.assertIsNone(integration._host)
            state.close.assert_called_once()
            integration.shutdown()  # Explicit cleanup and process exit are idempotent.


if __name__ == "__main__":
    suite = unittest.TestSuite([PanelTests(sys.argv[1])])
    result = unittest.TextTestRunner().run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
