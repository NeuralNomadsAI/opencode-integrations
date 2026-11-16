"""execute_code hints (host/scripts/opencode_houdini/hints.py) under system Python with a stub hou; no Houdini."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

FILE = Path(__file__).resolve().parents[1] / "host" / "scripts" / "opencode_houdini" / "hints.py"


def load(name):
    spec = importlib.util.spec_from_file_location(name, FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub_hou():
    hou = types.ModuleType("hou")

    class OperationFailed(Exception):
        __module__ = "hou"

    class Error(Exception):
        __module__ = "hou"

    class NodeType:
        __module__ = "hou"

        def __init__(self, name, description):
            self._name, self._description = name, description

        def name(self):
            return self._name

        def description(self):
            return self._description

        def nameWithCategory(self):
            return "Sop/" + self._name

    class Category:
        __module__ = "hou"

        def __init__(self, name, types_):
            self._name, self._types = name, {t: NodeType(t, d) for t, d in types_.items()}

        def name(self):
            return self._name

        def nodeTypes(self):
            return self._types

    sop = Category("Sop", {"box": "Box", "sphere": "Sphere", "skybox": "Sky Box", "attribwrangle": "Attribute Wrangle"})
    obj = Category("Object", {"geo": "Geometry", "cam": "Camera"})

    class Parm:
        __module__ = "hou"

        def __init__(self, name):
            self._name = name

        def name(self):
            return self._name

    class OpNode:
        __module__ = "hou"
        all = {}

        def __init__(self, path, category=None, parms=(), tuples=None, kind="geo", errors=()):
            self._path, self._category, self._kind, self._errors = path, category, kind, list(errors)
            self._parms = [Parm(p) for p in parms]
            self._tuples = tuples or {}
            OpNode.all[path] = self

        def path(self):
            return self._path

        def name(self):
            return self._path.rsplit("/", 1)[-1]

        def parent(self):
            return OpNode.all.get(self._path.rsplit("/", 1)[0] or "/")

        def children(self):
            return [n for p, n in OpNode.all.items() if p.rsplit("/", 1)[0] == self._path and p != self._path]

        def childTypeCategory(self):
            return self._category

        def type(self):
            return NodeType(self._kind, self._kind)

        def parm(self, name):
            return next((p for p in self._parms if p.name() == name), None)

        def parms(self):
            return list(self._parms)

        def parmTuple(self, name):
            return [Parm(p) for p in self._tuples[name]] if name in self._tuples else None

        def parmTuples(self):
            return [Parm(t) for t in self._tuples]

        def node(self, rel):
            return OpNode.all.get(self._path + "/" + rel)

        def errors(self):
            return self._errors

        def warnings(self):
            return []

        def inputAncestors(self):
            return []

        def createNode(self, node_type_name, node_name=None):
            """createNode(self, node_type_name, node_name=None, run_init_scripts=True) -> hou.Node

            Create a new node of type node_type_name as a child of this node.
            """
            raise OperationFailed("The attempted operation failed.\nInvalid node type name")

        def cook(self, force=False):
            if self._errors:
                raise OperationFailed("The attempted operation failed.\nError while cooking.")

    hou.OperationFailed, hou.Error, hou.OpNode, hou.Node = OperationFailed, Error, OpNode, OpNode
    OpNode("/", None, kind="root")
    OpNode("/obj", obj, kind="obj")
    OpNode("/obj/geo1", sop)
    OpNode("/obj/geo1/box1", None, parms=("type", "sizex", "sizey", "sizez", "scale"), tuples={"size": ("sizex", "sizey", "sizez")}, kind="box")
    OpNode("/obj/geo1/broken", None, kind="attribwrangle", errors=["Call to undefined function 'undefined_fn'. (1,9:20)"])
    hou.node = lambda path: OpNode.all.get(path)
    hou.nodeTypeCategories = lambda: {"Sop": sop, "Object": obj}
    hou.nodee = None
    del hou.nodee
    return hou


class NoHou(unittest.TestCase):
    def test_imports_and_explains_nothing_without_hou(self):
        saved = sys.modules.pop("hou", None)
        try:
            sys.modules["hou"] = None  # import hou raises ImportError
            module = load("hints_without_hou")
            self.assertIsNone(module.explain(ValueError("x"), "x", None))
            self.assertEqual(module.closest("boxx", ["box", "skybox", "sphere"]), ["box", "skybox"])
        finally:
            sys.modules.pop("hou", None)
            if saved is not None:
                sys.modules["hou"] = saved


class Hints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hou = stub_hou()
        sys.modules["hou"] = cls.hou
        cls.hints = load("hints_stub")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("hou", None)

    def explain(self, code):
        namespace = {"hou": self.hou}
        try:
            exec(compile(code, "<opencode execute_code>", "exec"), namespace)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            return self.hints.explain(exc, code, None)
        self.fail("the code did not fail")

    def test_closest(self):
        self.assertEqual(self.hints.closest("SIZEXX", ["sizex", "sizey", "scale"])[0], "sizex")
        self.assertEqual(self.hints.closest("", ["a"]), [])

    def test_unknown_node_type(self):
        hint = self.explain("geo = hou.node('/obj/geo1')\ngeo.createNode('boxx')\n")
        self.assertIn("/obj/geo1 (Sop network) has no node type 'boxx'", hint)
        self.assertIn("box", hint)

    def test_type_of_another_category(self):
        hint = self.explain("hou.node('/obj').createNode('box')\n")
        self.assertIn("'box' is a type of Sop", hint)
        self.assertIn("createNode('geo')", hint)

    def test_parm_none(self):
        hint = self.explain("box = hou.node('/obj/geo1/box1')\nbox.parm('sizexx').set(2)\n")
        self.assertIn("/obj/geo1/box1 has no parameter 'sizexx'", hint)
        self.assertIn("closest sizex", hint)

    def test_parm_is_a_tuple(self):
        hint = self.explain("hou.node('/obj/geo1/box1').parm('size').set(2)\n")
        self.assertIn("use parmTuple('size')", hint)

    def test_missing_node_path(self):
        hint = self.explain("hou.node('/obj/geo1/boxx').parm('sizex').set(2)\n")
        self.assertIn("/obj/geo1 has no child 'boxx'", hint)
        self.assertIn("box1", hint)

    def test_unsafe_receiver_is_not_evaluated(self):
        calls = []
        self.hou.spy = lambda: calls.append(1) or None
        hint = self.explain("hou.spy().parm('x').set(1)\n")
        self.assertEqual(calls, [1])  # only the run itself called it
        self.assertIsNone(hint)

    def test_cook_error(self):
        hint = self.explain("hou.node('/obj/geo1/broken').cook(force=True)\n")
        self.assertIn("undefined_fn", hint)

    def test_attribute_on_hou_object(self):
        hint = self.explain("hou.node('/obj/geo1/box1').parmm('sizex')\n")
        self.assertIn("closest parm", hint)
        hint = self.explain("hou.nodee('/obj')\n")
        self.assertIn("node", hint)

    def test_swig_type_error(self):
        hint = self.hints.explain(TypeError("in method 'OpNode_createNode', argument 2 of type 'char const *'"), "", None)
        self.assertIn("createNode(self, node_type_name", hint)

    def test_bounded(self):
        many = ["p%d" % i for i in range(500)]
        self.hou.OpNode("/obj/geo1/big", None, parms=many, kind="big")
        hint = self.explain("hou.node('/obj/geo1/big').parm('zz').set(1)\n")
        self.assertLessEqual(len(hint), self.hints.MAX_CHARS)
        self.assertIn("more)", hint)


if __name__ == "__main__":
    unittest.main(verbosity=2)
