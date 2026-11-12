"""Serves OpenCode Link from a background Blender for the Node tests.

Enables the installed add-on, which offers the session at once, applies the requested preferences,
then either listens without advertising (--listen-only, prints the endpoint for the conformance
checks) or keeps the offer (--offer). Serves requests on the main thread until the stop file
appears or the time limit passes. --off-after N stops the offer after N seconds.
Usage: blender -b --python serve.py -- [--listen-only|--offer] [--service NAME]
       [--config-dir DIR] [--seconds N] [--off-after N] [--stop-file PATH]
"""

import argparse
import importlib
import json
import os
import sys
import time

import addon_utils
import bpy

MODULE = "bl_ext.user_default.opencode_link"

parser = argparse.ArgumentParser()
parser.add_argument("--listen-only", action="store_true")
parser.add_argument("--offer", action="store_true")
parser.add_argument("--service", default="")
parser.add_argument("--config-dir", default="")
parser.add_argument("--seconds", type=float, default=60.0)
parser.add_argument("--off-after", type=float, default=0.0)
parser.add_argument("--stop-file", default="")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])

addon_utils.enable(MODULE, default_set=True, handle_error=None)
addon = importlib.import_module(MODULE + ".addon")
prefs = addon.preferences()
session = addon.session()
# Enabled, the add-on offers the session at once: each run chooses below, with its own settings.
session.stop()
prefs.service_name = args.service
prefs.config_dir = args.config_dir

cube = bpy.data.objects.new("Cube", bpy.data.meshes.new("Cube"))
bpy.context.scene.collection.objects.link(cube)
bpy.context.view_layer.update()

if args.listen_only:
    from importlib import import_module

    session.listener = import_module(MODULE + ".wire").Listener(session.core.handle)
    print("ENDPOINT " + json.dumps({"port": session.listener.port, "token": session.listener.token,
                                     "script_handle": session.app.script_handle()}), flush=True)
if args.offer:
    session.start(prefs)
    print("OFFERED", flush=True)

start = time.monotonic()
switched_off = False


def stop():
    global switched_off
    elapsed = time.monotonic() - start
    if args.off_after and not switched_off and elapsed >= args.off_after:
        session.stop()
        switched_off = True
        print("CONSENT_OFF", flush=True)
    return bool(args.stop_file) and os.path.exists(args.stop_file)


addon.pump(args.seconds, stop=stop)
if session.advertiser is not None:
    print("ADVERTISE " + json.dumps(session.advertiser.results), flush=True)
if session.listener is not None:
    session.listener.close()
print("SERVED", flush=True)
