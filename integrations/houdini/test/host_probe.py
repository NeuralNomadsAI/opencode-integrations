"""The integration's own host transport under system Python; no Houdini or licence required."""
import collections
import contextlib
import importlib
import json
import threading
import time
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
hou.ObjectWasDeleted = type("ObjectWasDeleted", (Exception,), {})
hou.getenv = lambda name, default=None: default


# A scene of nodes and Houdini's grouped undo, simulated: enough for execute_code's undo group, its rollback
# and the structural check of a restoration.
class _Node:
    def __init__(self, name):
        self._path = "/obj/" + name

    def path(self):
        return self._path

    def type(self):
        return types.SimpleNamespace(nameWithCategory=lambda: "Object/geo")

    def inputs(self):
        return []


class _Undo:
    def __init__(self):
        self.labels, self.stack, self.open, self.broken = [], [], None, False

    @contextlib.contextmanager
    def group(self, label):
        self.open = []
        try:
            yield
        finally:
            actions, self.open = self.open, None
            if actions:
                self.labels.append(label)
                self.stack.append(actions)

    def undoLabels(self):
        return list(self.labels)

    def performUndo(self):
        if self.broken:
            raise RuntimeError("undo failed")
        self.labels.pop()
        for action in reversed(self.stack.pop()):
            action()


SCENE = []


def add_node(name):
    node = _Node(name)
    SCENE.append(node)
    if hou.undos.open is not None:
        hou.undos.open.append(lambda: SCENE.remove(node))
    return node


class _Root:
    def allSubChildren(self, **kwargs):
        return list(SCENE)

    def setUserData(self, *args):
        pass

    def destroyUserData(self, *args):
        pass


hou.undos = _Undo()
hou.add_node = add_node
sys.modules["hou"] = hou
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host" / "scripts"))
module = importlib.import_module("opencode_houdini.host")
runner = importlib.import_module("opencode_houdini.runner")


