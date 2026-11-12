# SPDX-License-Identifier: GPL-3.0-or-later

"""State that lives as long as the Blender process, outside the add-on's own modules.

A hot reload re-imports every module of the add-on; what must survive it (the offer key, which keeps
the conversation's binding, and the document revision) is kept on this process-wide namespace. It is
never saved: a real Blender restart starts afresh.
"""

import sys
import types

NAME = "opencode_link_process_state"


def state():
    return sys.modules.setdefault(NAME, types.ModuleType(NAME))
