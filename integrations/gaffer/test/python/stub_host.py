"""Real HostServer and ScriptState.hello, with only the unavailable Gaffer application stubbed."""
import importlib
import json
import pathlib
import sys
import types

for name in ("IECore", "IECoreScene", "Gaffer", "GafferDispatch", "GafferImage", "GafferScene", "GafferUI"):
    sys.modules[name] = types.ModuleType(name)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "host" / "python"))
pkg = types.ModuleType("GafferOpenCode")
pkg.__path__ = [str(pathlib.Path(sys.path[0]) / "GafferOpenCode")]
sys.modules["GafferOpenCode"] = pkg
sys.modules["GafferOpenCode.Recipes"] = types.ModuleType("Recipes")
module = importlib.import_module("GafferOpenCode.Host")
sys.modules["Gaffer"].About = types.SimpleNamespace(versionString=lambda: "1.6.15.0")
state = object.__new__(module.ScriptState)
state.flush = lambda: None
state.recipes = types.SimpleNamespace(nodeTypes=lambda: [], catalogue=lambda: [])
state.handle = "test-script"
state.epoch = "test-epoch"
state.revision = 0
state.script = {"fileName": types.SimpleNamespace(getValue=lambda: "test.gfr")}
state.summary = lambda: {}

class Host:
    def dispatch(self, method, params):
        if method == "hello":
            if params.get("script_handle") != state.handle:
                raise module.HostError("stale_handle", "Select the exact script")
            return state.hello()
        raise module.HostError("method_not_found", "Unknown method")

server = module.HostServer(Host())
print(json.dumps(server.info()), flush=True)
sys.stdin.read()
server.close()