def state():
    value = module.ScriptState.__new__(module.ScriptState)
    value.handle = "hip:test-document"
    value.epoch = "test-epoch"
    value.revision = 1
    value.flush = lambda **kwargs: None
    value.summary = lambda: {}
    value.quarantined = False
    value.quarantine = None
    value.root = _Root()
    value._summary_dirty = False
    value._reconcile_after_rollback = lambda: None
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

    def test_refusals_keep_message_data_and_code(self):
        refusal = module.wire_error(module.HostError("busy", "Too many commands", {"limit": 256}))
        self.assertEqual(refusal, {"code": "busy", "message": "Too many commands", "data": {"limit": 256}})
        refusal = module.wire_error(module.HostError("unknown_ref", "Unknown ref: /obj/geo1", {"ref": object()}))
        self.assertEqual(refusal["code"], "invalid_args")
        self.assertEqual(refusal["message"], "Unknown ref: /obj/geo1")
        self.assertEqual(refusal["data"]["reason"], "unknown_ref")
        self.assertIsInstance(refusal["data"]["ref"], str)
        json.dumps(refusal, allow_nan=False)
        refusal = module.wire_error(module.HostError("expectation_failed", "x" * 5000, {"observed": "y" * 100000}))
        self.assertEqual(len(refusal["message"]), module.WIRE_MESSAGE_MAX)
        self.assertEqual(refusal["data"], {"reason": "expectation_failed", "truncated": True})

    def execute(self, value, code, **params):
        host = types.SimpleNamespace(saves=0, loads=0, state=value)
        return runner.execute_code(host, value, dict(params, code=code), lambda error, code, filename: "hint: " + str(error))

    def setUp(self):
        SCENE.clear()
        hou.undos = _Undo()

    def test_execute_code_groups_a_change_and_undoes_a_failure(self):
        value = state()
        out = self.execute(value, "hou.add_node('a')\nresult = {'nodes': 1}")
        self.assertEqual((out["status"], out["result"], out["changed"], out["undo_step"]), ("executed", {"nodes": 1}, True, "OpenCode: code"))
        self.assertEqual(hou.undos.labels, ["OpenCode: code"])
        out = self.execute(value, "hou.add_node('b')\nhou.add_node('c')\nraise ValueError('nope')")
        self.assertEqual((out["status"], out["restored"], out["hint"]), ("failed", True, "hint: nope"))
        self.assertIn("ValueError: nope", out["message"])
        self.assertEqual([node.path() for node in SCENE], ["/obj/a"])
        self.assertFalse(value.quarantined)

    def test_unproven_restoration_quarantines_until_released_with_the_read_revision(self):
        value = state()
        hou.undos.broken = True
        out = self.execute(value, "hou.add_node('a')\nraise RuntimeError('boom')")
        self.assertEqual((out["status"], out["restored"]), ("failed", False))
        self.assertTrue(value.quarantined)
        self.assertIn("failed code", out["quarantine"]["cause"])
        with self.assertRaises(module.HostError) as caught:
            self.execute(value, "hou.add_node('b')")
        self.assertEqual(module.wire_error(caught.exception)["code"], "quarantined")
        self.assertEqual(self.execute(value, "result = {'read': True}", read_only=True)["status"], "executed")
        with self.assertRaises(module.HostError) as caught:
            runner.release(value, {"revision": value.revision + 1})
        self.assertEqual(module.wire_error(caught.exception)["code"], "stale_revision")
        self.assertEqual(runner.release(value, {"revision": value.revision})["released"], True)
        self.assertFalse(value.quarantined)

    def test_read_only_runs_make_no_undo_group(self):
        value = state()
        out = self.execute(value, "print('hello')\nresult = {'x': 1}", read_only=True)
        self.assertEqual((out["stdout"], out["changed"], hou.undos.labels), ("hello\n", False, []))

    def test_status_answers_off_the_main_thread_during_a_long_call(self):
        host = module.Host.__new__(module.Host)
        host.state = state()
        host.ui = module.UIThreadGate()
        host.saves = host.loads = 0
        # A request from a connection's thread runs on the main thread (here, pumped as hython does).
        thread = threading.Thread(
            target=host.dispatch,
            args=("execute_code", {"script_handle": host.state.handle, "code": "import time\ntime.sleep(1.5)", "read_only": True}),
        )
        seen = []
        asker = threading.Thread(target=lambda: (time.sleep(0.5), seen.append(host.dispatch("status", {}))))
        thread.start()
        asker.start()
        host.ui.pump(3.0, stop=lambda: not thread.is_alive() and not asker.is_alive())
        thread.join()
        asker.join()
        status = seen[0]
        self.assertEqual((status["busy"], status["running"]), (True, "execute_code"))
        self.assertEqual(host.dispatch("status", {})["busy"], False)

    def test_hello_names_the_process_and_its_crash_folder(self):
        self.assertTrue(module.temp_dir().endswith("houdini_temp"))
        self.assertEqual(module.offer_label("shot/v2.hipnc"), "Houdini: shot v2.hipnc")
        self.assertIn("status", module.METHODS)
        self.assertIn("execute_code", module.METHODS)

    def test_unknown_method_and_stale_handle(self):
        host = module.Host.__new__(module.Host)
        host.state = state()
        host.ui = module.UIThreadGate()
        host.saves = host.loads = 0
        with self.assertRaises(module.HostError):
            host.dispatch("context", {"script_handle": "wrong"})
        with self.assertRaises(module.HostError):
            host.dispatch("no_such_method", {"script_handle": host.state.handle})
        host.state._closed = True
        with self.assertRaises(module.HostError):
            host.dispatch("hello", {})


if "--serve" in sys.argv:
    host = module.Host.__new__(module.Host)
    host.state = state()
    host.ui = module.UIThreadGate()
    host.saves = host.loads = 0
    server = module.HostServer(host)
    # Private pipe to the test process, never logged by the integration.
    print(json.dumps(server.info()), flush=True)
    # The main thread serves the requests (as hython's main thread does) until the test says stop.
    stopping = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.readline(), stopping.set()), daemon=True).start()
    while not stopping.is_set():
        host.ui.pump(0.2)
    server.close()
else:
    unittest.main()
