# SPDX-License-Identifier: GPL-3.0-or-later

"""Measured resources: the computer's memory, this Blender's own, the GPUs' and free disk space, each with
the source of its measurement.

- System RAM: GlobalMemoryStatusEx (Windows), /proc/meminfo MemAvailable (Linux), sysctl hw.memsize and
  vm_stat free + inactive + speculative pages (macOS).
- This Blender: K32GetProcessMemoryInfo working set and private bytes (Windows), /proc/self/status VmRSS
  and VmHWM (Linux), getrusage peak resident size (macOS).
- GPU memory (VRAM): the NVIDIA driver's own query, nvidia-smi (Cycles GPU rendering uses it); other GPUs
  are reported as not measured (Blender's Python API exposes no GPU memory). The Cycles devices the
  preferences select are listed next to it.
- Disk: shutil.disk_usage of the folder written to.

Heavy operations report these in their results and refuse only when an estimated need, times the
provisional `memory_margin`, exceeds what is measured as available, naming the measurement.
"""

import os
import shutil
import subprocess
import sys

from . import limits

MB = 1024 * 1024


def _mb(value):
    return round(value / MB, 1)


def _windows():
    import ctypes
    from ctypes import wintypes

    class Status(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    status = Status()
    status.dwLength = ctypes.sizeof(Status)
    system = None
    if kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        system = {"total_mb": _mb(status.ullTotalPhys), "available_mb": _mb(status.ullAvailPhys),
                  "source": "GlobalMemoryStatusEx"}
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    process = None
    if kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        process = {"resident_mb": _mb(counters.WorkingSetSize), "private_mb": _mb(counters.PrivateUsage),
                   "peak_resident_mb": _mb(counters.PeakWorkingSetSize), "source": "K32GetProcessMemoryInfo"}
    return system, process


def _linux():
    def fields(path):
        out = {}
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                name, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    out[name] = int(parts[0]) * 1024  # kB
        return out

    info, own = fields("/proc/meminfo"), fields("/proc/self/status")
    system = {"total_mb": _mb(info["MemTotal"]), "available_mb": _mb(info.get("MemAvailable", info.get("MemFree", 0))),
              "source": "/proc/meminfo MemAvailable"}
    process = {"resident_mb": _mb(own.get("VmRSS", 0)), "peak_resident_mb": _mb(own.get("VmHWM", 0)),
               "source": "/proc/self/status VmRSS"}
    return system, process


def _run(command):
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: no console for the query
    return subprocess.run(command, capture_output=True, text=True, timeout=limits.value("gpu_query_timeout_s"),
                          creationflags=flags, stdin=subprocess.DEVNULL).stdout


def _mac():
    import resource

    total = int(_run(["sysctl", "-n", "hw.memsize"]).strip() or 0)
    pages, size = {}, 4096
    for line in _run(["vm_stat"]).splitlines():
        if "page size of" in line:
            size = int(line.split("page size of")[1].split()[0])
        name, _, rest = line.partition(":")
        if rest.strip().rstrip(".").isdigit():
            pages[name.strip()] = int(rest.strip().rstrip("."))
    free = sum(pages.get(name, 0) for name in ("Pages free", "Pages inactive", "Pages speculative")) * size
    system = {"total_mb": _mb(total), "available_mb": _mb(free), "source": "sysctl hw.memsize, vm_stat free+inactive+speculative"}
    process = {"peak_resident_mb": _mb(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss), "source": "getrusage ru_maxrss (peak)"}
    return system, process


def memory():
    """(system, process) memory, or None each when it cannot be measured here."""
    try:
        if sys.platform == "win32":
            return _windows()
        if sys.platform.startswith("linux"):
            return _linux()
        if sys.platform == "darwin":
            return _mac()
    except Exception:  # noqa: BLE001 - an unmeasured resource is reported as such
        pass
    return None, None


def _nvidia_smi():
    found = shutil.which("nvidia-smi")
    if found is None and sys.platform == "win32":
        candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
        found = candidate if os.path.isfile(candidate) else None
    return found


def gpus():
    """(GPUs with total/used/free MB, source)."""
    tool = _nvidia_smi()
    if tool is None:
        return [], "not measured: no NVIDIA driver query (nvidia-smi) on this computer; Blender exposes no GPU memory"
    try:
        out = _run([tool, "--query-gpu=index,name,memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"])
    except Exception as error:  # noqa: BLE001
        return [], "not measured: nvidia-smi failed (%s)" % type(error).__name__
    found = []
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 5 and all(part.replace(".", "").isdigit() for part in parts[2:]):
            found.append({"index": int(parts[0]), "name": parts[1], "total_mb": float(parts[2]), "used_mb": float(parts[3]),
                          "free_mb": float(parts[4])})
    return found, "nvidia-smi (NVIDIA driver)"


def cycles_devices():
    """The compute devices Cycles' preferences select (no device enumeration is started)."""
    try:
        import bpy

        addon = bpy.context.preferences.addons.get("cycles")
        if addon is None:
            return None
        prefs = addon.preferences
        return {"compute_device_type": prefs.compute_device_type,
                "devices": [{"name": d.name, "type": d.type, "use": bool(d.use)} for d in prefs.devices]}
    except Exception:  # noqa: BLE001
        return None


def disk(path):
    try:
        folder = path if os.path.isdir(path) else os.path.dirname(path) or "."
        usage = shutil.disk_usage(folder)
        return {"free_mb": _mb(usage.free), "total_mb": _mb(usage.total), "source": "shutil.disk_usage"}
    except OSError:
        return None


def snapshot(gpu=True):
    system, process = memory()
    out = {"system": system or {"source": "not measured on %s" % sys.platform}, "blender": process or {"source": "not measured"}}
    if gpu:
        found, source = gpus()
        out["gpus"] = {"devices": found, "source": source}
        devices = cycles_devices()
        if devices is not None:
            out["cycles"] = devices
    return out


def check(what, ram_bytes=0, vram_bytes=0, disk_bytes=0, disk_path=None):
    """Refuses when an estimated need (times memory_margin) exceeds the measured availability; returns the
    measurements (reported in results)."""
    margin = limits.value("memory_margin")
    found = snapshot(gpu=vram_bytes > 0)
    system = found["system"]
    if ram_bytes and "available_mb" in system and ram_bytes * margin > system["available_mb"] * MB:
        raise ValueError("%s needs about %.0f MB of memory; %.0f MB is available (%s)." % (
            what, ram_bytes * margin / MB, system["available_mb"], system["source"]))
    devices = found.get("gpus", {}).get("devices") or []
    if vram_bytes and devices and vram_bytes * margin > max(d["free_mb"] for d in devices) * MB:
        best = max(devices, key=lambda d: d["free_mb"])
        raise ValueError("%s needs about %.0f MB of GPU memory; %s has %.0f MB free (%s)." % (
            what, vram_bytes * margin / MB, best["name"], best["free_mb"], found["gpus"]["source"]))
    if disk_bytes and disk_path:
        space = disk(disk_path)
        if space is not None:
            found["disk"] = space
            if disk_bytes * margin > space["free_mb"] * MB:
                raise ValueError("%s needs about %.0f MB of disk space; %.0f MB is free (%s of %s)." % (
                    what, disk_bytes * margin / MB, space["free_mb"], space["source"], disk_path))
    found["estimated"] = {key: _mb(value) for key, value in (("ram_mb", ram_bytes), ("vram_mb", vram_bytes),
                                                            ("disk_mb", disk_bytes)) if value}
    return found
