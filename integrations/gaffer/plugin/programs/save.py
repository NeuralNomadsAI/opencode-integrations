# SPDX-License-Identifier: MIT
# gaffer_save (fixed program): save the open script, or save it under a path (as the open file: its fileName
# changes, or as a copy written with serialiseToFile that leaves the open file's name and its own file alone). Says
# what was really written (path, bytes, time), read from the disk.
# PARAMS: {"path"?, "copy"?, "overwrite"?}
# The namespace holds Gaffer, script and application.
import os
import time

P = PARAMS  # noqa: F821
S = script  # noqa: F821
EXTENSIONS = (".gfr",)
current = S["fileName"].getValue()
never_saved = not current
target = P.get("path")
copy = bool(P.get("copy"))

if copy and not target:
    raise ValueError("copy: give path, the file the copy goes to")
if target:
    if not os.path.isabs(target):
        if never_saved:
            raise ValueError("path %r is relative and the script was never saved: give an absolute path" % target)
        target = os.path.join(os.path.dirname(current), target)
    target = os.path.normpath(target)
    if os.path.splitext(target)[1].lower() not in EXTENSIONS:
        raise ValueError("path must end with .gfr")
    same = bool(current) and os.path.normcase(target) == os.path.normcase(os.path.normpath(current))
    if os.path.exists(target) and not same and not P.get("overwrite"):
        raise ValueError("%s exists: pass overwrite true or name another file" % target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
elif never_saved:
    raise ValueError("the script was never saved: pass path")

written = os.path.normpath(target or current)
before = os.stat(written).st_mtime_ns if os.path.exists(written) else None
if copy:
    S.serialiseToFile(written)
else:
    if target:
        # Gaffer keeps file names with forward slashes.
        S["fileName"].setValue(written.replace("\\", "/"))
    S.save()
if not os.path.isfile(written):
    raise RuntimeError("nothing was written at %s" % written)
stat = os.stat(written)
if before is not None and stat.st_mtime_ns == before:
    raise RuntimeError("%s was not rewritten (its modification time did not change)" % written)
result = {"saved": written, "bytes": stat.st_size,
          "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
          "open_file": os.path.normpath(S["fileName"].getValue()) if S["fileName"].getValue() else None,
          "unsaved_changes": S["unsavedChanges"].getValue()}
if copy:
    result["copy"] = True
    result["note"] = "a copy: the open script keeps its name, and its own file is unchanged"
