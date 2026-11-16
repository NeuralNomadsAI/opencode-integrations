# SPDX-License-Identifier: MIT
# gaffer_verify with saved (fixed program): the same verification, read from the file as written on disk instead
# of the open script. This program only starts it and returns at once: a background `gaffer python`, started from
# this Gaffer (its launcher and environment: startup paths, extensions, variables), loads the .gfr into a fresh
# ScriptNode and runs the verify program there (criteria, errors, render). The plugin follows it from its log
# (OPENCODE_STEP lines: what it does now), bounds it with timeout_s and reads its result; Gaffer's main thread is
# never held while it runs. That process never offers itself to OpenCode (OPENCODE_GAFFER_ROLE=verify, no offer
# key) and opens no window.
# PARAMS: the verify parameters with "saved": true (the open file as saved) or a .gfr path; "timeout_s"?.
# JOB: verify.py, prepended by the plugin.
import json
import os
import subprocess
import tempfile
import time

P = PARAMS  # noqa: F821
S = script  # noqa: F821
target = P.get("saved")
current = S["fileName"].getValue()
never_saved = not current or not os.path.isfile(current)
if target is True:
    if never_saved:
        raise ValueError("saved: the open script was never saved, there is no file to read (gaffer_save first)")
    path = current
elif isinstance(target, str) and target:
    base = os.path.dirname(current) if not never_saved else os.getcwd()
    path = os.path.normpath(target if os.path.isabs(target) else os.path.join(base, target))
else:
    raise ValueError("saved: true (the open script as saved) or a .gfr path")
if not os.path.isfile(path):
    raise ValueError("saved: no file %s" % path)

exe = os.path.join(os.environ.get("GAFFER_ROOT") or "", "bin", "gaffer.cmd" if os.name == "nt" else "gaffer")
if not os.path.isfile(exe):
    raise RuntimeError("no Gaffer launcher at %s" % exe)
params = {key: value for key, value in P.items() if key != "saved"}
work = tempfile.mkdtemp(prefix="opencode_verify_saved_")
out_path = os.path.join(work, "result.json")
log_path = os.path.join(work, "verify.log")
job = os.path.join(work, "verify.py")
with open(job, "w", encoding="utf-8") as fh:
    fh.write(
        "import json, os, sys, time, traceback\n"
        "T0 = time.time()\n"
        "def STEP(**what):\n"
        "    print('OPENCODE_STEP ' + json.dumps(dict(what, at_s=round(time.time() - T0, 1)), default=repr), flush=True)\n"
        "STEP(step='load', file=%r)\n"
        "import Gaffer, GafferScene, GafferImage, GafferDispatch, IECore, imath\n"
        "warnings = []\n"
        "try:\n"
        "    script = Gaffer.ScriptNode()\n"
        "    script['fileName'].setValue(%r)\n"
        "    with IECore.CapturingMessageHandler() as captured:\n"
        "        try:\n            script.load(continueOnError=True)\n"
        "        except Exception as ex:\n            warnings.append(str(ex)[:1500])\n"
        "    warnings += [('%%s: %%s' %% (m.context, m.message))[:1500] for m in captured.messages\n"
        "                 if m.level <= IECore.Msg.Level.Warning][:20]\n"
        "    LOAD_S = round(time.time() - T0, 1)\n"
        "    STEP(step='loaded', seconds=LOAD_S)\n"
        % (path, path.replace("\\", "/"))
    )
    fh.write("    scope = {'Gaffer': Gaffer, 'GafferScene': GafferScene, 'GafferImage': GafferImage, 'GafferDispatch': GafferDispatch,\n"
             "             'IECore': IECore, 'imath': imath, 'script': script, 'application': None, '__name__': '__opencode__',\n"
             "             'result': {}, 'STEP': STEP, 'PARAMS': json.loads(%r)}\n" % json.dumps(params))
    fh.write("    exec(compile(%r, '<gaffer_verify>', 'exec'), scope)\n" % JOB)  # noqa: F821 - verify.py, from the plugin
    fh.write("    result = scope['result']\n    result['load_warnings'] = warnings\n    result['load_seconds'] = LOAD_S\n")
    fh.write("except Exception:\n    result = {'error': traceback.format_exc()[-3000:]}\n")
    # Written whole, then renamed: the plugin never reads half a result.
    fh.write("with open(%r, 'w', encoding='utf-8') as _out:\n    json.dump(result, _out, default=repr)\n" % (out_path + ".part"))
    fh.write("os.replace(%r, %r)\nSTEP(step='done')\n" % (out_path + ".part", out_path))
    # Leave at once: the result is written, nothing in the exit handlers can lose it.
    fh.write("sys.stdout.flush()\nos._exit(0)\n")
env = {k: v for k, v in os.environ.items() if k != "OPENCODE_GAFFER_OFFER_KEY"}
env["OPENCODE_GAFFER_ROLE"] = "verify"  # never offered to OpenCode
env["PYTHONUNBUFFERED"] = "1"
with open(log_path, "wb") as log:
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "env": env, "cwd": work}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # no console window
        command = '"%s" /d /s /c ""%s" python "%s""' % (os.environ.get("ComSpec") or "cmd.exe", exe, job)
    else:
        command = [exe, "python", job]
    child = subprocess.Popen(command, **kwargs)
stat = os.stat(path)
result = {"started": {"pid": child.pid, "work": work, "log": log_path, "result": out_path, "file": path,
                      "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                      "bytes": stat.st_size, "timeout_s": int(P.get("timeout_s") or 540)}}
