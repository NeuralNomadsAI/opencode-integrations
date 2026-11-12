# SPDX-License-Identifier: GPL-3.0-or-later

"""Model import (glTF/GLB, FBX, OBJ, USD) from a local file or a bounded HTTPS download, as a task.

A local file is imported at once; a URL is downloaded on the task's thread (HTTPS only, a size limit, a
timeout, archives unpacked without leaving their folder), then imported on Blender's main thread. The task
reports progress, the error that stopped it, and the created datablocks as references; the import is one
undo step and marks the file as modified.
"""

import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import bpy

from .. import tasks as tasks_module
from ..tasks import TaskFailure
from .. import limits, resources

FORMATS = {
    ".glb": "gltf",
    ".gltf": "gltf",
    ".fbx": "fbx",
    ".obj": "obj",
    ".usd": "usd",
    ".usda": "usd",
    ".usdc": "usd",
    ".usdz": "usd",
}
TIMEOUT = limits.value("download_timeout_s")
CHUNK = limits.value("download_chunk_bytes")
DEFAULT_MAX_MB = limits.value("download_default_mb")
USER_AGENT = "OpenCode-Link"


def _format_of(path, wanted=None):
    if wanted:
        if wanted not in set(FORMATS.values()):
            raise ValueError("format must be one of gltf, fbx, obj, usd")
        return wanted
    kind = FORMATS.get(os.path.splitext(path)[1].lower())
    if kind is None:
        raise ValueError("Unknown model format %r: use .glb, .gltf, .fbx, .obj or .usd/.usda/.usdc/.usdz" % os.path.basename(path))
    return kind


def _public(url):
    """A URL without its query string (signed download links carry credentials there)."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _run_import(kind, path):
    """Main thread: one importer operator."""
    if kind == "gltf":
        result = bpy.ops.import_scene.gltf(filepath=path)
    elif kind == "fbx":
        result = bpy.ops.import_scene.fbx(filepath=path)
    elif kind == "obj":
        result = bpy.ops.wm.obj_import(filepath=path)
    else:
        result = bpy.ops.wm.usd_import(filepath=path)
    if "FINISHED" not in result:
        raise RuntimeError("The %s importer did not finish (%s)" % (kind, ", ".join(sorted(result))))


def request_json(url, headers=None):
    request = urllib.request.Request(url, headers=dict(headers or {}, **{"User-Agent": USER_AGENT}))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode("utf-8") or "{}")
        except ValueError:
            body = {}
        return error.code, body
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise TaskFailure("%s could not be reached: %s" % (urllib.parse.urlsplit(url).netloc, getattr(error, "reason", error))) from None


def download(task, url, folder, max_bytes, headers=None, name=None):
    """The task's thread: a bounded HTTPS download into `folder`, with progress on the task."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise TaskFailure("Only https:// URLs are downloaded.")
    request = urllib.request.Request(url, headers=dict(headers or {}, **{"User-Agent": USER_AGENT}))
    task.update("Connecting to %s" % urllib.parse.urlsplit(url).netloc)
    try:
        response = urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        raise TaskFailure("Download refused: HTTP %d %s (%s)" % (error.code, error.reason, _public(url))) from None
    except (urllib.error.URLError, OSError) as error:
        raise TaskFailure("Download failed: %s (%s)" % (getattr(error, "reason", error), _public(url))) from None
    with response:
        total = int(response.headers.get("Content-Length") or 0) or None
        if total and total > max_bytes:
            raise TaskFailure("The file is %d MB, above the limit of %d MB." % (total >> 20, max_bytes >> 20))
        if total:
            try:
                resources.check("This download", disk_bytes=total, disk_path=folder)
            except ValueError as error:
                raise TaskFailure(str(error)) from None
        filename = name or os.path.basename(urllib.parse.urlsplit(response.geturl()).path) or "download"
        target = os.path.join(folder, os.path.basename(filename))
        done = 0
        with open(target, "wb") as handle:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > max_bytes:
                    raise TaskFailure("The download passed the limit of %d MB." % (max_bytes >> 20))
                handle.write(chunk)
                task.update("Downloading", {"bytes": done, "total": total,
                                            "percent": round(100.0 * done / total, 1) if total else None})
    return target


