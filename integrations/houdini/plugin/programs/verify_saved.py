# SPDX-License-Identifier: MIT
# houdini_verify with saved (fixed program): the same verification, read from the file as written on disk instead
# of the open scene: a background hython loads the .hip/.hipnc and runs the verify program there (criteria,
# errors, render), so what is delivered can be compared with what the open scene shows. That hython never offers
# itself to OpenCode (OPENCODE_HOUDINI_ROLE=verify, no offer key) and opens no window.
# PARAMS: the verify parameters with "saved": true (the open file as saved) or a .hip/.hipnc path; "timeout_s"?.
# JOB: verify.py, prepended by the plugin.
import json
import os
import shutil
import subprocess
import tempfile
import time

import hou

P = PARAMS  # noqa: F821
target = P.get("saved")
current = hou.hipFile.path()
never_saved = hou.hipFile.isNewFile() or not os.path.isfile(current)
if target is True:
    if never_saved:
        raise ValueError("saved: the open scene was never saved, there is no file to read (houdini_save first)")
    path = current
elif isinstance(target, str) and target:
    base = os.path.dirname(current) if not never_saved else os.getcwd()
    path = os.path.normpath(target if os.path.isabs(target) else os.path.join(base, target))
else:
    raise ValueError("saved: true (the open scene as saved) or a .hip, .hipnc or .hiplc path")
if not os.path.isfile(path):
    raise ValueError("saved: no file %s" % path)

exe = os.path.join(hou.getenv("HFS") or "", "bin", "hython.exe" if os.name == "nt" else "hython")
if not os.path.isfile(exe):
    raise RuntimeError("no hython at %s" % exe)
params = {key: value for key, value in P.items() if key != "saved"}
work = tempfile.mkdtemp(prefix="opencode_verify_saved_")
started = time.time()
try:
    out_path = os.path.join(work, "result.json")
    script = os.path.join(work, "verify.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("import json, os, sys, traceback\nimport hou\n")
        fh.write("warnings = []\ntry:\n    hou.hipFile.load(%r, suppress_save_prompt=True, ignore_load_warnings=False)\n" % path.replace("\\", "/"))
        fh.write("except hou.LoadWarning as w:\n    warnings.append(str(w)[:1500])\n")
        fh.write("try:\n    PARAMS = json.loads(%r)\n    result = {}\n" % json.dumps(params))
        fh.write("    exec(compile(%r, '<houdini_verify>', 'exec'))\n" % JOB)  # noqa: F821 - verify.py, from the plugin
        fh.write("    result['load_warnings'] = warnings\n")
        fh.write("except Exception:\n    result = {'error': traceback.format_exc()[-3000:]}\n")
        fh.write("with open(%r, 'w', encoding='utf-8') as _out:\n    json.dump(result, _out, default=repr)\n" % out_path)
        # hython may crash in its own exit handlers after renders: the result is written, leave at once.
        fh.write("sys.stdout.flush()\nos._exit(0)\n")
    env = {k: v for k, v in os.environ.items() if k != "OPENCODE_HOUDINI_OFFER_KEY"}
    env["OPENCODE_HOUDINI_ROLE"] = "verify"  # never offered to OpenCode
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # no console window
    try:
        run = subprocess.run([exe, script], timeout=int(P.get("timeout_s") or 540), **kwargs)
    except subprocess.TimeoutExpired:
        raise RuntimeError("the background hython reading %s did not finish within %s s (timeout_s)" % (path, P.get("timeout_s") or 540))
    if not os.path.exists(out_path):
        tail = run.stdout.decode("utf-8", "replace")[-1500:] if run.stdout else ""
        raise RuntimeError("the background hython reading %s gave no result (exit %s):\n%s" % (path, run.returncode, tail))
    with open(out_path, encoding="utf-8") as fh:
        result = json.load(fh)
finally:
    shutil.rmtree(work, ignore_errors=True)
if "error" in result:
    raise RuntimeError("verify in the background hython reading %s failed:\n%s" % (path, result["error"]))
stat = os.stat(path)
load_warnings = result.pop("load_warnings", [])
result["read"] = {"file": path, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                  "bytes": stat.st_size, "seconds": round(time.time() - started, 1),
                  "by": "a background hython on the file as written on disk, not the open scene",
                  **({"load_warnings": load_warnings} if load_warnings else {})}
