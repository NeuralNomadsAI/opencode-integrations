# SPDX-License-Identifier: GPL-3.0-or-later
# blender_verify with saved (fixed program): the same verification, read from the file as written on disk instead
# of the open document: a background Blender opens the file and runs the verify program there (criteria, visible,
# render), so what is written can be compared with what the open document shows.
# PARAMS: the verify parameters with "saved": true (the open file) or a .blend path. JOB: verify.py, prepended by
# the plugin.
import json
import os
import shutil
import subprocess
import tempfile
import time

import bpy

P = PARAMS  # noqa: F821
target = P.get("saved")
current = bpy.data.filepath
if target is True:
    if not current:
        raise ValueError("saved: the open file was never saved, there is no file to read (blender_save first)")
    path = current
elif isinstance(target, str) and target:
    path = target if os.path.isabs(target) else os.path.join(os.path.dirname(current) if current else os.getcwd(), target)
    path = os.path.normpath(path)
else:
    raise ValueError("saved: true (the open file as saved) or a .blend path")
if not os.path.isfile(path):
    raise ValueError("saved: no file %s" % path)

params = {key: value for key, value in P.items() if key != "saved"}
work = tempfile.mkdtemp(prefix="opencode_verify_saved_")
try:
    out_path = os.path.join(work, "result.json")
    script = os.path.join(work, "verify.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("import json\nPARAMS = json.loads(%r)\n" % json.dumps(params))
        fh.write(JOB)  # noqa: F821 - verify.py, prepended by the plugin
        fh.write("\nwith open(%r, 'w', encoding='utf-8') as _out:\n    json.dump(result, _out, default=repr)\n" % out_path)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("BLENDER_SYSTEM_") and k != "OPENCODE_LINK_OFFER_KEY"}
    env["OPENCODE_LINK_ROLE"] = "verify"  # never offered to OpenCode
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # no console window
    started = time.time()
    run = subprocess.run([bpy.app.binary_path, "-b", path, "--python-exit-code", "1", "--python", script],
                         timeout=int(P.get("timeout_s") or 540), **kwargs)
    if not os.path.exists(out_path):
        tail = run.stdout.decode("utf-8", "replace")[-1500:] if run.stdout else ""
        raise RuntimeError("the background Blender reading %s gave no result (exit %s):\n%s" % (path, run.returncode, tail))
    with open(out_path, encoding="utf-8") as fh:
        result = json.load(fh)
finally:
    shutil.rmtree(work, ignore_errors=True)
stat = os.stat(path)
result["read"] = {"file": path, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                  "bytes": stat.st_size, "seconds": round(time.time() - started, 1),
                  "by": "a background Blender on the file as written on disk, not the open document"}
