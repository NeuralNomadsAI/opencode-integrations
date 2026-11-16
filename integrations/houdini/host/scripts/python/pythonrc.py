# OpenCode Link: offers this Houdini to OpenCode once its interface runs (opencode_houdini.start_with_houdini).
# Houdini runs every pythonrc.py found on HOUDINI_PATH; this one never fails Houdini's startup.
try:
    import opencode_houdini

    opencode_houdini.start_with_houdini()
except Exception as error:  # noqa: BLE001
    print("OpenCode Link did not start: %s" % error)
