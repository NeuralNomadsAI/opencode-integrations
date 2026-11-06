import pathlib
import sys

import GafferUI

extensionRoot = pathlib.Path(__file__).resolve().parents[2]
pythonRoot = str(extensionRoot / "python")
if pythonRoot not in sys.path:
    sys.path.insert(0, pythonRoot)

import GafferOpenCode
GafferOpenCode.initialise(application)

import GafferOpenCodeUI
GafferUI.Layouts.acquire(application.root()).registerEditor("OpenCode for Gaffer")
