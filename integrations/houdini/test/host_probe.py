"""The integration's own host transport under system Python; no Houdini or licence required."""
import collections
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

hou = types.ModuleType("hou")
hou.nodeEventType = types.SimpleNamespace(**{name: name for name in (
    "BeingDeleted", "ChildCreated", "ChildDeleted", "ChildReordered", "FlagChanged", "InputRewired",
    "NameChanged", "ParmTupleChanged", "PositionChanged", "SelectionChanged", "SpareParmTemplatesChanged")})
hou.isUIAvailable = lambda: False
hou.applicationVersionString = lambda: "22.0.429"
hou.hipFile = types.SimpleNamespace(basename=lambda: "test.hip")
hou.parmData = types.SimpleNamespace(Int=1, Float=2, String=3)
sys.modules["hou"] = hou
file = Path(__file__).resolve().parents[1] / "host" / "scripts" / "opencode_houdini" / "host.py"
spec = importlib.util.spec_from_file_location("houdini_host", file)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def state():
    value = module.ScriptState.__new__(module.ScriptState)
    value.handle = "hip:test-document"
    value.epoch = "test-epoch"
    value.revision = 1
    value.flush = lambda **kwargs: None
    value.summary = lambda: {}
    value.quarantined = False
    value._closed = False
    value._commands = collections.OrderedDict()
    value.cancellation_state = lambda session: (0, False)
    value._check_cancelled = lambda *args: None
    return value


class HostTests(unittest.TestCase):
    def test_expectations_precede_even_dry_run(self):
        value = state()
        value._preflight = lambda operations: ([], {})
        def fail(*args):
            raise module.HostError("expectation_failed", "expected failure")
        value._verify = fail
        with self.assertRaisesRegex(module.HostError, "expected failure"):
            value.apply({"session_id": "ses_test", "if_revision": 1, "command_id": "a", "operations": [{"op": "node.create"}], "dry_run": True})

    def test_headless_mutation_refused(self):
        value = state()
        value._preflight = lambda operations: ([], {})
        value._verify = lambda *args: []
        with self.assertRaisesRegex(module.HostError, "Headless"):
            value.apply({"session_id": "ses_test", "if_revision": 1, "command_id": "a", "operations": [{}], "dry_run": False, "confirm": True})

    def test_stale_revision_and_command_reuse(self):
        value = state()
        with self.assertRaises(module.HostError) as caught:
            value.apply({"session_id": "ses_test", "if_revision": 0, "command_id": "a"})
        self.assertEqual(caught.exception.code, "revision_conflict")
        value._commands["a"] = {"digest": "different", "result": {}}
        with self.assertRaises(module.HostError) as caught:
            value.apply({"session_id": "ses_test", "if_revision": 1, "command_id": "a"})
        self.assertEqual(caught.exception.code, "command_id_reused")

    def test_string_code_injection_refused(self):
        with self.assertRaises(module.HostError):
            state()._coerce_data_type(hou.parmData.String, '`python("danger")`')

    def test_read_bounds_and_replay_ledger(self):
        with self.assertRaises(module.HostError):
            state().context({"refs": ["a"] * 51})
        value = state()
        request = {"session_id": "ses_test", "command_id": "repeat", "if_revision": 1}
        digest = module.hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        value._commands["repeat"] = {"digest": digest, "result": {"status": "already-applied"}}
        self.assertEqual(value.apply(request), {"status": "already-applied"})
        value._commands = {str(index): {} for index in range(module.COMMAND_LIMIT)}
        with self.assertRaises(module.HostError) as caught:
            value.apply({"session_id": "ses_test", "command_id": "new"})
        self.assertEqual(caught.exception.code, "busy")

    def test_unknown_method_and_stale_handle(self):
        host = module.Host.__new__(module.Host)
        host.state = state()
        host.ui = types.SimpleNamespace(run=lambda callback: callback())
        with self.assertRaises(module.HostError):
            host.dispatch("context", {"script_handle": "wrong"})
        with self.assertRaises(module.HostError):
            host.dispatch("execute_code", {"script_handle": host.state.handle})
        host.state._closed = True
        with self.assertRaises(module.HostError):
            host.dispatch("hello", {})


if "--serve" in sys.argv:
    host = module.Host.__new__(module.Host)
    host.state = state()
    host.ui = types.SimpleNamespace(run=lambda callback: callback())
    server = module.HostServer(host)
    # Private pipe to the test process, never logged by the integration.
    print(json.dumps(server.info()), flush=True)
    sys.stdin.readline()
    server.close()
else:
    unittest.main()
