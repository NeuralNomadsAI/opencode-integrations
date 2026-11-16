# Gaffer qualification

All checks use private HOME, USERPROFILE, XDG, TEMP and npm cache directories. Never attach the user's existing scripts, contact a shared OpenCode daemon or install into their Gaffer startup folder.

The normal suite tests the plugin, the panel's sidecar and the real `HostServer` under stub Gaffer modules. With `GAFFER_TEST_BIN` (Gaffer's launcher), `python/real_host.py` runs OpenCode Link in a real Gaffer with its event loop offscreen: the typed apply, each script's offer and label, `hello` and `status` off the main thread, `execute_code` with its undo step, rollback, hints and quarantine release, and the busy answer within 10 s while another request runs; `programs_gaffer.py` runs the plugin's fixed programs (inspect, verify, saved verify, save, render). The portable Gaffer 1.7.6.0 Windows archive runs without a system installation.

A windowed check runs Gaffer on a desktop the user never sees, with a private profile whose only OpenCode service is a fake one that records the offers: Gaffer offers its script at startup without any panel. Run the panel manually against a private OpenCode service to check streaming, model selection, cancellation, permission answers and Connect after detach.
