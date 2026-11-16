# SPDX-License-Identifier: MIT
# houdini_save (fixed program): save the open scene, or save it under a path (as the open file, or as a copy that
# leaves the open file's name alone: hscript mwrite -n). Says what was really written (path, bytes, time), read
# from the disk: hou.hipFile.hasUnsavedChanges() is not consulted (it stays true after a save in hython).
# PARAMS: {"path"?, "copy"?, "overwrite"?}
import os
import time

import hou

P = PARAMS  # noqa: F821
EXTENSIONS = (".hip", ".hipnc", ".hiplc")
current = hou.hipFile.path()
never_saved = hou.hipFile.isNewFile()
target = P.get("path")
copy = bool(P.get("copy"))


def license_name():
    try:
        return str(hou.licenseCategory()).rsplit(".", 1)[-1]
    except Exception:  # noqa: BLE001
        return "unknown"


if copy and not target:
    raise ValueError("copy: give path, the file the copy goes to")
if target:
    if not os.path.isabs(target):
        if never_saved:
            raise ValueError("path %r is relative and the scene was never saved: give an absolute path" % target)
        target = os.path.join(os.path.dirname(current), target)
    target = os.path.normpath(target)
    if os.path.splitext(target)[1].lower() not in EXTENSIONS:
        raise ValueError("path must end with %s" % ", ".join(EXTENSIONS))
    if '"' in target:
        raise ValueError("path must not hold a double quote")
    same = os.path.normcase(target) == os.path.normcase(os.path.normpath(current))
    if os.path.exists(target) and not same and not P.get("overwrite"):
        raise ValueError("%s exists: pass overwrite true or name another file" % target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
elif never_saved:
    raise ValueError("the scene was never saved: pass path")

before = None
written = (target or current).replace("\\", "/")
if os.path.exists(written):
    before = os.stat(written).st_mtime_ns
if copy:
    out, err = hou.hscript('mwrite -n "%s"' % written)
    if err.strip():
        raise RuntimeError("mwrite -n %s: %s" % (written, err.strip()[:300]))
elif target:
    hou.hipFile.save(written)
else:
    hou.hipFile.save()
if not os.path.isfile(written):
    raise RuntimeError("nothing was written at %s" % written)
stat = os.stat(written)
if before is not None and stat.st_mtime_ns == before:
    raise RuntimeError("%s was not rewritten (its modification time did not change)" % written)
result = {"saved": os.path.normpath(written), "bytes": stat.st_size,
          "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
          "open_file": os.path.normpath(hou.hipFile.path()), "license": license_name()}
if copy:
    result["copy"] = True
    result["note"] = "a copy: the open scene keeps its name, and its own file is unchanged"
lic = result["license"]
if lic in ("Apprentice", "ApprenticeHD", "Education") and not written.lower().endswith(".hipnc"):
    result["license_note"] = "%s files are non-commercial whatever their extension; .hipnc names them so" % lic
elif lic == "Indie" and not written.lower().endswith(".hiplc"):
    result["license_note"] = "Indie files are limited-commercial whatever their extension; .hiplc names them so"
