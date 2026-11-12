# Interface protocol

The CI runs the add-on's tests, the plugin's tools and the conformance checks in a background Blender. What needs Blender's interface (the Undo History, Edit mode, a GPU render, screenshots) is checked by hand, on a workstation, before each release of OpenCode Link, in a conversation with the plugin installed. Record the result in the pull request.

## Requirements

- Blender 5.3 or later, with OpenCode Link of this version installed (`blender_install_host`, or the archive in `host/blender/`) and enabled, in a profile you can change.
- An OpenCode client with this plugin in the global configuration.

## Steps

| #   | Ask the conversation, or do                                                | Check                                                                                                                                |
| --- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | With one Blender open, any Blender request (for example "read the scene"). | The conversation is attached without a permission request; `blender_hosts` shows it attached to "this conversation".                 |
| 2   | "Create three cubes in one apply."                                         | **Edit ▸ Undo History** shows **one** step `OpenCode: …`; one **Ctrl+Z** removes the three cubes, **Ctrl+Shift+Z** brings them back. |
| 3   | Select a cube, press **Tab** (Edit mode), then ask for a change.           | The change is refused as not in Object mode; nothing changed. Press **Tab** again.                                                   |
| 4   | Move a cube by hand, then ask for a change with the revision read before.  | Refused as changed since the last read; a replayed `command_id` applies nothing.                                                     |
| 5   | "Verify the render at frame 1" (EEVEE).                                    | `blender_verify` returns an image and its facts (camera used, black or not).                                                         |
| 6   | "Take a screenshot of the window" (`blender_lab`).                         | The image comes back as an attachment.                                                                                               |
| 7   | "Run `print(1)` in Blender."                                               | `blender_execute_code` asks permission; a refusal runs nothing.                                                                      |
| 8   | Disable OpenCode Link in the preferences.                                  | Within 15 s `blender_hosts` lists no Blender and the conversation is no longer attached.                                             |

## Quarantine

A failed restoration cannot be provoked from the interface: a modal operator only makes the add-on answer `busy`. The background host tests cover it by making the undo fail. In the interface, the quarantine shows as "Quarantined" in the add-on's panel, and **Reset OpenCode Link** clears it.

## Last recorded result

Blender 5.3.0 (Windows), OpenCode Link 0.4.0 through a conversation: the trial missions of the pull request that introduced 0.4.0 (steps 1, 2, 5 and 6 in a real scene).