def unpack(task, path, folder):
    """Unpacks a .zip in `folder` (never outside it) and returns the model file inside."""
    if not path.lower().endswith(".zip"):
        return path
    task.update("Unpacking")
    root = os.path.realpath(os.path.join(folder, "unpacked"))
    with zipfile.ZipFile(path) as archive:
        try:
            # The archive's directory gives the exact size it unpacks to.
            resources.check("Unpacking %s" % os.path.basename(path),
                            disk_bytes=sum(member.file_size for member in archive.infolist()), disk_path=folder)
        except ValueError as error:
            raise TaskFailure(str(error)) from None
        for member in archive.infolist():
            target = os.path.realpath(os.path.join(root, member.filename))
            if not target.startswith(root + os.sep) and target != root:
                raise TaskFailure("The archive has a path outside its folder: %s" % member.filename[:80])
        archive.extractall(root)
    found = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            if os.path.splitext(name)[1].lower() in FORMATS:
                found.append(os.path.join(base, name))
    if not found:
        raise TaskFailure("The archive holds no .glb, .gltf, .fbx, .obj or .usd file.")
    found.sort(key=lambda p: (os.path.basename(p) != "scene.gltf", p.count(os.sep), p))
    return found[0]


def import_downloaded(task, tasks, kind, path, title, after=None):
    """The task's thread: imports `path` on the main thread as one undo step; returns the created datablocks."""
    task.update("Importing")

    def run():
        before = tasks.names()
        with tasks.app.quiet():
            tasks.app.with_window(lambda: _run_import(kind, path))
            new_objects = [obj for obj in bpy.data.objects if obj.name not in before["objects"]]
            if after is not None:
                after(new_objects)
        created = tasks.finish_change(task, before, "import %s" % title)
        return {"objects": sum(1 for entry in created if entry["kind"] == "object"), "file": os.path.basename(path),
                "resources": resources.snapshot()}

    result = tasks.on_main(run)
    task.update("Imported %s" % title)
    return result


def _folder(task):
    base = bpy.app.tempdir or tempfile.gettempdir()
    folder = os.path.join(base, "opencode-link", task.id)
    os.makedirs(folder, exist_ok=True)
    return folder


def import_file(source, format=None, max_mb=DEFAULT_MAX_MB):
    """Import a model from a local file (at once) or an https:// URL (a task to follow with blender_tasks)."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required: a file path or an https:// URL")
    source = source.strip()
    tasks = tasks_module.current()
    max_bytes = int(max(1, int(max_mb))) << 20  # also checked against the measured free disk space
    parts = urllib.parse.urlsplit(source)
    if parts.scheme in ("http", "https", "ftp"):
        if parts.scheme != "https":
            raise ValueError("Only https:// URLs are downloaded.")
        title = os.path.basename(parts.path) or parts.netloc

        def run(task, tasks):
            folder = _folder(task)
            try:
                path = unpack(task, download(task, source, folder, max_bytes), folder)
                return import_downloaded(task, tasks, _format_of(path, format), path, title)
            finally:
                shutil.rmtree(folder, ignore_errors=True)

        return {"task": tasks.start("import", title, run).snapshot()}
    path = os.path.abspath(bpy.path.abspath(source))
    if not os.path.isfile(path):
        raise ValueError("File not found: %s" % os.path.basename(path))
    kind = _format_of(path, format)
    task = tasks_module.Task("import", os.path.basename(path))
    tasks.adopt(task)
    try:
        before = tasks.names()
        tasks.app.with_window(lambda: _run_import(kind, path))
        # The apply bumps the revision once for this side effect.
        tasks.finish_change(task, before, "import %s" % os.path.basename(path), bump=False)
        task.state, task.message = "done", "Imported %s" % os.path.basename(path)
        task.result = {"objects": sum(1 for entry in task.created if entry["kind"] == "object"), "file": os.path.basename(path)}
    except Exception as error:  # noqa: BLE001 - reported on the task and as a failed operation
        task.state, task.message = "failed", "%s: %s" % (type(error).__name__, error)
        task.ended = time.time()
        task.done.set()
        raise
    task.ended = time.time()
    task.done.set()
    return {"task": task.snapshot()}
