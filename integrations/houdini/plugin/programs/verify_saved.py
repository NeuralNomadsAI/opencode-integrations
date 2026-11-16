# SPDX-License-Identifier: MIT
# houdini_verify with saved (fixed program): the same verification, read from the file as written on disk instead
# of the open scene. This program only starts it and returns at once: a background hython, started from this
# Houdini (its environment: packages, HDA paths, variables), loads the .hip/.hipnc and runs the verify program there
# (criteria, errors, render). The plugin follows it from its log (OPENCODE_STEP lines: what it does now), bounds it
# with timeout_s and reads its result; Houdini's main thread is never held while it runs. That hython never offers
# itself to OpenCode (OPENCODE_HOUDINI_ROLE=verify, no offer key) and opens no window.
# PARAMS: the verify parameters with "saved": true (the open file as saved) or a .hip/.hipnc path; "timeout_s"?.
# JOB: verify.py, prepended by the plugin.
import json
import os
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
out_path = os.path.join(work, "result.json")
log_path = os.path.join(work, "verify.log")
script = os.path.join(work, "verify.py")
with open(script, "w", encoding="utf-8") as fh:
    fh.write(
        "import json, os, sys, time, traceback\nimport hou\n"
        "T0 = time.time()\n"
        "def STEP(**what):\n"
        "    print('OPENCODE_STEP ' + json.dumps(dict(what, at_s=round(time.time() - T0, 1)), default=repr), flush=True)\n"
        "warnings = []\n"
        "STEP(step='load', file=%r)\n"
        "try:\n    hou.hipFile.load(%r, suppress_save_prompt=True, ignore_load_warnings=False)\n"
        "except hou.LoadWarning as w:\n    warnings.append(str(w)[:1500])\n"
        "LOAD_S = round(time.time() - T0, 1)\n"
        "STEP(step='loaded', seconds=LOAD_S)\n"
        % (path, path.replace("\\", "/"))
    )
    fh.write("try:\n    PARAMS = json.loads(%r)\n    result = {}\n" % json.dumps(params))
    fh.write("    exec(compile(%r, '<houdini_verify>', 'exec'))\n" % JOB)  # noqa: F821 - verify.py, from the plugin
    fh.write("    result['load_warnings'] = warnings\n    result['load_seconds'] = LOAD_S\n")
    fh.write("except Exception:\n    result = {'error': traceback.format_exc()[-3000:]}\n")
    # Written whole, then renamed: the plugin never reads half a result.
    fh.write("with open(%r, 'w', encoding='utf-8') as _out:\n    json.dump(result, _out, default=repr)\n" % (out_path + ".part"))
    fh.write("os.replace(%r, %r)\nSTEP(step='done')\n" % (out_path + ".part", out_path))
    # hython may crash in its own exit handlers after renders: the result is written, leave at once.
    fh.write("sys.stdout.flush()\nos._exit(0)\n")
env = {k: v for k, v in os.environ.items() if k != "OPENCODE_HOUDINI_OFFER_KEY"}
env["OPENCODE_HOUDINI_ROLE"] = "verify"  # never offered to OpenCode
with open(log_path, "wb") as log:
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "env": env, "cwd": work}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # no console window
    child = subprocess.Popen([exe, script], **kwargs)
stat = os.stat(path)
result = {"started": {"pid": child.pid, "work": work, "log": log_path, "result": out_path, "file": path,
                      "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                      "bytes": stat.st_size, "timeout_s": int(P.get("timeout_s") or 540)}}
